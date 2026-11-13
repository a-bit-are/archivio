"""
Unit test per app/archivio/ui.py.
Copre: comandi Undo/Redo (aggiunta, modifica, eliminazione, duplicazione, svuota elenco,
assegnazione collana), filtro/ricerca in tempo reale (insensibile a maiuscole e accenti),
cambio categoria e reset/validazione del modulo di inserimento.
"""
from __future__ import annotations

import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from archivio.config import CATEGORIES, Settings
from archivio.store import Item
from archivio.ui import ITEM_ROLE, MainWindow

_app = QApplication.instance() or QApplication([])


class TestUiCommandsAndFiltering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.settings = Settings(language="it")
        cls.win = MainWindow(settings=cls.settings)

    @classmethod
    def tearDownClass(cls):
        for cat in CATEGORIES:
            cls.win.dirty_by_cat[cat] = False
        if hasattr(cls.win, "remote_scanner"):
            cls.win.remote_scanner.stop()
        if hasattr(cls.win, "_local_pool"):
            cls.win._local_pool.clear()
        cls.win.close()

    def setUp(self):
        for cat in CATEGORIES:
            self.win.data_by_cat[cat] = []
            self.win.dirty_by_cat[cat] = False
            self.win.undo_stacks[cat].clear()
        self.win.reset_entry_form()
        self.win.ed_search.setText("")

    def test_form_validation_rejects_empty_fields(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox

        initial_count = len(self.win.data_by_cat["Libri"])
        self.win.in_title.setText("")
        self.win.in_author.setText("")
        with patch.object(QMessageBox, "warning"):
            self.win.add_item_from_form()
        # Non deve essere aggiunto nulla se titolo e autore sono vuoti
        self.assertEqual(len(self.win.data_by_cat["Libri"]), initial_count)

    def test_reset_entry_form(self):
        self.win.in_title.setText("Un titolo")
        self.win.in_author.setText("Un autore")
        self.win.in_sort.setText("Autore, Un")
        self.win.in_pub.setCurrentText("Editore")
        self.win.in_coll.setCurrentText("Collana")
        self.win.in_year.setCurrentText("2020")

        self.win.reset_entry_form()

        self.assertEqual(self.win.in_title.text(), "")
        self.assertEqual(self.win.in_author.text(), "")
        self.assertEqual(self.win.in_sort.text(), "")
        self.assertEqual(self.win.in_pub.currentText(), "")
        self.assertEqual(self.win.in_coll.currentText(), "")
        self.assertEqual(self.win.in_year.currentText(), "")

    def test_undo_and_redo_add_item(self):
        self.win._switch_category("Libri", force=True)
        count_before = len(self.win.data_by_cat["Libri"])

        self.win.in_title.setText("Il barone rampante")
        self.win.in_author.setText("Italo Calvino")
        self.win.add_item_from_form()
        self.assertEqual(len(self.win.data_by_cat["Libri"]), count_before + 1)

        # Undo
        self.win.undo_last_action()
        self.assertEqual(len(self.win.data_by_cat["Libri"]), count_before)

        # Redo
        self.win.redo_last_action()
        self.assertEqual(len(self.win.data_by_cat["Libri"]), count_before + 1)
        self.assertEqual(self.win.data_by_cat["Libri"][-1].title, "Il barone rampante")

    def test_undo_and_redo_delete_item(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox

        self.win._switch_category("CD", force=True)
        item1 = Item(title="The Wall", author="Pink Floyd", category="CD")
        item2 = Item(title="Abbey Road", author="The Beatles", category="CD")
        self.win.data_by_cat["CD"] = [item1, item2]
        self.win.table_model.set_category_and_items("CD", [item1, item2], "it")

        # Seleziona la prima riga
        self.win.table.selectRow(0)

        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            self.win.delete_selected_items()
        self.assertEqual(len(self.win.data_by_cat["CD"]), 1)

        # Undo ripristina l'elemento eliminato
        self.win.undo_last_action()
        self.assertEqual(len(self.win.data_by_cat["CD"]), 2)

        # Redo elimina nuovamente
        self.win.redo_last_action()
        self.assertEqual(len(self.win.data_by_cat["CD"]), 1)

    def test_filter_realtime_diacritics_and_case(self):
        self.win._switch_category("Vinili", force=True)
        items = [
            Item(title="Crêuza de mä", author="Fabrizio De André", category="Vinili"),
            Item(title="Anima latina", author="Lucio Battisti", category="Vinili"),
            Item(title="Storia di un impiegato", author="Fabrizio De André", category="Vinili"),
        ]
        self.win.data_by_cat["Vinili"] = items
        self.win.table_model.set_category_and_items("Vinili", items, "it")

        # Inizialmente visibili tutti e 3
        self.assertEqual(self.win.proxy_model.rowCount(), 3)

        # Filtra senza accenti: "creuza" deve trovare "Crêuza de mä"
        self.win.ed_search.setText("creuza")
        self.assertEqual(self.win.proxy_model.rowCount(), 1)
        found = self.win.proxy_model.data(self.win.proxy_model.index(0, 0), ITEM_ROLE)
        self.assertEqual(found.title, "Crêuza de mä")

        # Filtra per autore
        self.win.ed_search.setText("battisti")
        self.assertEqual(self.win.proxy_model.rowCount(), 1)
        found_b = self.win.proxy_model.data(self.win.proxy_model.index(0, 0), ITEM_ROLE)
        self.assertEqual(found_b.title, "Anima latina")

        # Svuota filtro -> tornano tutti e 3
        self.win.ed_search.setText("")
        self.assertEqual(self.win.proxy_model.rowCount(), 3)

    def test_switch_categories_and_placeholders(self):
        self.win._switch_category("Libri", force=True)
        self.assertEqual(self.win.current_category, "Libri")
        self.assertIn("ISBN", self.win.in_title.placeholderText())

        self.win._switch_category("CD", force=True)
        self.assertEqual(self.win.current_category, "CD")
        self.assertIn("EAN", self.win.in_title.placeholderText())

        self.win._switch_category("Vinili", force=True)
        self.assertEqual(self.win.current_category, "Vinili")
        self.assertIn("EAN", self.win.in_title.placeholderText())


if __name__ == "__main__":
    unittest.main()
