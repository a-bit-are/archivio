"""
Servizio di Scansione da Dispositivo Mobile e Fotocamera del Computer
(via GitHub Pages + Cloud Relay HTTPS e Rete Locale).
Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3.
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QObject, Signal

from .catalog import (
    COLLECTION_TO_PUBLISHER_IT,
    DEFAULT_BOOK_PUBLISHERS_IT,
    ITALIAN_PUBLISHERS_PRIORITY,
    PUBLISHER_COLLECTIONS_IT,
    CatalogService,
    Suggestion,
    _direct_ratio,
)
from .config import resource_path
from .store import Item
from .textutil import fold, search_fold, sort_key_matches_author, surname_key

# Codici periodici da edicola noti (prefisso ISSN 977)
KNOWN_ISSN_SERIES: Dict[str, Tuple[str, str]] = {
    "9771120528": ("Urania", "Mondadori"),
    "9771120498": ("I Classici del Giallo Mondadori", "Mondadori"),
    "9770017007": ("Il Giallo Mondadori", "Mondadori"),
    "9771128608": ("La Biblioteca di Repubblica", "La Repubblica"),
    "9771129085": ("Classici dell'Arte", "Rizzoli / Skira"),
    "9772038084": ("Jazz in Vinile DeAgostini", "DeAgostini"),
    "9772531510": ("Prog Rock Italiano in Vinile", "DeAgostini"),
    "9772421265": ("Blues in Vinile", "DeAgostini"),
}

OCR_NOISE_WORDS = {
    "romanzo", "racconti", "poesie", "saggi", "edizione", "edizioni", "integrale",
    "bestseller", "premio", "strega", "nobel", "traduzione", "prefazione", "cura",
    "introduzione", "postfazione", "testo", "fronte", "latino", "greco", "originale",
    "stereo", "mono", "digital", "remaster", "remastered", "33rpm", "45rpm",
    "siae", "made in italy", "made in eu", "compact disc", "digital audio",
    "all rights reserved", "vietato il noleggio", "prezzo", "euro", "lire",
}

OCR_NOISE_PHRASES = (
    "a cura di", "introduzione di", "prefazione di", "traduzione di", "postfazione di",
    "testo latino a fronte", "testo greco a fronte", "testo originale a fronte",
    "con testo a fronte", "edizione integrale", "nuova edizione", "premio strega",
    "premio nobel", "all rights reserved", "compact disc", "digital audio",
)


def normalize_barcode_input(raw: str) -> Tuple[str, bool, str, str]:
    """
    Normalizza un codice a barre scansionato.
    Restituisce: (codice_pulito, is_issn_977, nome_collana_edicola, editore_edicola)
    - Se è un codice a 18 cifre (EAN-13 + supplemento 5 cifre del prezzo), estrae le prime 13 cifre.
    - Se inizia con 977 (13 cifre), lo identifica come codice periodico da edicola (ISSN).
    """
    cleaned = re.sub(r"[^0-9Xx]", "", (raw or "").strip()).upper()
    if len(cleaned) == 18 and cleaned[:3] in ("978", "979", "977"):
        cleaned = cleaned[:13]
    elif len(cleaned) == 15 and cleaned[:3] in ("978", "979", "977"):
        cleaned = cleaned[:13]

    if len(cleaned) >= 13 and cleaned.startswith("977"):
        prefix10 = cleaned[:10]
        series_name, pub_name = KNOWN_ISSN_SERIES.get(prefix10, ("Edizione Periodica da Edicola", ""))
        return cleaned[:13], True, series_name, pub_name

    return cleaned, False, "", ""


def _vision_binary_path() -> str:
    candidates = [
        resource_path("data", "vision_scanner"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vision_scanner"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return ""


def generate_qr_png(url: str, out_path: str) -> bool:
    """Genera un'immagine PNG del QR Code (nativo Apple CoreImage su macOS, libreria qrcode su Windows e Linux)."""
    bin_path = _vision_binary_path()
    if bin_path:
        try:
            res = subprocess.run(
                [bin_path, "qr", url, out_path],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if res.returncode == 0 and os.path.isfile(out_path):
                return True
        except Exception:
            pass

    # Fallback multi-piattaforma con modulo qrcode (Windows, Linux, macOS fallback)
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        img.save(out_path)
        return os.path.isfile(out_path)
    except Exception:
        pass
    return False


def _normalize_retail_barcode(raw_txt: str) -> str:
    """Estrae un codice ISBN / EAN / UPC valido (8, 10, 12 o 13 cifre) ignorando URL o QR code."""
    s = (raw_txt or "").strip()
    if not s or "://" in s or "/" in s:
        return ""
    # Rimuovi eventuale prefisso simbologia GS1 (es. ]E0)
    if s.startswith("]") and len(s) > 3:
        s = s[3:].strip()
    cleaned = re.sub(r"[^0-9Xx]", "", s).upper()
    if len(cleaned) in (8, 12, 13) and cleaned.isdigit():
        return cleaned
    if len(cleaned) == 10 and cleaned[:9].isdigit() and (cleaned[9].isdigit() or cleaned[9] == "X"):
        return cleaned
    return ""


def _decode_barcodes_cross_platform(img_or_gray) -> List[str]:
    """Decodifica codici a barre 1D/ISBN tramite zxing-cpp C++ su Windows e Linux."""
    try:
        import zxingcpp  # type: ignore
    except ImportError:
        return []
    out: List[str] = []
    try:
        results = zxingcpp.read_barcodes(
            img_or_gray,
            try_rotate=True,
            try_downscale=True,
            try_invert=True,
        )
        for r in results:
            fmt_str = str(getattr(r, "format", "")).upper()
            if "QR" in fmt_str and not str(getattr(r, "text", "")).strip().isdigit():
                continue
            code = _normalize_retail_barcode(str(getattr(r, "text", "") or ""))
            if code and code not in out:
                out.append(code)
    except Exception:
        pass
    return out


def _ocr_lines_windows(image_path: str) -> List[Dict[str, Any]]:
    """Esegue il motore OCR nativo di Windows 10/11 (`Windows.Media.Ocr`) senza finestre console."""
    abs_img = os.path.abspath(image_path)
    if not os.path.isfile(abs_img):
        return []
    ps_script = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$imgPath = $env:ARCHIVIO_OCR_IMG
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imgPath)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $imgH = [double]$bitmap.PixelHeight
    if ($imgH -le 0) { $imgH = 1.0 }

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {
        $lang = New-Object Windows.Globalization.Language('en-US')
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
    }
    if ($null -eq $engine) {
        Write-Output '{"lines":[]}'
        exit 0
    }
    $res = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    foreach ($line in $res.Lines) {
        $minY = 999999.0
        $maxY = 0.0
        foreach ($w in $line.Words) {
            $r = $w.BoundingRect
            if ($r.Y -lt $minY) { $minY = [double]$r.Y }
            if (($r.Y + $r.Height) -gt $maxY) { $maxY = [double]($r.Y + $r.Height) }
        }
        $hNorm = ($maxY - $minY) / $imgH
        if ($hNorm -le 0 -or $hNorm -gt 1.0) { $hNorm = 0.05 }
        $yCenter = ($minY + $maxY) * 0.5
        $yVision = 1.0 - ($yCenter / $imgH)
        $lines += @{
            text = [string]$line.Text
            height = [math]::Round($hNorm, 4)
            y = [math]::Round($yVision, 4)
            confidence = 0.92
        }
    }
    @{ lines = @($lines) } | ConvertTo-Json -Depth 4 -Compress
} finally {
    if ($null -ne $stream) { $stream.Dispose() }
}
"""
    env = os.environ.copy()
    env["ARCHIVIO_OCR_IMG"] = abs_img
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=10,
            creationflags=no_win,
        )
        if res.returncode == 0 and res.stdout.strip():
            raw_out = res.stdout.strip()
            idx = raw_out.find("{")
            if idx >= 0:
                parsed = json.loads(raw_out[idx:])
                if isinstance(parsed, dict) and isinstance(parsed.get("lines"), list):
                    return parsed["lines"]
    except Exception:
        pass
    return []


def _find_tesseract_bin() -> Optional[str]:
    """Individua l'eseguibile `tesseract` (incluso in AppImage o installato nel sistema)."""
    import shutil as _shutil

    candidates: List[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "tesseract"))
        candidates.append(os.path.join(meipass, "tesseract.exe"))
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(os.path.join(appdir, "usr", "bin", "tesseract"))
    found = _shutil.which("tesseract")
    if found:
        candidates.append(found)
    if sys.platform == "win32":
        candidates.append(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        candidates.append(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _ocr_lines_tesseract(image_path: str) -> List[Dict[str, Any]]:
    """Esegue OCR tramite Tesseract TSV restituendo righe compatibili con il formato Vision (`height`, `y`)."""
    tess = _find_tesseract_bin()
    if not tess or not os.path.isfile(image_path):
        return []
    img_h = 1000.0
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            img_h = float(max(im.size[1], 1))
    except Exception:
        pass

    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    tsv_text = ""
    for lang_args in (["-l", "ita+eng"], ["-l", "eng"], []):
        try:
            res = subprocess.run(
                [tess, image_path, "stdout", *lang_args, "--psm", "3", "tsv"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=no_win,
            )
            if res.returncode == 0 and res.stdout.strip():
                tsv_text = res.stdout
                break
        except Exception:
            continue

    if not tsv_text:
        return []

    grouped: Dict[Tuple[str, str, str, str], List[Tuple[int, int, float, str]]] = {}
    for idx, raw_ln in enumerate(tsv_text.splitlines()):
        if idx == 0 or not raw_ln.strip():
            continue
        cols = raw_ln.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        word = cols[11].strip()
        if not word:
            continue
        try:
            conf = float(cols[10])
            top = int(cols[7])
            height = int(cols[9])
        except ValueError:
            continue
        if conf < 25.0:
            continue
        key = (cols[1], cols[2], cols[3], cols[4])
        grouped.setdefault(key, []).append((top, top + max(height, 1), conf, word))

    lines_out: List[Dict[str, Any]] = []
    for words in grouped.values():
        txt = " ".join(w[3] for w in words).strip()
        if len(txt) < 2:
            continue
        min_t = min(w[0] for w in words)
        max_b = max(w[1] for w in words)
        avg_conf = sum(w[2] for w in words) / (100.0 * len(words))
        h_norm = max(0.01, min(1.0, (max_b - min_t) / img_h))
        y_center = (min_t + max_b) * 0.5
        y_vision = max(0.0, min(1.0, 1.0 - (y_center / img_h)))
        lines_out.append(
            {
                "text": txt,
                "height": round(h_norm, 4),
                "y": round(y_vision, 4),
                "confidence": round(max(0.35, min(1.0, avg_conf)), 3),
            }
        )
    return lines_out


def list_mac_cameras() -> List[Dict[str, str]]:
    """Elenca le fotocamere disponibili sul computer (macOS AVFoundation, Windows DirectShow/WMI, Linux V4L2)."""
    if sys.platform == "darwin":
        bin_path = _vision_binary_path()
        if not bin_path:
            return []
        try:
            res = subprocess.run(
                [bin_path, "cameras"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if res.returncode == 0 and res.stdout.strip():
                raw = json.loads(res.stdout.strip())
                if isinstance(raw, list):
                    return [
                        {"id": str(c.get("id") or ""), "name": str(c.get("name") or "Fotocamera del computer")}
                        for c in raw
                        if isinstance(c, dict)
                    ]
        except Exception:
            pass
        return []

    if sys.platform == "win32":
        names: List[str] = []
        no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            ps_cmd = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "Get-CimInstance Win32_PnPEntity | "
                "Where-Object { $_.PNPClass -in @('Camera','Image') -and $_.Status -eq 'OK' } | "
                "Select-Object -ExpandProperty Name"
            )
            res = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=4,
                creationflags=no_win,
            )
            if res.returncode == 0 and res.stdout.strip():
                for ln in res.stdout.splitlines():
                    nm = ln.strip()
                    if nm and nm not in names:
                        names.append(nm)
        except Exception:
            pass
        if names:
            return [{"id": str(i), "name": nm} for i, nm in enumerate(names)]
        # Fallback rapido DirectShow se WMI non ha restituito nomi
        try:
            import cv2  # type: ignore
            found_cams: List[Dict[str, str]] = []
            for idx in range(3):
                cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                if cap is not None and cap.isOpened():
                    found_cams.append({"id": str(idx), "name": f"Fotocamera {idx + 1}"})
                    cap.release()
            return found_cams
        except Exception:
            return []

    # Linux (V4L2 /sys/class/video4linux)
    v4l_root = "/sys/class/video4linux"
    if os.path.isdir(v4l_root):
        linux_cams: List[Dict[str, str]] = []
        for entry in sorted(os.listdir(v4l_root)):
            if not entry.startswith("video"):
                continue
            num_str = entry[5:]
            if not num_str.isdigit() or not os.path.exists(f"/dev/{entry}"):
                continue
            idx_file = os.path.join(v4l_root, entry, "index")
            if os.path.isfile(idx_file):
                try:
                    with open(idx_file, "r", encoding="utf-8") as f:
                        if f.read().strip() != "0":
                            continue
                except Exception:
                    pass
            cam_name = f"Fotocamera ({entry})"
            name_file = os.path.join(v4l_root, entry, "name")
            if os.path.isfile(name_file):
                try:
                    with open(name_file, "r", encoding="utf-8", errors="replace") as f:
                        rd = f.read().strip()
                        if rd:
                            cam_name = rd
                except Exception:
                    pass
            linux_cams.append({"id": num_str, "name": cam_name})
        if linux_cams:
            return linux_cams

    try:
        import cv2  # type: ignore
        fallback_cams: List[Dict[str, str]] = []
        for idx in range(3):
            cap = cv2.VideoCapture(idx)
            if cap is not None and cap.isOpened():
                fallback_cams.append({"id": str(idx), "name": f"Fotocamera {idx + 1}"})
                cap.release()
        return fallback_cams
    except Exception:
        return []


class MacCameraSignals(QObject):
    """Segnali Qt thread-safe per lo streaming live della fotocamera del computer."""
    cameraStarted = Signal(str)
    frameReady = Signal(bytes, str)      # (jpeg_bytes, live_barcode_or_empty)
    ocrCaptured = Signal(object)         # dict {"barcodes": [...], "lines": [...]}
    cameraError = Signal(str)


class MacCameraSession:
    """Gestisce lo streaming live della fotocamera e il rilevamento codici su macOS, Windows e Linux."""

    def __init__(self) -> None:
        self.signals = MacCameraSignals()
        self._proc: Optional[subprocess.Popen] = None
        self._running = False
        self._ocr_requested = False
        self._session_token = 0
        self._lock = threading.Lock()

    def _safe_emit(self, sig, *args) -> None:
        try:
            sig.emit(*args)
        except RuntimeError:
            pass

    @property
    def is_running(self) -> bool:
        if sys.platform == "darwin":
            return self._running and self._proc is not None and self._proc.poll() is None
        return self._running

    def start(self, camera_id: str = "") -> None:
        self.stop()
        if sys.platform == "darwin":
            self._start_macos(camera_id)
        else:
            self._start_opencv(camera_id)

    def _start_macos(self, camera_id: str) -> None:
        bin_path = _vision_binary_path()
        if not bin_path:
            self._safe_emit(self.signals.cameraError, "Componente nativo vision_scanner non trovato.")
            return
        cmd = [bin_path, "livecam"]
        if camera_id.strip():
            cmd.append(camera_id.strip())
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except Exception as exc:
            self._safe_emit(self.signals.cameraError, str(exc))
            return

        with self._lock:
            self._proc = proc
            self._running = True

        def _reader() -> None:
            try:
                assert proc.stdout is not None
                for raw_line in proc.stdout:
                    if not self._running or proc is not self._proc:
                        break
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    mtype = msg.get("type")
                    if mtype == "ready":
                        cam_name = str(msg.get("camera") or msg.get("message") or "Fotocamera del computer")
                        self._safe_emit(self.signals.cameraStarted, cam_name)
                    elif mtype == "error":
                        self._safe_emit(self.signals.cameraError, str(msg.get("message") or "Errore fotocamera"))
                    elif mtype == "frame":
                        b64_jpg = str(msg.get("jpeg_b64") or msg.get("jpeg") or "")
                        barcodes = msg.get("barcodes") or []
                        first_bc = str(msg.get("barcode") or (barcodes[0] if barcodes else "") or "")
                        if b64_jpg:
                            try:
                                jpg_bytes = base64.b64decode(b64_jpg)
                                self._safe_emit(self.signals.frameReady, jpg_bytes, first_bc)
                            except Exception:
                                pass
                    elif mtype == "ocr_result":
                        self._safe_emit(self.signals.ocrCaptured, msg)
            except Exception as exc:
                if self._running and proc is self._proc:
                    self._safe_emit(self.signals.cameraError, str(exc))
            finally:
                for pipe in (proc.stdin, proc.stdout):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except Exception:
                            pass

        threading.Thread(target=_reader, daemon=True).start()

    def _start_opencv(self, camera_id: str) -> None:
        try:
            import cv2  # type: ignore
        except ImportError:
            self._safe_emit(
                self.signals.cameraError,
                "Modulo fotocamera (opencv-python-headless) non installato.",
            )
            return

        cam_idx = int(camera_id.strip()) if camera_id.strip().isdigit() else 0
        with self._lock:
            self._session_token += 1
            my_token = self._session_token
            self._running = True
            self._ocr_requested = False

        def _cv_loop() -> None:
            cap = None
            try:
                backends = (
                    [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
                    if sys.platform == "win32"
                    else [getattr(cv2, "CAP_V4L2", cv2.CAP_ANY), cv2.CAP_ANY]
                )
                for api_pref in backends:
                    try:
                        c = cv2.VideoCapture(cam_idx, api_pref)
                        if c is not None and c.isOpened():
                            cap = c
                            break
                        if c is not None:
                            c.release()
                    except Exception:
                        continue

                if cap is None or not cap.isOpened():
                    with self._lock:
                        if self._session_token == my_token:
                            self._running = False
                    self._safe_emit(
                        self.signals.cameraError,
                        "Impossibile avviare la fotocamera. Verifica che sia collegata e che l'accesso alla fotocamera sia consentito nelle impostazioni di sistema.",
                    )
                    return

                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_AUTOFOCUS, 1)

                started_emitted = False
                frame_no = 0
                last_bc = ""
                last_bc_ts = 0.0
                empty_streak = 0

                while True:
                    with self._lock:
                        if not self._running or self._session_token != my_token:
                            break
                        want_ocr = self._ocr_requested
                        if want_ocr:
                            self._ocr_requested = False

                    ok, frame = cap.read()
                    if not ok or frame is None or frame.size == 0:
                        empty_streak += 1
                        if empty_streak > 45:
                            self._safe_emit(
                                self.signals.cameraError,
                                "Nessun segnale video dalla fotocamera selezionata.",
                            )
                            break
                        time.sleep(0.05)
                        continue

                    empty_streak = 0
                    if not started_emitted:
                        started_emitted = True
                        self._safe_emit(self.signals.cameraStarted, f"Fotocamera {cam_idx + 1}")

                    frame_no += 1
                    now = time.time()

                    # Rilevamento codici a barre sul fotogramma reale non specchiato
                    if frame_no % 3 == 1:
                        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                        found = _decode_barcodes_cross_platform(gray)
                        if found:
                            last_bc = found[0]
                            last_bc_ts = now
                        elif now - last_bc_ts > 0.9:
                            last_bc = ""

                    if want_ocr:
                        import tempfile
                        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jpg")
                        os.close(tmp_fd)
                        try:
                            cv2.imwrite(tmp_path, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                            ocr_res = run_apple_vision_on_image(tmp_path, mode="ocr")
                            self._safe_emit(self.signals.ocrCaptured, ocr_res)
                        finally:
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass

                    # Orientamento reale naturale (non a specchio) per leggere correttamente testi e barcode
                    disp_frame = frame
                    h_px, w_px = disp_frame.shape[:2]
                    if w_px > 960:
                        scale = 960.0 / float(w_px)
                        disp_frame = cv2.resize(
                            disp_frame,
                            (960, max(1, int(h_px * scale))),
                            interpolation=cv2.INTER_AREA,
                        )
                    ok_enc, buf = cv2.imencode(".jpg", disp_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
                    if ok_enc:
                        self._safe_emit(self.signals.frameReady, buf.tobytes(), last_bc)

                    time.sleep(0.04)
            finally:
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
                with self._lock:
                    if self._session_token == my_token:
                        self._running = False

        threading.Thread(target=_cv_loop, daemon=True).start()

    def request_ocr(self) -> bool:
        if sys.platform == "darwin":
            return self._send_macos_cmd("ocr")
        with self._lock:
            if not self._running:
                return False
            self._ocr_requested = True
            return True

    def request_scan_file(self, image_path: str) -> bool:
        if sys.platform == "darwin" and image_path:
            return self._send_macos_cmd(f"scanfile {image_path}")
        return False

    def _send_macos_cmd(self, cmd: str) -> bool:
        with self._lock:
            proc = self._proc
            if not self._running or proc is None or proc.stdin is None or proc.poll() is not None:
                return False
            try:
                proc.stdin.write(cmd + "\n")
                proc.stdin.flush()
                return True
            except Exception:
                return False

    def stop(self) -> None:
        with self._lock:
            self._running = False
            self._session_token += 1
            proc = self._proc
            self._proc = None
        if proc is not None:
            try:
                if proc.stdin and proc.poll() is None:
                    proc.stdin.write("quit\n")
                    proc.stdin.flush()
            except Exception:
                pass
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=0.5)
                    except Exception:
                        pass
            for pipe in (proc.stdin, proc.stdout):
                if pipe is not None:
                    try:
                        pipe.close()
                    except Exception:
                        pass


def run_apple_vision_on_image(image_path: str, mode: str = "scan") -> Dict[str, Any]:
    """
    Esegue il riconoscimento di codici a barre e testo OCR su un'immagine:
    - macOS: motore nativo Apple Vision (`vision_scanner`)
    - Windows: `zxing-cpp` + motore nativo `Windows.Media.Ocr` (con fallback Tesseract)
    - Linux: `zxing-cpp` + `Tesseract OCR`
    """
    if not os.path.isfile(image_path):
        return {"barcodes": [], "lines": []}

    if sys.platform == "darwin":
        bin_path = _vision_binary_path()
        if not bin_path:
            return {"barcodes": [], "lines": []}
        try:
            res = subprocess.run(
                [bin_path, mode, image_path],
                capture_output=True,
                text=True,
                timeout=25,
            )
            if res.returncode == 0 and res.stdout.strip():
                return json.loads(res.stdout.strip())
        except Exception:
            pass
        return {"barcodes": [], "lines": []}

    barcodes: List[str] = []
    try:
        from PIL import Image, ImageOps

        with Image.open(image_path) as im_raw:
            im_rgb = ImageOps.exif_transpose(im_raw).convert("RGB")
            for c in _decode_barcodes_cross_platform(im_rgb):
                if c not in barcodes:
                    barcodes.append(c)
            if not barcodes:
                gray = ImageOps.autocontrast(im_rgb.convert("L"), cutoff=2)
                for c in _decode_barcodes_cross_platform(gray):
                    if c not in barcodes:
                        barcodes.append(c)
            if not barcodes:
                flipped = ImageOps.mirror(im_rgb)
                for c in _decode_barcodes_cross_platform(flipped):
                    if c not in barcodes:
                        barcodes.append(c)
    except Exception:
        pass

    # Se siamo in modalità 'barcode' pura e abbiamo già trovato il codice, evitiamo l'OCR
    if mode == "barcode" and barcodes:
        return {"barcodes": barcodes, "lines": []}

    lines: List[Dict[str, Any]] = []
    if sys.platform == "win32":
        lines = _ocr_lines_windows(image_path)
        if not lines:
            lines = _ocr_lines_tesseract(image_path)
    else:
        lines = _ocr_lines_tesseract(image_path)

    return {"barcodes": barcodes, "lines": lines}


def extract_isbn_or_ean_from_ocr_lines(lines: List[Dict[str, Any]]) -> str:
    """Cerca un codice ISBN o EAN stampato in cifre vicino al codice a barre se le barre sono danneggiate."""
    full_text = "\n".join(str(x.get("text") or "") for x in lines)
    # 1. Cerca pattern esplicito ISBN
    m_isbn = re.search(r"ISBN(?:-1[03])?[:\s]*([0-9Xx\- ]{10,17})", full_text, re.IGNORECASE)
    if m_isbn:
        digits = re.sub(r"[^0-9Xx]", "", m_isbn.group(1)).upper()
        if len(digits) in (10, 13):
            return digits
    # 2. Cerca sequenza di 12 o 13 cifre (EAN-13 / UPC-12)
    for m in re.finditer(r"\b(\d[\d \-]{10,15}\d)\b", full_text):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) in (12, 13):
            return digits
    return ""


def parse_cover_ocr_lines(
    lines: List[Dict[str, Any]],
) -> Tuple[List[str], str, str, set, List[Tuple[str, str]]]:
    """
    Analizza le righe lette da Apple Vision sulla copertina di un libro o vinile:
    1) Individua e separa eventuali nomi di Editore (es. 'MONDADORI', 'RIZZOLI', 'EINAUDI')
       o Collana (es. 'BUR CLASSICI', 'OSCAR MONDADORI', 'PICCOLA BIBLIOTECA ADELPHI')
       così non inquinano la ricerca del Titolo/Autore ma vengono pre-impostati nei campi!
    2) Raggruppa in ordine di lettura dall'alto in basso (`-y`) le righe adiacenti con la stessa
       altezza di carattere (così un titolo su 2 righe come 'LA COSCIENZA' + 'DI ZENO' resta unito).
    3) Costruisce coppie probabili (Titolo, Autore) e permutazioni per interrogare i cataloghi
       con parametri strutturati oltre che full-text combinato.
    Restituisce: (queries, detected_publisher, detected_collection, cover_tokens_set, likely_pairs)
    """
    detected_pub = ""
    detected_coll = ""
    valid_rows: List[Tuple[float, float, str]] = []  # (height, y, text)
    cover_tokens: set = set()

    for item in lines:
        raw = str(item.get("text") or "").strip()
        if len(raw) < 2:
            continue
        conf = float(item.get("confidence") or 0.5)
        if conf < 0.28:
            continue
        # Ignora numeri/prezzi isolati
        if re.fullmatch(r"[\d\s.,€£$L/#-]+", raw) and raw not in ("1984", "2001"):
            continue

        f_line = fold(raw)
        # Scarta diciture editoriali di copertina ("testo latino a fronte", "introduzione di...", ecc.)
        if any(f_line.startswith(np) or np in f_line for np in OCR_NOISE_PHRASES):
            continue

        # Verifica se la riga è il nome di una Collana nota
        matched_coll = False
        for c_fold, c_pub in COLLECTION_TO_PUBLISHER_IT.items():
            if len(c_fold) >= 4 and (f_line == c_fold or f_line.startswith(c_fold + " ")):
                if not detected_coll:
                    # Trova il nome con maiuscole corrette
                    for p_list in PUBLISHER_COLLECTIONS_IT.values():
                        for c_disp in p_list:
                            if fold(c_disp) == c_fold:
                                detected_coll = c_disp
                                break
                    detected_coll = detected_coll or raw.title()
                if not detected_pub and c_pub:
                    detected_pub = c_pub
                matched_coll = True
                break
        if matched_coll:
            continue

        # Verifica se la riga è il nome di un Editore italiano noto
        if f_line in ITALIAN_PUBLISHERS_PRIORITY:
            if not detected_pub:
                detected_pub = ITALIAN_PUBLISHERS_PRIORITY[f_line]
            continue

        # Rimuovi singole parole di rumore ("ROMANZO", "SAGGI", ecc.)
        words = [w for w in raw.split() if fold(w) not in OCR_NOISE_WORDS]
        cleaned_txt = " ".join(words).strip(" ,.;:-")
        if len(cleaned_txt) < 2:
            continue

        for tok in search_fold(cleaned_txt).split():
            if len(tok) >= 3:
                cover_tokens.add(tok)
            elif len(tok) == 2 and tok not in ("di", "da", "in", "su", "il", "lo", "la", "le", "ed", "al", "ai", "ad", "un", "ha", "ho"):
                cover_tokens.add(tok)

        height = float(item.get("height") or 0.05)
        y_pos = float(item.get("y") or 0.5)
        valid_rows.append((height, y_pos, cleaned_txt))

    if not valid_rows:
        return [], detected_pub, detected_coll, cover_tokens, []

    # Filtra via scritte minuscole di quarta/aletta (tieni righe >= 14% del carattere più grande,
    # così non tagliamo fuori il nome dell'autore quando il titolo ha un font enorme)
    max_h = max(r[0] for r in valid_rows)
    prominent = [r for r in valid_rows if r[0] >= max_h * 0.14]
    if not prominent:
        prominent = valid_rows

    # Ordina dall'alto verso il basso (in Apple Vision y=1.0 è in alto, y=0.0 è in basso)
    prominent.sort(key=lambda r: -r[1])

    # Unisci righe consecutive che appartengono allo stesso blocco grafico (altezza simile e interlinea naturale)
    merged_raw: List[Tuple[float, float, str]] = []
    for h, y, txt in prominent:
        if merged_raw:
            prev_h, prev_y, prev_txt = merged_raw[-1]
            max_lh = max(h, prev_h)
            if abs(h - prev_h) <= max_lh * 0.35 and abs(prev_y - y) <= max(0.12, max_lh * 2.2) and len(prev_txt.split()) <= 6:
                merged_raw[-1] = (max_lh, y, f"{prev_txt} {txt}")
                continue
        merged_raw.append((h, y, txt))

    merged_blocks: List[Tuple[float, str]] = [(h, txt) for h, _, txt in merged_raw]

    # Ordina i blocchi fusi per importanza visiva (altezza carattere decrescente)
    by_size = sorted(merged_blocks, key=lambda x: x[0], reverse=True)
    top_texts = [t for _, t in by_size[:5]]
    reading_texts = [t for _, t in merged_blocks[:5]]
    raw_by_size = [t for _, _, t in sorted(prominent, key=lambda r: r[0], reverse=True)[:5]]

    # Costruisci coppie probabili (Titolo, Autore) e permutazioni
    likely_pairs: List[Tuple[str, str]] = []
    if len(top_texts) >= 2:
        likely_pairs.append((top_texts[0], top_texts[1]))
        likely_pairs.append((top_texts[1], top_texts[0]))
    if len(reading_texts) >= 2:
        p1 = (reading_texts[0], reading_texts[1])
        p2 = (reading_texts[1], reading_texts[0])
        if p1 not in likely_pairs:
            likely_pairs.append(p1)
        if p2 not in likely_pairs:
            likely_pairs.append(p2)
    if len(top_texts) >= 3:
        likely_pairs.append((top_texts[0], top_texts[2]))
        likely_pairs.append((top_texts[1], top_texts[2]))

    queries: List[str] = []
    if len(top_texts) >= 2:
        queries.append(f"{top_texts[0]} {top_texts[1]}")
    if len(reading_texts) >= 2:
        queries.append(f"{reading_texts[0]} {reading_texts[1]}")
    if top_texts:
        queries.append(top_texts[0])
    if len(top_texts) >= 2:
        queries.append(top_texts[1])
    for rt in raw_by_size[:3]:
        queries.append(rt)
    if len(top_texts) >= 3:
        queries.append(f"{top_texts[0]} {top_texts[2]}")
        queries.append(f"{top_texts[1]} {top_texts[2]}")
        queries.append(top_texts[2])

    out: List[str] = []
    seen = set()
    for q in queries:
        qn = search_fold(q)
        if qn and qn not in seen:
            seen.add(qn)
            out.append(q)

    # Rimuovi duplicati in likely_pairs
    uniq_pairs: List[Tuple[str, str]] = []
    seen_pairs = set()
    for t_c, a_c in likely_pairs:
        pair_key = (search_fold(t_c), search_fold(a_c))
        if pair_key not in seen_pairs and t_c.strip() and a_c.strip():
            seen_pairs.add(pair_key)
            uniq_pairs.append((t_c.strip(), a_c.strip()))

    return out, detected_pub, detected_coll, cover_tokens, uniq_pairs


def build_queries_from_ocr_lines(lines: List[Dict[str, Any]]) -> List[str]:
    queries, _, _, _, _ = parse_cover_ocr_lines(lines)
    return queries


def detect_lan_ip() -> str:
    """Rileva l'indirizzo IP locale del computer sulla rete Wi-Fi / Ethernet domestica."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
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
        self._running = True
        self.lan_ip = detect_lan_ip()
        self._start_http_server()
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
                    self._send_json({"ok": True, "room": service.room_token})
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
                    length = int(self.headers.get("Content-Length") or "0")
                    if length <= 0 or length > 16 * 1024 * 1024:
                        self._send_json(
                            {"status": "error", "message": "Payload size out of bounds"},
                            status=413 if length > 16 * 1024 * 1024 else 400,
                        )
                        return
                    raw_body = self.rfile.read(length)
                    payload = json.loads(raw_body.decode("utf-8"))
                    if str(payload.get("room") or "").strip() != service.room_token:
                        self._send_json(
                            {"status": "error", "message": "Sessione scaduta o token non valido"},
                            status=403,
                        )
                        return
                    resp = service.process_remote_action(payload)
                    self._send_json(resp)
                except Exception as exc:
                    self._send_json({"status": "error", "message": str(exc)}, status=500)

            def _send_json(self, obj: Dict[str, Any], status: int = 200) -> None:
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._httpd = ThreadingHTTPServer(("0.0.0.0", 0), ScannerRequestHandler)
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
                                if ev.get("event") != "message":
                                    continue
                                raw_msg = str(ev.get("message") or "")
                                att = ev.get("attachment")
                                if (not raw_msg.strip()) and isinstance(att, dict) and att.get("url"):
                                    att_req = urllib.request.Request(
                                        str(att["url"]), headers={"User-Agent": "Archivio/1.0.0"}
                                    )
                                    with urllib.request.urlopen(att_req, timeout=10) as att_resp:
                                        raw_msg = att_resp.read().decode("utf-8", errors="ignore")
                                if not raw_msg.strip():
                                    continue

                                msg_obj = json.loads(raw_msg)
                                if "_chunk" in msg_obj and "_total" in msg_obj and "_id" in msg_obj:
                                    now_ts = time.time()
                                    for old_k in [k for k, v in chunk_buf.items() if now_ts - v["ts"] > 60]:
                                        chunk_buf.pop(old_k, None)
                                    cid = str(msg_obj["_id"])
                                    idx = int(msg_obj["_chunk"])
                                    tot = int(msg_obj["_total"])
                                    entry = chunk_buf.setdefault(cid, {"ts": now_ts, "total": tot, "parts": {}})
                                    entry["ts"] = now_ts
                                    entry["parts"][idx] = str(msg_obj.get("_data") or "")
                                    if len(entry["parts"]) < tot:
                                        continue
                                    full_json = "".join(entry["parts"].get(i, "") for i in range(tot))
                                    chunk_buf.pop(cid, None)
                                    payload = json.loads(full_json)
                                else:
                                    payload = msg_obj

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
            return self._handle_vision_ocr_payload(
                barcodes, lines, req_cat, lang, data_by_cat, auto_add, sticky_coll, device_label=device_label
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
            cached = self.catalog.cache.get(f"editions:v9:{category}:{cache_id}")
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
        for c, m in coll_meta.items():
            if not isinstance(m, dict):
                continue
            cy = m.get("year")
            cp = m.get("publisher")
            if cy and cy not in year_colls:
                year_colls[cy] = c
            if cp and cp not in pub_colls:
                pub_colls[cp] = c

        return {
            "publishers": pubs[:30],
            "collections": colls[:40],
            "years": yrs[:40],
            "default_publisher": (bundle.default_publisher if bundle else "") or (pubs[0] if pubs else ""),
            "default_year": (bundle.default_year if bundle else "") or (yrs[0] if yrs else ""),
            "publisher_years": pub_years,
            "year_publishers": year_pubs,
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
        if len(code) == 13 and code[:3] in ("978", "979") and category != "Libri":
            category = "Libri"
            self.signals.categoryChanged.emit("Libri")

        archive_items = data_by_cat.get(category, [])
        suggestions = self.catalog.search_online(category, code, lang=lang, field_mode="title", limit=35)
        if not suggestions:
            suggestions = self.catalog.search_local(category, code, archive_items, lang=lang, field_mode="title", limit=10)

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

    def _handle_vision_ocr_payload(
        self,
        barcodes: List[str],
        lines: List[Dict[str, Any]],
        category: str,
        lang: str,
        data_by_cat: Dict[str, List[Item]],
        auto_add: bool,
        sticky_coll: str,
        device_label: str = "Fotocamera del computer",
    ) -> Dict[str, Any]:
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
        if not queries:
            return {"status": "not_found", "code": "OCR Copertina", "category": category}
        return self._handle_text_search(
            queries[0],
            category,
            lang,
            data_by_cat,
            auto_add=auto_add,
            sticky_coll=sticky_coll or det_coll,
            extra_queries=queries[1:5],
            detected_pub=det_pub,
            cover_tokens=cover_tokens,
            likely_pairs=likely_pairs,
            device_label=device_label,
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
            barcodes = vision_res.get("barcodes") or []
            if barcodes:
                return self._handle_barcode_scan(barcodes[0], category, lang, data_by_cat, auto_add, sticky_coll)

            # Se il riflesso sul cellophane ha impedito la lettura delle barre, controlla le cifre stampate sotto
            lines = vision_res.get("lines") or []
            ocr_code = extract_isbn_or_ean_from_ocr_lines(lines)
            if ocr_code:
                return self._handle_barcode_scan(ocr_code, category, lang, data_by_cat, auto_add, sticky_coll)

            # Ultimo fallback: se l'utente ha fotografato direttamente la copertina col tasto barcode
            queries, det_pub, det_coll, cover_tokens, likely_pairs = parse_cover_ocr_lines(lines)
            if queries:
                return self._handle_text_search(
                    queries[0], category, lang, data_by_cat, auto_add, sticky_coll or det_coll,
                    extra_queries=queries[1:5], detected_pub=det_pub, cover_tokens=cover_tokens, likely_pairs=likely_pairs
                )

            self.signals.scanLogEvent.emit("warn", "Nessun codice a barre rilevato nella foto inviata")
            return {"status": "not_found", "code": "Foto", "category": category}
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
            # Se nella foto c'è anche un barcode ISBN/EAN valido (non 977), usalo!
            for bc in vision_res.get("barcodes") or []:
                clean_c, is_issn, _, _ = normalize_barcode_input(bc)
                if clean_c and not is_issn and len(clean_c) in (10, 12, 13):
                    return self._handle_barcode_scan(clean_c, category, lang, data_by_cat, auto_add, sticky_coll)

            lines = vision_res.get("lines") or []
            queries, det_pub, det_coll, cover_tokens, likely_pairs = parse_cover_ocr_lines(lines)
            if not queries:
                return {"status": "not_found", "code": "OCR Copertina", "category": category}

            return self._handle_text_search(
                queries[0],
                category,
                lang,
                data_by_cat,
                auto_add=False,
                sticky_coll=sticky_coll or det_coll,
                extra_queries=queries[1:5],
                detected_pub=det_pub,
                cover_tokens=cover_tokens,
                likely_pairs=likely_pairs,
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
        device_label: str = "Dispositivo remoto",
    ) -> Dict[str, Any]:
        archive_items = data_by_cat.get(category, [])
        all_queries = [query] + [q for q in (extra_queries or []) if q and q != query]

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

        # Fuzzy token matching helper per tollerare refusi OCR e punteggiatura
        def _token_matches_cover(tok: str, c_tokens: set) -> bool:
            if tok in c_tokens:
                return True
            if len(tok) <= 2:
                return False
            for ct in c_tokens:
                if len(ct) >= 3:
                    if tok.startswith(ct) or ct.startswith(tok):
                        return True
                    if abs(len(tok) - len(ct)) <= 1 and _direct_ratio(tok, ct) >= 80.0:
                        return True
            return False

        # Se proviene da OCR di copertina (`cover_tokens`), premia i candidati in cui sia il titolo
        # sia l'autore compaiono tra le parole effettivamente lette sulla copertina!
        if cover_tokens:
            for s in merged_map.values():
                t_toks = [
                    w for w in search_fold(s.title).split()
                    if len(w) >= 3 or (len(w) == 2 and w not in ("di", "da", "in", "su", "il", "lo", "la", "le", "ed"))
                ]
                a_toks = [
                    w for w in search_fold(s.author).split()
                    if len(w) >= 3 or (len(w) == 2 and w not in ("di", "da", "in", "su", "il", "lo", "la", "le", "ed"))
                ]
                t_hits = sum(1 for w in t_toks if _token_matches_cover(w, cover_tokens))
                a_hits = sum(1 for w in a_toks if _token_matches_cover(w, cover_tokens))
                t_ratio = t_hits / max(1, len(t_toks))
                a_ratio = a_hits / max(1, len(a_toks))

                if t_hits >= 1 and a_hits >= 1:
                    s.score += 8500.0 + (t_ratio * 4000.0) + (a_ratio * 3000.0) + (t_hits + a_hits) * 500.0
                elif t_hits >= 1:
                    s.score += (t_ratio * 3500.0) + t_hits * 400.0
                elif a_hits >= 1:
                    s.score += (a_ratio * 2000.0) + a_hits * 300.0

                if detected_pub and s.publisher:
                    if search_fold(detected_pub) in search_fold(s.publisher) or search_fold(s.publisher) in search_fold(detected_pub):
                        s.score += 1800.0

                if sticky_coll and s.collection:
                    if search_fold(sticky_coll) in search_fold(s.collection) or search_fold(s.collection) in search_fold(sticky_coll):
                        s.score += 1800.0

        merged = sorted(merged_map.values(), key=lambda s: -s.score)
        best = merged[0]
        if detected_pub and not best.publisher:
            best.publisher = detected_pub
        item = self._suggestion_to_item(best, category, "", sticky_coll, lang)
        if detected_pub:
            item.publisher = detected_pub
        is_dup = self._check_duplicate(item, archive_items)
        ed_dict = self._build_editions_payload(
            category, best.work_id, item.title, item.author, item.publisher, item.year, item.collection, lang, fetch_remote=False
        )

        candidates = [
            {
                "title": s.title,
                "author": s.author,
                "publisher": detected_pub or s.publisher or (s.publishers[0] if s.publishers else ""),
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
        # Per CD/Vinili le date digitali di streaming (spesso recentissime) non sono quelle del supporto fisico:
        # se esiste un anno più antico plausibile tra quelli noti, si preferisce quello.
        if category != "Libri" and yr.isdigit() and sug.years:
            this_year = datetime.date.today().year
            older = sorted(y for y in sug.years if y.isdigit() and 1982 <= int(y) < int(yr))
            if int(yr) >= this_year and older:
                yr = older[0]
        # Usa solo la cache locale immediata senza bloccare per secondi su chiamate HTTP sincronizzate
        norm_key = f"{search_fold(sug.title)}|{search_fold(sug.author)}"
        cache_id = sug.work_id or norm_key
        cached_b = (
            self.catalog.cache.get(f"editions:v9:{category}:{cache_id}")
            or self.catalog.cache.get(f"editions:v8:{category}:{cache_id}")
        )
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
        try:
            raw = base64.b64decode(b64_str)
            fd, path = tempfile.mkstemp(prefix="archivio_scan_", suffix=".jpg")
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            return path
        except Exception:
            return ""
