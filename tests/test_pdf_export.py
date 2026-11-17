"""
Test unitari per l'esportazione PDF e la formattazione dei titoli dei fogli.
"""
import os
import sys
import tempfile
import unittest

from PySide6.QtGui import QGuiApplication

# Assicura istanza QGuiApplication per la gestione dei font Qt
_app = QGuiApplication.instance() or QGuiApplication(sys.argv)

from archivio.pdf_export import format_sheet_title, export_category_pdf
from archivio.store import Item


class TestPdfExport(unittest.TestCase):
    def test_format_sheet_title(self):
        # Conversione standard di snake_case e kebab-case
        self.assertEqual(format_sheet_title("libri_musica"), "Libri Musica")
        self.assertEqual(format_sheet_title("libri_musica.csv"), "Libri Musica")
        self.assertEqual(format_sheet_title("/path/to/vinili_jazz.csv"), "Vinili Jazz")
        self.assertEqual(format_sheet_title("cd-rock-italiano"), "CD Rock Italiano")

        # Preservazione acronimi noti
        self.assertEqual(format_sheet_title("cd_jazz"), "CD Jazz")
        self.assertEqual(format_sheet_title("lp_classica"), "LP Classica")
        self.assertEqual(format_sheet_title("ep_rarita"), "EP Rarita")

        # Fallback su default_category
        self.assertEqual(format_sheet_title("", default_category="Libri"), "Libri")
        self.assertEqual(format_sheet_title("   ", default_category="CD"), "CD")

    def test_export_category_pdf_creates_valid_file(self):
        items = [
            Item(title="Il secondo senso", author="Antonello Ricci", publisher="Franco Angeli", year="2016", category="Libri"),
            Item(title="A day in the life of the Beatles", author="Don McCullin", publisher="Rizzoli", collection="La Scala", year="2010", category="Libri"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_pdf = os.path.join(tmpdir, "test_output.pdf")
            exported_count = export_category_pdf(
                file_path=out_pdf,
                category="Libri",
                items=items,
                lang="it",
                sort_label="Ordine alfabetico per autore (A-Z)",
                group_by_collection=False,
                sheet_title="libri_musica",
            )
            self.assertEqual(exported_count, 2)
            self.assertTrue(os.path.exists(out_pdf))
            self.assertGreater(os.path.getsize(out_pdf), 1000)

            with open(out_pdf, "rb") as f:
                header = f.read(5)
            self.assertEqual(header, b"%PDF-")

    def test_export_pdf_with_group_by_collection(self):
        items = [
            Item(title="Delitto e castigo", author="Fëdor Dostoevskij", publisher="Einaudi", collection="Grandi Classici Vol. I", year="1980", category="Libri"),
            Item(title="I fratelli Karamazov", author="Fëdor Dostoevskij", publisher="Einaudi", collection="Grandi Classici Vol. II", year="1981", category="Libri"),
            Item(title="Il nome della rosa", author="Umberto Eco", publisher="Bompiani", collection="", year="1980", category="Libri"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_pdf = os.path.join(tmpdir, "test_grouped.pdf")
            count = export_category_pdf(
                file_path=out_pdf,
                category="Libri",
                items=items,
                lang="it",
                group_by_collection=True,
                sheet_title="libri_collane",
            )
            self.assertEqual(count, 3)
            self.assertTrue(os.path.exists(out_pdf))
            self.assertGreater(os.path.getsize(out_pdf), 1000)

    def test_export_pdf_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out_pdf = os.path.join(tmpdir, "test_empty.pdf")
            count = export_category_pdf(
                file_path=out_pdf,
                category="Libri",
                items=[],
                lang="it",
                sheet_title="vuoto",
            )
            self.assertEqual(count, 0)
            self.assertTrue(os.path.exists(out_pdf))

    def test_export_pdf_music_categories(self):
        cd_items = [
            Item(title="The Wall", author="Pink Floyd", publisher="Harvest", year="1979", category="CD"),
            Item(title="Kind of Blue", author="Miles Davis", publisher="Columbia", year="1959", category="CD"),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_pdf = os.path.join(tmpdir, "cd.pdf")
            count = export_category_pdf(
                file_path=out_pdf,
                category="CD",
                items=cd_items,
                lang="it",
                sheet_title="cd_classici",
            )
            self.assertEqual(count, 2)
            self.assertTrue(os.path.exists(out_pdf))

    def test_export_pdf_english_localization(self):
        items = [
            Item(
                title="1984",
                author="George Orwell",
                publisher="Penguin",
                year="1949",
                collection="Penguin Classics #1",
                category="Libri",
            )
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            out_pdf = os.path.join(tmpdir, "books_en.pdf")
            count = export_category_pdf(
                file_path=out_pdf,
                category="Libri",
                items=items,
                lang="en",
                group_by_collection=True,
                sheet_title="books_english",
            )
            self.assertEqual(count, 1)
            self.assertTrue(os.path.exists(out_pdf))


if __name__ == "__main__":
    unittest.main()
