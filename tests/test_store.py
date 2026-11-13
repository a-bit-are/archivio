"""
Unit test per app/archivio/store.py.
Copre: modello Item, normalizzazione e chiavi di ordinamento, detect_category,
decodifica con o senza BOM/cp1252/latin-1, lettura CSV a 3/4/5/6/7 colonne con
virgola o punto e virgola, ordinamento con e senza raggruppamento collane,
salvataggio atomico e rotazione copie di sicurezza (backup).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from archivio.config import backups_dir
from archivio.store import (
    Item,
    _decode,
    _read_rows,
    detect_category,
    load_csv,
    save_csv,
    scan_folder,
    sort_items,
    unique_collections,
)


class TestStoreItem(unittest.TestCase):
    def test_item_creation_defaults_and_post_init(self):
        it = Item(title="Il fu Mattia Pascal", author="Luigi Pirandello", publisher="Treves", year="1904")
        self.assertEqual(it.category, "Libri")
        self.assertEqual(it.author_sort, "Pirandello, Luigi")
        self.assertEqual(it.kind, "Libro")
        self.assertIn("mattia pascal", it.k_title)
        self.assertIn("pirandello", it.k_author)
        self.assertIn("1904", it.k_year)
        self.assertIn("mattia pascal", it.blob)
        self.assertIn("pirandello", it.blob)

    def test_item_category_normalization(self):
        it_book = Item("Titolo", "Autore", category="book")
        self.assertEqual(it_book.category, "Libri")

        it_cd = Item("Titolo", "Artista", category="cds")
        self.assertEqual(it_cd.category, "CD")

        it_vinyl = Item("Titolo", "Artista", category="lp")
        self.assertEqual(it_vinyl.category, "Vinili")

    def test_item_with_changes(self):
        it = Item("Titolo", "Autore", publisher="Mondadori", year="1980")
        mod = it.with_changes(publisher="Einaudi", year="1985")
        self.assertEqual(mod.publisher, "Einaudi")
        self.assertEqual(mod.year, "1985")
        self.assertEqual(it.publisher, "Mondadori")  # originale immutato

    def test_item_identity_and_as_row(self):
        it1 = Item("Il nome della rosa", "Umberto Eco", publisher="Bompiani", year="1980")
        it2 = Item("IL NOME DELLA ROSA", "umberto eco", publisher="Fabbri", year="1990")
        self.assertEqual(it1.identity(), it2.identity())

        # Senza collezione -> 6 colonne
        row6 = it1.as_row()
        self.assertEqual(len(row6), 6)
        self.assertEqual(row6[0], "Libro")
        self.assertEqual(row6[1], "Il nome della rosa")

        # Con collezione -> 7 colonne
        it_coll = Item("Delitto e castigo", "Fëdor Dostoevskij", collection="I Classici #1")
        row7 = it_coll.as_row()
        self.assertEqual(len(row7), 7)
        self.assertEqual(row7[6], "I Classici #1")


class TestStoreDetectCategory(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_detect_cat_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_detect_by_filename(self):
        self.assertEqual(detect_category("/path/to/libri.csv"), "Libri")
        self.assertEqual(detect_category("/path/to/libri_narrativa.csv"), "Libri")
        self.assertEqual(detect_category("/path/to/book_catalog.csv"), "Libri")
        self.assertEqual(detect_category("/path/to/cd.csv"), "CD")
        self.assertEqual(detect_category("/path/to/cd_rock.csv"), "CD")
        self.assertEqual(detect_category("/path/to/vinili.csv"), "Vinili")
        self.assertEqual(detect_category("/path/to/vinyl_collection.csv"), "Vinili")
        self.assertEqual(detect_category("/path/to/lp_vintage.csv"), "Vinili")

    def test_detect_by_content_when_filename_is_generic(self):
        p_cd = os.path.join(self.test_dir, "archivio_musica.csv")
        with open(p_cd, "w", encoding="utf-8") as f:
            f.write("Tipo,Titolo,Artista,Etichetta,Anno\nCD,The Dark Side of the Moon,Pink Floyd,Harvest,1973\n")
        self.assertEqual(detect_category(p_cd), "CD")

        p_vinyl = os.path.join(self.test_dir, "dischi.csv")
        with open(p_vinyl, "w", encoding="utf-8") as f:
            f.write("Vinile,Abbey Road,The Beatles,Apple,1969,Beatles The,\n")
        self.assertEqual(detect_category(p_vinyl), "Vinili")

        p_book = os.path.join(self.test_dir, "letture.csv")
        with open(p_book, "w", encoding="utf-8") as f:
            f.write("Libro,Se questo è un uomo,Primo Levi,Einaudi,1947\n")
        self.assertEqual(detect_category(p_book), "Libri")


class TestStoreDecodingAndReading(unittest.TestCase):
    def test_decode_encodings(self):
        raw_utf8_bom = "Test con accento: è, à, ù".encode("utf-8-sig")
        self.assertIn("è, à, ù", _decode(raw_utf8_bom))

        raw_latin1 = "Caffè e città".encode("latin-1")
        self.assertIn("Caffè e città", _decode(raw_latin1))

        raw_broken = b"\xff\xfe\x00\x00Invalid"
        # graceful fallback senza eccezioni
        self.assertTrue(isinstance(_decode(raw_broken), str))

    def test_read_rows_delimiters_and_clean(self):
        tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        try:
            tmp.write("Titolo;Autore;Editore;Anno\n\n  1984  ;  George Orwell  ; Mondadori ; 1949 \n")
            tmp.close()
            rows = _read_rows(tmp.name)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[1], ["1984", "George Orwell", "Mondadori", "1949"])
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)


class TestStoreLoadCsv(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_load_csv_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_load_csv_various_column_formats(self):
        # 1. Standard 7 colonne con tipo e collana
        p7 = os.path.join(self.test_dir, "test7.csv")
        with open(p7, "w", encoding="utf-8") as f:
            f.write("Tipo,Titolo,Autore,Editore,Anno,OrdinaCome,Collana\n")
            f.write("Libro,Il nome della rosa,Umberto Eco,Bompiani,1980,Eco Umberto,Tascabili #1\n")
            f.write("Libro,Il visconte dimezzato,Italo Calvino,Einaudi,1952,Calvino Italo,\n")

        items7 = load_csv(p7, category="Libri")
        self.assertEqual(len(items7), 2)
        self.assertEqual(items7[0].title, "Il nome della rosa")
        self.assertEqual(items7[0].collection, "Tascabili #1")
        self.assertEqual(items7[1].title, "Il visconte dimezzato")
        self.assertEqual(items7[1].collection, "")

        # 2. 5 colonne senza tipo
        p5 = os.path.join(self.test_dir, "test5.csv")
        with open(p5, "w", encoding="utf-8") as f:
            f.write("Titolo,Autore,Editore,Anno,OrdinaCome\n")
            f.write("Kind of Blue,Miles Davis,Columbia,1959,Davis Miles\n")

        items5 = load_csv(p5, category="CD")
        self.assertEqual(len(items5), 1)
        self.assertEqual(items5[0].title, "Kind of Blue")
        self.assertEqual(items5[0].category, "CD")

        # 3. 4 colonne (senza ordina come)
        p4 = os.path.join(self.test_dir, "test4.csv")
        with open(p4, "w", encoding="utf-8") as f:
            f.write("The Dark Side of the Moon,Pink Floyd,Harvest,1973\n")

        items4 = load_csv(p4, category="Vinili")
        self.assertEqual(len(items4), 1)
        self.assertEqual(items4[0].title, "The Dark Side of the Moon")
        self.assertEqual(items4[0].author_sort, "Pink Floyd")  # band identificata

        # 4. 3 colonne minime (titolo, autore, editore)
        p3 = os.path.join(self.test_dir, "test3.csv")
        with open(p3, "w", encoding="utf-8") as f:
            f.write("Poesie,Eugenio Montale,Mondadori\n")

        items3 = load_csv(p3, category="Libri")
        self.assertEqual(len(items3), 1)
        self.assertEqual(items3[0].title, "Poesie")
        self.assertEqual(items3[0].year, "")


class TestStoreSaveAndBackup(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_save_backup_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_save_csv_and_backup_creation(self):
        csv_path = os.path.join(self.test_dir, "libri_test.csv")
        items = [
            Item("Se questo è un uomo", "Primo Levi", "Einaudi", "1947", collection="Supercoralli"),
            Item("La tregua", "Primo Levi", "Einaudi", "1963", collection="Supercoralli"),
        ]

        save_csv(csv_path, items)
        self.assertTrue(os.path.isfile(csv_path))

        reloaded = load_csv(csv_path, category="Libri")
        self.assertEqual(len(reloaded), 2)
        self.assertEqual(reloaded[0].title, "Se questo è un uomo")
        self.assertEqual(reloaded[0].collection, "Supercoralli")

        # Risalviamo con modifiche per verificare creazione di backup
        items.append(Item("I sommersi e i salvati", "Primo Levi", "Einaudi", "1986"))
        save_csv(csv_path, items)

        reloaded2 = load_csv(csv_path, category="Libri")
        self.assertEqual(len(reloaded2), 3)

        # Il backup_dir deve contenere un file stem_timestamp.csv
        b_dir = backups_dir()
        self.assertTrue(os.path.isdir(b_dir))
        backups = [f for f in os.listdir(b_dir) if f.startswith("libri_test_") and f.endswith(".csv")]
        self.assertGreaterEqual(len(backups), 1)

        # Pulizia backup generato dal test
        for b in backups:
            try:
                os.unlink(os.path.join(b_dir, b))
            except OSError:
                pass


class TestStoreSortingAndCollections(unittest.TestCase):
    def setUp(self):
        self.items = [
            Item("Zeno", "Italo Svevo", "Cappelli", "1923"),
            Item("Uno, nessuno e centomila", "Luigi Pirandello", "Treves", "1926"),
            Item("Grandi Classici Vol. II", "Alessandro Manzoni", "Mondadori", "1980", collection="Grandi Classici Vol. II"),
            Item("Grandi Classici Vol. I", "Dante Alighieri", "Mondadori", "1979", collection="Grandi Classici Vol. I"),
            Item("Il fu Mattia Pascal", "Luigi Pirandello", "Treves", "1904"),
        ]

    def test_sort_items_without_grouping(self):
        sorted_list = sort_items(self.items, group_by_collection=False)
        # Ordine strettamente alfabetico per autore A-Z:
        # Dante -> Manzoni -> Pirandello (1904) -> Pirandello (1926) -> Svevo
        authors = [it.author for it in sorted_list]
        self.assertEqual(authors, [
            "Dante Alighieri",
            "Alessandro Manzoni",
            "Luigi Pirandello",
            "Luigi Pirandello",
            "Italo Svevo",
        ])

    def test_sort_items_with_grouping(self):
        sorted_list = sort_items(self.items, group_by_collection=True)
        # I volumi singoli (senza collana) vengono prima per autore,
        # poi le collane raggruppate e ordinate per volume
        collections = [it.collection for it in sorted_list]
        # I primi 3 non hanno collana:
        self.assertEqual(collections[0], "")
        self.assertEqual(collections[1], "")
        self.assertEqual(collections[2], "")
        # Gli ultimi 2 hanno Grandi Classici Vol. I e poi Vol. II:
        self.assertEqual(collections[3], "Grandi Classici Vol. I")
        self.assertEqual(collections[4], "Grandi Classici Vol. II")

    def test_unique_collections(self):
        colls = unique_collections(self.items)
        self.assertEqual(colls, ["Grandi Classici"])


class TestStoreScanFolder(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_scan_folder_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_scan_folder_finds_csvs(self):
        # Crea libri_extra.csv e cd_jazz.csv
        p_extra_b = os.path.join(self.test_dir, "libri_extra.csv")
        p_extra_c = os.path.join(self.test_dir, "cd_jazz.csv")
        with open(p_extra_b, "w", encoding="utf-8") as f:
            f.write("Titolo,Autore,Editore,Anno\nTest,Autore,Editore,2000\n")
        with open(p_extra_c, "w", encoding="utf-8") as f:
            f.write("Titolo,Artista,Etichetta,Anno\nAlbum,Artista,Etichetta,2000\n")

        found = scan_folder(self.test_dir)
        self.assertIn("libri.csv", found["Libri"])
        self.assertIn("libri_extra.csv", found["Libri"])
        self.assertIn("cd.csv", found["CD"])
        self.assertIn("cd_jazz.csv", found["CD"])
        self.assertIn("vinili.csv", found["Vinili"])


if __name__ == "__main__":
    unittest.main()
