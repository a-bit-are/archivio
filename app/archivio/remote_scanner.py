"""
Servizio di Scansione da Dispositivo Mobile e Fotocamera del Computer
(via GitHub Pages + Cloud Relay HTTPS e Rete Locale).
Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3.
"""
from __future__ import annotations

import base64
import datetime
import io
import json
import math
import operator
import os
import re
import secrets
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from .camera_session import (  # noqa: F401
    MacCameraSession,
    MacCameraSignals,
    list_mac_cameras,
)
from .catalog import (
    COLLECTION_TO_PUBLISHER_IT,
    ITALIAN_PUBLISHERS_PRIORITY,
    PUBLISHER_COLLECTIONS_IT,
    CatalogService,
    Suggestion,
    _direct_ratio,
)
from .config import resource_path
from .store import Item
from .textutil import fold, search_fold, sort_key_matches_author, surname_key
from .vision_engine import (  # noqa: F401
    KNOWN_ISSN_SERIES,
    OCR_NOISE_PHRASES,
    OCR_NOISE_WORDS,
    _RECORD_LABEL_NOISE_TOKENS,
    _SOUNDTRACK_CREDIT_PREFIX_RE,
    _SOUNDTRACK_MUSIC_CREDIT_RE,
    _TRIVIAL_STOPWORDS,
    _XP_COVER_CACHE,
    _XP_COVER_CACHE_LOCK,
    _candidate_cover_crops,
    _clean_soundtrack_credit_prefix,
    _decode_barcodes_cross_platform,
    _deinterleave_mixed_case_line,
    _extract_meaningful_tokens,
    _extract_soundtrack_hints,
    _find_tesseract_bin,
    _is_visual_match_trusted,
    _load_cross_platform_seed_covers,
    _match_cover_pil_image,
    _normalize_retail_barcode,
    _ocr_lines_tesseract,
    _ocr_lines_windows,
    _resolve_seed_covers_path,
    _token_matches_set,
    _vision_binary_path,
    build_queries_from_ocr_lines,
    extract_cover_vec256,
    extract_isbn_or_ean_from_ocr_lines,
    generate_qr_png,
    match_cover_artwork_cross_platform,
    match_cover_artwork_from_bytes,
    merge_visual_matches,
    normalize_barcode_input,
    parse_cover_ocr_lines,
    quantize_vec256_b64,
    run_apple_vision_on_image,
)


def detect_lan_ip() -> str:
    """Rileva l'indirizzo IP locale del computer sulla rete Wi-Fi / Ethernet domestica."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        ip = socket.gethostbyname(hostname)
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return "127.0.0.1"


class RemoteScannerSignals(QObject):
    """Segnali Qt thread-safe verso la MainWindow."""
    categoryChanged = Signal(str)
    itemPreviewed = Signal(str, object, str)     # (category, Item, badge_text)
    itemAdded = Signal(str, object)              # (category, Item)
    undoRequested = Signal()
    scanLogEvent = Signal(str, str)              # (level, message)


MAX_NTFY_ATTACHMENT_BYTES = 6 * 1024 * 1024


def _is_safe_ntfy_attachment_url(url: str) -> bool:
    """Consente esclusivamente URL HTTPS verso ntfy.sh senza credenziali o porte custom."""
    try:
        parsed = urllib.parse.urlparse(str(url or "").strip())
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "ntfy.sh"
        and parsed.port is None
        and parsed.username is None
        and parsed.password is None
    )


class _NoFqdnThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer che evita socket.getfqdn() bloccante (fino a 35s su macOS/DNS lenti)."""

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host or "localhost"
        self.server_port = port


class RemoteScannerService:
    """
    Gestisce:
    1) Il micro-server HTTP locale per la rete locale offline Wi-Fi (`scanner.html` e API `/api/action`).
    2) Il canale Cloud Pub/Sub (`ntfy.sh`) per collegare qualsiasi dispositivo mobile tramite
       la pagina ufficiale GitHub Pages in HTTPS (funziona sia in Wi-Fi che sotto rete cellulare 4G/5G).
    """

    def __init__(
        self,
        catalog: CatalogService,
        get_state_fn: Callable[[], Tuple[str, str, Dict[str, List[Item]]]],
    ):
        self.catalog = catalog
        self.get_state_fn = get_state_fn
        self.signals = RemoteScannerSignals()

        self.room_token = self._generate_token()
        self.port = 0
        self.lan_ip = detect_lan_ip()
        self.custom_gh_pages_url = "https://a-bit-are.github.io/archivio/scanner.html"
        self.connection_mode = "ghpages"  # "ghpages" | "local"
        self.online_enabled = True

        self._httpd: Optional[ThreadingHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self._ntfy_stop = threading.Event()
        self._ntfy_thread: Optional[threading.Thread] = None
        self._ntfy_sem = threading.Semaphore(4)
        self._running = False

    @staticmethod
    def _generate_token() -> str:
        alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
        p1 = "".join(secrets.choice(alphabet) for _ in range(4))
        p2 = "".join(secrets.choice(alphabet) for _ in range(4))
        return f"ARCH-{p1}-{p2}"

    def regenerate_session(self) -> str:
        self.room_token = self._generate_token()
        self._restart_ntfy_listener()
        return self.room_token

    def set_connection_mode(self, mode: str, gh_pages_url: Optional[str] = None) -> None:
        if mode in ("ghpages", "local"):
            self.connection_mode = mode
        if gh_pages_url is not None:
            self.custom_gh_pages_url = gh_pages_url.strip()

    def start(self) -> None:
        if self._running:
            return
        self.lan_ip = detect_lan_ip()
        try:
            self._start_http_server()
            self._running = True
        except Exception:
            self._running = False
            raise
        self._restart_ntfy_listener()

    def stop(self) -> None:
        self._running = False
        self._ntfy_stop.set()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass
            self._httpd = None

    def get_scanner_html_bytes(self) -> bytes:
        candidates = [
            resource_path("data", "scanner.html"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "scanner.html"),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "docs", "scanner.html")),
        ]
        for p in candidates:
            if os.path.isfile(p):
                with open(p, "rb") as f:
                    return f.read()
        return b"<html><body>Scanner non trovato</body></html>"

    def local_url(self, lang: str = "it", category: str = "Libri") -> str:
        return f"http://{self.lan_ip}:{self.port}/scanner.html?room={self.room_token}&lang={lang}&cat={category}"

    def ghpages_url(self, lang: str = "it", category: str = "Libri") -> str:
        base = self.custom_gh_pages_url.strip().rstrip("/")
        if not base:
            return ""
        if not base.endswith(".html"):
            base += "/scanner.html"
        return f"{base}?room={self.room_token}&lang={lang}&cat={category}"

    def internet_url(self, lang: str = "it", category: str = "Libri") -> str:
        if self.connection_mode == "local":
            return self.local_url(lang, category)
        return self.ghpages_url(lang, category) or self.local_url(lang, category)

    # ------------------------------------------------------------------
    # 1. SERVER HTTP LOCALE
    # ------------------------------------------------------------------
    def _start_http_server(self) -> None:
        service = self

        class ScannerRequestHandler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path.startswith("/api/ping"):
                    self._send_json({"ok": True})
                    return
                html = service.get_scanner_html_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)

            def do_POST(self) -> None:  # noqa: N802
                if not self.path.startswith("/api/action"):
                    self._send_json({"error": "Not found"}, status=404)
                    return
                try:
                    self.connection.settimeout(15.0)
                    raw_len = self.headers.get("Content-Length")
                    try:
                        length = int(raw_len or "0")
                    except ValueError:
                        self._send_json({"status": "error", "message": "Invalid Content-Length"}, status=400)
                        return
                    if length <= 0 or length > 16 * 1024 * 1024:
                        self._send_json(
                            {"status": "error", "message": "Payload size out of bounds"},
                            status=413 if length > 16 * 1024 * 1024 else 400,
                        )
                        return
                    raw_body = self.rfile.read(length)
                    try:
                        payload = json.loads(raw_body.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError):
                        self._send_json({"status": "error", "message": "Invalid JSON payload"}, status=400)
                        return
                    if not isinstance(payload, dict):
                        self._send_json({"status": "error", "message": "Invalid JSON object"}, status=400)
                        return
                    supplied_room = str(payload.get("room") or "").strip()
                    if not secrets.compare_digest(supplied_room, service.room_token):
                        self._send_json(
                            {"status": "error", "message": "Sessione scaduta o token non valido"},
                            status=403,
                        )
                        return
                    resp = service.process_remote_action(payload)
                    self._send_json(resp)
                except Exception:
                    self._send_json({"status": "error", "message": "Errore interno del server"}, status=500)

            def _send_json(self, obj: Dict[str, Any], status: int = 200) -> None:
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = _NoFqdnThreadingHTTPServer(("0.0.0.0", 0), ScannerRequestHandler)
        self.port = self._httpd.server_address[1]
        self._http_thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._http_thread.start()

    # ------------------------------------------------------------------
    # 2. CANALE CLOUD RELAY NTFY.SH (PER GITHUB PAGES STATICO SU WI-FI E 4G/5G)
    #    Supporta chunking automatico <3KB e fallback allegati per risposte/foto >4KB
    # ------------------------------------------------------------------
    def _restart_ntfy_listener(self) -> None:
        self._ntfy_stop.set()
        if os.environ.get("ARCHIVIO_DISABLE_NTFY") == "1" or os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        self._ntfy_stop = threading.Event()
        stop_ev = self._ntfy_stop
        token = self.room_token

        def _handle_payload_async(pl: Dict[str, Any]) -> None:
            try:
                reply = self.process_remote_action(pl)
                self._publish_ntfy_reply(token, reply)
            except Exception:
                pass
            finally:
                self._ntfy_sem.release()

        def _listen() -> None:
            url = f"https://ntfy.sh/archivio_req_{token}/json"
            chunk_buf: Dict[str, Dict[str, Any]] = {}
            backoff = 2.0
            while self._running and not stop_ev.is_set() and token == self.room_token:
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "Archivio/1.0.0"})
                    with urllib.request.urlopen(req, timeout=45) as resp:
                        backoff = 2.0
                        for raw_line in resp:
                            if stop_ev.is_set() or not self._running or token != self.room_token:
                                break
                            line = raw_line.decode("utf-8", errors="ignore").strip()
                            if not line:
                                continue
                            try:
                                ev = json.loads(line)
                                if not isinstance(ev, dict) or ev.get("event") != "message":
                                    continue
                                raw_msg = str(ev.get("message") or "")
                                att = ev.get("attachment")
                                if (not raw_msg.strip()) and isinstance(att, dict) and att.get("url"):
                                    att_url = str(att["url"]).strip()
                                    if not _is_safe_ntfy_attachment_url(att_url):
                                        continue
                                    att_req = urllib.request.Request(
                                        att_url, headers={"User-Agent": "Archivio/1.0.0"}
                                    )
                                    with urllib.request.urlopen(att_req, timeout=10) as att_resp:
                                        if not _is_safe_ntfy_attachment_url(att_resp.geturl()):
                                            continue
                                        att_bytes = att_resp.read(MAX_NTFY_ATTACHMENT_BYTES + 1)
                                        if len(att_bytes) > MAX_NTFY_ATTACHMENT_BYTES:
                                            continue
                                        raw_msg = att_bytes.decode("utf-8", errors="ignore")
                                if not raw_msg.strip():
                                    continue

                                msg_obj = json.loads(raw_msg)
                                if not isinstance(msg_obj, dict):
                                    continue
                                if "_chunk" in msg_obj and "_total" in msg_obj and "_id" in msg_obj:
                                    now_ts = time.time()
                                    for old_k in [k for k, v in chunk_buf.items() if now_ts - v["ts"] > 60]:
                                        chunk_buf.pop(old_k, None)
                                    cid = str(msg_obj["_id"])
                                    idx = int(msg_obj["_chunk"])
                                    tot = int(msg_obj["_total"])
                                    part_str = str(msg_obj.get("_data") or "")
                                    if not cid or tot <= 0 or tot > 512 or idx < 0 or idx >= tot or len(part_str) > 16384:
                                        continue
                                    if cid not in chunk_buf and len(chunk_buf) >= 32:
                                        continue
                                    entry = chunk_buf.setdefault(cid, {"ts": now_ts, "total": tot, "parts": {}})
                                    if entry["total"] != tot:
                                        continue
                                    entry["ts"] = now_ts
                                    entry["parts"][idx] = part_str
                                    if len(entry["parts"]) < tot:
                                        continue
                                    full_json = "".join(entry["parts"].get(i, "") for i in range(tot))
                                    chunk_buf.pop(cid, None)
                                    if len(full_json.encode("utf-8")) > MAX_NTFY_ATTACHMENT_BYTES:
                                        continue
                                    payload = json.loads(full_json)
                                else:
                                    payload = msg_obj

                                if not isinstance(payload, dict):
                                    continue
                                if self._ntfy_sem.acquire(blocking=False):
                                    threading.Thread(
                                        target=_handle_payload_async, args=(payload,), daemon=True
                                    ).start()
                            except Exception:
                                continue
                except Exception:
                    stop_ev.wait(backoff)
                    backoff = min(backoff * 1.5, 20.0)

        self._ntfy_thread = threading.Thread(target=_listen, daemon=True)
        self._ntfy_thread.start()

    def _publish_ntfy_reply(self, token: str, reply: Dict[str, Any]) -> None:
        try:
            url = f"https://ntfy.sh/archivio_res_{token}"
            raw_str = json.dumps(reply, ensure_ascii=False)
            raw_bytes = raw_str.encode("utf-8")
            # ntfy.sh converte i body > 4096 byte in allegati binari; inviamo sempre pacchetti < 3000 byte
            if len(raw_bytes) <= 2900:
                req = urllib.request.Request(
                    url, data=raw_bytes, method="POST", headers={"User-Agent": "Archivio/1.0.0"}
                )
                with urllib.request.urlopen(req, timeout=6):
                    pass
                return

            slice_len = 2200
            parts = [raw_str[i : i + slice_len] for i in range(0, len(raw_str), slice_len)]
            msg_id = secrets.token_hex(6)
            total = len(parts)
            for idx, part in enumerate(parts):
                pkt = json.dumps(
                    {"_id": msg_id, "_chunk": idx, "_total": total, "_data": part},
                    ensure_ascii=False,
                ).encode("utf-8")
                req = urllib.request.Request(
                    url, data=pkt, method="POST", headers={"User-Agent": "Archivio/1.0.0"}
                )
                with urllib.request.urlopen(req, timeout=6):
                    pass
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 4. ELABORAZIONE DELLE AZIONI DA DISPOSITIVO MOBILE O FOTOCAMERA MAC
    # ------------------------------------------------------------------
    def process_remote_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        resp = self._dispatch_remote_action(payload)
        if payload.get("req_id") is not None:
            resp["req_id"] = payload.get("req_id")
        if payload.get("req_kind"):
            resp["req_kind"] = payload.get("req_kind")
        return resp

    def _dispatch_remote_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        action = str(payload.get("action") or "").strip()
        cur_cat, lang, data_by_cat = self.get_state_fn()
        req_cat = str(payload.get("category") or cur_cat or "Libri")
        if req_cat not in ("Libri", "CD", "Vinili"):
            req_cat = cur_cat

        auto_add = bool(payload.get("auto_add"))
        sticky_coll = str(payload.get("sticky_collection") or "").strip()
        device_label = str(payload.get("device_label") or "dispositivo mobile").strip()

        if action == "set_category":
            self.signals.categoryChanged.emit(req_cat)
            self.signals.scanLogEvent.emit("info", f"Categoria cambiata da {device_label}: {req_cat}")
            return {"status": "ok", "category": req_cat}

        if action == "undo_last":
            self.signals.undoRequested.emit()
            self.signals.scanLogEvent.emit("warn", f"Annullato ultimo inserimento da {device_label}")
            return {"status": "undone", "category": req_cat}

        if action == "preview_item":
            raw_it = payload.get("item") or {}
            it = self._dict_to_item(raw_it, req_cat, sticky_coll)
            is_dup = self._check_duplicate(it, data_by_cat.get(req_cat, []))
            want_remote = bool(payload.get("fetch_remote", True))
            ed_dict = self._build_editions_payload(req_cat, "", it.title, it.author, it.publisher, it.year, it.collection, lang, fetch_remote=want_remote)
            if not it.publisher and ed_dict.get("default_publisher"):
                it.publisher = ed_dict["default_publisher"]
            if not it.year and ed_dict.get("default_year"):
                it.year = ed_dict["default_year"]
            self.signals.itemPreviewed.emit(req_cat, it, f"Anteprima da {device_label}")
            return {
                "status": "ok",
                "category": req_cat,
                "item": self._item_to_dict(it),
                "editions": ed_dict,
                "is_duplicate": is_dup,
                "added": False,
                "source": device_label,
            }

        if action == "fetch_editions":
            title = str(payload.get("title") or "").strip()
            author = str(payload.get("author") or "").strip()
            work_id = str(payload.get("work_id") or "").strip()
            ed_dict = self._build_editions_payload(req_cat, work_id, title, author, "", "", sticky_coll, lang, fetch_remote=True)
            return {
                "status": "editions_only",
                "category": req_cat,
                "editions": ed_dict,
            }

        if action == "autocomplete":
            query = str(payload.get("query") or "").strip()
            field_mode = str(payload.get("field_mode") or "both").strip()
            return self._handle_live_autocomplete(query, field_mode, req_cat, lang, data_by_cat, sticky_coll)

        if action == "add_item":
            raw_it = payload.get("item") or {}
            it = self._dict_to_item(raw_it, req_cat, sticky_coll)
            if not it.title or not it.author:
                return {"status": "error", "message": "Titolo e Autore obbligatori"}
            self.signals.itemAdded.emit(req_cat, it)
            self.signals.scanLogEvent.emit("ok", f"✓ Aggiunto da {device_label} [{req_cat}]: {it.title} — {it.author}")
            return {
                "status": "ok",
                "category": req_cat,
                "item": self._item_to_dict(it),
                "added": True,
                "source": device_label,
            }

        if action == "scan_barcode":
            raw_code = str(payload.get("code") or "").strip()
            return self._handle_barcode_scan(
                raw_code, req_cat, lang, data_by_cat, auto_add, sticky_coll, device_label=device_label
            )

        if action == "scan_ocr_lines":
            barcodes = payload.get("barcodes") or []
            lines = payload.get("lines") or []
            visual_matches = payload.get("visual_matches") or []
            return self._handle_vision_ocr_payload(
                barcodes,
                lines,
                req_cat,
                lang,
                data_by_cat,
                auto_add,
                sticky_coll,
                visual_matches=visual_matches,
                device_label=device_label,
            )

        if action == "decode_photo_barcode":
            b64 = str(payload.get("image_b64") or "")
            return self._handle_photo_barcode(b64, req_cat, lang, data_by_cat, auto_add, sticky_coll)

        if action == "decode_photo_ocr":
            b64 = str(payload.get("image_b64") or "")
            return self._handle_photo_ocr(b64, req_cat, lang, data_by_cat, auto_add, sticky_coll)

        if action == "search_text":
            query = str(payload.get("query") or "").strip()
            return self._handle_text_search(query, req_cat, lang, data_by_cat, auto_add, sticky_coll)

        return {"status": "ok", "category": req_cat}

    def _build_editions_payload(
        self,
        category: str,
        work_id: str,
        title: str,
        author: str,
        cur_pub: str,
        cur_year: str,
        cur_coll: str,
        lang: str,
        fetch_remote: bool = False,
    ) -> Dict[str, Any]:
        """
        Costruisce il dizionario completo delle scelte a tendina per lo smartphone:
        - Case Editrici / Etichette discografiche (`publishers`)
        - Collane / Collezioni (`collections` + mappa per editore `collections_by_pub`)
        - Anni di uscita (`years` + mappa `publisher_years`)
        - Metadati automatici quando si sceglie una collana (`collection_meta`)
        """
        bundle = None
        if title and author:
            norm_key = f"{search_fold(title)}|{search_fold(author)}"
            cache_id = work_id or norm_key
            cached = self.catalog.cache.get(f"editions:v10:{category}:{cache_id}")
            if cached or fetch_remote:
                try:
                    bundle = self.catalog.fetch_editions(category, work_id, title, author, lang=lang)
                except Exception:
                    bundle = None

        pubs: List[str] = []
        if cur_pub:
            pubs.append(cur_pub)
        if bundle and bundle.publishers:
            for p in bundle.publishers:
                if p and p not in pubs:
                    pubs.append(p)

        yrs: List[str] = []
        if cur_year:
            yrs.append(cur_year)
        if bundle and bundle.years:
            for y in bundle.years:
                if y and y not in yrs:
                    yrs.append(y)

        colls: List[str] = []
        if cur_coll:
            colls.append(cur_coll)
        if bundle and bundle.collections:
            for c in bundle.collections:
                if c and c not in colls:
                    colls.append(c)
        for p in pubs[:4]:
            for c in self.catalog.collections_for_publisher(p, category, author):
                if c and c not in colls:
                    colls.append(c)

        collections_by_pub: Dict[str, List[str]] = {}
        for p in pubs:
            c_list = self.catalog.collections_for_publisher(p, category, author)
            if c_list:
                collections_by_pub[p] = c_list

        pub_years: Dict[str, str] = dict(bundle.publisher_years) if (bundle and bundle.publisher_years) else {}
        coll_meta: Dict[str, Dict[str, str]] = dict(bundle.collection_meta) if (bundle and bundle.collection_meta) else {}

        # Aggiungi nella mappa coll_meta anche tutte le collane standard per consentire risoluzione istantanea sul telefono
        for c_name in colls:
            cf = fold(c_name)
            if cf not in coll_meta:
                rp, ry = self.catalog.resolve_collection_metadata(c_name, category, author=author, bundle=bundle)
                if rp or ry:
                    coll_meta[c_name] = {"publisher": rp, "year": ry}
                    coll_meta[cf] = {"publisher": rp, "year": ry}

        year_pubs: Dict[str, str] = dict(bundle.year_publishers) if (bundle and bundle.year_publishers) else {}
        if not year_pubs and pub_years:
            for p, y in pub_years.items():
                if y and y not in year_pubs:
                    year_pubs[y] = p

        year_colls: Dict[str, str] = dict(bundle.year_collections) if (bundle and bundle.year_collections) else {}
        pub_colls: Dict[str, str] = dict(bundle.publisher_collections) if (bundle and bundle.publisher_collections) else {}
        pub_all_years: Dict[str, List[str]] = (
            {k: list(v) for k, v in bundle.publisher_all_years.items()}
            if (bundle and bundle.publisher_all_years)
            else {}
        )
        year_all_pubs: Dict[str, List[str]] = (
            {k: list(v) for k, v in bundle.year_all_publishers.items()}
            if (bundle and bundle.year_all_publishers)
            else {}
        )
        pair_colls: Dict[str, List[str]] = (
            {k: list(v) for k, v in bundle.pair_collections.items()}
            if (bundle and bundle.pair_collections)
            else {}
        )
        for c, m in coll_meta.items():
            if not isinstance(m, dict):
                continue
            cy = m.get("year")
            cp = m.get("publisher")
            if cy and cy not in year_colls:
                year_colls[cy] = c
            if cp and cp not in pub_colls:
                pub_colls[cp] = c
            if cp and cy:
                pk = f"{fold(cp)}|{cy}"
                lst = pair_colls.setdefault(pk, [])
                if all(fold(x) != fold(c) for x in lst):
                    lst.append(c)

        return {
            "publishers": pubs[:30],
            "collections": colls[:40],
            "years": yrs[:40],
            "default_publisher": (bundle.default_publisher if bundle else "") or (pubs[0] if pubs else ""),
            "default_year": (bundle.default_year if bundle else "") or (yrs[0] if yrs else ""),
            "publisher_years": pub_years,
            "year_publishers": year_pubs,
            "publisher_all_years": pub_all_years,
            "year_all_publishers": year_all_pubs,
            "pair_collections": pair_colls,
            "year_collections": year_colls,
            "publisher_collections": pub_colls,
            "collections_by_pub": collections_by_pub,
            "collection_meta": coll_meta,
            "is_single_edition": bool(len(pubs) <= 1 and len(yrs) <= 1),
        }

    def _search_combined_suggestions(
        self,
        query: str,
        category: str,
        lang: str,
        archive_items: List[Item],
        field_mode: str = "both",
        include_online: bool = True,
        limit: int = 8,
    ) -> List[Suggestion]:
        """
        Cerca contemporaneamente per Titolo e per Autore/Artista così l'utente sullo smartphone
        trova subito sia 'Cicerone' (tutte le sue opere più famose) sia 'L'amicizia' o 'Cicerone amicizia'!
        """
        merged_map: Dict[str, Suggestion] = {}
        modes = ("title", "author") if field_mode == "both" else (field_mode,)

        for fm in modes:
            for s in self.catalog.search_local(category, query, archive_items, lang=lang, field_mode=fm, limit=12):
                self.catalog._merge_suggestion(merged_map, s)

        if include_online:
            for fm in modes:
                for s in self.catalog.search_online(category, query, lang=lang, field_mode=fm, limit=10):
                    self.catalog._merge_suggestion(merged_map, s)

        return sorted(merged_map.values(), key=lambda s: -s.score)[:limit]

    def _handle_live_autocomplete(
        self,
        query: str,
        field_mode: str,
        category: str,
        lang: str,
        data_by_cat: Dict[str, List[Item]],
        sticky_coll: str,
    ) -> Dict[str, Any]:
        if len(query.strip()) < 2:
            return {"status": "autocomplete", "candidates": []}
        archive_items = data_by_cat.get(category, [])
        sugs = self._search_combined_suggestions(
            query,
            category,
            lang,
            archive_items,
            field_mode=field_mode,
            include_online=bool(self.online_enabled),
            limit=5,
        )
        candidates = [
            {
                "title": s.title,
                "author": s.author,
                "publisher": s.publisher or (s.publishers[0] if s.publishers else ""),
                "year": s.year or (s.years[0] if s.years else ""),
                "collection": s.collection or sticky_coll,
                "work_id": s.work_id,
                "publishers": s.publishers,
                "years": s.years,
                "collections": s.collections,
            }
            for s in sugs[:4]
        ]
        return {"status": "autocomplete", "category": category, "candidates": candidates}

    def _handle_barcode_scan(
        self,
        raw_code: str,
        category: str,
        lang: str,
        data_by_cat: Dict[str, List[Item]],
        auto_add: bool,
        sticky_coll: str,
        device_label: str = "dispositivo mobile",
    ) -> Dict[str, Any]:
        code, is_issn, issn_series, issn_pub = normalize_barcode_input(raw_code)
        if not code:
            return {"status": "not_found", "code": raw_code, "category": category}

        # Accorgimento #3: Rilevamento codice periodico da edicola (ISSN 977)
        if is_issn:
            self.signals.scanLogEvent.emit(
                "warn",
                f"Codice periodico da edicola (ISSN {code}): {issn_series}. Scansiona il titolo con OCR.",
            )
            return {
                "status": "issn_warning",
                "code": code,
                "collection": sticky_coll or issn_series,
                "publisher": issn_pub,
                "category": category,
            }

        # Se è un codice ISBN 978/979 ma la categoria era rimasta su CD/Vinili, commuta su Libri
        is_isbn_13 = len(code) == 13 and code[:3] in ("978", "979")
        if is_isbn_13 and category != "Libri":
            category = "Libri"
            self.signals.categoryChanged.emit("Libri")

        archive_items = data_by_cat.get(category, [])
        suggestions = self.catalog.search_online(category, code, lang=lang, field_mode="title", limit=35)
        if not suggestions:
            suggestions = self.catalog.search_local(category, code, archive_items, lang=lang, field_mode="title", limit=10)

        # Se era selezionato Libri ma il barcode non è ISBN (es. UPC/EAN di un disco), prova in Vinili
        if not suggestions and category == "Libri" and not is_isbn_13:
            vinyl_items = data_by_cat.get("Vinili", [])
            suggestions = self.catalog.search_online("Vinili", code, lang=lang, field_mode="title", limit=35)
            if not suggestions:
                suggestions = self.catalog.search_local("Vinili", code, vinyl_items, lang=lang, field_mode="title", limit=10)
            if suggestions:
                category = "Vinili"
                archive_items = vinyl_items
                self.signals.categoryChanged.emit("Vinili")

        if not suggestions:
            self.signals.scanLogEvent.emit("warn", f"Nessun risultato per il codice {code} in {category}")
            return {"status": "not_found", "code": code, "category": category}

        best = suggestions[0]
        item = self._suggestion_to_item(best, category, code, sticky_coll, lang)
        is_dup = self._check_duplicate(item, archive_items)
        ed_dict = self._build_editions_payload(
            category, best.work_id, item.title, item.author, item.publisher, item.year, item.collection, lang, fetch_remote=False
        )

        candidates = [
            {
                "title": s.title,
                "author": s.author,
                "publisher": s.publisher,
                "year": s.year,
                "collection": s.collection or sticky_coll,
                "work_id": s.work_id,
            }
            for s in suggestions[:35]
        ]
        is_multi_isbn = len(candidates) > 1

        if auto_add and not is_dup and not is_multi_isbn and item.title and item.author:
            self.signals.itemAdded.emit(category, item)
            self.signals.scanLogEvent.emit(
                "ok", f"✓ Raffica [{category}] ({code}): {item.title} — {item.author} ({item.publisher}, {item.year})"
            )
            return {
                "status": "ok",
                "category": category,
                "code": code,
                "item": self._item_to_dict(item),
                "editions": ed_dict,
                "candidates": candidates,
                "multi_isbn": False,
                "is_duplicate": False,
                "added": True,
                "source": f"Barcode {code}",
            }

        self.signals.itemPreviewed.emit(category, item, f"Codice {code}")
        if is_multi_isbn:
            self.signals.scanLogEvent.emit(
                "warn",
                f"Codice ISBN {code} condiviso da {len(candidates)} volumi (promozione/collana): seleziona il titolo su {device_label}",
            )
        else:
            self.signals.scanLogEvent.emit(
                "info", f"Riconosciuto codice {code}: {item.title} — {item.author} ({item.publisher}, {item.year})"
            )
        return {
            "status": "ok",
            "category": category,
            "code": code,
            "item": self._item_to_dict(item),
            "editions": ed_dict,
            "candidates": candidates,
            "multi_isbn": is_multi_isbn,
            "is_duplicate": is_dup,
            "added": False,
            "source": f"Barcode {code}",
        }

    def _resolve_vision_scan(
        self,
        barcodes: List[str],
        lines: List[Dict[str, Any]],
        visual_matches: Optional[List[Dict[str, Any]]],
        category: str,
        lang: str,
        data_by_cat: Dict[str, List[Item]],
        auto_add: bool,
        sticky_coll: str,
        device_label: str = "Fotocamera del computer",
        prefer_raw_barcode: bool = False,
        not_found_code: str = "OCR Copertina",
        not_found_log: str = "",
    ) -> Dict[str, Any]:
        """Risolve un frame scansionato combinando barcode, testo OCR e riconoscimento visivo della copertina."""
        if prefer_raw_barcode and barcodes:
            return self._handle_barcode_scan(
                barcodes[0], category, lang, data_by_cat, auto_add, sticky_coll, device_label=device_label
            )
        for bc in barcodes or []:
            clean_c, is_issn, _, _ = normalize_barcode_input(bc)
            if clean_c and not is_issn and len(clean_c) in (10, 12, 13):
                return self._handle_barcode_scan(
                    clean_c, category, lang, data_by_cat, auto_add, sticky_coll, device_label=device_label
                )
        ocr_code = extract_isbn_or_ean_from_ocr_lines(lines or [])
        if ocr_code:
            return self._handle_barcode_scan(
                ocr_code, category, lang, data_by_cat, auto_add, sticky_coll, device_label=device_label
            )

        queries, det_pub, det_coll, cover_tokens, likely_pairs = parse_cover_ocr_lines(lines or [])

        valid_visual: List[Dict[str, Any]] = []
        for vm in visual_matches or []:
            if not isinstance(vm, dict):
                continue
            v_title = str(vm.get("title") or "").strip()
            v_artist = str(vm.get("artist") or "").strip()
            try:
                v_dist = float(vm.get("distance", 9.9))
            except (TypeError, ValueError):
                v_dist = 9.9
            if v_title and v_artist and v_dist <= 0.75:
                valid_visual.append({
                    "title": v_title,
                    "artist": v_artist,
                    "year": str(vm.get("year") or ""),
                    "distance": v_dist,
                })
        valid_visual.sort(key=lambda x: float(x["distance"]))

        trusted_visual = [
            vm for vm in valid_visual
            if _is_visual_match_trusted(vm, lines, cover_tokens)
        ]

        def _build_visual_queries_and_pairs() -> Tuple[List[str], List[Tuple[str, str]]]:
            v_queries: List[str] = []
            v_pairs: List[Tuple[str, str]] = []
            for vm in trusted_visual[:3]:
                vt = vm["title"]
                va = vm["artist"]
                vq = f"{vt} {va}".strip()
                if vq and vq not in v_queries:
                    v_queries.append(vq)
                if (vt, va) not in v_pairs:
                    v_pairs.append((vt, va))
            if trusted_visual:
                t0 = trusted_visual[0]["title"]
                if t0 and t0 not in v_queries:
                    v_queries.append(t0)
            return v_queries, v_pairs

        best_dist = float(trusted_visual[0]["distance"]) if trusted_visual else 9.9
        max_line_h = max(
            (float(x.get("height") or 0.0) for x in (lines or []) if isinstance(x, dict)),
            default=0.0,
        )
        if trusted_visual and best_dist <= 0.65 and max_line_h < 0.032:
            queries = []
            det_pub = ""
            det_coll = ""
            cover_tokens = set()
            likely_pairs = []

        if trusted_visual and category == "Libri":
            if not queries or (best_dist <= 0.68 and not det_pub and not det_coll):
                category = "Vinili"
                self.signals.categoryChanged.emit("Vinili")

        if not queries:
            if not trusted_visual:
                if not_found_log:
                    self.signals.scanLogEvent.emit("warn", not_found_log)
                return {"status": "not_found", "code": not_found_code, "category": category}
            vis_queries, vis_pairs = _build_visual_queries_and_pairs()
            return self._handle_text_search(
                vis_queries[0],
                category,
                lang,
                data_by_cat,
                auto_add=auto_add,
                sticky_coll=sticky_coll or det_coll,
                extra_queries=vis_queries[1:5],
                detected_pub=det_pub,
                cover_tokens=cover_tokens,
                likely_pairs=vis_pairs,
                visual_matches=trusted_visual,
                device_label=device_label,
            )

        use_queries = list(queries)
        use_pairs = list(likely_pairs or [])
        if trusted_visual and best_dist <= 0.68:
            vis_queries, vis_pairs = _build_visual_queries_and_pairs()
            for vq in reversed(vis_queries[:2]):
                if vq in use_queries:
                    use_queries.remove(vq)
                use_queries.insert(0, vq)
            for vp in reversed(vis_pairs[:2]):
                if vp in use_pairs:
                    use_pairs.remove(vp)
                use_pairs.insert(0, vp)

        credit_artists, teaser_tokens = _extract_soundtrack_hints(lines)

        res = self._handle_text_search(
            use_queries[0],
            category,
            lang,
            data_by_cat,
            auto_add=auto_add,
            sticky_coll=sticky_coll or det_coll,
            extra_queries=use_queries[1:5],
            detected_pub=det_pub,
            cover_tokens=cover_tokens,
            likely_pairs=use_pairs,
            visual_matches=trusted_visual,
            device_label=device_label,
            credit_artists=credit_artists,
            teaser_tokens=teaser_tokens,
        )
        if res.get("status") == "not_found" and trusted_visual:
            vis_queries, vis_pairs = _build_visual_queries_and_pairs()
            if vis_queries and (use_queries[0] != vis_queries[0] or category == "Libri"):
                if category == "Libri":
                    category = "Vinili"
                    self.signals.categoryChanged.emit("Vinili")
                return self._handle_text_search(
                    vis_queries[0],
                    category,
                    lang,
                    data_by_cat,
                    auto_add=auto_add,
                    sticky_coll=sticky_coll or det_coll,
                    extra_queries=vis_queries[1:5],
                    detected_pub=det_pub,
                    cover_tokens=cover_tokens,
                    likely_pairs=vis_pairs,
                    visual_matches=trusted_visual,
                    device_label=device_label,
                )
        return res

    def _handle_vision_ocr_payload(
        self,
        barcodes: List[str],
        lines: List[Dict[str, Any]],
        category: str,
        lang: str,
        data_by_cat: Dict[str, List[Item]],
        auto_add: bool,
        sticky_coll: str,
        visual_matches: Optional[List[Dict[str, Any]]] = None,
        device_label: str = "Fotocamera del computer",
    ) -> Dict[str, Any]:
        return self._resolve_vision_scan(
            barcodes=barcodes,
            lines=lines,
            visual_matches=visual_matches,
            category=category,
            lang=lang,
            data_by_cat=data_by_cat,
            auto_add=auto_add,
            sticky_coll=sticky_coll,
            device_label=device_label,
            prefer_raw_barcode=False,
            not_found_code="OCR Copertina",
        )

    def _handle_photo_barcode(
        self,
        b64_data: str,
        category: str,
        lang: str,
        data_by_cat: Dict[str, List[Item]],
        auto_add: bool,
        sticky_coll: str,
    ) -> Dict[str, Any]:
        tmp_path = self._save_base64_image(b64_data)
        if not tmp_path:
            return {"status": "not_found", "code": "Foto non valida", "category": category}
        try:
            vision_res = run_apple_vision_on_image(tmp_path, mode="scan")
            return self._resolve_vision_scan(
                barcodes=vision_res.get("barcodes") or [],
                lines=vision_res.get("lines") or [],
                visual_matches=vision_res.get("visual_matches") or [],
                category=category,
                lang=lang,
                data_by_cat=data_by_cat,
                auto_add=auto_add,
                sticky_coll=sticky_coll,
                device_label="Dispositivo remoto",
                prefer_raw_barcode=True,
                not_found_code="Foto",
                not_found_log="Nessun codice a barre rilevato nella foto inviata",
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _handle_photo_ocr(
        self,
        b64_data: str,
        category: str,
        lang: str,
        data_by_cat: Dict[str, List[Item]],
        auto_add: bool,
        sticky_coll: str,
    ) -> Dict[str, Any]:
        tmp_path = self._save_base64_image(b64_data)
        if not tmp_path:
            return {"status": "not_found", "code": "Foto non valida", "category": category}
        try:
            vision_res = run_apple_vision_on_image(tmp_path, mode="ocr")
            return self._resolve_vision_scan(
                barcodes=vision_res.get("barcodes") or [],
                lines=vision_res.get("lines") or [],
                visual_matches=vision_res.get("visual_matches") or [],
                category=category,
                lang=lang,
                data_by_cat=data_by_cat,
                auto_add=False,
                sticky_coll=sticky_coll,
                device_label="Dispositivo remoto",
                prefer_raw_barcode=False,
                not_found_code="OCR Copertina",
            )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _handle_text_search(
        self,
        query: str,
        category: str,
        lang: str,
        data_by_cat: Dict[str, List[Item]],
        auto_add: bool,
        sticky_coll: str,
        extra_queries: Optional[List[str]] = None,
        detected_pub: str = "",
        cover_tokens: Optional[set] = None,
        likely_pairs: Optional[List[Tuple[str, str]]] = None,
        visual_matches: Optional[List[Dict[str, Any]]] = None,
        device_label: str = "Dispositivo remoto",
        credit_artists: Optional[List[str]] = None,
        teaser_tokens: Optional[set] = None,
    ) -> Dict[str, Any]:
        archive_items = data_by_cat.get(category, [])
        all_queries = [query] + [q for q in (extra_queries or []) if q and q != query]
        for ca in (credit_artists or []):
            if ca and ca not in all_queries:
                all_queries.append(ca)

        merged_map: Dict[str, Suggestion] = {}
        # 1. Ricerca locale istantanea (<2ms) su tutte le varianti della query (sia titolo che autore)
        for q_str in all_queries[:5]:
            for fm in ("title", "author"):
                for s in self.catalog.search_local(category, q_str, archive_items, lang=lang, field_mode=fm, limit=10):
                    self.catalog._merge_suggestion(merged_map, s)
        for tc, ac in (likely_pairs or [])[:4]:
            if tc and ac:
                for s in self.catalog.search_local(category, tc, archive_items, lang=lang, field_mode="title", limit=10, context_author=ac):
                    self.catalog._merge_suggestion(merged_map, s)

        # 2. Ricerca online in parallelo strutturata + full-text
        from concurrent.futures import ThreadPoolExecutor

        tasks: List[Tuple[str, str, str]] = []  # (query, field_mode, context_author)
        for tc, ac in (likely_pairs or [])[:4]:
            if tc and ac:
                tasks.append((tc, "title", ac))
                tasks.append((ac, "title", tc))

        for q_str in all_queries[:5]:
            tasks.append((q_str, "title", ""))
            if len(q_str.split()) <= 3:
                tasks.append((q_str, "author", ""))

        seen_tasks = set()
        uniq_tasks: List[Tuple[str, str, str]] = []
        for q_s, fm, ctx_a in tasks:
            t_key = (search_fold(q_s), fm, search_fold(ctx_a))
            if t_key not in seen_tasks and q_s.strip():
                seen_tasks.add(t_key)
                uniq_tasks.append((q_s, fm, ctx_a))

        with ThreadPoolExecutor(max_workers=max(1, min(len(uniq_tasks), 10))) as pool:
            futs = [
                pool.submit(
                    self.catalog.search_online,
                    category,
                    q_s,
                    "it" if lang == "it" else "en",
                    fm,
                    12,
                    context_author=ctx_a,
                )
                for q_s, fm, ctx_a in uniq_tasks
            ]
            for fut in futs:
                try:
                    for s in fut.result() or []:
                        self.catalog._merge_suggestion(merged_map, s)
                except Exception:
                    pass

        if not merged_map:
            return {"status": "not_found", "code": query, "ocr_query": query, "category": category}

        # Se proviene da OCR di copertina (`cover_tokens`), premia i candidati in cui sia il titolo
        # sia l'autore compaiono tra le parole effettivamente lette sulla copertina!
        if cover_tokens:
            for s in merged_map.values():
                t_toks = _extract_meaningful_tokens(s.title)
                a_toks = _extract_meaningful_tokens(s.author)
                t_matched = [w for w in t_toks if _token_matches_set(w, cover_tokens)]
                a_matched = [w for w in a_toks if _token_matches_set(w, cover_tokens)]
                t_hits = len(t_matched)
                a_hits = len(a_matched)
                a_tok_set = set(a_toks)
                t_non_author_hits = sum(1 for w in t_matched if w not in a_tok_set)
                uniq_covered = {
                    ct for ct in cover_tokens
                    if any(_token_matches_set(w, {ct}) for w in t_matched + a_matched)
                }
                uniq_hits = len(uniq_covered)
                t_ratio = t_hits / max(1, len(t_toks))
                a_ratio = a_hits / max(1, len(a_toks))
                is_self_titled = bool(t_toks and a_toks and set(t_toks) == a_tok_set)

                if t_hits >= 1 and a_hits >= 1:
                    s.score += (
                        8500.0
                        + (t_ratio * 3500.0)
                        + (a_ratio * 2500.0)
                        + (uniq_hits * 1800.0)
                        + (t_non_author_hits * 1400.0)
                    )
                    if is_self_titled and len(cover_tokens - uniq_covered) >= 1:
                        s.score -= 1200.0
                elif t_hits >= 1:
                    s.score += (t_ratio * 3500.0) + t_hits * 400.0 + (uniq_hits * 600.0)
                elif a_hits >= 1:
                    s.score += (a_ratio * 2000.0) + a_hits * 300.0 + (uniq_hits * 400.0)

                if credit_artists and a_tok_set:
                    for ca in credit_artists:
                        ca_toks = _extract_meaningful_tokens(ca)
                        if len(ca_toks) >= 2 and all(_token_matches_set(w, a_tok_set) for w in ca_toks):
                            s.score += 8500.0
                            if teaser_tokens and t_matched and all(_token_matches_set(w, teaser_tokens) for w in t_matched):
                                s.score += 1200.0
                            break
                    else:
                        if (
                            teaser_tokens
                            and t_matched
                            and a_hits == 0
                            and all(_token_matches_set(w, teaser_tokens) for w in t_matched)
                        ):
                            s.score -= 3500.0

                if detected_pub and s.publisher:
                    if search_fold(detected_pub) in search_fold(s.publisher) or search_fold(s.publisher) in search_fold(detected_pub):
                        s.score += 1800.0

                if sticky_coll and s.collection:
                    if search_fold(sticky_coll) in search_fold(s.collection) or search_fold(s.collection) in search_fold(sticky_coll):
                        s.score += 1800.0

        if visual_matches:
            valid_vm = sorted(
                [
                    vm for vm in visual_matches
                    if _is_visual_match_trusted(vm, None, cover_tokens or set())
                ],
                key=lambda x: float(x.get("distance", 9.9)),
            )
            for s in merged_map.values():
                s_title_fold = search_fold(s.title)
                s_author_fold = search_fold(s.author)
                best_bonus = 0.0
                for idx, vm in enumerate(valid_vm):
                    vm_title_fold = search_fold(str(vm.get("title") or ""))
                    vm_artist_fold = search_fold(str(vm.get("artist") or ""))
                    if not vm_title_fold or not vm_artist_fold:
                        continue
                    title_ok = (
                        vm_title_fold == s_title_fold
                        or vm_title_fold in s_title_fold
                        or s_title_fold in vm_title_fold
                    )
                    artist_ok = (
                        vm_artist_fold == s_author_fold
                        or vm_artist_fold in s_author_fold
                        or s_author_fold in vm_artist_fold
                    )
                    if title_ok and artist_ok:
                        dist = float(vm.get("distance", 9.9))
                        closeness = max(0.0, 0.75 - dist)
                        rank_bonus = 2000.0 if idx == 0 else (500.0 if idx == 1 else 0.0)
                        bonus = closeness * 12000.0 + rank_bonus
                        if vm_title_fold == s_title_fold and vm_artist_fold == s_author_fold:
                            bonus += 800.0
                        if bonus > best_bonus:
                            best_bonus = bonus
                if best_bonus > 0:
                    s.score += best_bonus

        merged = sorted(merged_map.values(), key=lambda s: -s.score)
        best = merged[0]
        eff_pub = detected_pub if (detected_pub and category == "Libri") else ""
        if eff_pub and not best.publisher:
            best.publisher = eff_pub
        item = self._suggestion_to_item(best, category, "", sticky_coll, lang)
        if eff_pub:
            item.publisher = eff_pub
        is_dup = self._check_duplicate(item, archive_items)
        ed_dict = self._build_editions_payload(
            category, best.work_id, item.title, item.author, item.publisher, item.year, item.collection, lang, fetch_remote=False
        )

        candidates = [
            {
                "title": s.title,
                "author": s.author,
                "publisher": eff_pub or s.publisher or (s.publishers[0] if s.publishers else ""),
                "year": s.year or (s.years[0] if s.years else ""),
                "collection": s.collection or sticky_coll,
                "work_id": s.work_id,
            }
            for s in merged[:4]
        ]

        if auto_add and not is_dup and item.title and item.author:
            self.signals.itemAdded.emit(category, item)
            self.signals.scanLogEvent.emit("ok", f"✓ Raffica OCR [{category}]: {item.title} — {item.author}")
            return {
                "status": "ok",
                "category": category,
                "ocr_query": query,
                "item": self._item_to_dict(item),
                "editions": ed_dict,
                "candidates": candidates,
                "is_duplicate": False,
                "added": True,
                "source": "OCR / Ricerca",
            }

        self.signals.itemPreviewed.emit(category, item, f"OCR · {query[:28]}")
        self.signals.scanLogEvent.emit("info", f"Ricerca/OCR «{query[:35]}» → {item.title} — {item.author}")
        return {
            "status": "ok",
            "category": category,
            "ocr_query": query,
            "item": self._item_to_dict(item),
            "editions": ed_dict,
            "candidates": candidates,
            "is_duplicate": is_dup,
            "added": False,
            "source": "OCR / Ricerca",
        }

    def _suggestion_to_item(
        self,
        sug: Suggestion,
        category: str,
        code: str,
        sticky_coll: str,
        lang: str,
    ) -> Item:
        pub = sug.publisher or (sug.publishers[0] if sug.publishers else "")
        yr = sug.year or (sug.years[0] if sug.years else "")
        # La Collana si compila solo se è realmente legata a questa edizione (dato della fonte, ISBN/EAN
        # o scelta esplicita): mai "la prima della lista" di quelle teoricamente possibili.
        coll = sug.collection or sticky_coll
        # Per CD/Vinili le date digitali di streaming/remaster non sono quelle della prima stampa fisica:
        # se esiste un anno più antico plausibile (dal 1948 per i Vinili, dal 1982 per i CD), si preferisce quello.
        if category != "Libri" and yr.isdigit() and sug.years:
            this_year = datetime.date.today().year
            min_yr = 1948 if category == "Vinili" else 1982
            older = sorted(y for y in sug.years if y.isdigit() and min_yr <= int(y) < int(yr))
            if older and (not getattr(sug, "from_isbn", False) or int(yr) >= this_year):
                yr = older[0]
        # Usa solo la cache locale immediata senza bloccare per secondi su chiamate HTTP sincronizzate
        norm_key = f"{search_fold(sug.title)}|{search_fold(sug.author)}"
        cache_id = sug.work_id or norm_key
        cached_b = self.catalog.cache.get(f"editions:v10:{category}:{cache_id}")
        if cached_b:
            pub = pub or cached_b.get("default_publisher") or ""
            yr = yr or cached_b.get("default_year") or ""
        if coll:
            res_pub, res_yr = self.catalog.resolve_collection_metadata(
                coll, category, author=sug.author, bundle=None
            )
            pub = pub or res_pub
            yr = yr or res_yr

        return Item(
            title=sug.title,
            author=sug.author,
            publisher=pub,
            year=yr,
            author_sort=(
                sug.author_sort
                if sug.author_sort and sort_key_matches_author(sug.author_sort, sug.author)
                else surname_key(sug.author, category)
            ),
            isbn_or_barcode=code,
            collection=coll,
            category=category,
        )

    @staticmethod
    def _dict_to_item(d: Dict[str, Any], category: str, sticky_coll: str) -> Item:
        title = str(d.get("title") or "").strip()
        author = str(d.get("author") or "").strip()
        pub = str(d.get("publisher") or "").strip()
        yr = str(d.get("year") or "").strip()
        coll = str(d.get("collection") or sticky_coll or "").strip()
        code = str(d.get("isbn_or_barcode") or "").strip()
        return Item(
            title=title,
            author=author,
            publisher=pub,
            year=yr,
            author_sort=surname_key(author, category),
            isbn_or_barcode=code,
            collection=coll,
            category=category,
        )

    @staticmethod
    def _item_to_dict(it: Item) -> Dict[str, str]:
        return {
            "title": it.title,
            "author": it.author,
            "publisher": it.publisher,
            "year": it.year,
            "author_sort": it.author_sort,
            "collection": it.collection,
            "isbn_or_barcode": it.isbn_or_barcode,
            "category": it.category,
        }

    @staticmethod
    def _check_duplicate(new_item: Item, existing: List[Item]) -> bool:
        nt = fold(new_item.title)
        na = fold(new_item.author)
        if not nt or not na:
            return False
        return any(fold(i.title) == nt and fold(i.author) == na for i in existing)

    @staticmethod
    def _save_base64_image(b64_str: str) -> str:
        if not b64_str:
            return ""
        if "," in b64_str:
            b64_str = b64_str.split(",", 1)[1]
        path = ""
        try:
            raw = base64.b64decode(b64_str)
            fd, path = tempfile.mkstemp(prefix="archivio_scan_", suffix=".jpg")
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            return path
        except Exception:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
            return ""
