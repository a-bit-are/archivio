"""Modelli dati (Suggestion, EditionBundle), cache SQLite WAL e SeedIndex in RAM."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional, Tuple

from .catalog_data import (
    CACHE_TTL_SECONDS,
    CURATED_BOOKS_SEED,
    CURATED_MUSIC_SEED,
    SEARCH_STOPWORDS,
)
from .catalog_scoring import _extract_year, _publishers_match
from .config import cache_path, resource_path
from .textutil import collection_sort_parts, fold, search_fold, surname_key


@dataclass
class Suggestion:
    """Singola proposta di autocompletamento."""
    title: str
    author: str
    year: str = ""
    publisher: str = ""
    author_sort: str = ""
    work_id: str = ""        # Open Library '/works/OL..W' oppure MusicBrainz release-group MBID
    category: str = "Libri"  # "Libri" | "CD" | "Vinili"
    source: str = "catalog"  # "archive" | "catalog" | "online"
    popularity: int = 0
    publishers: List[str] = field(default_factory=list)
    years: List[str] = field(default_factory=list)
    collection: str = ""
    collections: List[str] = field(default_factory=list)
    from_isbn: bool = False
    score: float = 0.0

    def display_author_sort(self) -> str:
        return self.author_sort or surname_key(self.author, self.category)


@dataclass
class EditionBundle:
    """Risultato dell'arricchimento edizioni (case editrici/etichette, anni e collane per un'opera)."""
    category: str
    work_id: str
    title: str
    author: str
    author_sort: str
    default_publisher: str
    default_year: str
    publishers: List[str]
    years: List[str]
    collections: List[str] = field(default_factory=list)
    default_collection: str = ""
    collection_meta: Dict[str, Dict[str, str]] = field(default_factory=dict)
    publisher_years: Dict[str, str] = field(default_factory=dict)
    year_publishers: Dict[str, str] = field(default_factory=dict)
    year_collections: Dict[str, str] = field(default_factory=dict)
    publisher_collections: Dict[str, str] = field(default_factory=dict)
    publisher_all_years: Dict[str, List[str]] = field(default_factory=dict)
    year_all_publishers: Dict[str, List[str]] = field(default_factory=dict)
    pair_collections: Dict[str, List[str]] = field(default_factory=dict)
    edition_count: int = 0
    from_cache: bool = False

    def __post_init__(self) -> None:
        canon_pub: Dict[str, str] = {}
        for p in self.publishers:
            ps = str(p or "").strip()
            if ps:
                canon_pub.setdefault(fold(ps), ps)
        for src_p in (
            list(self.publisher_all_years.keys())
            + list(self.publisher_years.keys())
            + list(self.year_publishers.values())
            + [p for ps in self.year_all_publishers.values() for p in (ps or [])]
            + [
                str((m or {}).get("publisher") or "")
                for m in self.collection_meta.values()
                if isinstance(m, dict)
            ]
        ):
            ps = str(src_p or "").strip()
            if ps:
                pf = fold(ps)
                if pf not in canon_pub or (canon_pub[pf] == pf and ps != pf):
                    canon_pub[pf] = ps

        canon_coll: Dict[str, str] = {}
        for c in self.collections:
            cs = str(c or "").strip()
            if cs:
                canon_coll.setdefault(fold(cs), cs)
        for c in self.collection_meta.keys():
            cs = str(c or "").strip()
            if cs:
                cf = fold(cs)
                if cf not in canon_coll or (canon_coll[cf] == cf and cs != cf):
                    canon_coll[cf] = cs

        norm_pay: Dict[str, List[str]] = {}

        def _add_py(p_val: str, y_val: str) -> None:
            ps = str(p_val or "").strip()
            ys = str(y_val or "").strip()
            if not ps or not ys:
                return
            cp = canon_pub.get(fold(ps), ps)
            lst = norm_pay.setdefault(cp, [])
            if ys not in lst:
                lst.append(ys)

        for p_k, yrs in self.publisher_all_years.items():
            for y in (yrs or []):
                _add_py(p_k, y)
        for p_k, y in self.publisher_years.items():
            _add_py(p_k, y)
        for y, p_k in self.year_publishers.items():
            _add_py(p_k, y)
        for y, pubs in self.year_all_publishers.items():
            for p_k in (pubs or []):
                _add_py(p_k, y)
        for m in self.collection_meta.values():
            if isinstance(m, dict):
                _add_py(m.get("publisher") or "", m.get("year") or "")
        self.publisher_all_years = norm_pay

        pub_order = {fold(p): idx for idx, p in enumerate(self.publishers) if p}
        norm_yap: Dict[str, List[str]] = {}
        seen_yap: Dict[str, set[str]] = {}

        def _add_yp(y_val: str, p_val: str) -> None:
            ys = str(y_val or "").strip()
            ps = str(p_val or "").strip()
            if not ys or not ps:
                return
            cp = canon_pub.get(fold(ps), ps)
            cf = fold(cp)
            s_set = seen_yap.setdefault(ys, set())
            if cf not in s_set:
                s_set.add(cf)
                norm_yap.setdefault(ys, []).append(cp)

        for y, pubs in self.year_all_publishers.items():
            for p in (pubs or []):
                _add_yp(y, p)
        if self.year_publishers:
            for y, p in self.year_publishers.items():
                _add_yp(y, p)
        for p in self.publishers:
            for y in norm_pay.get(canon_pub.get(fold(p), p), []):
                _add_yp(y, p)
        for p, yrs in norm_pay.items():
            for y in yrs:
                _add_yp(y, p)
        if not self.year_all_publishers and pub_order:
            for y, pubs in norm_yap.items():
                explicit_first = self.year_publishers.get(y)
                exp_f = fold(explicit_first) if explicit_first else ""
                pubs.sort(
                    key=lambda p: (
                        0 if exp_f and fold(p) == exp_f else 1,
                        pub_order.get(fold(p), 9999),
                        p,
                    )
                )
        self.year_all_publishers = norm_yap

        if not self.publisher_years and self.publisher_all_years:
            for p, yrs in self.publisher_all_years.items():
                if yrs:
                    self.publisher_years[p] = yrs[0]
                    self.publisher_years.setdefault(fold(p), yrs[0])
        if not self.year_publishers and self.year_all_publishers:
            for y, pubs in self.year_all_publishers.items():
                if pubs:
                    self.year_publishers[y] = pubs[0]
        elif self.year_all_publishers:
            for y, pubs in self.year_all_publishers.items():
                if y not in self.year_publishers and pubs:
                    self.year_publishers[y] = pubs[0]

        norm_pc: Dict[str, List[str]] = {}
        seen_pc: Dict[str, set[str]] = {}

        def _add_pair_coll(p_val: str, y_val: str, c_val: str) -> None:
            ps = str(p_val or "").strip()
            ys = str(y_val or "").strip()
            cs = str(c_val or "").strip()
            if not ps or not ys or not cs:
                return
            cc = canon_coll.get(fold(cs), cs)
            pk = f"{fold(ps)}|{ys}"
            s_set = seen_pc.setdefault(pk, set())
            if fold(cc) not in s_set:
                s_set.add(fold(cc))
                norm_pc.setdefault(pk, []).append(cc)

        for pk, colls in self.pair_collections.items():
            if "|" in pk:
                pk_p, pk_y = pk.rsplit("|", 1)
                for c in (colls or []):
                    _add_pair_coll(pk_p, pk_y, c)
        for c, m in self.collection_meta.items():
            if isinstance(m, dict):
                _add_pair_coll(m.get("publisher") or "", m.get("year") or "", c)
        self.pair_collections = norm_pc

        if not self.year_collections and self.collection_meta:
            for c, m in self.collection_meta.items():
                if not isinstance(m, dict):
                    continue
                y = m.get("year")
                cc = canon_coll.get(fold(c), c)
                if y and y not in self.year_collections:
                    self.year_collections[y] = cc
        if not self.publisher_collections and self.collection_meta:
            for c, m in self.collection_meta.items():
                if not isinstance(m, dict):
                    continue
                p = m.get("publisher")
                cc = canon_coll.get(fold(c), c)
                if p:
                    cp = canon_pub.get(fold(p), p)
                    if cp not in self.publisher_collections:
                        self.publisher_collections[cp] = cc
                        self.publisher_collections[fold(cp)] = cc

    def years_for_publisher(self, publisher: str) -> List[str]:
        """Restituisce tutti gli anni noti per uno specifico editore/etichetta in questo bundle."""
        p_raw = (publisher or "").strip()
        if not p_raw:
            return []
        p_fold = fold(p_raw)
        p_sfold = search_fold(p_raw)
        out: List[str] = []
        seen: set[str] = set()

        def _add_yrs(yrs_list: List[str]) -> None:
            for y in yrs_list or []:
                yc = str(y or "").strip()
                if yc and yc not in seen:
                    seen.add(yc)
                    out.append(yc)

        # 1) Corrispondenze esatte (normalizzate)
        for k, yrs in self.publisher_all_years.items():
            if k == p_raw or fold(k) == p_fold or search_fold(k) == p_sfold:
                _add_yrs(yrs)
        for k, yr in self.publisher_years.items():
            if yr and (k == p_raw or fold(k) == p_fold or search_fold(k) == p_sfold):
                _add_yrs([yr])
        for y, pubs in self.year_all_publishers.items():
            if any(fold(p) == p_fold or search_fold(p) == p_sfold for p in (pubs or [])):
                _add_yrs([y])
        for y, p in self.year_publishers.items():
            if p and (fold(p) == p_fold or search_fold(p) == p_sfold):
                _add_yrs([y])
        for meta in self.collection_meta.values():
            if isinstance(meta, dict):
                mp = meta.get("publisher") or ""
                my = meta.get("year") or ""
                if mp and my and (fold(mp) == p_fold or search_fold(mp) == p_sfold):
                    _add_yrs([my])

        # 2) Fallback sicuro su match per parole intere (_publishers_match)
        if not out:
            for k, yrs in self.publisher_all_years.items():
                if _publishers_match(k, p_raw):
                    _add_yrs(yrs)
            for k, yr in self.publisher_years.items():
                if yr and _publishers_match(k, p_raw):
                    _add_yrs([yr])
            for y, pubs in self.year_all_publishers.items():
                if any(_publishers_match(p, p_raw) for p in (pubs or [])):
                    _add_yrs([y])
            for y, p in self.year_publishers.items():
                if p and _publishers_match(p, p_raw):
                    _add_yrs([y])
            for meta in self.collection_meta.values():
                if isinstance(meta, dict):
                    mp = meta.get("publisher") or ""
                    my = meta.get("year") or ""
                    if mp and my and _publishers_match(mp, p_raw):
                        _add_yrs([my])
        return out

    def publishers_for_year(self, year: str) -> List[str]:
        """Restituisce tutti gli editori/etichette attestati per uno specifico anno in questo bundle."""
        y_clean = _extract_year(str(year or "")) or str(year or "").strip()
        if not y_clean:
            return []
        out: List[str] = []
        seen: set[str] = set()

        def _add_pub(p: str) -> None:
            ps = str(p or "").strip()
            if ps and fold(ps) not in seen:
                seen.add(fold(ps))
                out.append(ps)

        for p in self.year_all_publishers.get(y_clean, []):
            _add_pub(p)
        if self.year_publishers.get(y_clean):
            _add_pub(self.year_publishers[y_clean])
        for k, yrs in self.publisher_all_years.items():
            if y_clean in (yrs or []):
                _add_pub(k)
        for k, yr in self.publisher_years.items():
            if yr == y_clean:
                _add_pub(k)
        for meta in self.collection_meta.values():
            if isinstance(meta, dict) and meta.get("year") == y_clean and meta.get("publisher"):
                _add_pub(meta["publisher"])
        return out

    def collections_for_pair(self, publisher: str, year: str) -> List[str]:
        """Restituisce le collane compatibili con la coppia (editore, anno)."""
        p_raw = (publisher or "").strip()
        y_clean = _extract_year(str(year or "")) or str(year or "").strip()
        if not p_raw or not y_clean:
            return []
        p_fold = fold(p_raw)
        p_sfold = search_fold(p_raw)
        mixed_coll_folds = {fold(k) for k in self.collection_meta.keys() if k != fold(k)}
        out: List[str] = []
        seen: set[str] = set()

        def _add_coll(c: str) -> None:
            cs = str(c or "").strip()
            if cs and fold(cs) not in seen:
                seen.add(fold(cs))
                out.append(cs)

        for c in self.pair_collections.get(f"{p_fold}|{y_clean}", []):
            _add_coll(c)
        for pk, colls in self.pair_collections.items():
            if "|" not in pk:
                continue
            pk_p, pk_y = pk.rsplit("|", 1)
            if pk_y == y_clean and (pk_p == p_fold or search_fold(pk_p) == p_sfold):
                for c in (colls or []):
                    _add_coll(c)
        for c_name, meta in self.collection_meta.items():
            if not isinstance(meta, dict):
                continue
            mp = meta.get("publisher") or ""
            my = meta.get("year") or ""
            if my == y_clean and mp and (fold(mp) == p_fold or search_fold(mp) == p_sfold):
                if c_name != fold(c_name) or fold(c_name) not in mixed_coll_folds:
                    _add_coll(c_name)

        if not out:
            for pk, colls in self.pair_collections.items():
                if "|" not in pk:
                    continue
                pk_p, pk_y = pk.rsplit("|", 1)
                if pk_y == y_clean and _publishers_match(pk_p, p_raw):
                    for c in (colls or []):
                        _add_coll(c)
            for c_name, meta in self.collection_meta.items():
                if not isinstance(meta, dict):
                    continue
                mp = meta.get("publisher") or ""
                my = meta.get("year") or ""
                if my == y_clean and mp and _publishers_match(mp, p_raw):
                    if c_name != fold(c_name) or fold(c_name) not in mixed_coll_folds:
                        _add_coll(c_name)
        return out


class PersistentCache:
    """Cache SQLite persistente su disco per ricerche ed edizioni (con connessione riutilizzata e pulizia TTL)."""

    def __init__(self, db_file: Optional[str] = None):
        self.db_file = db_file or cache_path()
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._closed = False
        self._writes_since_prune = 0
        self._init_db()

    def _get_conn(self) -> Optional[sqlite3.Connection]:
        if self._closed:
            return None
        if self._conn is None:
            parent = os.path.dirname(self.db_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(self.db_file, timeout=5, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kv_cache ("
                "  k TEXT PRIMARY KEY,"
                "  v TEXT NOT NULL,"
                "  ts INTEGER NOT NULL"
                ")"
            )
            conn.commit()
            self._conn = conn
        return self._conn

    def _init_db(self) -> None:
        with self._lock:
            try:
                self._get_conn()
            except Exception:
                self._conn = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    def clear(self) -> None:
        """Chiude la connessione WAL, rimuove il database e i file -wal/-shm, poi reinizializza."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception:
                    pass
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            for path in (self.db_file, f"{self.db_file}-wal", f"{self.db_file}-shm"):
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            self._writes_since_prune = 0
            self._closed = False
            self._get_conn()

    def get(self, key: str, max_age: int = CACHE_TTL_SECONDS) -> Optional[dict]:
        now = int(time.time())
        with self._lock:
            try:
                conn = self._get_conn()
                if conn is None:
                    return out
                if conn is None:
                    return None
                row = conn.execute(
                    "SELECT v, ts FROM kv_cache WHERE k = ?", (key,)
                ).fetchone()
                if not row:
                    return None
                val_str, ts = row
                if now - int(ts) > max_age:
                    return None
                return json.loads(val_str)
            except Exception:
                return None

    def get_many(self, keys: List[str], max_age: int = CACHE_TTL_SECONDS) -> Dict[str, dict]:
        """Legge più chiavi con la connessione SQLite persistente (molto più veloce di tanti `get` consecutivi)."""
        keys = list(dict.fromkeys(keys))
        if not keys:
            return {}
        now = int(time.time())
        out: Dict[str, dict] = {}
        with self._lock:
            try:
                conn = self._get_conn()
                if conn is None:
                    return out
                for i in range(0, len(keys), 500):
                    chunk = keys[i : i + 500]
                    marks = ",".join("?" * len(chunk))
                    for k, val_str, ts in conn.execute(
                        f"SELECT k, v, ts FROM kv_cache WHERE k IN ({marks})", chunk
                    ):
                        if now - int(ts) > max_age:
                            continue
                        try:
                            out[k] = json.loads(val_str)
                        except ValueError:
                            continue
            except Exception:
                return out
        return out

    def set(self, key: str, value: dict) -> None:
        now = int(time.time())
        with self._lock:
            try:
                payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                conn = self._get_conn()
                if conn is None:
                    return
                conn.execute(
                    "INSERT OR REPLACE INTO kv_cache (k, v, ts) VALUES (?, ?, ?)",
                    (key, payload, now),
                )
                self._writes_since_prune += 1
                if self._writes_since_prune >= 200:
                    self._writes_since_prune = 0
                    conn.execute("DELETE FROM kv_cache WHERE ts < ?", (now - CACHE_TTL_SECONDS,))
                conn.commit()
            except Exception:
                pass

    def search_cached_items(self, category: str, query_tokens: List[str], limit: int = 35) -> List[dict]:
        """Cerca velocemente (<1ms) elementi recenti già memorizzati in cache per parole chiave o radici."""
        sub_tokens = [tok for tok in query_tokens if tok not in SEARCH_STOPWORDS and len(tok) >= 2]
        if not sub_tokens:
            sub_tokens = query_tokens
        if not sub_tokens:
            return []

        base_cond = "k LIKE ?"
        base_param = f"search:v11:{category}:%"
        sub_conds: List[str] = []
        sub_params: List[str] = []
        for tok in sub_tokens[:4]:
            p = tok[:4] if len(tok) >= 4 else tok
            sub_conds.append("v LIKE ?")
            sub_params.append(f"%{p}%")

        def _query_rows(conds: List[str], parms: List[str]) -> list:
            joiner = " AND " if len(conds) >= 2 else " OR "
            where_clause = f"{base_cond} AND ({joiner.join(conds)})"
            sql = f"SELECT v FROM kv_cache WHERE {where_clause} ORDER BY ts DESC LIMIT {limit}"
            return conn.execute(sql, [base_param] + parms).fetchall()

        items: List[dict] = []
        seen = set()
        with self._lock:
            try:
                conn = self._get_conn()
                if conn is None:
                    return []
                rows = _query_rows(sub_conds, sub_params)
                if not rows and len(sub_conds) >= 2:
                    # Fallback rilassato: se l'utente ha interrotto il titolo aggiungendo un'altra parola,
                    # ritenta senza l'ultima parola (o con le prime due parole significative)
                    rows = _query_rows(sub_conds[:-1], sub_params[:-1])
                if not rows and len(sub_conds) >= 3:
                    rows = _query_rows(sub_conds[:2], sub_params[:2])
                for r in rows:
                    data = json.loads(r[0])
                    if isinstance(data, dict) and isinstance(data.get("items"), list):
                        for it in data["items"]:
                            if not isinstance(it, dict):
                                continue
                            t = it.get("title", "").strip().lower()
                            a = it.get("author", "").strip().lower()
                            if (t, a) not in seen:
                                seen.add((t, a))
                                items.append(it)
            except Exception:
                pass
        return items


class SeedIndex:
    """Indice in memoria con lookup istantaneo (<2ms) per Libri e Musica."""

    def __init__(self):
        self.books: List[Tuple[str, str, str, int, str, str, str, str, str, str]] = []
        self.music: List[Tuple[str, str, str, int, str, str, str]] = []
        self._book_token_map: Dict[str, List[int]] = {}
        self._music_token_map: Dict[str, List[int]] = {}
        self.loaded = False
        self._lock = threading.Lock()

    def ensure_loaded(self) -> None:
        if self.loaded:
            return
        with self._lock:
            if self.loaded:
                return
            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
            self._load_books(os.path.join(data_dir, "seed_books.json"))
            self._load_music(
                os.path.join(data_dir, "seed_music.json"),
                covers_path=os.path.join(data_dir, "seed_covers.json"),
            )
            self.loaded = True

    def _load_books(self, path: str) -> None:
        curated_len = len(CURATED_BOOKS_SEED)
        raw_items = list(CURATED_BOOKS_SEED)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                raw_items.extend(raw.get("items") or [])
            except Exception:
                pass
        for idx_row, row in enumerate(raw_items):
            if len(row) < 5:
                continue
            title = str(row[0] or "").strip().rstrip(".")
            author = str(row[1] or "").strip()
            first_year = str(row[2] or "").strip()
            raw_pop = int(row[3] or 0)
            wkey = str(row[4] or "").strip()
            pub = str(row[5] or "").strip() if len(row) > 5 else ""
            ed_year = str(row[6] or "").strip() if len(row) > 6 else ""
            lang = str(row[7] or "").strip() if len(row) > 7 else ""
            st = search_fold(title)
            sa = search_fold(author)
            if st == sa:
                continue
            # Scala la popolarità del catalogo locale così che i grandi classici
            # siano sempre ordinati prima di opere minori o autopubblicate online
            if idx_row < curated_len:
                pop = max(raw_pop * 12, 55000)
            else:
                pop = max(raw_pop * 140, 4800)
            b_idx = len(self.books)
            self.books.append((title, author, first_year, pop, wkey, pub, ed_year, lang, st, sa))
            for tok in set((st + " " + sa).split()):
                if len(tok) >= 2:
                    p = tok[:3]
                    self._book_token_map.setdefault(p, []).append(b_idx)
                    if p != tok[:2]:
                        self._book_token_map.setdefault(tok[:2], []).append(b_idx)

    def _load_music(self, path: str, covers_path: str = "") -> None:
        seen_keys: set = set()

        def _append_music_row(title: str, artist: str, year: str, pop: int, mbid: str) -> None:
            st = search_fold(title)
            sa = search_fold(artist)
            if not st or not sa:
                return
            key = (st, sa)
            if key in seen_keys:
                return
            seen_keys.add(key)
            m_idx = len(self.music)
            self.music.append((title, artist, year, pop, mbid, st, sa))
            for tok in set((st + " " + sa).split()):
                if len(tok) >= 2:
                    p = tok[:3]
                    self._music_token_map.setdefault(p, []).append(m_idx)
                    if p != tok[:2]:
                        self._music_token_map.setdefault(tok[:2], []).append(m_idx)

        for row in CURATED_MUSIC_SEED:
            if len(row) < 4:
                continue
            title = str(row[0] or "").strip()
            artist = str(row[1] or "").strip()
            year = str(row[2] or "").strip()
            raw_pop = int(row[3] or 0)
            mbid = str(row[4] or "").strip() if len(row) > 4 else ""
            _append_music_row(title, artist, year, max(raw_pop, 4500), mbid)

        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                items = raw.get("items") or []
                for row in items:
                    if len(row) < 4:
                        continue
                    title = str(row[0] or "").strip()
                    artist = str(row[1] or "").strip()
                    year = str(row[2] or "").strip()
                    raw_pop = int(row[3] or 0)
                    st = search_fold(title)
                    if len(st) <= 1 and raw_pop < 50000:
                        continue
                    pop = max(raw_pop, 4500)
                    mbid = str(row[4] or "").strip() if len(row) > 4 else ""
                    _append_music_row(title, artist, year, pop, mbid)
            except Exception:
                pass

        if covers_path and os.path.exists(covers_path):
            try:
                with open(covers_path, "r", encoding="utf-8") as f:
                    raw_cov = json.load(f)
                for row in raw_cov.get("items") or []:
                    if len(row) < 3:
                        continue
                    title = str(row[0] or "").strip()
                    artist = str(row[1] or "").strip()
                    year = str(row[2] or "").strip()
                    st = search_fold(title)
                    if len(st) <= 1:
                        continue
                    _append_music_row(title, artist, year, 6500, "")
            except Exception:
                pass
