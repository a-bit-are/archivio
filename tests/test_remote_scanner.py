"""
Unit test per app/archivio/remote_scanner.py.
Copre: normalizzazione codici a barre (EAN-13, EAN-18 con prezzo supplementare,
codici periodici ISSN 977 da edicola), generazione codice QR (PNG), conversione da
Suggestion a Item senza collane generiche forzate e ciclo di vita del server scanner.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

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
        svc = RemoteScannerService(
            catalog=CatalogService(),
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
        svc = RemoteScannerService(
            catalog=CatalogService(),
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
            cat_svc.cache.close()


if __name__ == "__main__":
    unittest.main()
