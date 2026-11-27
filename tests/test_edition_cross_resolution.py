import atexit
import os
import shutil
import tempfile
import unittest

if "ARCHIVIO_SUPPORT_DIR" not in os.environ:
    _TEST_SUPPORT_DIR = tempfile.mkdtemp(prefix="archivio_test_cfg_")
    os.environ["ARCHIVIO_SUPPORT_DIR"] = _TEST_SUPPORT_DIR
    atexit.register(shutil.rmtree, _TEST_SUPPORT_DIR, ignore_errors=True)
from archivio.catalog import (
    CatalogService,
    EditionBundle,
    PersistentCache,
    Suggestion,
    _clean_publisher_name,
    _parse_abebooks_publisher_field,
    _publishers_match,
)

class TestEditionCrossResolution(unittest.TestCase):
    def setUp(self):
        self.catalog = CatalogService()

    def tearDown(self):
        self.catalog.close()

    def test_bundle_post_init_and_cross_resolution(self):
        bundle = EditionBundle(
            category="Libri",
            work_id="work1",
            title="Il nome della rosa",
            author="Umberto Eco",
            author_sort="Eco, Umberto",
            default_publisher="Bompiani",
            default_year="1980",
            publishers=["Bompiani", "Mondadori", "Einaudi"],
            years=["1980", "1984", "2014"],
            collections=["I grandi tascabili", "Oscar Mondadori", "Supercoralli"],
            publisher_years={
                "Bompiani": "1980",
                "Mondadori": "1984",
                "Einaudi": "2014",
            },
            collection_meta={
                "I grandi tascabili": {"publisher": "Bompiani", "year": "1980"},
                "Oscar Mondadori": {"publisher": "Mondadori", "year": "1984"},
                "Supercoralli": {"publisher": "Einaudi", "year": "2014"},
            },
        )

        # 1. Post init auto-populates mappings
        self.assertEqual(bundle.year_publishers.get("1980"), "Bompiani")
        self.assertEqual(bundle.year_publishers.get("1984"), "Mondadori")
        self.assertEqual(bundle.year_publishers.get("2014"), "Einaudi")
        self.assertEqual(bundle.publisher_collections.get("Mondadori"), "Oscar Mondadori")
        self.assertEqual(bundle.year_collections.get("1984"), "Oscar Mondadori")

        # 2. Test resolve_publisher_metadata: change from Mondadori to Einaudi
        # Current collection is "Oscar Mondadori" which belongs to Mondadori.
        yr, coll, clear = self.catalog.resolve_publisher_metadata(
            "Einaudi",
            category="Libri",
            author="Umberto Eco",
            bundle=bundle,
            current_collection="Oscar Mondadori",
        )
        self.assertEqual(yr, "2014")
        self.assertEqual(coll, "Supercoralli")
        self.assertTrue(clear)  # "Oscar Mondadori" must be cleared because it belongs to Mondadori!

        # 3. Test resolve_publisher_metadata with unknown collection
        yr2, coll2, clear2 = self.catalog.resolve_publisher_metadata(
            "Bompiani",
            category="Libri",
            author="Umberto Eco",
            bundle=bundle,
            current_collection="Oscar grandi classici",
        )
        self.assertEqual(yr2, "1980")
        self.assertEqual(coll2, "I grandi tascabili")
        self.assertTrue(clear2)  # Oscar grandi classici belongs to Mondadori, so it must be cleared for Bompiani!

        # 4. Test resolve_year_metadata: change year from 1980 to 1984
        # Current collection is "I grandi tascabili" (Bompiani 1980)
        pub, coll_y, clear_y = self.catalog.resolve_year_metadata(
            "1984",
            category="Libri",
            author="Umberto Eco",
            bundle=bundle,
            current_collection="I grandi tascabili",
            current_publisher="Bompiani",
        )
        self.assertEqual(pub, "Mondadori")
        self.assertEqual(coll_y, "Oscar Mondadori")
        self.assertTrue(clear_y)

        # 5. Test resolve_year_metadata: change year to 2014
        pub3, coll_y3, clear_y3 = self.catalog.resolve_year_metadata(
            "2014",
            category="Libri",
            author="Umberto Eco",
            bundle=bundle,
            current_collection="Oscar Mondadori",
            current_publisher="Mondadori",
        )
        self.assertEqual(pub3, "Einaudi")
        self.assertEqual(coll_y3, "Supercoralli")
        self.assertTrue(clear_y3)

    def test_music_cross_resolution(self):
        bundle = EditionBundle(
            category="Vinili",
            work_id="work2",
            title="The Dark Side of the Moon",
            author="Pink Floyd",
            author_sort="Pink Floyd",
            default_publisher="Harvest",
            default_year="1973",
            publishers=["Harvest", "EMI", "Pink Floyd Records"],
            years=["1973", "1983", "2016"],
            collections=["Discovery Edition"],
            publisher_years={
                "Harvest": "1973",
                "EMI": "1983",
                "Pink Floyd Records": "2016",
            },
        )
        # Select EMI -> Year should be 1983
        yr, _, _ = self.catalog.resolve_publisher_metadata(
            "EMI",
            category="Vinili",
            author="Pink Floyd",
            bundle=bundle,
        )
        self.assertEqual(yr, "1983")

        # Select 2016 -> Label should be Pink Floyd Records
        lbl, _, _ = self.catalog.resolve_year_metadata(
            "2016",
            category="Vinili",
            author="Pink Floyd",
            bundle=bundle,
        )
        self.assertEqual(lbl, "Pink Floyd Records")

    def test_publishers_match_and_clean_publisher_name(self):
        self.assertTrue(_publishers_match("Einaudi", "Einaudi"))
        self.assertTrue(_publishers_match("Einaudi", "G. Einaudi per la Scuola"))
        self.assertTrue(_publishers_match("BUR", "BUR Rizzoli"))
        self.assertTrue(_publishers_match("C.D.E.", "CDE"))
        self.assertFalse(_publishers_match("CDE", "Einaudi"))
        self.assertFalse(_publishers_match("CDE", "DeAgostini"))
        self.assertFalse(_publishers_match("ed", "Einaudi"))

        self.assertEqual(_clean_publisher_name("1993", "it"), "")
        self.assertEqual(_clean_publisher_name("cde", "it"), "CDE")
        self.assertEqual(_clean_publisher_name("club degli editori", "it"), "Club degli Editori")

    def test_many_to_many_bundle_and_pair_resolution(self):
        bundle = EditionBundle(
            category="Libri",
            work_id="fiabe_it",
            title="Fiabe italiane",
            author="Italo Calvino",
            author_sort="Calvino, Italo",
            default_publisher="Einaudi",
            default_year="1976",
            publishers=["Einaudi", "Mondadori", "CDE"],
            years=["1976", "1986", "2011", "2012"],
            collections=["Letture per la scuola media", "La bibliotechina"],
            publisher_all_years={
                "Einaudi": ["1976", "1986", "2012"],
                "Mondadori": ["2011", "2012"],
                "CDE": ["2012"],
            },
            year_all_publishers={
                "1976": ["Einaudi"],
                "1986": ["Einaudi"],
                "2011": ["Mondadori"],
                "2012": ["Einaudi", "Mondadori", "CDE"],
            },
            collection_meta={
                "Letture per la scuola media": {"publisher": "Einaudi", "year": "1986"},
                "La bibliotechina": {"publisher": "Einaudi", "year": "2012"},
            },
        )

        self.assertEqual(bundle.years_for_publisher("Einaudi"), ["1976", "1986", "2012"])
        self.assertEqual(bundle.publishers_for_year("2012"), ["Einaudi", "Mondadori", "CDE"])
        self.assertEqual(bundle.collections_for_pair("Einaudi", "1986"), ["Letture per la scuola media"])
        self.assertEqual(bundle.collections_for_pair("CDE", "1986"), [])

        # Se l'anno corrente 1986 è valido per Einaudi, va preservato e la collana deve corrispondere al 1986
        yr_e, coll_e, clear_e = self.catalog.resolve_publisher_metadata(
            "Einaudi",
            category="Libri",
            author="Italo Calvino",
            bundle=bundle,
            current_collection="La bibliotechina",
            current_year="1986",
        )
        self.assertEqual(yr_e, "1986")
        self.assertEqual(coll_e, "Letture per la scuola media")
        self.assertTrue(clear_e)

        # Con preserve_publisher=True su CDE + 1986, non deve mai iniettare la collana Einaudi
        pub_c, coll_c, clear_c = self.catalog.resolve_year_metadata(
            "1986",
            category="Libri",
            author="Italo Calvino",
            bundle=bundle,
            current_collection="La bibliotechina",
            current_publisher="CDE",
            preserve_publisher=True,
        )
        self.assertEqual(pub_c, "Einaudi")
        self.assertEqual(coll_c, "")
        self.assertTrue(clear_c)

    def test_ui_bidirectional_flow(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
        from PySide6.QtWidgets import QApplication
        from archivio.config import Settings
        from archivio.ui import MainWindow

        app = QApplication.instance() or QApplication([])
        tmp_archive = tempfile.mkdtemp(prefix="archivio_test_arch_")
        settings = Settings(archive_dir=tmp_archive)
        settings.language = "it"
        settings.enable_dark_mode = False
        settings.online_enabled = False
        win = MainWindow(settings)

        bundle = EditionBundle(
            category="Libri",
            work_id="work1",
            title="Il nome della rosa",
            author="Umberto Eco",
            author_sort="Eco, Umberto",
            default_publisher="Bompiani",
            default_year="1980",
            publishers=["Bompiani", "Mondadori", "Einaudi"],
            years=["1980", "1984", "2014"],
            collections=["I grandi tascabili", "Oscar Mondadori", "Supercoralli"],
            publisher_years={
                "Bompiani": "1980",
                "Mondadori": "1984",
                "Einaudi": "2014",
            },
            collection_meta={
                "I grandi tascabili": {"publisher": "Bompiani", "year": "1980"},
                "Oscar Mondadori": {"publisher": "Mondadori", "year": "1984"},
                "Supercoralli": {"publisher": "Einaudi", "year": "2014"},
            },
        )
        try:
            # --- Direzione 1: Cambio Editore da stato auto-compilato -> aggiorna Anno e Collana ---
            win.current_category = "Libri"
            win.in_title.setText("Il nome della rosa")
            win.in_author.setText("Umberto Eco")
            win._edition_req_id = 1
            win._on_editions_fetched(1, bundle)

            self.assertEqual(win.in_pub.currentText().strip(), "Bompiani")
            self.assertEqual(win.in_year.currentText().strip(), "1980")

            win.in_coll.set_choices(["I grandi tascabili"], "I grandi tascabili")
            self.assertEqual(win.in_coll.currentText().strip(), "I grandi tascabili")

            # 1a. Utente cambia Editore in 'Mondadori' -> Anno 1984, Collana Oscar Mondadori
            win._apply_publisher_changed("Mondadori")
            self.assertEqual(win.in_year.currentText().strip(), "1984")
            self.assertEqual(win.in_coll.currentText().strip(), "Oscar Mondadori")

            # 1b. Utente passa ad 'Adelphi' (senza collane) -> Oscar Mondadori viene rimossa
            win._apply_publisher_changed("Adelphi")
            self.assertEqual(win.in_coll.currentText().strip(), "")

            # 1c. Utente passa a 'Einaudi' -> Anno 2014 e Collana Supercoralli (anno non bloccato dai cambi precedenti)
            win._apply_publisher_changed("Einaudi")
            self.assertEqual(win.in_year.currentText().strip(), "2014")
            self.assertEqual(win.in_coll.currentText().strip(), "Supercoralli")

            # --- Direzione 2: Cambio Anno da stato auto-compilato -> aggiorna Editore e Collana ---
            win.reset_entry_form()
            win.in_title.setText("Il nome della rosa")
            win.in_author.setText("Umberto Eco")
            win._edition_req_id = 2
            win._on_editions_fetched(2, bundle)

            win._apply_year_changed("1984")
            self.assertEqual(win.in_pub.currentText().strip(), "Mondadori")
            self.assertEqual(win.in_coll.currentText().strip(), "Oscar Mondadori")

            win._apply_year_changed("2014")
            self.assertEqual(win.in_pub.currentText().strip(), "Einaudi")
            self.assertEqual(win.in_coll.currentText().strip(), "Supercoralli")
        finally:
            win.remote_scanner.stop()
            win.close()
            shutil.rmtree(tmp_archive, ignore_errors=True)

    def test_ui_cde_1986_and_suggestion_preservation(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
        from PySide6.QtWidgets import QApplication
        from archivio.config import Settings
        from archivio.ui import MainWindow

        app = QApplication.instance() or QApplication([])
        tmp_archive = tempfile.mkdtemp(prefix="archivio_test_arch_")
        settings = Settings(archive_dir=tmp_archive)
        settings.language = "it"
        settings.enable_dark_mode = False
        settings.online_enabled = False
        win = MainWindow(settings)

        bundle_with_cde = EditionBundle(
            category="Libri",
            work_id="fiabe_cde",
            title="Fiabe italiane",
            author="Italo Calvino",
            author_sort="Calvino, Italo",
            default_publisher="Einaudi",
            default_year="2012",
            publishers=["Einaudi", "Mondadori", "CDE"],
            years=["1976", "1986", "2011", "2012"],
            collections=["Letture per la scuola media", "La bibliotechina"],
            publisher_all_years={
                "Einaudi": ["1976", "1986", "2012"],
                "Mondadori": ["2011", "2012"],
                "CDE": ["2012"],
            },
            year_all_publishers={
                "1976": ["Einaudi"],
                "1986": ["Einaudi"],
                "2011": ["Mondadori"],
                "2012": ["Einaudi", "Mondadori", "CDE"],
            },
            collection_meta={
                "Letture per la scuola media": {"publisher": "Einaudi", "year": "1986"},
                "La bibliotechina": {"publisher": "Einaudi", "year": "2012"},
            },
        )
        bundle_without_cde = EditionBundle(
            category="Libri",
            work_id="fiabe_nocde",
            title="Fiabe italiane",
            author="Italo Calvino",
            author_sort="Calvino, Italo",
            default_publisher="Einaudi",
            default_year="2012",
            publishers=["Einaudi", "Mondadori"],
            years=["1976", "1986", "2011", "2012"],
            collections=["Letture per la scuola media", "La bibliotechina"],
            publisher_all_years={
                "Einaudi": ["1976", "1986", "2012"],
                "Mondadori": ["2011", "2012"],
            },
            year_all_publishers={
                "1976": ["Einaudi"],
                "1986": ["Einaudi"],
                "2011": ["Mondadori"],
                "2012": ["Einaudi", "Mondadori"],
            },
            collection_meta={
                "Letture per la scuola media": {"publisher": "Einaudi", "year": "1986"},
                "La bibliotechina": {"publisher": "Einaudi", "year": "2012"},
            },
        )
        try:
            # A) Ordine 1: prima Editore 'CDE' (presente in bundle con 2012), poi Anno '1986'
            win.current_category = "Libri"
            win.in_title.setText("Fiabe italiane")
            win.in_author.setText("Italo Calvino")
            win._edition_req_id = 1
            win._on_editions_fetched(1, bundle_with_cde)

            win.in_pub.setEditText("CDE")
            win._on_pub_edited("CDE")
            self.assertEqual(win.in_year.currentText().strip(), "2012")
            win.in_year.setEditText("1986")
            win._on_year_edited("1986")
            self.assertEqual(win.in_pub.currentText().strip(), "CDE")
            self.assertEqual(win.in_year.currentText().strip(), "1986")
            self.assertEqual(win.in_coll.currentText().strip(), "")

            # B) Ordine 2: prima Anno '1986' (-> Einaudi + Letture per la scuola media), poi Editore 'CDE'
            #    Sia con CDE nel bundle (2012) sia con CDE non in elenco, Anno resta 1986 e collana Einaudi viene tolta!
            for idx, bnd in enumerate((bundle_with_cde, bundle_without_cde), start=2):
                win.reset_entry_form()
                win.in_title.setText("Fiabe italiane")
                win.in_author.setText("Italo Calvino")
                win._edition_req_id = idx
                win._on_editions_fetched(idx, bnd)

                win.in_year.setEditText("1986")
                win._on_year_edited("1986")
                self.assertEqual(win.in_pub.currentText().strip(), "Einaudi")
                self.assertEqual(win.in_coll.currentText().strip(), "Letture per la scuola media")

                win.in_pub.setEditText("CDE")
                win._on_pub_edited("CDE")
                self.assertEqual(win.in_pub.currentText().strip(), "CDE")
                self.assertEqual(win.in_year.currentText().strip(), "1986")
                self.assertEqual(win.in_coll.currentText().strip(), "")

                # C) FocusOut / editingFinished prima di Aggiungi non altera né Editore né Anno
                if win.in_pub.lineEdit():
                    win.in_pub.lineEdit().editingFinished.emit()
                if win.in_year.lineEdit():
                    win.in_year.lineEdit().editingFinished.emit()
                self.assertEqual(win.in_pub.currentText().strip(), "CDE")
                self.assertEqual(win.in_year.currentText().strip(), "1986")

            # Aggiungi salva esattamente CDE / 1986 / ""
            win.add_item_from_form()
            saved = win.data_by_cat["Libri"][-1]
            self.assertEqual(saved.publisher, "CDE")
            self.assertEqual(saved.year, "1986")
            self.assertEqual(saved.collection, "")

            # D) Multi-anno stesso editore: se l'utente ha scelto 1986 e poi sceglie Einaudi, resta 1986!
            win.reset_entry_form()
            win.in_title.setText("Fiabe italiane")
            win.in_author.setText("Italo Calvino")
            win._edition_req_id = 10
            win._on_editions_fetched(10, bundle_without_cde)
            win.in_year.setEditText("1986")
            win._on_year_edited("1986")
            win._apply_publisher_changed("Einaudi")
            self.assertEqual(win.in_pub.currentText().strip(), "Einaudi")
            self.assertEqual(win.in_year.currentText().strip(), "1986")
            self.assertEqual(win.in_coll.currentText().strip(), "Letture per la scuola media")

            # E) Risposta asincrona tardiva _on_editions_fetched dopo _apply_suggestion non sovrascrive Editore/Anno già valorizzati
            from unittest.mock import patch

            win.reset_entry_form()
            sug = Suggestion(
                title="Fiabe italiane",
                author="Italo Calvino",
                publisher="Gallimard",
                year="1995",
                category="Libri",
                source="online",
            )
            with patch.object(win, "_start_edition_fetch"):
                win._apply_suggestion(sug)
            win._on_editions_fetched(win._edition_req_id, bundle_without_cde)
            self.assertEqual(win.in_pub.currentText().strip(), "Gallimard")
            self.assertEqual(win.in_year.currentText().strip(), "1995")
        finally:
            for cat in win.dirty_by_cat:
                win.dirty_by_cat[cat] = False
            if hasattr(win, "_local_pool"):
                win._local_pool.clear()
            win.remote_scanner.stop()
            win.close()
            shutil.rmtree(tmp_archive, ignore_errors=True)

    def test_edit_dialog_preserves_year_on_publisher_change(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
        from PySide6.QtWidgets import QApplication
        from archivio.store import Item
        from archivio.ui import EditItemDialog

        app = QApplication.instance() or QApplication([])
        it = Item(
            title="Fiabe italiane",
            author="Italo Calvino",
            publisher="Einaudi",
            year="1986",
            collection="Letture per la scuola media",
            category="Libri",
        )
        dlg = EditItemDialog(
            it,
            "Libri",
            "it",
            self.catalog,
            ["Letture per la scuola media"],
            None,
            online_enabled=False,
        )
        try:
            bundle = EditionBundle(
                category="Libri",
                work_id="fiabe_cde",
                title="Fiabe italiane",
                author="Italo Calvino",
                author_sort="Calvino, Italo",
                default_publisher="Einaudi",
                default_year="2012",
                publishers=["Einaudi", "CDE"],
                years=["1986", "2012"],
                collections=["Letture per la scuola media", "La bibliotechina"],
                publisher_all_years={"Einaudi": ["1986", "2012"], "CDE": ["2012"]},
                collection_meta={
                    "Letture per la scuola media": {"publisher": "Einaudi", "year": "1986"},
                    "La bibliotechina": {"publisher": "Einaudi", "year": "2012"},
                },
            )
            dlg._edition_req_id = 1
            dlg._on_dlg_editions_fetched(1, bundle)
            idx_cde = dlg.cb_pub.findText("CDE")
            self.assertGreaterEqual(idx_cde, 0)
            dlg.cb_pub.setCurrentIndex(idx_cde)
            dlg._on_dlg_pub_activated(idx_cde)
            self.assertEqual(dlg.cb_year.currentText().strip(), "1986")
            self.assertEqual(dlg.cb_coll.currentText().strip(), "")
        finally:
            dlg.reject()
            dlg.close()

    def test_single_edition_behavior(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
        from PySide6.QtWidgets import QApplication
        from archivio.config import Settings
        from archivio.ui import MainWindow

        app = QApplication.instance() or QApplication([])
        tmp_archive = tempfile.mkdtemp(prefix="archivio_test_arch_")
        settings = Settings(archive_dir=tmp_archive)
        settings.language = "it"
        settings.enable_dark_mode = False
        settings.online_enabled = False
        win = MainWindow(settings)

        single_bundle = EditionBundle(
            category="Libri",
            work_id="single1",
            title="Libro Raro a Edizione Unica",
            author="Autore Sconosciuto",
            author_sort="Autore, Sconosciuto",
            default_publisher="Edizioni di Pregio",
            default_year="2005",
            publishers=["Edizioni di Pregio"],
            years=["2005"],
            collections=[],
            publisher_years={"Edizioni di Pregio": "2005"},
            collection_meta={},
        )
        win.current_category = "Libri"
        win.in_title.setText("Libro Raro a Edizione Unica")
        win.in_author.setText("Autore Sconosciuto")
        win._edition_req_id = 2
        win._on_editions_fetched(2, single_bundle)

        # Il combobox editore deve contenere SOLO l'unico editore reale
        self.assertEqual(win.in_pub.count(), 1)
        self.assertEqual(win.in_pub.itemText(0), "Edizioni di Pregio")
        self.assertEqual(win.in_pub.currentText().strip(), "Edizioni di Pregio")

        # Il badge deve indicare chiaramente 'Edizione unica'
        self.assertEqual(win.lbl_edition_badge.text(), "Edizione unica")
        self.assertFalse(win.lbl_edition_badge.isHidden())

        # Il menu a tendina non deve aprirsi perché c'è una sola opzione
        win.in_pub.showPopup()
        self.assertFalse(win.in_pub.view().isVisible() if win.in_pub.view() else False)
        win.remote_scanner.stop()
        win.close()
        shutil.rmtree(tmp_archive, ignore_errors=True)

    def test_remote_scanner_edition_choices(self):
        from dataclasses import asdict
        from archivio.remote_scanner import RemoteScannerService
        from archivio.store import Item

        service = RemoteScannerService(
            catalog=self.catalog,
            get_state_fn=lambda: ("Libri", "it", {"Libri": []}),
        )

        single_bundle = EditionBundle(
            category="Libri",
            work_id="single_mobile",
            title="Saggio a Edizione Unica",
            author="Autore Speciale",
            author_sort="Autore, Speciale",
            default_publisher="Piccolo Editore Speciale",
            default_year="1999",
            publishers=["Piccolo Editore Speciale"],
            years=["1999"],
            collections=["Collana Unica"],
            publisher_years={"Piccolo Editore Speciale": "1999"},
            collection_meta={"Collana Unica": {"publisher": "Piccolo Editore Speciale", "year": "1999"}},
        )

        self.catalog.cache.set(
            "editions:v10:Libri:single_mobile",
            asdict(single_bundle),
        )

        res = service._build_editions_payload(
            category="Libri",
            work_id="single_mobile",
            title="Saggio a Edizione Unica",
            author="Autore Speciale",
            cur_pub="Piccolo Editore Speciale",
            cur_year="1999",
            cur_coll="Collana Unica",
            lang="it",
            fetch_remote=False,
        )

        # 1. Verifica che is_single_edition sia True per i client mobile
        self.assertTrue(res["is_single_edition"])

        # 2. Verifica che la lista editori contenga SOLO l'editore reale e NON inietti 20 editori fake
        self.assertEqual(res["publishers"], ["Piccolo Editore Speciale"])

        # 3. Verifica popolamento bidirezionale per client mobile
        self.assertEqual(res["year_publishers"].get("1999"), "Piccolo Editore Speciale")
        self.assertEqual(res["year_collections"].get("1999"), "Collana Unica")
        self.assertEqual(res["publisher_collections"].get("Piccolo Editore Speciale"), "Collana Unica")
        self.assertEqual(res["publisher_years"].get("Piccolo Editore Speciale"), "1999")
        self.assertEqual(res["publisher_all_years"].get("Piccolo Editore Speciale"), ["1999"])
        self.assertEqual(res["year_all_publishers"].get("1999"), ["Piccolo Editore Speciale"])
        self.assertEqual(res["pair_collections"].get("piccolo editore speciale|1999"), ["Collana Unica"])

    def test_abebooks_publisher_parser_and_city_filtering(self):
        self.assertEqual(_parse_abebooks_publisher_field(", Milano : Mondadori, 1971", "it"), ("Mondadori", "1971", ""))
        self.assertEqual(
            _parse_abebooks_publisher_field("Torino, Giulio Einaudi, Editore 1943., 1943", "it"),
            ("Einaudi", "1943", ""),
        )
        self.assertEqual(_parse_abebooks_publisher_field("CDE, Milano, 1984", "it"), ("CDE", "1984", ""))
        self.assertEqual(
            _parse_abebooks_publisher_field("MONDADORI 1976, Xxiii Ed. . Collana: Lo Specchio, 1976", "it"),
            ("Mondadori", "1976", "Lo Specchio"),
        )
        self.assertEqual(_parse_abebooks_publisher_field("Milano", "it"), ("", "", ""))
        self.assertEqual(_parse_abebooks_publisher_field("????, 2022", "it"), ("", "2022", ""))
        self.assertEqual(_parse_abebooks_publisher_field("Audible Studios, 2021", "it"), ("", "", ""))

    def test_fetch_book_editions_resilient_when_sbn_and_google_fail(self):
        import json
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PersistentCache(db_file=os.path.join(tmpdir, "ed_test_cache.sqlite"))
            svc = CatalogService(cache=cache)

            def _fake_get_json(url, **_kwargs):
                if "opac.sbn.it" in url:
                    return {"error": {"code": 0, "msg": "Solr error"}}
                if "googleapis.com" in url:
                    return None
                if "openlibrary.org/search.json" in url:
                    return {
                        "docs": [
                            {
                                "key": "/works/OL610100W",
                                "title": "Ossi di seppia",
                                "author_name": ["Eugenio Montale"],
                                "publisher": ["Mondadori", "Fratelli Ribet ed."],
                                "publish_year": [1928, 1948, 1991],
                                "first_publish_year": 1925,
                                "edition_count": 4,
                            }
                        ]
                    }
                if "/works/OL610100W/editions.json" in url:
                    return {
                        "size": 2,
                        "entries": [
                            {
                                "publishers": ["Arnoldo Mondadori"],
                                "publish_date": "1987",
                                "languages": [{"key": "/languages/ita"}],
                                "series": ["Lo specchio"],
                            },
                            {
                                "publishers": ["Arnoldo Mondadori"],
                                "publish_date": "1991",
                                "languages": [{"key": "/languages/ita"}],
                                "series": ["Nuovo specchio"],
                            },
                        ],
                    }
                if "archive.org/advancedsearch.php" in url:
                    return {
                        "response": {
                            "docs": [
                                {
                                    "title": "Ossi di seppia",
                                    "creator": "Eugenio Montale",
                                    "publisher": "Milano : Arnoldo Mondadori Editore",
                                    "year": 1948,
                                }
                            ]
                        }
                    }
                return None

            abe_html = (
                '<div>'
                '<span data-test-id="listing-title">Ossi di seppia</h2>'
                '<a data-test-id="listing-author-link">Eugenio Montale</a>'
                '<p data-test-id="language-0">Lingua: Italiano</p>'
                '<p data-test-id="publisher-0">Editore: Torino, Giulio Einaudi, Editore 1943., 1943</p>'
                '<span data-test-id="listing-title">Ossi di seppia (analisi del libro)</h2>'
                '<a data-test-id="listing-author-link">Tommaso Rossi</a>'
                '<p data-test-id="language-1">Lingua: Italiano</p>'
                '<p data-test-id="publisher-1">Editore: Lecturae.it, 2025</p>'
                '</div>'
            ).encode("utf-8")

            ibs_dl = json.dumps(
                {
                    "ecommerce": {
                        "impression": [
                            [
                                {
                                    "item_name": "Ossi di seppia",
                                    "item_author": "Eugenio Montale",
                                    "item_brand": "Mondadori",
                                    "year_edition": "2024",
                                    "item_series": "Oscar moderni",
                                    "item_category": "Libri",
                                },
                                {
                                    "item_name": "Ossi di seppia",
                                    "item_author": "Eugenio Montale",
                                    "item_brand": "Edizioni di Storia e Letteratura",
                                    "year_edition": "2015",
                                    "item_series": "Edizioni Gobettiane",
                                    "item_category": "Libri",
                                },
                                {
                                    "item_name": "Guida alla letteratura di Montale: ossi di seppia",
                                    "item_author": "Tiziana Arvigo",
                                    "item_brand": "Carocci",
                                    "year_edition": "2001",
                                    "item_series": "Piccola biblioteca letteraria",
                                    "item_category": "Libri",
                                },
                            ]
                        ]
                    }
                }
            )
            ibs_html = f"<script>dataLayer.push({ibs_dl})</script>".encode("utf-8")

            class _FakeResp:
                def __init__(self, payload: bytes):
                    self._payload = payload

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

                def read(self) -> bytes:
                    return self._payload

            def _fake_urlopen(req, **_kwargs):
                u = req.full_url if hasattr(req, "full_url") else str(req)
                if "abebooks" in u:
                    return _FakeResp(abe_html)
                if "ibs.it" in u:
                    return _FakeResp(ibs_html)
                raise OSError("unexpected url")

            try:
                with patch.object(svc, "_http_get_json", side_effect=_fake_get_json), patch(
                    "urllib.request.urlopen", side_effect=_fake_urlopen
                ):
                    bundle = svc.fetch_editions("Libri", "", "Ossi di seppia", "Eugenio Montale", "it")
                for expected_pub in ("Mondadori", "Einaudi", "Edizioni di Storia e Letteratura", "Fratelli Ribet"):
                    self.assertIn(expected_pub, bundle.publishers)
                self.assertNotIn("Carocci", bundle.publishers)
                self.assertNotIn("Lecturae.it", bundle.publishers)
                for expected_yr in ("1928", "1943", "1948", "2015", "2024"):
                    self.assertIn(expected_yr, bundle.years)
                self.assertIn("Lo Specchio", bundle.collections)
                self.assertIn("Oscar moderni", bundle.collections)
                self.assertIn("Edizioni Gobettiane", bundle.collections)
            finally:
                svc.close()

if __name__ == "__main__":
    unittest.main()
