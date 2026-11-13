"""
Test di verifica per l'evidenziazione e il tracciamento della posizione
dell'ultimo inserimento nella tabella principale.
"""
from __future__ import annotations

import unittest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from archivio.config import Settings
from archivio.store import Item
from archivio.ui import LAST_INSERTED_ROLE, MainWindow

app = QApplication.instance() or QApplication([])


class TestLastInsertedHighlight(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(language="it")
        self.win = MainWindow(settings=self.settings)

    def tearDown(self):
        for cat in self.win.dirty_by_cat:
            self.win.dirty_by_cat[cat] = False
        if hasattr(self.win, "remote_scanner"):
            self.win.remote_scanner.stop()
        if hasattr(self.win, "_local_pool"):
            self.win._local_pool.clear()
        self.win.close()

    def test_add_from_form_reveals_and_highlights(self):
        initial_items = [
            Item(title="Il barone rampante", author="Italo Calvino"),
            Item(title="Se una notte d'inverno un viaggiatore", author="Italo Calvino"),
            Item(title="La coscienza di Zeno", author="Italo Svevo"),
        ]
        self.win.data_by_cat["Libri"] = list(initial_items)
        self.win.table_model.set_category_and_items("Libri", initial_items, "it")
        self.assertIsNone(self.win.table_model.last_inserted_item)

        self.win.in_title.setText("Il nome della rosa")
        self.win.in_author.setText("Umberto Eco")
        self.win.in_pub.setCurrentText("Bompiani")
        self.win.in_year.setCurrentText("1980")

        self.win.add_item_from_form()

        # Inserimento riuscito
        self.assertEqual(len(self.win.data_by_cat["Libri"]), 4)
        inserted = self.win.data_by_cat["Libri"][-1]
        self.assertEqual(inserted.title, "Il nome della rosa")

        # Elemento tracciato come ultimo inserimento
        self.assertIs(self.win.table_model.last_inserted_item, inserted)
        self.assertIs(self.win.last_inserted_by_cat["Libri"], inserted)

        # Coordinate proxy
        src_row = self.win.table_model.items.index(inserted)
        src_idx = self.win.table_model.index(src_row, 0)
        proxy_idx = self.win.proxy_model.mapFromSource(src_idx)
        proxy_row = proxy_idx.row()

        # Selezione attiva sulla riga inserita
        selected_rows = [i.row() for i in self.win.table.selectionModel().selectedRows()]
        self.assertIn(proxy_row, selected_rows)

        # Ruolo LAST_INSERTED_ROLE vero solo sulla riga inserita
        self.assertTrue(proxy_idx.data(LAST_INSERTED_ROLE))
        for r in range(self.win.proxy_model.rowCount()):
            if r != proxy_row:
                self.assertFalse(self.win.proxy_model.index(r, 0).data(LAST_INSERTED_ROLE))

        # Colore sfondo caldo ambra (#FEF3D6)
        bg = proxy_idx.data(Qt.ItemDataRole.BackgroundRole)
        self.assertEqual(bg.name().lower(), "#fef3d6")

        # Tooltip contiene la dicitura dell'ultimo inserimento
        tip = proxy_idx.data(Qt.ItemDataRole.ToolTipRole)
        self.assertIn("Ultimo inserimento", tip)

        # Messaggio nella status bar con posizione e totale
        status_msg = self.win.statusBar().currentMessage()
        self.assertIn(f"posizione {proxy_row + 1} di 4", status_msg)

    def test_remote_scan_reveals_and_highlights(self):
        new_it = Item(title="La luna e i falò", author="Cesare Pavese", category="Libri")
        self.win._on_remote_item_added("Libri", new_it)

        inserted = self.win.data_by_cat["Libri"][-1]
        self.assertIs(self.win.table_model.last_inserted_item, inserted)
        status_msg = self.win.statusBar().currentMessage()
        self.assertIn("posizione 1 di 1", status_msg)

    def test_duplicate_item_reveals_and_highlights(self):
        initial_items = [Item(title="Originale", author="Autore Test")]
        self.win.data_by_cat["Libri"] = list(initial_items)
        self.win.table_model.set_category_and_items("Libri", initial_items, "it")
        self.win.table.selectRow(0)

        self.win.duplicate_selected_item()
        self.assertEqual(len(self.win.data_by_cat["Libri"]), 2)
        duplicated = self.win.data_by_cat["Libri"][-1]
        self.assertIs(self.win.table_model.last_inserted_item, duplicated)
        status_msg = self.win.statusBar().currentMessage()
        self.assertIn("Duplicato", status_msg)

    def test_undo_and_redo_highlight(self):
        self.win.in_title.setText("Book A")
        self.win.in_author.setText("Author A")
        self.win.add_item_from_form()
        self.assertIsNotNone(self.win.table_model.last_inserted_item)

        # Undo annulla l'evidenziazione
        self.win.undo_last_action()
        self.assertIsNone(self.win.table_model.last_inserted_item)

        # Redo ripristina l'evidenziazione
        self.win.redo_last_action()
        self.assertIsNotNone(self.win.table_model.last_inserted_item)
        self.assertEqual(self.win.table_model.last_inserted_item.title, "Book A")

    def test_switch_category_preserves_per_cat_highlight(self):
        self.win.in_title.setText("Book A")
        self.win.in_author.setText("Author A")
        self.win.add_item_from_form()
        book_a = self.win.table_model.last_inserted_item

        self.win._switch_category("CD")
        self.assertIsNone(self.win.table_model.last_inserted_item)

        self.win.in_title.setText("Album CD")
        self.win.in_author.setText("Artist CD")
        self.win.add_item_from_form()
        album_cd = self.win.table_model.last_inserted_item
        self.assertIsNotNone(album_cd)

        # Torna a Libri e verifica che l'evidenziazione sia ancora sul libro
        self.win._switch_category("Libri")
        self.assertIs(self.win.table_model.last_inserted_item, book_a)

    def tearDown(self):
        for cat in ("Libri", "CD", "Vinili"):
            self.win.dirty_by_cat[cat] = False
        if hasattr(self.win, "remote_scanner"):
            self.win.remote_scanner.stop()
        if hasattr(self.win, "_local_pool"):
            self.win._local_pool.clear()
        self.win.close()


if __name__ == "__main__":
    unittest.main()
