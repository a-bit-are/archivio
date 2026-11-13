"""
Test dell'interfaccia di autocompletamento: ricerca locale in background, chiusura immediata della tendina
al cambio di campo, scorciatoie della fotocamera, pulizia dei campi dopo l'inserimento da scansione e
pertinenza dei suggerimenti di Collana / Collezione e della chiave di ordinamento.
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSizePolicy

from archivio.catalog import CatalogService
from archivio.config import Settings
from archivio.store import Item
from archivio.textutil import sort_key_matches_author
from archivio.ui import MacCameraScannerDialog, MainWindow

app = QApplication.instance() or QApplication(sys.argv)


def _pump(predicate, timeout: float = 4.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestSuggestionPopup(unittest.TestCase):
    def setUp(self):
        self.win = MainWindow(settings=Settings(language="it", online=False))
        self.win.show()
        self.win.activateWindow()
        self.win.raise_()
        _pump(lambda: self.win.isActiveWindow(), 1.0)

    def tearDown(self):
        # Nessun dialogo "modifiche non salvate" durante la chiusura nei test
        for cat in self.win.dirty_by_cat:
            self.win.dirty_by_cat[cat] = False
        if hasattr(self.win, "remote_scanner"):
            self.win.remote_scanner.stop()
        if hasattr(self.win, "_local_pool"):
            self.win._local_pool.clear()
        self.win.close()

    def test_local_suggestions_arrive_in_background_and_hide_on_focus_change(self):
        self.win.in_title.setFocus()
        _pump(lambda: self.win.in_title.hasFocus(), 1.0)
        if not self.win.in_title.hasFocus():
            self.skipTest("piattaforma offscreen senza gestione del focus")
        QTest.keyClicks(self.win.in_title, "il nome della rosa")
        self.assertTrue(_pump(lambda: self.win.popup.isVisible()), "la tendina doveva comparire")

        # Passando da Titolo ad Autore la tendina sparisce subito, senza dover digitare altro
        self.win.in_author.setFocus()
        self.assertTrue(_pump(lambda: not self.win.popup.isVisible(), 1.0))
        # ...e nessun risultato tardivo la fa riapparire
        _pump(lambda: False, 0.4)
        self.assertFalse(self.win.popup.isVisible())

    def test_escape_dismisses_and_stays_closed(self):
        self.win.in_title.setFocus()
        _pump(lambda: self.win.in_title.hasFocus(), 1.0)
        if not self.win.in_title.hasFocus():
            self.skipTest("piattaforma offscreen senza gestione del focus")
        QTest.keyClicks(self.win.in_title, "umberto eco")
        self.assertTrue(_pump(lambda: self.win.popup.isVisible()))
        QTest.keyClick(self.win.in_title, Qt.Key.Key_Escape)
        self.assertFalse(self.win.popup.isVisible())
        _pump(lambda: False, 0.4)
        self.assertFalse(self.win.popup.isVisible())

    def test_form_is_cleared_after_scanned_insertion(self):
        self.win._switch_category("CD", force=True)
        self.win.in_title.setText("Vecchio titolo")
        self.win.in_author.setText("Vecchio autore")
        item = Item(title="Wall of Eyes", author="The Smile", publisher="XL Recordings", year="2024", category="CD")
        self.win._on_remote_item_added("CD", item)
        self.assertEqual([i.title for i in self.win.data_by_cat["CD"]], ["Wall of Eyes"])
        self.assertEqual(self.win.in_title.text(), "")
        self.assertEqual(self.win.in_author.text(), "")
        self.assertEqual(self.win.in_sort.text(), "")
        self.assertEqual(self.win.in_coll.currentText(), "")

    @patch("archivio.ui.list_mac_cameras", return_value=[{"id": "cam1", "name": "Test Cam"}])
    def test_camera_space_key_captures_even_when_a_button_has_focus(self, _mock_cams):
        dlg = MacCameraScannerDialog(
            self.win.remote_scanner, self.win.settings, lambda: "CD", parent=None
        )
        try:
            calls = []
            dlg.capture_barcode_now = lambda: calls.append("capture")  # type: ignore[method-assign]
            self.assertTrue(dlg._handle_action_key(Qt.Key.Key_Space))
            self.assertEqual(calls, ["capture"])
            from PySide6.QtGui import QShortcut

            keys = {sc.key().toString() for sc in dlg.findChildren(QShortcut)}
            self.assertIn("Space", keys)
        finally:
            dlg.cam_session.stop()
            dlg.close()

    @patch("archivio.ui.list_mac_cameras", return_value=[{"id": "cam1", "name": "Test Cam"}])
    def test_camera_space_vs_enter_behavior(self, _mock_cams):
        """Spazio deve prendere il codice e Invio inserire (non devono fare la stessa cosa)."""
        dlg = MacCameraScannerDialog(
            self.win.remote_scanner, self.win.settings, lambda: "CD", parent=None
        )
        try:
            capture_calls = []
            add_calls = []
            dlg.capture_barcode_now = lambda: capture_calls.append("capture")  # type: ignore[method-assign]
            dlg._on_card_add_clicked = lambda: add_calls.append("add")  # type: ignore[method-assign]

            # 1. Senza anteprima: Spazio acquisisce il codice, Invio NON acquisisce
            dlg.card_preview.hide()
            self.assertTrue(dlg._handle_action_key(Qt.Key.Key_Space))
            self.assertEqual(capture_calls, ["capture"])
            self.assertEqual(add_calls, [])

            # Invio senza anteprima: non acquisisce codice, mostra avviso
            self.assertTrue(dlg._handle_action_key(Qt.Key.Key_Return))
            self.assertEqual(capture_calls, ["capture"])  # Nessuna nuova cattura
            self.assertEqual(add_calls, [])

            # 2. Con anteprima visibile: Invio inserisce l'articolo, Spazio acquisisce il prossimo codice
            dlg.card_preview.show()
            self.assertTrue(dlg._handle_action_key(Qt.Key.Key_Return))
            self.assertEqual(add_calls, ["add"])
            self.assertEqual(capture_calls, ["capture"])

            # Spazio mentre c'è anteprima: prende comunque il codice per scansionare il prossimo
            self.assertTrue(dlg._handle_action_key(Qt.Key.Key_Space))
            self.assertEqual(capture_calls, ["capture", "capture"])
        finally:
            dlg.cam_session.stop()
            dlg.close()

    @patch("archivio.ui.list_mac_cameras", return_value=[{"id": "cam1", "name": "Test Cam"}])
    def test_camera_window_fullscreen_and_native_chrome(self, _mock_cams):
        """Verifica apertura in sovraimpressione sopra Archivio e passaggio a schermo intero con tasto verde/Esc."""
        self.win.open_mac_camera_dialog()
        dlg = self.win._mac_cam_dialog
        self.assertIsNotNone(dlg)
        try:
            # Controllo flag finestra e assenza pulsanti ridondanti
            flags = dlg.windowFlags()
            self.assertTrue(bool(flags & Qt.WindowType.Window))
            self.assertTrue(bool(flags & Qt.WindowType.WindowMinMaxButtonsHint))
            self.assertTrue(bool(flags & Qt.WindowType.WindowFullscreenButtonHint))
            self.assertFalse(hasattr(dlg, "btn_stay_on_top"))
            self.assertFalse(hasattr(dlg, "btn_fullscreen"))
            self.assertIs(dlg._overlay_parent, self.win)

            # Video preview deve espandersi dinamicamente (e poter tornare piccolo con Ignored)
            self.assertEqual(dlg.lbl_video.sizePolicy().horizontalPolicy(), QSizePolicy.Policy.Ignored)
            self.assertEqual(dlg.lbl_video.sizePolicy().verticalPolicy(), QSizePolicy.Policy.Ignored)

            # Test passaggio overlay -> fullscreen -> overlay tramite _on_green_zoom_clicked / Esc
            dlg._on_green_zoom_clicked()
            self.assertTrue(dlg.isFullScreen())
            self.assertTrue(dlg._handle_action_key(Qt.Key.Key_Escape))
            self.assertFalse(dlg.isFullScreen())
        finally:
            dlg.cam_session.stop()
            dlg.close()

    @patch("archivio.ui.list_mac_cameras", return_value=[{"id": "cam1", "name": "Test Cam"}])
    def test_camera_auto_capture_default_and_no_card_wipe(self, _mock_cams):
        """Verifica che Acquisisci automaticamente sia attivo di default e non azzeri la scheda aperta."""
        dlg = MacCameraScannerDialog(
            self.win.remote_scanner, self.win.settings, lambda: "CD", parent=None
        )
        try:
            self.assertTrue(dlg.chk_auto.isChecked())
            lookups = []
            dlg._start_barcode_lookup = lambda code: lookups.append(code)  # type: ignore[method-assign]
            dlg._render_frame = lambda *_args: None  # type: ignore[method-assign]

            # Primo codice: avvia lookup automatico
            dlg._on_frame_ready(b"jpg", "602498523919")
            self.assertEqual(lookups, ["602498523919"])

            # Simuliamo scheda anteprima aperta: anche dopo cooldown > 2.8s lo stesso codice NON deve azzerare la scheda
            dlg.card_preview.show()
            dlg._last_auto_ts -= 10.0
            dlg._on_frame_ready(b"jpg", "602498523919")
            self.assertEqual(lookups, ["602498523919"])

            # Codice diverso invece parte subito
            dlg._on_frame_ready(b"jpg", "9788804680987")
            self.assertEqual(lookups, ["602498523919", "9788804680987"])
        finally:
            dlg.cam_session.stop()
            dlg.close()

    @patch("archivio.ui.list_mac_cameras", return_value=[{"id": "cam1", "name": "Test Cam"}])
    @patch.object(Settings, "save", return_value=None)
    def test_camera_prefs_persist_and_fullscreen_close_defers(self, mock_save, _mock_cams):
        dlg = MacCameraScannerDialog(
            self.win.remote_scanner, self.win.settings, lambda: "CD", parent=None
        )
        try:
            dlg.chk_auto.setChecked(False)
            self.assertFalse(self.win.settings.camera_auto_capture)
            dlg.chk_burst.setChecked(True)
            self.assertTrue(self.win.settings.camera_burst_mode)
            self.assertGreaterEqual(mock_save.call_count, 2)

            with patch.object(sys, "platform", "darwin"), patch.object(dlg, "isFullScreen", return_value=True), patch.object(
                dlg, "showNormal"
            ) as mock_normal:
                from PySide6.QtGui import QCloseEvent

                ev = QCloseEvent()
                dlg.closeEvent(ev)
                self.assertFalse(ev.isAccepted())
                self.assertTrue(dlg._close_after_fs)
                mock_normal.assert_called_once()
        finally:
            dlg._close_after_fs = False
            dlg._force_close_now = True
            dlg.cam_session.stop()
            dlg.close()

    def test_mainwindow_close_cancel_preserves_remote_scanner(self):
        from PySide6.QtGui import QCloseEvent
        from PySide6.QtWidgets import QMessageBox

        self.win.dirty_by_cat["Libri"] = True
        self.win.remote_scanner._running = True
        with patch("archivio.ui.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            ev = QCloseEvent()
            self.win.closeEvent(ev)
            self.assertFalse(ev.isAccepted())
            self.assertTrue(self.win.remote_scanner._running)
        self.win.dirty_by_cat["Libri"] = False
        self.win.remote_scanner._running = False

    @patch("archivio.ui.list_mac_cameras", return_value=[{"id": "cam1", "name": "Test Cam"}])
    def test_frozen_cover_retake_restores_frame_and_uses_scanfile(self, _mock_cams):
        import io
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (120, 140, 160)).save(buf, format="JPEG")
        jpg_bytes = buf.getvalue()

        dlg = MacCameraScannerDialog(
            self.win.remote_scanner, self.win.settings, lambda: "CD", parent=None
        )
        try:
            dlg._last_full_jpg_bytes = jpg_bytes
            dlg.freeze_cover_photo()
            self.assertTrue(dlg._is_cover_frozen)

            render_calls = []
            dlg._render_frame = lambda b, bc: render_calls.append((len(b), bc))  # type: ignore[method-assign]
            dlg.retake_cover_photo()
            self.assertFalse(dlg._is_cover_frozen)
            self.assertTrue(render_calls)

            # Ripete freeze e verifica percorso rapido scanfile + timeout recovery
            dlg.freeze_cover_photo()
            scanned_paths = []
            with patch.object(dlg.cam_session, "request_scan_file", side_effect=lambda p: scanned_paths.append(p) or True):
                dlg.confirm_search_from_frozen_photo()
                self.assertEqual(len(scanned_paths), 1)
                self.assertFalse(dlg.btn_capture_ocr.isEnabled())
                self.assertTrue(os.path.exists(scanned_paths[0]))
                dlg._on_ocr_timeout(dlg._pending_ocr_req_id)
                self.assertTrue(dlg.btn_capture_ocr.isEnabled())
                self.assertFalse(dlg._ocr_waiting)
                self.assertFalse(os.path.exists(scanned_paths[0]))
        finally:
            dlg.cam_session.stop()
            dlg.close()

    def test_edit_dialog_non_blocking_and_preserves_isbn(self):
        from archivio.catalog import EditionBundle
        from archivio.ui import EditItemDialog

        it = Item(
            title="Il nome della rosa",
            author="Umberto Eco",
            publisher="Bompiani",
            year="1980",
            isbn_or_barcode="9788845246340",
            category="Libri",
        )
        with patch.object(self.win.catalog, "fetch_editions") as mock_fetch, patch(
            "archivio.ui.QThreadPool.globalInstance"
        ) as mock_pool:
            dlg = EditItemDialog(it, "Libri", "it", self.win.catalog, ["Tascabili Bompiani"], self.win)
            try:
                # Aprire "Modifica" non deve bloccare il thread GUI con fetch_editions sincrono
                mock_fetch.assert_not_called()
                mock_pool.return_value.start.assert_called_once()
                bundle = EditionBundle(
                    category="Libri",
                    work_id="",
                    title="Il nome della rosa",
                    author="Umberto Eco",
                    author_sort="Eco, Umberto",
                    default_publisher="Bompiani",
                    default_year="1980",
                    publishers=["Bompiani", "La nave di Teseo"],
                    years=["1980", "2020"],
                    collections=["I grandi tascabili"],
                )
                dlg._on_dlg_editions_fetched(dlg._edition_req_id, bundle)
                pubs = [dlg.cb_pub.itemText(i) for i in range(dlg.cb_pub.count())]
                self.assertIn("La nave di Teseo", pubs)
                self.assertEqual(dlg.updated_item().isbn_or_barcode, "9788845246340")
            finally:
                dlg.reject()

    @patch("archivio.ui.list_mac_cameras", return_value=[{"id": "cam1", "name": "Test Cam"}])
    def test_candidate_click_does_not_block_with_remote_fetch(self, _mock_cams):
        dlg = MacCameraScannerDialog(
            self.win.remote_scanner, self.win.settings, lambda: "CD", parent=None
        )
        try:
            cand = {
                "title": "Avete Ragione Tutti²",
                "author": "Artista",
                "publisher": "A1 Entertainment",
                "year": "2010",
                "collection": "",
            }
            dlg._multi_candidates = [cand]
            dlg.list_candidates.addItem("Avete Ragione Tutti²")
            item_w = dlg.list_candidates.item(0)
            with patch.object(self.win.remote_scanner, "process_remote_action", wraps=self.win.remote_scanner.process_remote_action) as mock_act, patch.object(
                self.win.catalog, "fetch_editions"
            ) as mock_fetch:
                dlg.chk_burst.setChecked(False)
                dlg._on_candidate_clicked(item_w)
                mock_act.assert_called_once()
                sent = mock_act.call_args[0][0]
                self.assertEqual(sent.get("action"), "preview_item")
                self.assertFalse(sent.get("fetch_remote", True))
                # Nessun fetch_editions sincrono sul main thread durante preview_item
                mock_fetch.assert_not_called()
        finally:
            dlg.cam_session.stop()
            dlg.close()

    def test_edit_dialog_full_width_and_filters_unrelated_collections(self):
        from archivio.ui import EditItemDialog

        cd_items = [
            Item(
                title="Mina Fossati",
                author="Mina",
                publisher="PDU",
                year="2019",
                collection="Deluxe Edition",
                category="CD",
            ),
            Item(
                title="Crime of the Century",
                author="Supertramp",
                publisher="A&M",
                year="1974",
                collection="The Supertramp Remasters",
                category="CD",
            ),
            Item(
                title="Mozart Vol.1",
                author="Mozart",
                publisher="Hobby&Work",
                year="1995",
                collection="I grandi della musica classica",
                category="CD",
            ),
        ]
        self.win.data_by_cat["CD"] = cd_items
        self.win.current_category = "CD"

        captured_dialogs = []
        orig_exec = EditItemDialog.exec

        def _fake_exec(dlg_self):
            captured_dialogs.append(dlg_self)
            dlg_self.show()
            colls = [dlg_self.cb_coll.itemText(i) for i in range(dlg_self.cb_coll.count())]
            self.assertIn("Deluxe Edition", colls)
            self.assertNotIn("The Supertramp Remasters", colls)
            self.assertNotIn("I grandi della musica classica", colls)
            self.assertGreaterEqual(dlg_self.cb_pub.width(), dlg_self.ed_title.width() - 4)
            self.assertGreaterEqual(dlg_self.cb_coll.width(), dlg_self.ed_title.width() - 4)
            self.assertGreaterEqual(dlg_self.cb_year.width(), dlg_self.ed_title.width() - 4)
            return EditItemDialog.DialogCode.Rejected

        with patch.object(EditItemDialog, "exec", _fake_exec), patch(
            "archivio.ui.QThreadPool.globalInstance"
        ):
            self.win._edit_source_row(0)

        self.assertEqual(len(captured_dialogs), 1)
        captured_dialogs[0].close()

    def test_refresh_collection_choices_excludes_unrelated_archive_items(self):
        from archivio.catalog import EditionBundle

        self.win.data_by_cat["CD"] = [
            Item(
                title="Bugiardo più che mai",
                author="Mina",
                publisher="PDU",
                year="1999",
                collection="Sorrisi e Canzoni Collana",
                category="CD",
            ),
            Item(
                title="Breakfast in America",
                author="Supertramp",
                publisher="A&M",
                year="1979",
                collection="The Supertramp Remasters",
                category="CD",
            ),
        ]
        self.win.current_category = "CD"
        self.win.in_title.setText("Mina Fossati")
        self.win.in_author.setText("Mina")
        self.win._current_work_collections = []
        self.win._current_bundle = None
        self.win._refresh_collection_choices(preserve_current=False)

        colls = [self.win.in_coll.itemText(i) for i in range(self.win.in_coll.count())]
        self.assertNotIn("Sorrisi e Canzoni Collana", colls)
        self.assertNotIn("The Supertramp Remasters", colls)

        # Verifica anche il filtraggio per editore tramite collection_meta del bundle
        self.win.current_category = "Libri"
        self.win.in_title.setText("Il nome della rosa")
        self.win.in_author.setText("Umberto Eco")
        self.win._current_bundle = EditionBundle(
            category="Libri",
            work_id="w1",
            title="Il nome della rosa",
            author="Umberto Eco",
            author_sort="Eco, Umberto",
            default_publisher="Mondadori",
            default_year="1984",
            publishers=["Mondadori", "Einaudi"],
            years=["1984", "2014"],
            collections=["Oscar Mondadori", "Supercoralli"],
            collection_meta={
                "Oscar Mondadori": {"publisher": "Mondadori", "year": "1984"},
                "Supercoralli": {"publisher": "Einaudi", "year": "2014"},
            },
        )
        self.win._current_work_collections = ["Oscar Mondadori", "Supercoralli"]
        self.win.in_pub.set_choices(["Mondadori", "Einaudi"], "Mondadori")
        self.win._refresh_collection_choices(preserve_current=False)
        lib_colls = [self.win.in_coll.itemText(i) for i in range(self.win.in_coll.count())]
        self.assertIn("Oscar Mondadori", lib_colls)
        self.assertNotIn("Supercoralli", lib_colls)

    def test_edit_dialog_suggests_artist_titles_and_preserves_box_set_fields(self):
        from archivio.ui import EditItemDialog

        it = Item(
            title="The Dark Side of the Moon",
            author="Pink Floyd",
            publisher="EMI",
            year="2007",
            collection="Oh, by the Way (Box Set)",
            category="CD",
        )
        dlg = EditItemDialog(
            it,
            "CD",
            "it",
            self.win.catalog,
            ["Oh, by the Way (Box Set)"],
            self.win,
            archive_items=[it],
            online_enabled=False,
        )
        try:
            dlg.show()
            dlg.activateWindow()
            dlg.ed_title.setFocus()
            _pump(lambda: dlg.ed_title.hasFocus(), 1.0)
            if not dlg.ed_title.hasFocus():
                self.skipTest("piattaforma offscreen senza gestione del focus")

            # Cancella il titolo (simula uso su elemento duplicato di un cofanetto) -> compaiono i dischi di Pink Floyd
            dlg.ed_title.clear()
            self.assertTrue(_pump(lambda: dlg.popup.isVisible(), 2.5), "la tendina dei titoli doveva apparire")
            shown_titles = [
                dlg.popup.list_widget.item(i).data(Qt.ItemDataRole.UserRole).title
                for i in range(dlg.popup.list_widget.count())
            ]
            self.assertIn("Wish You Were Here", shown_titles)

            # Seleziona Wish You Were Here e verifica che i campi del cofanetto restino intatti
            for i in range(dlg.popup.list_widget.count()):
                sug = dlg.popup.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
                if sug.title == "Wish You Were Here":
                    dlg.popup.list_widget.setCurrentRow(i)
                    break
            dlg.popup.accept_current()
            app.processEvents()

            res = dlg.updated_item()
            self.assertEqual(res.title, "Wish You Were Here")
            self.assertEqual(res.author, "Pink Floyd")
            self.assertEqual(res.publisher, "EMI")
            self.assertEqual(res.collection, "Oh, by the Way (Box Set)")
            self.assertEqual(res.year, "2007")
        finally:
            dlg.reject()
            dlg.close()

    def test_catalog_search_online_empty_query_with_context_author(self):
        import tempfile
        from archivio.catalog import PersistentCache

        with tempfile.TemporaryDirectory() as tmpdir:
            svc = CatalogService(cache=PersistentCache(db_file=os.path.join(tmpdir, "online_test_cache.sqlite")))
            called_urls = []

            def _fake_get_json(url, **_kwargs):
                called_urls.append(url)
                if "itunes.apple.com" in url:
                    return {
                        "results": [
                            {
                                "collectionName": "Wish You Were Here",
                                "artistName": "Pink Floyd",
                                "releaseDate": "1975-09-12T07:00:00Z",
                                "copyright": "℗ 1975 Pink Floyd Music Ltd.",
                            }
                        ]
                    }
                if "musicbrainz.org" in url:
                    return {"release-groups": []}
                return None

            with patch.object(svc, "_http_get_json", side_effect=_fake_get_json):
                hits = svc.search_online(
                    category="CD",
                    query="",
                    lang="it",
                    field_mode="title",
                    context_author="Pink Floyd",
                )
            self.assertTrue(called_urls)
            self.assertTrue(any(h.title == "Wish You Were Here" for h in hits))

    def test_clean_combobox_disables_implicit_completer_and_repolishes_safely(self):
        import gc
        from archivio.ui import CleanComboBox

        cb = CleanComboBox(editable=True)
        self.assertIsNone(cb.completer())
        cb.set_choices(["EMI", "Parlophone"], "EMI")
        self.assertEqual(cb.property("hasChoices"), "true")
        cb.setCurrentIndex(1)
        cb._deselect_text_soon()
        cb.set_choices(["EMI"], "EMI")
        self.assertEqual(cb.property("hasChoices"), "false")
        cb.deleteLater()
        app.processEvents()
        gc.collect()
        app.processEvents()

    def test_repeated_duplicate_edit_cycles_with_async_workers_do_not_crash(self):
        import gc
        import time
        from PySide6.QtCore import QTimer
        from archivio.catalog import EditionBundle, Suggestion
        from archivio.ui import EditItemDialog

        self.win.current_category = "CD"
        base_item = Item(
            title="The Dark Side of the Moon",
            author="Pink Floyd",
            publisher="EMI",
            year="1973",
            collection="Oh, by the Way",
            category="CD",
        )
        self.win._apply_snapshot("CD", [base_item])
        self.win.settings.online_enabled = True

        def _fake_online(**_kw):
            time.sleep(0.01)
            return [
                Suggestion(
                    title="Wish You Were Here",
                    author="Pink Floyd",
                    publisher="EMI",
                    year="1975",
                    collection="Oh, by the Way",
                    category="CD",
                    work_id="pf-wywh",
                )
            ]

        def _fake_editions(**_kw):
            time.sleep(0.01)
            return EditionBundle(
                category="CD",
                work_id="pf-wywh",
                title="Wish You Were Here",
                author="Pink Floyd",
                author_sort="Pink Floyd",
                default_publisher="EMI",
                default_year="1975",
                publishers=["EMI", "Harvest"],
                years=["1975", "2011"],
                collections=["Oh, by the Way", "Discovery Edition"],
            )

        with patch.object(self.win.catalog, "search_online", side_effect=_fake_online), patch.object(
            self.win.catalog, "fetch_editions", side_effect=_fake_editions
        ):
            for cycle in range(10):
                self.win.table.selectRow(0)
                self.win.duplicate_selected_item()
                row = len(self.win.data_by_cat["CD"]) - 1
                self.win._refresh_collection_choices(preserve_current=True)

                def _drive_dialog(idx=cycle):
                    for w in app.topLevelWidgets():
                        if isinstance(w, EditItemDialog) and w.isVisible():
                            w.ed_title.setText(f"Wish {idx}")
                            w._on_dlg_title_edited(f"Wish {idx}")
                            w.cb_coll.set_choices(["Oh, by the Way", "Discovery Edition"], "Oh, by the Way")
                            w.accept()
                            break

                QTimer.singleShot(12, _drive_dialog)
                self.win._edit_source_row(row)
                gc.collect()
                app.processEvents()

            self.assertEqual(len(self.win.data_by_cat["CD"]), 11)
            # Attende il completamento dei worker ritirati e verifica la pulizia finale
            self.assertTrue(_pump(lambda: len(self.win._retired_workers) == 0, 2.0))


class TestRelevance(unittest.TestCase):
    def test_sort_key_must_belong_to_author(self):
        self.assertTrue(sort_key_matches_author("Cremonini, Cesare", "Cesare Cremonini"))
        self.assertTrue(sort_key_matches_author("Beatles, The", "The Beatles"))
        self.assertFalse(sort_key_matches_author("Mozart, Wolfgang Amadeus", "Cesare Cremonini"))

    def test_music_collections_are_artist_specific_only(self):
        svc = CatalogService()
        cremonini = svc.collections_for_publisher("Virgin", "CD", "Cesare Cremonini")
        self.assertEqual(cremonini, [])
        floyd = svc.collections_for_publisher("EMI", "CD", "Pink Floyd")
        self.assertIn("Discovery Edition", floyd)
        self.assertNotIn("Original Album Classics", floyd)
        self.assertNotIn("Sorrisi e Canzoni Collana", floyd)


if __name__ == "__main__":
    unittest.main()
