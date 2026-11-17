"""Modello dati (Item) e lettura/scrittura sicura dei file CSV."""
from __future__ import annotations

import csv
import io
import os
import shutil
import time
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

from .config import CATEGORIES, DEFAULT_FILES, atomic_write_text, backups_dir
from .textutil import collection_sort_parts, fold, search_fold, surname_key, title_sort_key, year_of

CATEGORY_INFO = {
    "Libri": {"type": "Libro", "prefix": "libri", "default": "libri.csv", "music": False, "format": None},
    "CD": {"type": "CD", "prefix": "cd", "default": "cd.csv", "music": True, "format": "CD"},
    "Vinili": {"type": "Vinile", "prefix": "vinili", "default": "vinili.csv", "music": True, "format": "Vinyl"},
}

_TYPE_TO_CATEGORY = {
    "libro": "Libri", "libri": "Libri", "book": "Libri", "books": "Libri",
    "cd": "CD", "cds": "CD",
    "vinile": "Vinili", "vinili": "Vinili", "vinyl": "Vinili", "vinyls": "Vinili", "lp": "Vinili",
}


@dataclass(eq=False)
class Item:
    title: str = ""
    author: str = ""
    publisher: str = ""
    year: str = ""
    author_sort: str = ""
    isbn_or_barcode: str = ""
    category: str = "Libri"
    collection: str = ""
    # Campi derivati pre-calcolati per ordinamento e filtro istantaneo (<1ms)
    k_title: str = field(default="", repr=False)
    k_author: str = field(default="", repr=False)
    k_publisher: str = field(default="", repr=False)
    k_year: str = field(default="", repr=False)
    k_collection_base: str = field(default="", repr=False)
    k_collection_num: int = field(default=999999, repr=False)
    k_collection: str = field(default="", repr=False)
    collection_base_display: str = field(default="", repr=False)
    blob: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if self.category not in CATEGORY_INFO:
            mapped = _TYPE_TO_CATEGORY.get(str(self.category or "").strip().lower())
            self.category = mapped or "Libri"
        is_music = CATEGORY_INFO[self.category]["music"]
        if not self.author_sort:
            self.author_sort = surname_key(self.author, is_music=is_music)
        self.collection = (self.collection or "").strip()
        t_key = title_sort_key(self.title)
        sk = search_fold(self.author_sort or self.author)
        self.k_author = f"{sk}\x00{t_key}"
        self.k_publisher = f"{search_fold(self.publisher)}\x00{sk}\x00{t_key}"
        y = year_of(self.year)
        self.k_year = f"{y or '0000'}\x00{sk}\x00{t_key}"
        self.k_title = f"{t_key}\x00{sk}"
        c_base, c_num, c_disp = collection_sort_parts(self.collection)
        self.k_collection_base = c_base
        self.k_collection_num = c_num
        self.collection_base_display = c_disp
        self.k_collection = f"{c_base}\x00{c_num:06d}\x00{self.k_author}" if c_base else ""
        self.blob = fold(
            " ".join((self.title, self.author, self.author_sort, self.publisher, str(self.year), self.collection))
        )

    @property
    def kind(self) -> str:
        return CATEGORY_INFO.get(self.category, CATEGORY_INFO["Libri"])["type"]

    @property
    def sort_key(self) -> str:
        return self.author_sort

    def with_changes(self, **changes) -> "Item":
        if "sort_key" in changes and "author_sort" not in changes:
            changes["author_sort"] = changes.pop("sort_key")
        valid = {"title", "author", "publisher", "year", "author_sort", "isbn_or_barcode", "category", "collection"}
        clean = {k: v for k, v in changes.items() if k in valid}
        return replace(self, **clean)

    def identity(self) -> str:
        return f"{fold(self.title)}|{fold(self.author)}"

    def as_row(self) -> List[str]:
        if self.collection:
            return [self.kind, self.title, self.author, self.publisher, self.year, self.author_sort, self.collection]
        return [self.kind, self.title, self.author, self.publisher, self.year, self.author_sort]


def detect_category(path: str) -> Optional[str]:
    """Determina la categoria di un file CSV dal nome (libri*, cd*, vinili*) o dal contenuto."""
    base = os.path.basename(path).lower()
    if base.endswith(".csv"):
        base = base[:-4]
    if base.startswith(("libri", "libro", "book")):
        return "Libri"
    if base.startswith("cd"):
        return "CD"
    if base.startswith(("vinili", "vinile", "vinyl", "lp")):
        return "Vinili"
    try:
        rows = _read_rows(path, max_rows=5)
    except OSError:
        return None
    for row in rows:
        if row:
            cat = _TYPE_TO_CATEGORY.get(row[0].strip().lower())
            if cat:
                return cat
    return None


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_rows(path: str, max_rows: Optional[int] = None) -> List[List[str]]:
    if not os.path.isfile(path):
        return []
    with open(path, "rb") as f:
        text = _decode(f.read())
    text = text.replace("\r\r\n", "\n").replace("\r\n", "\n").replace("\r", "\n")
    sample = "\n".join(text.split("\n")[:20])
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        if sample.count(";") > sample.count(","):
            delimiter = ";"
    rows: List[List[str]] = []
    for row in csv.reader(io.StringIO(text), delimiter=delimiter):
        if not row or all(not c.strip() for c in row):
            continue
        rows.append([c.strip() for c in row])
        if max_rows and len(rows) >= max_rows:
            break
    return rows


def load_csv(path: str, category: str = "Libri") -> List[Item]:
    """Legge un archivio CSV. Tollera BOM, ';' o ',' come separatore, intestazioni e righe a 4/5/6/7 colonne."""
    if not os.path.isfile(path):
        return []
    items: List[Item] = []
    for row in _read_rows(path):
        first = row[0].lower()
        if first in ("tipo", "type", "titolo", "title"):
            continue
        collection = ""
        if len(row) >= 5 and first in _TYPE_TO_CATEGORY:
            _kind, title, author, publisher, year = row[:5]
            sort_key = row[5] if len(row) >= 6 else ""
            collection = row[6] if len(row) >= 7 else ""
            cat = _TYPE_TO_CATEGORY.get(first, category)
        elif len(row) >= 4:
            title, author, publisher, year = row[:4]
            sort_key = row[4] if len(row) >= 5 else ""
            collection = row[5] if len(row) >= 6 else ""
            cat = category
        elif len(row) == 3:
            title, author, publisher = row
            year, sort_key, cat = "", "", category
        else:
            continue
        if not title:
            continue
        items.append(
            Item(
                title=title,
                author=author,
                publisher=publisher,
                year=year,
                author_sort=sort_key,
                category=cat,
                collection=collection,
            )
        )
    return items


def sort_items(items: List[Item], group_by_collection: bool = False) -> List[Item]:
    """
    Ordina una lista di elementi:
      - Se `group_by_collection=False` (default): tutti gli elementi seguono il normale ordine alfabetico per cognome autore A-Z.
      - Se `group_by_collection=True`: i volumi singoli sono ordinati alfabeticamente per
        cognome autore, mentre i volumi appartenenti a una Collana / Collezione vengono raggruppati
        a parte per nome collana (e all'interno della stessa collana per eventuale numero volume,
        poi cognome autore e titolo).
    """
    if group_by_collection:
        return sorted(
            items,
            key=lambda i: (
                1 if i.k_collection_base else 0,
                i.k_collection_base,
                i.k_collection_num,
                i.k_author,
                i.k_title,
                i.k_year,
            ),
        )
    return sorted(items, key=lambda i: (i.k_author, i.k_title, i.k_year))


def unique_collections(items: List[Item]) -> List[str]:
    """Restituisce l'elenco ordinato delle Collane / Collezioni distinte presenti nella lista."""
    seen: Dict[str, str] = {}
    for it in items:
        base_disp = it.collection_base_display or it.collection.strip()
        if not base_disp:
            continue
        key = search_fold(base_disp)
        if key and key not in seen:
            seen[key] = base_disp
    return sorted(seen.values(), key=str.lower)


def _backup(path: str, keep: int = 20) -> None:
    if not os.path.exists(path):
        return
    stem = os.path.splitext(os.path.basename(path))[0]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest_dir = backups_dir()
    try:
        dest = os.path.join(dest_dir, f"{stem}_{stamp}.csv")
        seq = 1
        while os.path.exists(dest):
            dest = os.path.join(dest_dir, f"{stem}_{stamp}_{seq:02d}.csv")
            seq += 1
        shutil.copy2(path, dest)
        olds = sorted(f for f in os.listdir(dest_dir) if f.startswith(stem + "_") and f.endswith(".csv"))
        for old in olds[:-keep]:
            os.unlink(os.path.join(dest_dir, old))
    except OSError:
        pass


def save_csv(path_or_items, items_or_path=None) -> None:
    """
    Salvataggio atomico con copia di sicurezza automatica della versione precedente.
    Accetta sia `save_csv(path, items)` sia `save_csv(items, path)`.
    """
    if isinstance(path_or_items, str):
        path = path_or_items
        items = items_or_path or []
    else:
        items = path_or_items or []
        path = str(items_or_path)

    if not path.lower().endswith(".csv"):
        path += ".csv"
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for it in items:
        writer.writerow(it.as_row())
    _backup(path)
    atomic_write_text(path, buf.getvalue())


def scan_folder(folder: str) -> Dict[str, List[str]]:
    """Restituisce `{categoria: [nomi file csv]}` per la cartella indicata."""
    found: Dict[str, List[str]] = {c: [DEFAULT_FILES[c]] for c in CATEGORIES}
    if not folder or not os.path.isdir(folder):
        return found
    for name in sorted(os.listdir(folder), key=str.lower):
        if name.startswith(".") or not name.lower().endswith(".csv"):
            continue
        path = os.path.join(folder, name)
        cat = detect_category(path)
        if cat and name not in found[cat]:
            found[cat].append(name)
    for c in CATEGORIES:
        default_fn = DEFAULT_FILES[c]
        others = sorted([x for x in found[c] if x != default_fn], key=str.lower)
        found[c] = [default_fn] + others
    return found
