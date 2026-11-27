"""
Test di regressione mirati per sicurezza, integrità dati, concorrenza,
ciclo di vita UI e correttezza bibliografica/discografica.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if "ARCHIVIO_SUPPORT_DIR" not in os.environ:
    _TEST_SUPPORT_DIR = tempfile.mkdtemp(prefix="archivio_test_reg_cfg_")
    os.environ["ARCHIVIO_SUPPORT_DIR"] = _TEST_SUPPORT_DIR
    atexit.register(shutil.rmtree, _TEST_SUPPORT_DIR, ignore_errors=True)

from PySide6.QtWidgets import QApplication

from archivio.catalog import (
    CatalogService,
    PersistentCache,
    Suggestion,
    _barcode_variants,
)
from archivio.config import Settings
from archivio.remote_scanner import RemoteScannerService, _is_safe_ntfy_attachment_url
from archivio.store import Item, _backup
from archivio.ui import FirstRunSetupDialog, MainWindow

app = QApplication.instance() or QApplication([])


class TestRemoteScannerSecurity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="archivio_reg_rs_")
        cache = PersistentCache(db_file=os.path.join(self._tmp, "cache.sqlite"))
        self.cat = CatalogService(cache=cache)
        self.svc = RemoteScannerService(
            catalog=self.cat,
            get_state_fn=lambda: ("Libri", "it", {"Libri": [], "CD": [], "Vinili": []}),
        )
        self.svc._start_http_server()

    def tearDown(self):
        self.svc.stop()
        self.cat.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_ping_does_not_leak_room_token_or_wildcard_cors(self):
        url = f"http://127.0.0.1:{self.svc.port}/api/ping"
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))
            body = json.loads(resp.read().decode("utf-8"))
            self.assertEqual(body, {"ok": True})
            self.assertNotIn("room", body)

    def test_post_action_rejects_malformed_inputs_and_hides_internal_errors(self):
        url = f"http://127.0.0.1:{self.svc.port}/api/action"

        # Non-dict JSON body -> 400
        req_list = urllib.request.Request(
            url,
            data=b"[1, 2, 3]",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_list, timeout=2.0)
        self.assertEqual(ctx.exception.code, 400)
        self.assertIsNone(ctx.exception.headers.get("Access-Control-Allow-Origin"))
        ctx.exception.close()

        # Wrong room token -> 403
        req_bad = urllib.request.Request(
            url,
            data=json.dumps({"action": "set_category", "category": "CD", "room": "ARCH-0000-0000"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx2:
            urllib.request.urlopen(req_bad, timeout=2.0)
        self.assertEqual(ctx2.exception.code, 403)
        ctx2.exception.close()

        # Internal exception -> generic 500 without leaking raw Python exception message
        with mock.patch.object(self.svc, "process_remote_action", side_effect=RuntimeError("SECRET_INTERNAL_STACK")):
            req_err = urllib.request.Request(
                url,
                data=json.dumps({"action": "set_category", "category": "CD", "room": self.svc.room_token}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx3:
                urllib.request.urlopen(req_err, timeout=2.0)
            self.assertEqual(ctx3.exception.code, 500)
            err_body = ctx3.exception.read().decode("utf-8")
            self.assertNotIn("SECRET_INTERNAL_STACK", err_body)
            ctx3.exception.close()

    def test_ntfy_attachment_url_allowlist(self):
        self.assertTrue(_is_safe_ntfy_attachment_url("https://ntfy.sh/file/abc123.txt"))
        self.assertFalse(_is_safe_ntfy_attachment_url("file:///etc/passwd"))
        self.assertFalse(_is_safe_ntfy_attachment_url("http://127.0.0.1:8080/secret"))
        self.assertFalse(_is_safe_ntfy_attachment_url("https://evil.example/payload"))
        self.assertFalse(_is_safe_ntfy_attachment_url("https://user:pass@ntfy.sh/file/a"))
        self.assertFalse(_is_safe_ntfy_attachment_url("https://ntfy.sh:8443/file/a"))

    def test_scanner_html_synced_and_hardened(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        docs_html = os.path.join(root, "docs", "scanner.html")
        data_html = os.path.join(root, "app", "archivio", "data", "scanner.html")
        with open(docs_html, "rb") as f1, open(data_html, "rb") as f2:
            b1 = f1.read()
            b2 = f2.read()
        self.assertEqual(b1, b2)
        txt = b1.decode("utf-8")
        self.assertNotIn('"DEFAULT"', txt)
        self.assertIn("escapeHtml(data.code", txt)
        self.assertIn("escapeHtml(CURRENT_CAT)", txt)
        self.assertIn("escapeHtml(c.publisher", txt)

    def test_scanner_js_suite_passes(self):
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node non installato")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        res = subprocess.run(
            [node_bin, "--test", os.path.join(root, "tests", "test_scanner_js.mjs")],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(res.returncode, 0, res.stdout + "\n" + res.stderr)


class TestStoreAndCatalogRegressions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="archivio_reg_cat_")
        cache = PersistentCache(db_file=os.path.join(self._tmp, "cache.sqlite"))
        self.cat = CatalogService(cache=cache)

    def tearDown(self):
        self.cat.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_backup_rotation_does_not_delete_prefixed_sheets(self):
        with mock.patch("archivio.store.backups_dir", return_value=self._tmp):
            libri_csv = os.path.join(self._tmp, "libri.csv")
            jazz_csv = os.path.join(self._tmp, "libri_jazz.csv")
            with open(libri_csv, "w", encoding="utf-8") as f:
                f.write("Libro,A,B,C,2020,B\n")
            with open(jazz_csv, "w", encoding="utf-8") as f:
                f.write("Libro,J,K,L,2020,K\n")
            jazz_backup = os.path.join(self._tmp, "libri_jazz_20260101-120000.csv")
            shutil.copy2(jazz_csv, jazz_backup)

            for idx in range(5):
                _backup(libri_csv, keep=2)
                # Ensure unique timestamped backups even within same second
                self.assertTrue(os.path.exists(jazz_backup), f"Deleted sibling backup on iteration {idx}")

    def test_item_with_changes_recomputes_auto_author_sort_only(self):
        it = Item(title="Il nome della rosa", author="Umberto Eco", category="Libri")
        self.assertEqual(it.author_sort, "Eco, Umberto")
        it2 = it.with_changes(author="Italo Calvino")
        self.assertEqual(it2.author_sort, "Calvino, Italo")

        manual = Item(title="Test", author="Umberto Eco", author_sort="Custom Sort", category="Libri")
        manual2 = manual.with_changes(author="Italo Calvino")
        self.assertEqual(manual2.author_sort, "Custom Sort")

    def test_resolve_collection_metadata_avoids_substring_false_positives(self):
        self.assertEqual(self.cat.resolve_collection_metadata("Letture per la scuola", "Libri"), ("", ""))
        self.assertEqual(self.cat.resolve_collection_metadata("Edimburgo", "Libri"), ("", ""))
        self.assertEqual(self.cat.resolve_collection_metadata("Assurdo", "Libri"), ("", ""))
        self.assertEqual(self.cat.resolve_collection_metadata("ET Scrittori", "Libri")[0], "Einaudi")
        self.assertEqual(self.cat.resolve_collection_metadata("Oscar moderni classici", "Libri")[0], "Mondadori")

    def test_barcode_variants_isbn10_isbn13_and_ean8(self):
        v10 = _barcode_variants("0-201-61622-X")
        self.assertEqual(v10, ["020161622X", "9780201616224"])
        v13 = _barcode_variants("9780201616224")
        self.assertEqual(v13, ["9780201616224", "020161622X"])
        v8 = _barcode_variants("12345678")
        self.assertEqual(v8, ["12345678"])

    def test_cd_pre_1982_filtered_and_archive_edition_preserved(self):
        fake_itunes = {
            "results": [
                {
                    "collectionName": "Obscure Test Album",
                    "artistName": "Test Artist",
                    "releaseDate": "1973-03-01T08:00:00Z",
                    "copyright": "℗ 1973 Test Records",
                }
            ]
        }
        with mock.patch.object(self.cat, "_http_get_json", return_value=fake_itunes):
            hits = self.cat._search_itunes_music("CD", "obscure test", "obscure test", "title", None)
        self.assertTrue(hits)
        self.assertEqual(hits[0].year, "")
        self.assertEqual(hits[0].years, [])

        arch_sug = Suggestion(
            title="1984",
            author="George Orwell",
            publisher="Einaudi",
            year="2021",
            category="Libri",
            source="archive",
            score=9900.0,
        )
        applied = self.cat._apply_curated_profile(arch_sug)
        self.assertEqual(applied.publisher, "Einaudi")
        self.assertEqual(applied.year, "2021")

    def test_musicbrainz_context_author_not_double_escaped(self):
        captured_urls = []

        def fake_get(url, **kwargs):
            captured_urls.append(url)
            return {"release-groups": []}

        with mock.patch.object(self.cat, "_http_get_json", side_effect=fake_get):
            self.cat._search_musicbrainz(
                "CD", "Back in Black", "back in black", "title", 10, None, context_author="AC/DC"
            )
        self.assertTrue(captured_urls)
        self.assertNotIn("\\\\\\/", urllib.parse.unquote(captured_urls[0]))

    def test_persistent_cache_does_not_reopen_after_close(self):
        self.cat.cache.set("k1", {"ok": 1})
        self.assertEqual(self.cat.cache.get("k1"), {"ok": 1})
        self.cat.close()
        self.assertIsNone(self.cat.cache.get("k1"))
        self.cat.cache.set("k2", {"ok": 2})
        self.assertIsNone(self.cat.cache._conn)

    def test_http_get_json_resets_stale_429(self):
        self.cat._set_last_http_code(429)
        res = self.cat._http_get_json("https://example.invalid", is_cancelled=lambda: True)
        self.assertIsNone(res)
        self.assertEqual(self.cat._get_last_http_code(), 0)


class TestUiLifecycleRegressions(unittest.TestCase):
    def setUp(self):
        self._tmp_archive = tempfile.mkdtemp(prefix="archivio_reg_ui_")
        self.settings = Settings(language="it", archive_dir=self._tmp_archive)
        self.win = MainWindow(settings=self.settings)

    def tearDown(self):
        for cat in ("Libri", "CD", "Vinili"):
            self.win.dirty_by_cat[cat] = False
        self.win.close()
        shutil.rmtree(self._tmp_archive, ignore_errors=True)

    def test_first_run_dialog_rejects_empty_folder(self):
        dlg = FirstRunSetupDialog(Settings(language="it", archive_dir=self._tmp_archive), self.win)
        dlg.ed_folder.setText("   ")
        with mock.patch.object(dlg, "accept") as mock_accept:
            dlg._validate_and_accept()
            mock_accept.assert_not_called()
        dlg.close()

    def test_local_req_id_invalidated_on_switch_and_reset(self):
        before = self.win._local_req_id
        self.win.reset_entry_form()
        self.assertGreater(self.win._local_req_id, before)
        mid = self.win._local_req_id
        self.win._switch_category("CD")
        self.assertGreater(self.win._local_req_id, mid)

    def test_close_event_stops_pending_scanner_start_and_closes_remote_dialog(self):
        self.assertTrue(self.win._scanner_start_timer.isActive())
        self.win.open_remote_scanner_dialog()
        self.assertTrue(self.win._remote_dialog.isVisible())
        self.win.close()
        self.assertTrue(self.win._closing)
        self.assertFalse(self.win._scanner_start_timer.isActive())
        self.assertFalse(self.win._remote_dialog.isVisible())
        self.assertFalse(self.win.remote_scanner._running)
        # Simulating timer callback after close must not restart scanner
        self.win._start_remote_scanner_if_open()
        self.assertFalse(self.win.remote_scanner._running)


if __name__ == "__main__":
    unittest.main()
