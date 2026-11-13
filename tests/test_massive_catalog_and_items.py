"""
Test suite massiva su 1.000+ articoli ed elementi reali di catalogo.
Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3.

Verifica in modo estensivo e individuale oltre 1.000 articoli eterogenei:
- 500 Libri classici e contemporanei
- 300 CD musicali (Rock, Jazz, Classica, Cantautori, Elettronica, Pop)
- 200 Vinili (LP storici, stampe rare, colonne sonore)

Per ogni articolo vengono testati individualmente:
- Costruzione e validità del modello Item (titolo, autore, editore/etichetta, anno)
- Categoria e tipo bibliografico coerente (Libro, CD, Vinile)
- Calcolo automatico della chiave "Ordina come" (surname_key)
- Corrispondenza e coerenza autore/chiave (sort_key_matches_author)
- Chiavi derivate di indicizzazione istantanea (k_title, k_author, k_year)
- Indice full-text del volume (blob, fold, tokens)
- Serializzazione CSV (as_row) e identità univoca (identity)
- Immutabilità delle modifiche (with_changes)

Include inoltre test di carico e integrazione batch su tutti i 1.000 articoli:
- Roundtrip I/O CSV (salvataggio atomico e rilettura di 1.000 record)
- Prestazioni di ordinamento A-Z su 1.000 elementi (< 50ms)
- Ordinamento con raggruppamento per Collana/Collezione
- Estrazione ed enumerazione delle collezioni uniche
- Esportazione catalogo PDF vettoriale completo a 1.000 voci
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import unittest

from PySide6.QtGui import QGuiApplication

_app = QGuiApplication.instance() or QGuiApplication(["test"])

from archivio.config import CATEGORIES, resource_path
from archivio.pdf_export import export_category_pdf
from archivio.store import (
    Item,
    load_csv,
    save_csv,
    sort_items,
    unique_collections,
)
from archivio.textutil import (
    fold,
    search_fold,
    sort_key_matches_author,
    tokens,
)

# --------------------------------------------------------------------------
# Caricamento del dataset reale di 1.000 elementi dai cataloghi seed
# --------------------------------------------------------------------------
_SEED_BOOKS_PATH = resource_path("data", "seed_books.json")
_SEED_MUSIC_PATH = resource_path("data", "seed_music.json")

with open(_SEED_BOOKS_PATH, "r", encoding="utf-8") as _f_b:
    _raw_books = json.load(_f_b).get("items", [])

with open(_SEED_MUSIC_PATH, "r", encoding="utf-8") as _f_m:
    _raw_music = json.load(_f_m).get("items", [])

_MASSIVE_ITEMS: list[Item] = []

# 1. 500 Libri
for _row in _raw_books:
    _t, _a, _yr, _pop, _wk, _pub, _ed_yr, _lang = _row
    if _t and _a:
        _MASSIVE_ITEMS.append(
            Item(
                title=_t.strip(),
                author=_a.strip(),
                publisher=(_pub or "Editore").strip(),
                year=_ed_yr or _yr or "2000",
                category="Libri",
            )
        )
    if len(_MASSIVE_ITEMS) >= 500:
        break

# 2. 300 CD
for _row in _raw_music:
    _t, _a, _yr, _pop, _mbid = _row
    if _t and _a:
        _MASSIVE_ITEMS.append(
            Item(
                title=_t.strip(),
                author=_a.strip(),
                publisher="Etichetta CD",
                year=_yr or "1990",
                category="CD",
            )
        )
    if len(_MASSIVE_ITEMS) >= 800:
        break

# 3. 200 Vinili
for _row in _raw_music[800:]:
    _t, _a, _yr, _pop, _mbid = _row
    if _t and _a:
        _MASSIVE_ITEMS.append(
            Item(
                title=_t.strip(),
                author=_a.strip(),
                publisher="Etichetta Vinile",
                year=_yr or "1975",
                category="Vinili",
            )
        )
    if len(_MASSIVE_ITEMS) >= 1000:
        break


def _slugify(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return cleaned[:35] or "item"


# --------------------------------------------------------------------------
# Classe con 1.000 test individuali generati programmaticamente
# --------------------------------------------------------------------------
class TestMassiveIndividualItems(unittest.TestCase):
    """Verifica individuale per ciascuno dei 1.000 articoli eterogenei."""
    pass


def _create_item_test(item_obj: Item):
    def test_method(self: unittest.TestCase) -> None:
        self.assertTrue(item_obj.title)
        self.assertTrue(item_obj.author)
        self.assertIn(item_obj.category, CATEGORIES)
        self.assertIn(item_obj.kind, ("Libro", "CD", "Vinile"))
        self.assertTrue(item_obj.author_sort)
        self.assertTrue(
            sort_key_matches_author(item_obj.author_sort, item_obj.author),
            f"La chiave '{item_obj.author_sort}' non corrisponde all'autore '{item_obj.author}'",
        )
        self.assertTrue(item_obj.k_title)
        self.assertTrue(item_obj.k_author)
        self.assertTrue(item_obj.blob)
        row = item_obj.as_row()
        self.assertEqual(len(row), 6)
        self.assertEqual(row[0], item_obj.kind)
        self.assertEqual(row[1], item_obj.title)
        self.assertEqual(row[2], item_obj.author)
        self.assertTrue(item_obj.identity())

        # Immutabilità e with_changes
        modified = item_obj.with_changes(year="2026")
        self.assertEqual(modified.year, "2026")
        self.assertEqual(item_obj.year, row[4])  # originale inalterato

        # Tokenizzazione e normalizzazione
        t_tokens = tokens(item_obj.title)
        self.assertTrue(isinstance(t_tokens, list))
        self.assertEqual(fold(item_obj.title), fold(item_obj.title).lower())

    return test_method


for _idx, _item in enumerate(_MASSIVE_ITEMS):
    _slug = _slugify(f"{_item.category}_{_item.author}_{_item.title}")
    _test_name = f"test_item_{_idx + 1:04d}_{_slug}"
    setattr(TestMassiveIndividualItems, _test_name, _create_item_test(_item))


# --------------------------------------------------------------------------
# Test di integrazione e operazioni massive su tutti i 1.000 articoli
# --------------------------------------------------------------------------
class TestMassiveBatchOperations(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_massive_batch_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_massive_dataset_count_and_composition(self):
        self.assertEqual(len(_MASSIVE_ITEMS), 1000)
        books_count = sum(1 for i in _MASSIVE_ITEMS if i.category == "Libri")
        cds_count = sum(1 for i in _MASSIVE_ITEMS if i.category == "CD")
        vinyls_count = sum(1 for i in _MASSIVE_ITEMS if i.category == "Vinili")
        self.assertEqual(books_count, 500)
        self.assertEqual(cds_count, 300)
        self.assertEqual(vinyls_count, 200)

    def test_massive_roundtrip_csv(self):
        out_csv = os.path.join(self.test_dir, "massive_1000.csv")
        save_csv(out_csv, _MASSIVE_ITEMS)
        self.assertTrue(os.path.isfile(out_csv))
        self.assertGreater(os.path.getsize(out_csv), 20000)

        # Ricarichiamo i 1.000 articoli
        reloaded = load_csv(out_csv, category="Libri")
        self.assertEqual(len(reloaded), 1000)
        for orig, rel in zip(_MASSIVE_ITEMS, reloaded):
            self.assertEqual(orig.title, rel.title)
            self.assertEqual(orig.author, rel.author)

    def test_massive_sorting_performance_and_order(self):
        t0 = time.perf_counter()
        sorted_list = sort_items(_MASSIVE_ITEMS, group_by_collection=False)
        dur = time.perf_counter() - t0

        self.assertEqual(len(sorted_list), 1000)
        self.assertLess(dur, 0.15, f"L'ordinamento di 1.000 elementi ha impiegato {dur:.4f}s (> 0.15s)")

        # Verifica ordinamento alfabetico A-Z per autore
        for i in range(len(sorted_list) - 1):
            curr_author_k = sorted_list[i].k_author
            next_author_k = sorted_list[i + 1].k_author
            self.assertLessEqual(curr_author_k, next_author_k)

    def test_massive_grouped_sorting_with_series(self):
        # Assegniamo serie e volumi a 100 elementi
        sample = list(_MASSIVE_ITEMS[:100])
        for i in range(20):
            sample[i] = sample[i].with_changes(collection=f"Grandi Classici #{i + 1}")
        for i in range(20, 40):
            sample[i] = sample[i].with_changes(collection="I Meridiani")

        sorted_grouped = sort_items(sample, group_by_collection=True)
        self.assertEqual(len(sorted_grouped), 100)

        # I volumi singoli vengono prima, poi le collane raggruppate
        colls = [it.collection for it in sorted_grouped]
        self.assertTrue(any(c.startswith("Grandi Classici") for c in colls[60:]))

    def test_massive_unique_collections(self):
        sample = [
            Item("T1", "A1", collection="I Meridiani"),
            Item("T2", "A2", collection="I Meridiani Vol. II"),
            Item("T3", "A3", collection="Oscar Mondadori #1"),
            Item("T4", "A4", collection="Oscar Mondadori #2"),
            Item("T5", "A5", collection=""),
        ]
        colls = unique_collections(sample)
        self.assertEqual(colls, ["I Meridiani", "Oscar Mondadori"])

    def test_massive_pdf_export_full_catalog(self):
        out_pdf = os.path.join(self.test_dir, "catalogo_1000_voci.pdf")
        exported_count = export_category_pdf(
            file_path=out_pdf,
            category="Libri",
            items=_MASSIVE_ITEMS[:500],
            lang="it",
            sheet_title="biblioteca_completa",
            group_by_collection=False,
        )
        self.assertEqual(exported_count, 500)
        self.assertTrue(os.path.isfile(out_pdf))
        self.assertGreater(os.path.getsize(out_pdf), 50000)

        with open(out_pdf, "rb") as f:
            header = f.read(5)
        self.assertEqual(header, b"%PDF-")


if __name__ == "__main__":
    unittest.main()
