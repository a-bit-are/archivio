import unittest
from archivio.catalog import CatalogService, EditionBundle

class TestEditionCrossResolution(unittest.TestCase):
    def setUp(self):
        self.catalog = CatalogService()

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

    def test_ui_bidirectional_flow(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
        from PySide6.QtWidgets import QApplication
        from archivio.config import Settings
        from archivio.ui import MainWindow

        app = QApplication.instance() or QApplication([])
        settings = Settings()
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
        win.current_category = "Libri"
        win.in_title.setText("Il nome della rosa")
        win.in_author.setText("Umberto Eco")
        win._edition_req_id = 1
        win._on_editions_fetched(1, bundle)

        # Inizialmente Bompiani / 1980
        self.assertEqual(win.in_pub.currentText().strip(), "Bompiani")
        self.assertEqual(win.in_year.currentText().strip(), "1980")

        # Impostiamo una collana Bompiani
        win.in_coll.set_choices(["I grandi tascabili"], "I grandi tascabili")
        self.assertEqual(win.in_coll.currentText().strip(), "I grandi tascabili")

        # 1. Utente cambia Editore in 'Mondadori'
        win._apply_publisher_changed("Mondadori")
        # -> Anno deve diventare 1984
        self.assertEqual(win.in_year.currentText().strip(), "1984")
        # -> Collana 'I grandi tascabili' di Bompiani deve essere tolta / aggiornata a Oscar Mondadori
        self.assertEqual(win.in_coll.currentText().strip(), "Oscar Mondadori")

        # 2. Utente cambia Anno in '2014'
        win._apply_year_changed("2014")
        # -> Editore deve diventare Einaudi
        self.assertEqual(win.in_pub.currentText().strip(), "Einaudi")
        # -> Collana deve diventare Supercoralli
        self.assertEqual(win.in_coll.currentText().strip(), "Supercoralli")

        # 3. Utente inserisce un editore senza collana (es. Adelphi)
        # Verifichiamo che la vecchia collana Supercoralli (Einaudi) venga tolta!
        win._apply_publisher_changed("Adelphi")
        self.assertEqual(win.in_coll.currentText().strip(), "")
        win.remote_scanner.stop()
        win.close()

    def test_single_edition_behavior(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.*=false")
        from PySide6.QtWidgets import QApplication
        from archivio.config import Settings
        from archivio.ui import MainWindow

        app = QApplication.instance() or QApplication([])
        settings = Settings()
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
            "editions:v9:Libri:single_mobile",
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

if __name__ == "__main__":
    unittest.main()
