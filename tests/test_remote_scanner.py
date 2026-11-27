"""
Unit test per app/archivio/remote_scanner.py.
Copre: normalizzazione codici a barre (EAN-13, EAN-18 con prezzo supplementare,
codici periodici ISSN 977 da edicola), generazione codice QR (PNG), conversione da
Suggestion a Item senza collane generiche forzate e ciclo di vita del server scanner.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

if "ARCHIVIO_SUPPORT_DIR" not in os.environ:
    _TEST_SUPPORT_DIR = tempfile.mkdtemp(prefix="archivio_test_cfg_")
    os.environ["ARCHIVIO_SUPPORT_DIR"] = _TEST_SUPPORT_DIR
    atexit.register(shutil.rmtree, _TEST_SUPPORT_DIR, ignore_errors=True)

from archivio.catalog import CatalogService, Suggestion
from archivio.config import Settings
from archivio.remote_scanner import (
    RemoteScannerService,
    generate_qr_png,
    normalize_barcode_input,
)


class TestBarcodeNormalization(unittest.TestCase):
    def test_standard_isbn_13(self):
        code, is_issn, series, pub = normalize_barcode_input("978-88-04-66823-7")
        self.assertEqual(code, "9788804668237")
        self.assertFalse(is_issn)
        self.assertEqual(series, "")
        self.assertEqual(pub, "")

    def test_barcode_with_5_digits_price_addon_18_digits(self):
        # Codice a barre tipico dei libri anglosassoni con addon a 5 cifre per il prezzo
        raw = "9780140449136 90000"
        code, is_issn, series, pub = normalize_barcode_input(raw)
        self.assertEqual(code, "9780140449136")
        self.assertFalse(is_issn)

    def test_barcode_with_2_digits_issue_addon_15_digits(self):
        raw = "9788806220457 01"
        code, is_issn, series, pub = normalize_barcode_input(raw)
        self.assertEqual(code, "9788806220457")
        self.assertFalse(is_issn)

    def test_issn_977_periodical_series(self):
        # Urania Mondadori
        code, is_issn, series, pub = normalize_barcode_input("9771120528005")
        self.assertEqual(code, "9771120528005")
        self.assertTrue(is_issn)
        self.assertEqual(series, "Urania")
        self.assertEqual(pub, "Mondadori")

        # Jazz in Vinile DeAgostini
        code, is_issn, series, pub = normalize_barcode_input("9772038084001")
        self.assertTrue(is_issn)
        self.assertEqual(series, "Jazz in Vinile DeAgostini")
        self.assertEqual(pub, "DeAgostini")

    def test_isbn10_with_check_digit_x(self):
        code, is_issn, _, _ = normalize_barcode_input("88-04-66823-X")
        self.assertEqual(code, "880466823X")
        self.assertFalse(is_issn)


class TestQrGeneration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_qr_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_generate_qr_png_creates_valid_png(self):
        out_png = os.path.join(self.test_dir, "test_qr.png")
        success = generate_qr_png("https://github.com/a-bit-are/archivio", out_png)
        self.assertTrue(success)
        self.assertTrue(os.path.isfile(out_png))
        self.assertGreater(os.path.getsize(out_png), 100)

        with open(out_png, "rb") as f:
            header = f.read(8)
        # Firma PNG standard \x89PNG\r\n\x1a\n
        self.assertEqual(header, b"\x89PNG\r\n\x1a\n")


class TestSuggestionToItem(unittest.TestCase):
    def test_music_suggestion_does_not_force_generic_collection(self):
        cat_svc = CatalogService()
        self.addCleanup(cat_svc.close)
        svc = RemoteScannerService(
            catalog=cat_svc,
            get_state_fn=lambda: ("CD", "cd.csv", {"CD": []}),
        )
        sug = Suggestion(
            title="Wall of Eyes",
            author="The Smile",
            publisher="XL Recordings",
            year="2024",
            category="CD",
            collection="",
            collections=["Original Album Classics", "SHM-CD Japanese Edition"],  # cache generica scartata
        )
        item = svc._suggestion_to_item(sug, "CD", "1234567890123", "", "it")
        self.assertEqual(item.title, "Wall of Eyes")
        self.assertEqual(item.author, "The Smile")
        self.assertEqual(item.publisher, "XL Recordings")
        self.assertEqual(item.year, "2024")
        self.assertEqual(item.category, "CD")
        # Deve rimanere vuota e NON iniettare "Original Album Classics"
        self.assertEqual(item.collection, "")

    def test_book_suggestion_preserves_explicit_series(self):
        cat_svc = CatalogService()
        self.addCleanup(cat_svc.close)
        svc = RemoteScannerService(
            catalog=cat_svc,
            get_state_fn=lambda: ("Libri", "libri.csv", {"Libri": []}),
        )
        sug = Suggestion(
            title="1984",
            author="George Orwell",
            publisher="Mondadori",
            year="1949",
            category="Libri",
            collection="Oscar moderni",
            collections=["Oscar moderni"],
        )
        item = svc._suggestion_to_item(sug, "Libri", "9788804668237", "", "it")
        self.assertEqual(item.collection, "Oscar moderni")


class TestRemoteScannerServerLifecycle(unittest.TestCase):
    def test_server_start_and_clean_stop(self):
        cat_svc = CatalogService()
        self.addCleanup(cat_svc.close)
        svc = RemoteScannerService(
            catalog=cat_svc,
            get_state_fn=lambda: ("Libri", "libri.csv", {"Libri": []}),
        )
        self.assertIsNotNone(svc.room_token)
        self.assertTrue(svc.room_token.startswith("ARCH-"))

        # Regenerate session token
        token2 = svc.regenerate_session()
        self.assertTrue(token2.startswith("ARCH-"))
        self.assertNotEqual(token2, "")

        # Test connection mode
        svc.set_connection_mode("local")
        self.assertEqual(svc.connection_mode, "local")
        svc.set_connection_mode("ghpages")
        self.assertEqual(svc.connection_mode, "ghpages")

        # HTML scanner bytes exist
        html_bytes = svc.get_scanner_html_bytes()
        self.assertGreater(len(html_bytes), 50)

    def test_http_api_action_requires_valid_room_token(self):
        import json
        import urllib.error
        import urllib.request

        cat_svc = CatalogService()
        svc = RemoteScannerService(
            catalog=cat_svc,
            get_state_fn=lambda: ("Libri", "it", {"Libri": []}),
        )
        svc._start_http_server()
        try:
            url = f"http://127.0.0.1:{svc.port}/api/action"

            # Richiesta senza/con room errato -> 403
            bad_req = urllib.request.Request(
                url,
                data=json.dumps({"action": "set_category", "category": "CD", "room": "WRONG-ROOM"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(bad_req, timeout=2.0)
            self.assertEqual(ctx.exception.code, 403)
            ctx.exception.close()

            # Richiesta con room valido -> 200 ok
            good_req = urllib.request.Request(
                url,
                data=json.dumps({"action": "set_category", "category": "CD", "room": svc.room_token}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(good_req, timeout=2.0) as resp:
                self.assertEqual(resp.status, 200)
                body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(body.get("status"), "ok")
        finally:
            svc.stop()
            cat_svc.close()


class TestVisualCoverRecognition(unittest.TestCase):
    def _make_service(self, initial_category: str = "Libri"):
        cat_svc = CatalogService()
        # Evita chiamate di rete durante i test: verifica il catalogo locale + profili curati
        cat_svc.search_online = lambda *args, **kwargs: []
        emitted_cats = []
        svc = RemoteScannerService(
            catalog=cat_svc,
            get_state_fn=lambda: (initial_category, "it", {"Libri": [], "Vinili": [], "CD": []}),
        )
        svc.signals.categoryChanged.connect(emitted_cats.append)
        return cat_svc, svc, emitted_cats

    def test_seed_covers_index_contains_pink_floyd_atom_heart_mother(self):
        import base64
        import json

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        covers_path = os.path.join(root, "app", "archivio", "data", "seed_covers.json")
        self.assertTrue(os.path.isfile(covers_path))
        with open(covers_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        self.assertEqual(payload.get("dim"), 768)
        self.assertEqual(payload.get("xp_dim"), 256)
        items = payload.get("items") or []
        self.assertGreaterEqual(len(items), 200)
        self.assertTrue(all(len(base64.b64decode(r[4])) == 1536 for r in items))
        self.assertTrue(all(len(r) >= 6 and len(base64.b64decode(r[5])) == 256 for r in items))
        titles = {(r[0], r[1]) for r in items}
        self.assertIn(("Atom Heart Mother", "Pink Floyd"), titles)

    def test_scan_ocr_lines_visual_match_switches_libri_to_vinili_and_resolves_ahm(self):
        cat_svc, svc, emitted_cats = self._make_service(initial_category="Libri")
        try:
            # Simula sia il tasto Spazio che Scatta foto + Invio quando la copertina non ha testo OCR
            res = svc.process_remote_action(
                {
                    "action": "scan_ocr_lines",
                    "category": "Libri",
                    "barcodes": [],
                    "lines": [],
                    "visual_matches": [
                        {"title": "Atom Heart Mother", "artist": "Pink Floyd", "year": "1970", "distance": 0.36}
                    ],
                    "auto_add": False,
                    "device_label": "Fotocamera del computer",
                }
            )
            self.assertEqual(res.get("status"), "ok")
            self.assertEqual(res.get("category"), "Vinili")
            self.assertIn("Vinili", emitted_cats)
            item = res.get("item") or {}
            self.assertEqual(item.get("title"), "Atom Heart Mother")
            self.assertEqual(item.get("author"), "Pink Floyd")
            self.assertEqual(item.get("publisher"), "Harvest")
            self.assertEqual(item.get("year"), "1970")
        finally:
            cat_svc.close()

    def test_scan_ocr_lines_rejects_high_distance_visual_match(self):
        cat_svc, svc, emitted_cats = self._make_service(initial_category="Vinili")
        try:
            res = svc.process_remote_action(
                {
                    "action": "scan_ocr_lines",
                    "category": "Vinili",
                    "barcodes": [],
                    "lines": [],
                    "visual_matches": [
                        {"title": "Atom Heart Mother", "artist": "Pink Floyd", "year": "1970", "distance": 0.89}
                    ],
                }
            )
            self.assertEqual(res.get("status"), "not_found")
            self.assertEqual(emitted_cats, [])
        finally:
            cat_svc.close()

    def test_vision_scanner_blank_frame_and_ahm_variants(self):
        import sys
        from PIL import Image, ImageEnhance, ImageOps
        from archivio.remote_scanner import _vision_binary_path, run_apple_vision_on_image

        if sys.platform != "darwin":
            self.skipTest("Apple Vision disponibile solo su macOS")
        bin_path = _vision_binary_path()
        if not bin_path:
            self.skipTest("Binario vision_scanner non presente")

        cat_svc, svc, _ = self._make_service(initial_category="Libri")
        tmp_dir = tempfile.mkdtemp(prefix="archivio_ahm_test_")
        try:
            # 1. Fotogramma vuoto / grigio uniforme: non deve produrre falsi positivi
            blank_path = os.path.join(tmp_dir, "blank.jpg")
            Image.new("RGB", (640, 480), (125, 125, 125)).save(blank_path, quality=90)
            blank_vis = run_apple_vision_on_image(blank_path, mode="ocr")
            self.assertEqual(blank_vis.get("visual_matches") or [], [])
            blank_res = svc.process_remote_action(
                {
                    "action": "scan_ocr_lines",
                    "category": "Vinili",
                    "barcodes": blank_vis.get("barcodes") or [],
                    "lines": blank_vis.get("lines") or [],
                    "visual_matches": blank_vis.get("visual_matches") or [],
                }
            )
            self.assertEqual(blank_res.get("status"), "not_found")

            # 2. Varianti reali (normale, ruotata+scurita tipo webcam, specchiata) se la copertina in cache è presente
            cached_ahm = os.path.join(
                tempfile.gettempdir(), "archivio_cover_artwork_cache", "0000_pink_floyd_atom_heart_mother.jpg"
            )
            if os.path.isfile(cached_ahm):
                variants = []
                with Image.open(cached_ahm) as orig:
                    rgb = orig.convert("RGB")
                    p_norm = os.path.join(tmp_dir, "ahm_norm.jpg")
                    rgb.save(p_norm, quality=95)
                    variants.append(p_norm)

                    # Simula webcam 1280x720: vinile ruotato, più piccolo al centro, leggermente scuro
                    bg = Image.new("RGB", (1280, 720), (42, 38, 34))
                    rot = ImageEnhance.Brightness(rgb).enhance(0.82).resize((440, 440)).rotate(8, expand=True, fillcolor=(42, 38, 34))
                    bg.paste(rot, ((1280 - rot.width) // 2, (720 - rot.height) // 2))
                    p_cam = os.path.join(tmp_dir, "ahm_webcam.jpg")
                    bg.save(p_cam, quality=90)
                    variants.append(p_cam)

                    # Simula immagine specchiata orizzontalmente
                    p_mir = os.path.join(tmp_dir, "ahm_mirrored.jpg")
                    ImageOps.mirror(rgb).save(p_mir, quality=95)
                    variants.append(p_mir)

                for vp in variants:
                    vis = run_apple_vision_on_image(vp, mode="ocr")
                    vms = vis.get("visual_matches") or []
                    self.assertTrue(vms, f"Nessun match visivo per {vp}")
                    self.assertEqual(vms[0]["title"], "Atom Heart Mother")
                    self.assertEqual(vms[0]["artist"], "Pink Floyd")
                    self.assertLessEqual(float(vms[0]["distance"]), 0.75)

                    res = svc.process_remote_action(
                        {
                            "action": "scan_ocr_lines",
                            "category": "Libri",
                            "barcodes": vis.get("barcodes") or [],
                            "lines": vis.get("lines") or [],
                            "visual_matches": vms,
                            "device_label": "Fotocamera del computer",
                        }
                    )
                    self.assertEqual(res.get("status"), "ok")
                    self.assertEqual(res.get("category"), "Vinili")
                    self.assertEqual(res["item"]["title"], "Atom Heart Mother")
                    self.assertEqual(res["item"]["author"], "Pink Floyd")
                    self.assertEqual(res["item"]["publisher"], "Harvest")
                    self.assertEqual(res["item"]["year"], "1970")
        finally:
            cat_svc.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_cross_platform_visual_matcher_recognizes_ahm_variants_and_rejects_blank(self):
        from unittest.mock import patch
        from PIL import Image, ImageDraw, ImageEnhance, ImageOps
        import archivio.remote_scanner as rs

        cat_svc, svc, _ = self._make_service(initial_category="Libri")
        tmp_dir = tempfile.mkdtemp(prefix="archivio_xp_ahm_test_")
        try:
            # Frame vuoto grigio uniforme e copertina testuale bianca: devono essere scartati
            blank_path = os.path.join(tmp_dir, "blank.jpg")
            Image.new("RGB", (640, 480), (125, 125, 125)).save(blank_path, quality=90)
            self.assertEqual(rs.match_cover_artwork_cross_platform(blank_path), [])

            text_path = os.path.join(tmp_dir, "text_cover.jpg")
            im_text = Image.new("RGB", (900, 500), "white")
            draw = ImageDraw.Draw(im_text)
            draw.text((80, 120), "IL NOME DELLA ROSA", fill="black")
            draw.text((80, 220), "UMBERTO ECO", fill="black")
            im_text.save(text_path, quality=90)
            self.assertEqual(rs.match_cover_artwork_cross_platform(text_path), [])

            cached_ahm = os.path.join(
                tempfile.gettempdir(), "archivio_cover_artwork_cache", "0000_pink_floyd_atom_heart_mother.jpg"
            )
            if os.path.isfile(cached_ahm):
                with Image.open(cached_ahm) as orig:
                    rgb = orig.convert("RGB")
                    p_norm = os.path.join(tmp_dir, "ahm_norm.jpg")
                    rgb.save(p_norm, quality=95)

                    bg = Image.new("RGB", (1280, 720), (42, 38, 34))
                    rot = (
                        ImageEnhance.Brightness(rgb)
                        .enhance(0.82)
                        .resize((440, 440))
                        .rotate(8, expand=True, fillcolor=(42, 38, 34))
                    )
                    bg.paste(rot, ((1280 - rot.width) // 2, (720 - rot.height) // 2))
                    p_cam = os.path.join(tmp_dir, "ahm_webcam.jpg")
                    bg.save(p_cam, quality=90)

                    p_mir = os.path.join(tmp_dir, "ahm_mirrored.jpg")
                    ImageOps.mirror(rgb).save(p_mir, quality=95)

                for plat in ("win32", "linux"):
                    with (
                        patch.object(rs.sys, "platform", plat),
                        patch.object(rs, "_decode_barcodes_cross_platform", return_value=[]),
                        patch.object(rs, "_ocr_lines_windows", return_value=[]),
                        patch.object(rs, "_ocr_lines_tesseract", return_value=[]),
                    ):
                        # Blank rifiutato anche via run_apple_vision_on_image su Windows/Linux
                        self.assertEqual(
                            rs.run_apple_vision_on_image(blank_path, mode="ocr").get("visual_matches") or [],
                            [],
                        )
                        for vp in (p_norm, p_cam, p_mir):
                            vis = rs.run_apple_vision_on_image(vp, mode="ocr")
                            vms = vis.get("visual_matches") or []
                            self.assertTrue(vms, f"Nessun match multipiattaforma ({plat}) per {vp}")
                            self.assertEqual(vms[0]["title"], "Atom Heart Mother")
                            self.assertEqual(vms[0]["artist"], "Pink Floyd")
                            self.assertLessEqual(float(vms[0]["distance"]), 0.65)

                            res = svc.process_remote_action(
                                {
                                    "action": "scan_ocr_lines",
                                    "category": "Libri",
                                    "barcodes": vis.get("barcodes") or [],
                                    "lines": vis.get("lines") or [],
                                    "visual_matches": vms,
                                    "device_label": "Fotocamera del computer",
                                }
                            )
                            self.assertEqual(res.get("status"), "ok")
                            self.assertEqual(res.get("category"), "Vinili")
                            self.assertEqual(res["item"]["title"], "Atom Heart Mother")
                            self.assertEqual(res["item"]["author"], "Pink Floyd")
                            self.assertEqual(res["item"]["publisher"], "Harvest")
                            self.assertEqual(res["item"]["year"], "1970")
        finally:
            cat_svc.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_wide_16_9_frame_and_tiny_ocr_noise_visual_fusion(self):
        import io
        from PIL import Image, ImageDraw, ImageEnhance
        import archivio.remote_scanner as rs

        cat_svc, svc, emitted_cats = self._make_service(initial_category="Libri")
        try:
            cached_ahm = os.path.join(
                tempfile.gettempdir(), "archivio_cover_artwork_cache", "0000_pink_floyd_atom_heart_mother.jpg"
            )
            if os.path.isfile(cached_ahm):
                with Image.open(cached_ahm) as orig:
                    rgb = ImageEnhance.Brightness(orig.convert("RGB")).enhance(0.92).resize((450, 454))
                # Simula frame 16:9 reale (1007x454): vinile a tutta altezza al centro-sinistra + libreria ai lati + dita sul bordo destro
                wide = Image.new("RGB", (1007, 454), (70, 68, 62))
                draw = ImageDraw.Draw(wide)
                draw.rectangle([0, 0, 270, 454], fill=(95, 88, 76))
                draw.rectangle([720, 0, 1007, 454], fill=(110, 105, 96))
                wide.paste(rgb, (270, 0))
                # Simula mano/dita sul lato destro della copertina
                draw.rectangle([620, 180, 735, 310], fill=(175, 130, 105))
                buf = io.BytesIO()
                wide.save(buf, format="JPEG", quality=92)
                vms = rs.match_cover_artwork_from_bytes(buf.getvalue())
                self.assertTrue(vms, "Nessun match da bytes su frame 16:9 a tutta altezza")
                self.assertEqual(vms[0]["title"], "Atom Heart Mother")

                # Fusione con consenso tra due motori deve ridurre la distanza del match condiviso
                fused = rs.merge_visual_matches(
                    [{"title": "Atom Heart Mother", "artist": "Pink Floyd", "year": "1970", "distance": 0.60}],
                    vms,
                )
                self.assertEqual(fused[0]["title"], "Atom Heart Mother")
                self.assertLess(fused[0]["distance"], vms[0]["distance"])

                # Rumore OCR minuscolo (bollino EMI + costola libro sullo sfondo) non deve bloccare il vinile né sovrascrivere l'etichetta
                res = svc.process_remote_action(
                    {
                        "action": "scan_ocr_lines",
                        "category": "Libri",
                        "barcodes": [],
                        "lines": [
                            {"text": "EMI", "height": 0.018, "y": 0.90, "confidence": 0.85},
                            {"text": "MONDADORI", "height": 0.020, "y": 0.25, "confidence": 0.65},
                        ],
                        "visual_matches": fused,
                        "device_label": "Fotocamera del computer",
                    }
                )
                self.assertEqual(res.get("status"), "ok")
                self.assertEqual(res.get("category"), "Vinili")
                self.assertIn("Vinili", emitted_cats)
                self.assertEqual(res["item"]["title"], "Atom Heart Mother")
                self.assertEqual(res["item"]["author"], "Pink Floyd")
                self.assertEqual(res["item"]["publisher"], "Harvest")
        finally:
            cat_svc.close()

    def test_http_server_start_skips_blocking_getfqdn(self):
        cat_svc = CatalogService()
        svc = RemoteScannerService(cat_svc, lambda: ("Libri", "it", {"Libri": [], "CD": [], "Vinili": []}))
        try:
            with patch("socket.getfqdn", side_effect=AssertionError("socket.getfqdn non deve bloccare l'avvio")):
                svc.start()
            self.assertGreater(svc.port, 0)
        finally:
            svc.stop()
            cat_svc.close()


if __name__ == "__main__":
    unittest.main()
