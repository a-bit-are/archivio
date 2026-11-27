"""
Motore di catalogo e autocompletamento multi-fonte in parallelo (Open Source Edition).
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
import heapq
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

from .catalog_data import (  # noqa: F401
    ARTIST_COLLECTION_META,
    CACHE_TTL_SECONDS,
    CD_FORMAT_KEYWORDS,
    COLLECTION_TO_PUBLISHER_IT,
    CURATED_BOOKS_SEED,
    CURATED_EDITION_PROFILES,
    CURATED_MUSIC_SEED,
    DEFAULT_BOOK_PUBLISHERS_IT,
    ITALIAN_PUBLISHERS_PRIORITY,
    MUSIC_COLLECTIONS_CD,
    MUSIC_COLLECTIONS_COMMON,
    MUSIC_COLLECTIONS_VINYL,
    NOISE_BOOK_WORDS,
    NOISE_TITLE_WORDS,
    NON_BOOK_PUBLISHERS_FOLD,
    PUBLISHER_COLLECTIONS_IT,
    SEARCH_STOPWORDS,
    USER_AGENT,
    VINYL_FORMAT_KEYWORDS,
    _ALBUM_SUFFIX_CLEAN_RE,
    _AMBIGUOUS_PUB_HINT_WORDS,
    _CITY_ONLY_PUBLISHERS_FOLD,
    _COLLECTION_TO_PUBLISHER_SORTED,
    _ITALIAN_PUBS_BY_LEN,
    _LOCAL_PRUNE,
    _PUBLISHER_HINT_ENTRIES,
    _clean_album_title_suffixes,
)
from .catalog_scoring import (  # noqa: F401
    _author_matches,
    _barcode_variants,
    _book_edition_title_matches,
    _clean_publisher_name,
    _clean_sbn_author,
    _clean_sbn_text,
    _clean_sbn_title,
    _direct_ratio,
    _direct_ratio_cut,
    _extract_itunes_label,
    _extract_query_hints,
    _extract_sbn_fallback_author,
    _extract_year,
    _fuzzy_score,
    _fuzzy_score_cut,
    _is_cd_format,
    _is_isbn_or_barcode,
    _is_vinyl_format,
    _isbn10_to_13,
    _isbn13_to_10,
    _parse_abebooks_publisher_field,
    _parse_sbn_collection,
    _parse_sbn_publication,
    _popularity_bonus,
    _publishers_match,
    _read_response_bounded,
    _rf_fuzz,
    _rf_process,
    _score_suggestion_core,
    _score_title_within_author,
    score_suggestion,
)
from .catalog_cache import (  # noqa: F401
    EditionBundle,
    PersistentCache,
    SeedIndex,
    Suggestion,
)
from .config import cache_path, resource_path
from .store import Item
from .textutil import fold, search_fold, sort_key_matches_author, surname_key


class CatalogService:
    """
    Servizio centrale di autocompletamento e recupero edizioni multi-motore (Libri, CD, Vinili).
    """

    def __init__(self, cache: Optional[PersistentCache] = None, discogs_token: str = ""):
        self.cache = cache or PersistentCache()
        self.seed = SeedIndex()
        clean_tok = str(discogs_token or "").strip()
        self.discogs_token = "" if ("\r" in clean_tok or "\n" in clean_tok) else clean_tok
        self._mb_lock = threading.Lock()
        self._http_lock = threading.Lock()
        self._last_mb_call = 0.0
        self._gb_disabled_until = 0.0
        self._last_http_code = 0
        self._thread_local = threading.local()
        self._search_pool = ThreadPoolExecutor(max_workers=24)
        self._mb_pool = ThreadPoolExecutor(max_workers=4)
        threading.Thread(target=self.seed.ensure_loaded, daemon=True).start()

    def close(self) -> None:
        """Chiude il pool HTTP e la cache SQLite associata al catalogo."""
        try:
            self._search_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        try:
            self._mb_pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        if getattr(self, "cache", None) is not None:
            self.cache.close()

    def _set_last_http_code(self, code: int) -> None:
        self._thread_local.last_http_code = code
        with self._http_lock:
            self._last_http_code = code

    def _get_last_http_code(self) -> int:
        tl_code = getattr(self._thread_local, "last_http_code", None)
        if tl_code is not None:
            return int(tl_code)
        with self._http_lock:
            return int(self._last_http_code)

    def _http_get_json(
        self,
        url: str,
        timeout: float = 3.5,
        is_musicbrainz: bool = False,
        extra_headers: Optional[Dict[str, str]] = None,
        fast_fail: bool = False,
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> Optional[dict]:
        self._set_last_http_code(0)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        attempts = 1 if (fast_fail and not is_musicbrainz) else 2
        for attempt in range(attempts):
            if is_cancelled and is_cancelled():
                return None
            if is_musicbrainz:
                with self._mb_lock:
                    if is_cancelled and is_cancelled():
                        return None
                    elapsed = time.time() - self._last_mb_call
                    remaining = 1.05 - elapsed
                    while remaining > 0.0:
                        if is_cancelled and is_cancelled():
                            return None
                        time.sleep(min(0.05, remaining))
                        remaining = 1.05 - (time.time() - self._last_mb_call)
                    if is_cancelled and is_cancelled():
                        return None
                    self._last_mb_call = time.time()
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    self._set_last_http_code(200)
                    return json.loads(_read_response_bounded(resp).decode("utf-8"))
            except Exception as exc:
                code = getattr(exc, "code", None)
                self._set_last_http_code(int(code) if code else 0)
                if code in (400, 401, 403, 404) or attempt == attempts - 1:
                    return None
                retry_wait = 1.2 * (attempt + 1) if is_musicbrainz else 0.25
                deadline_r = time.time() + retry_wait
                while time.time() < deadline_r:
                    if is_cancelled and is_cancelled():
                        return None
                    time.sleep(min(0.05, max(0.0, deadline_r - time.time())))
        return None

    def _http_post_json(self, url: str, payload: dict, timeout: float = 2.0) -> Optional[list]:
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(_read_response_bounded(resp).decode("utf-8"))
        except Exception:
            return None

    def collections_for_publisher(self, publisher: str, category: str = "Libri", author: str = "") -> List[str]:
        """
        Restituisce le collane storiche associate a un editore (per i Libri) o le collezioni
        / ristampe / box set filtrati per formato (CD vs Vinili) e per Artista.
        """
        if category != "Libri":
            out: List[str] = []
            auth_norm = search_fold(author)
            if auth_norm:
                for a_key, cat_map in ARTIST_COLLECTION_META.items():
                    if a_key == auth_norm or a_key in auth_norm or auth_norm in a_key:
                        for c_name in (cat_map.get(category) or {}).keys():
                            if c_name not in out:
                                out.append(c_name)
            # Nessun elenco generico di "collezioni di formato": suggerisce solo ciò che riguarda davvero
            # l'artista (box set / ristampe note) o ciò che le edizioni dell'album rivelano (MusicBrainz).
            return out

        clean_p = _clean_publisher_name(publisher, "it")
        if clean_p in PUBLISHER_COLLECTIONS_IT:
            return list(PUBLISHER_COLLECTIONS_IT[clean_p])
        p_fold = fold(publisher)
        if p_fold:
            for k, colls in PUBLISHER_COLLECTIONS_IT.items():
                if fold(k) in p_fold or p_fold in fold(k):
                    return list(colls)
        return []

    def resolve_collection_metadata(
        self,
        collection: str,
        category: str = "Libri",
        author: str = "",
        bundle: Optional[EditionBundle] = None,
    ) -> Tuple[str, str]:
        """
        Data una Collana / Collezione selezionata dall'utente (es. 'Discovery Edition',
        'DeAgostini Jazz in Vinile', 'Oscar moderni #36', 'Supercoralli', 'Piccola Biblioteca Adelphi'),
        restituisce `(editore_o_etichetta, anno_edizione)` corrispondenti per aggiornare
        automaticamente i campi Editore/Etichetta e Anno.
        """
        c_raw = (collection or "").strip()
        if not c_raw:
            return "", ""

        # Rimuovi eventuale numero di volume finale (es. 'Oscar moderni #36' -> 'Oscar moderni')
        c_base = re.sub(r"\s*(?:#|n\.?\s*|vol\.?\s*)\d+\s*$", "", c_raw, flags=re.IGNORECASE).strip()
        c_fold = fold(c_raw)
        c_base_fold = fold(c_base)

        resolved_pub = ""
        resolved_yr = ""

        # 1) Metadati specifici dell'opera correntemente selezionata (da SBN, Google Books, OpenLibrary, MusicBrainz)
        if bundle and bundle.collection_meta:
            for cand_key in (c_raw, c_base, c_fold, c_base_fold):
                meta = bundle.collection_meta.get(cand_key)
                if meta:
                    resolved_pub = meta.get("publisher") or resolved_pub
                    resolved_yr = meta.get("year") or resolved_yr
                    if resolved_pub and resolved_yr:
                        return resolved_pub, resolved_yr
            # Cerca anche match per prefisso/contenimento nelle chiavi di collection_meta
            for k_meta, meta in bundle.collection_meta.items():
                kf = fold(k_meta)
                if kf and (kf == c_base_fold or c_base_fold.startswith(kf) or kf.startswith(c_base_fold)):
                    resolved_pub = resolved_pub or (meta.get("publisher") or "")
                    resolved_yr = resolved_yr or (meta.get("year") or "")
                    if resolved_pub and resolved_yr:
                        return resolved_pub, resolved_yr

        # 2) Musica (CD / Vinili): controlla collezioni specifiche dell'artista e del formato
        if category != "Libri":
            auth_norm = search_fold(author)
            # 2a) Prima nell'artista attivo
            for a_key, cat_map in ARTIST_COLLECTION_META.items():
                if auth_norm and (a_key == auth_norm or a_key in auth_norm or auth_norm in a_key):
                    for fmt_k in (category, "CD", "Vinili"):
                        for col_name, (p_val, y_val) in (cat_map.get(fmt_k) or {}).items():
                            if fold(col_name) == c_base_fold or fold(col_name) in c_fold:
                                resolved_pub = resolved_pub or p_val
                                resolved_yr = resolved_yr or y_val
            # 2b) Poi in tutte le collezioni d'artista note (es. se l'utente ha selezionato 'Discovery Edition')
            if not resolved_pub or not resolved_yr:
                for _a_key, cat_map in ARTIST_COLLECTION_META.items():
                    for fmt_k in (category, "CD", "Vinili"):
                        for col_name, (p_val, y_val) in (cat_map.get(fmt_k) or {}).items():
                            if fold(col_name) == c_base_fold:
                                resolved_pub = resolved_pub or p_val
                                resolved_yr = resolved_yr or y_val
            # 2c) Poi nei dizionari CD / Vinili e pattern noti
            fmt_dicts = (
                (MUSIC_COLLECTIONS_CD, MUSIC_COLLECTIONS_VINYL)
                if category == "CD"
                else (MUSIC_COLLECTIONS_VINYL, MUSIC_COLLECTIONS_CD)
            )
            for f_dict in fmt_dicts:
                for col_name, (p_val, y_val) in f_dict.items():
                    if fold(col_name) == c_base_fold or fold(col_name) in c_fold:
                        resolved_pub = resolved_pub or p_val
                        resolved_yr = resolved_yr or y_val
            if not resolved_pub:
                if "de agostini" in c_fold or "deagostini" in c_fold:
                    resolved_pub = "DeAgostini"
                    resolved_yr = resolved_yr or "2016"
                elif "tone poet" in c_fold or "blue note" in c_fold:
                    resolved_pub = "Blue Note"
                    resolved_yr = resolved_yr or "2019"
                elif "original album classics" in c_fold:
                    resolved_pub = "Sony Music"
                    resolved_yr = resolved_yr or "2010"
                elif "original album series" in c_fold:
                    resolved_pub = "Warner Music"
                    resolved_yr = resolved_yr or "2010"
                elif "mobile fidelity" in c_fold or "mofi" in c_fold:
                    resolved_pub = "Mobile Fidelity Sound Lab"
                elif "sorrisi" in c_fold:
                    resolved_pub = "TV Sorrisi e Canzoni"

        # 3) Libri: mappa Collana -> Casa Editrice
        else:
            if not resolved_pub:
                if c_base_fold in COLLECTION_TO_PUBLISHER_IT:
                    resolved_pub = COLLECTION_TO_PUBLISHER_IT[c_base_fold]
                else:
                    # Cerca parola/frase su confini di parola (evita collisioni su sottostringhe come 'et' in 'letture')
                    padded = f" {c_base_fold} "
                    for k_coll, pub_target in _COLLECTION_TO_PUBLISHER_SORTED:
                        if f" {k_coll} " in padded:
                            resolved_pub = pub_target
                            break

        # 4) Se abbiamo individuato l'Editore/Etichetta ma non ancora l'anno specifico della collana,
        #    usa l'anno di pubblicazione registrato per quell'editore/etichetta nel bundle dell'opera!
        if resolved_pub and not resolved_yr and bundle:
            yrs = bundle.years_for_publisher(resolved_pub)
            if yrs:
                resolved_yr = yrs[0]

        return resolved_pub, resolved_yr

    def resolve_publisher_metadata(
        self,
        publisher: str,
        category: str = "Libri",
        author: str = "",
        bundle: Optional[EditionBundle] = None,
        current_collection: str = "",
        current_year: str = "",
    ) -> Tuple[str, str, bool]:
        """
        Dato un Editore / Etichetta (es. 'Mondadori', 'Einaudi', 'Adelphi', 'EMI', 'Harvest'):
        Restituisce (anno_risolto, collana_risolta, collana_incompatibile_da_togliere).
        - anno_risolto: l'anno registrato per quell'editore nel bundle dell'opera (preserva current_year se già valido).
        - collana_risolta: eventuale collana specifica dell'edizione di quell'editore/anno.
        - collana_incompatibile_da_togliere: True se l'attuale current_collection appartiene a un ALTRO editore
          o a un anno incompatibile, e quindi va rimossa.
        """
        p_raw = (publisher or "").strip()
        if not p_raw:
            return "", "", False

        p_fold = fold(p_raw)
        p_sfold = search_fold(p_raw)
        cur_y = _extract_year(str(current_year or ""))

        # 1) Risolvi l'anno associato a questo editore per l'opera corrente (preservando cur_y se valido)
        pub_yrs = bundle.years_for_publisher(p_raw) if bundle else []
        if cur_y and cur_y in pub_yrs:
            resolved_yr = cur_y
        elif pub_yrs:
            resolved_yr = pub_yrs[0]
        else:
            resolved_yr = ""

        target_yr = cur_y if (cur_y and (not pub_yrs or cur_y in pub_yrs)) else resolved_yr

        # 2) Risolvi eventuale collana specifica per questo editore (e anno compatibile)
        resolved_coll = ""
        if bundle:
            if target_yr:
                pair_colls = bundle.collections_for_pair(p_raw, target_yr)
                if pair_colls:
                    resolved_coll = pair_colls[0]

            if not resolved_coll and bundle.publisher_collections:
                for cand_c in (
                    bundle.publisher_collections.get(p_raw),
                    bundle.publisher_collections.get(p_fold),
                    bundle.publisher_collections.get(p_sfold),
                ):
                    if cand_c:
                        c_meta = (
                            bundle.collection_meta.get(cand_c)
                            or bundle.collection_meta.get(fold(cand_c))
                            or {}
                        )
                        c_yr = c_meta.get("year") or ""
                        if not target_yr or not c_yr or c_yr == target_yr:
                            resolved_coll = cand_c
                            break
                if not resolved_coll:
                    for k_pub, c_val in bundle.publisher_collections.items():
                        if c_val and _publishers_match(k_pub, p_raw):
                            c_meta = (
                                bundle.collection_meta.get(c_val)
                                or bundle.collection_meta.get(fold(c_val))
                                or {}
                            )
                            c_yr = c_meta.get("year") or ""
                            if not target_yr or not c_yr or c_yr == target_yr:
                                resolved_coll = c_val
                                break

            if not resolved_coll and bundle.collection_meta:
                mixed_coll_folds = {fold(k) for k in bundle.collection_meta.keys() if k != fold(k)}
                for c_name, meta in bundle.collection_meta.items():
                    if not isinstance(meta, dict):
                        continue
                    if c_name == fold(c_name) and fold(c_name) in mixed_coll_folds:
                        continue
                    mp = meta.get("publisher") or ""
                    my = meta.get("year") or ""
                    if mp and _publishers_match(mp, p_raw):
                        if not target_yr or not my or my == target_yr:
                            resolved_coll = c_name
                            break

        # 3) Verifica se l'attuale current_collection è incompatibile e va tolta
        should_clear_coll = False
        cur_c = (current_collection or "").strip()
        if cur_c:
            col_pub, col_yr = self.resolve_collection_metadata(cur_c, category, author, bundle)
            if col_pub:
                if not _publishers_match(col_pub, p_raw):
                    should_clear_coll = True
            else:
                c_base = re.sub(r"\s*(?:#|n\.?\s*|vol\.?\s*)\d+\s*$", "", cur_c, flags=re.IGNORECASE).strip()
                other_pub = (
                    COLLECTION_TO_PUBLISHER_IT.get(fold(c_base))
                    or COLLECTION_TO_PUBLISHER_IT.get(fold(cur_c))
                )
                if other_pub and not _publishers_match(other_pub, p_raw):
                    should_clear_coll = True
            if not should_clear_coll and target_yr and col_yr and col_yr != target_yr:
                should_clear_coll = True

        return resolved_yr, resolved_coll, should_clear_coll

    def resolve_year_metadata(
        self,
        year_str: str,
        category: str = "Libri",
        author: str = "",
        bundle: Optional[EditionBundle] = None,
        current_collection: str = "",
        current_publisher: str = "",
        preserve_publisher: bool = False,
    ) -> Tuple[str, str, bool]:
        """
        Dato un Anno inserito o selezionato dall'utente (es. '1980', '1995'):
        Restituisce (editore_risolto, collana_risolta, collana_incompatibile_da_togliere).
        - editore_risolto: l'editore che ha pubblicato l'opera in quell'anno (preserva current_publisher se compatibile).
        - collana_risolta: eventuale collana dell'edizione compatibile con editore effettivo e anno.
        - collana_incompatibile_da_togliere: True se l'attuale current_collection apparteneva a un'altra
          edizione/anno/editore diversa da quella selezionata, e quindi va rimossa.
        """
        y_clean = _extract_year(str(year_str or ""))
        if not y_clean:
            return "", "", False

        cur_p = (current_publisher or "").strip()

        # 1) Risolvi l'editore per quest'anno (preservando current_publisher se attestato per quell'anno)
        resolved_pub = ""
        yr_pubs = bundle.publishers_for_year(y_clean) if bundle else []
        if cur_p and yr_pubs:
            for p_cand in yr_pubs:
                if _publishers_match(p_cand, cur_p):
                    resolved_pub = p_cand
                    break
        if not resolved_pub and yr_pubs:
            resolved_pub = yr_pubs[0]

        eff_pub = cur_p if (preserve_publisher and cur_p) else (resolved_pub or cur_p)

        # 2) Risolvi eventuale collana per (eff_pub, y_clean) senza mai iniettare collane di un altro editore
        resolved_coll = ""
        if bundle:
            if eff_pub:
                pair_colls = bundle.collections_for_pair(eff_pub, y_clean)
                if pair_colls:
                    resolved_coll = pair_colls[0]
            if not resolved_coll and bundle.year_collections and y_clean in bundle.year_collections:
                cand_c = bundle.year_collections[y_clean]
                if cand_c:
                    c_meta = (
                        bundle.collection_meta.get(cand_c)
                        or bundle.collection_meta.get(fold(cand_c))
                        or {}
                    )
                    cand_pub = c_meta.get("publisher") or ""
                    if not cand_pub:
                        cand_pub, _ = self.resolve_collection_metadata(cand_c, category, author, bundle)
                    if not eff_pub:
                        resolved_coll = cand_c
                    elif cand_pub and _publishers_match(cand_pub, eff_pub):
                        resolved_coll = cand_c
                    elif not cand_pub and (
                        not preserve_publisher
                        or not resolved_pub
                        or _publishers_match(eff_pub, resolved_pub)
                    ):
                        resolved_coll = cand_c
            if not resolved_coll and bundle.collection_meta:
                mixed_coll_folds = {fold(k) for k in bundle.collection_meta.keys() if k != fold(k)}
                for c_name, meta in bundle.collection_meta.items():
                    if not isinstance(meta, dict):
                        continue
                    if c_name == fold(c_name) and fold(c_name) in mixed_coll_folds:
                        continue
                    if meta.get("year") == y_clean:
                        mp = meta.get("publisher") or ""
                        if not eff_pub or (mp and _publishers_match(mp, eff_pub)) or (not mp and not preserve_publisher):
                            resolved_coll = c_name
                            break

        # 3) Verifica se l'attuale current_collection è incompatibile e va tolta
        should_clear_coll = False
        cur_c = (current_collection or "").strip()
        if cur_c:
            col_pub, col_yr = self.resolve_collection_metadata(cur_c, category, author, bundle)
            if col_yr and col_yr != y_clean:
                should_clear_coll = True
            elif eff_pub and col_pub and not _publishers_match(col_pub, eff_pub):
                should_clear_coll = True
            elif not eff_pub and resolved_pub and col_pub and not _publishers_match(col_pub, resolved_pub):
                should_clear_coll = True

        return resolved_pub, resolved_coll, should_clear_coll

    def _apply_curated_profile(self, sug: Suggestion) -> Suggestion:
        clean_t = _clean_album_title_suffixes(sug.title) if sug.category != "Libri" else sug.title
        key = f"{search_fold(clean_t)}|{search_fold(sug.author)}"
        alt_t = re.sub(r"\s*\(live\)\s*$", "", clean_t, flags=re.IGNORECASE).strip()
        alt_key = f"{search_fold(alt_t)}|{search_fold(sug.author)}"
        prof = CURATED_EDITION_PROFILES.get(key) or CURATED_EDITION_PROFILES.get(alt_key)
        if not prof:
            if sug.publisher and not sug.collections:
                sug.collections = self.collections_for_publisher(sug.publisher, sug.category, sug.author)
            return sug
        if prof.get("artist_sort") or prof.get("author_sort"):
            sug.author_sort = prof.get("artist_sort") or prof.get("author_sort") or sug.author_sort
        cat_prof = prof.get(sug.category)
        if cat_prof:
            prof_pubs = list(cat_prof.get("publishers") or [])
            prof_yrs = list(cat_prof.get("years") or [])
            if sug.from_isbn or sug.source == "archive":
                # Se proviene da un codice ISBN/EAN/UPC specifico o dall'archivio utente, preserva
                # l'etichetta/editore e l'anno reali come principali!
                merged_p = ([sug.publisher] if sug.publisher else []) + list(sug.publishers or []) + prof_pubs
                merged_y = ([sug.year] if sug.year else []) + list(sug.years or []) + prof_yrs
                sug.publishers = list(dict.fromkeys([p for p in merged_p if p]))
                sug.years = list(dict.fromkeys([y for y in merged_y if y]))
                sug.publisher = sug.publisher or (sug.publishers[0] if sug.publishers else "")
                sug.year = sug.year or (sug.years[0] if sug.years else "")
            else:
                sug.publishers = prof_pubs
                sug.years = prof_yrs
                sug.publisher = cat_prof.get("default_publisher") or (sug.publishers[0] if sug.publishers else sug.publisher)
                sug.year = cat_prof.get("default_year") or (sug.years[0] if sug.years else sug.year)
            for p in sug.publishers[:3]:
                for c in self.collections_for_publisher(p, sug.category, sug.author):
                    if c not in sug.collections:
                        sug.collections.append(c)
        return sug

    def _merge_suggestion(self, results: Dict[str, Suggestion], sug: Suggestion) -> None:
        sug = self._apply_curated_profile(sug)
        st = search_fold(sug.title)
        sa = search_fold(sug.author)
        if not st or not sa:
            return
        dedup = f"{st}|{sa}"
        existing = results.get(dedup)
        if not existing and sug.from_isbn:
            # Se più motori ISBN (es. SBN, IBS, Hoepli, Amazon) restituiscono lo stesso libro
            # con lievi varianti nel nome autore ('Matsuo Basho' vs 'Bashō', oppure coautori su Amazon)
            # o sottotitolo ('Ediz. illustrata'), uniamoli in un'unica scheda completa!
            for k_ex, ex in results.items():
                if not ex.from_isbn:
                    continue
                ex_t = search_fold(ex.title)
                ex_a = search_fold(ex.author)
                same_title = (st == ex_t) or (
                    min(len(st), len(ex_t)) >= 8 and (st.startswith(ex_t) or ex_t.startswith(st))
                )
                same_author = (
                    sa == ex_a
                    or _author_matches(sa, ex_a)
                    or _author_matches(ex_a, sa)
                    or bool(set(w for w in sa.split() if len(w) >= 4) & set(w for w in ex_a.split() if len(w) >= 4))
                )
                if (st == ex_t) or (same_title and same_author) or (
                    same_author and max(sug.score, ex.score) >= 9400.0 and min(sug.score, ex.score) < 9100.0
                ):
                    existing = ex
                    break
        if not existing:
            results[dedup] = sug
            return
        # Unisci editori, anni, collane, work_id e mantieni il punteggio migliore
        for p in sug.publishers:
            if p and p not in existing.publishers:
                existing.publishers.append(p)
        if sug.publisher and sug.publisher not in existing.publishers:
            existing.publishers.insert(0, sug.publisher)
        if not existing.publisher and sug.publisher:
            existing.publisher = sug.publisher

        for y in sug.years:
            if y and y not in existing.years:
                existing.years.append(y)
        if sug.year and sug.year not in existing.years:
            existing.years.insert(0, sug.year)
        if not existing.year and sug.year:
            existing.year = sug.year

        for c in sug.collections:
            if c and c not in existing.collections:
                existing.collections.append(c)
        if sug.collection and sug.collection not in existing.collections:
            existing.collections.insert(0, sug.collection)
        if not existing.collection and sug.collection:
            existing.collection = sug.collection
        is_better = (sug.score > existing.score) or (sug.from_isbn and not existing.from_isbn)
        if sug.from_isbn:
            existing.from_isbn = True
            if sug.collection and (is_better or not existing.collection):
                existing.collection = sug.collection
            if sug.publisher and (is_better or not existing.publisher):
                existing.publisher = sug.publisher
            if sug.year and (is_better or not existing.year):
                existing.year = sug.year
            # Se uno dei due motori ha tutti i coautori (es. 'Gianfranco Coppola, Marino Niola'), preserviamoli
            if "," in sug.author and "," not in existing.author and search_fold(existing.author) in search_fold(sug.author):
                existing.author = sug.author

        if not existing.work_id and sug.work_id:
            existing.work_id = sug.work_id
        if sug.score > existing.score:
            existing.score = sug.score
            existing.title = sug.title
            if not ("," in existing.author and "," not in sug.author and search_fold(sug.author) in search_fold(existing.author)):
                existing.author = sug.author
            if sug.author_sort:
                existing.author_sort = sug.author_sort

    def _cached_editions_map(self, category: str, mbids: List[str]) -> Dict[str, dict]:
        """Recupera in un'unica query SQLite le edizioni già in cache per un gruppo di MBID (v10)."""
        ids = [m for m in dict.fromkeys(mbids) if m]
        if not ids:
            return {}
        found = self.cache.get_many(
            [f"editions:v10:{category}:{m}" for m in ids]
        )
        out: Dict[str, dict] = {}
        for m in ids:
            ed = found.get(f"editions:v10:{category}:{m}")
            if ed:
                out[m] = ed
        return out

    # ------------------------------------------------------------------
    # 1. RICERCA LOCALE ISTANTANEA (Archivio + Seed + Cache + RapidFuzz), eseguita in un thread dedicato
    # ------------------------------------------------------------------
    def search_local(
        self,
        category: str,
        query: str,
        archive_items: List[Item],
        lang: str = "it",
        field_mode: str = "title",
        limit: int = 25,
        context_author: str = "",
        context_title: str = "",
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> List[Suggestion]:
        """
        Ricerca locale istantanea. Può essere interrotta cooperativamente (`is_cancelled`): in tal caso
        restituisce [] (usato dal thread in background quando l'utente ha già digitato un altro carattere).
        """
        q_norm = search_fold(query)
        ctx_author_norm = search_fold(context_author) if field_mode == "title" else ""
        ctx_title_norm = search_fold(context_title) if field_mode == "author" else ""

        def _stop(n: int) -> bool:
            return bool(is_cancelled) and (n & 255) == 0 and bool(is_cancelled())

        def _top(scored: List[Tuple[float, int]]) -> List[Tuple[int, float]]:
            """
            Tiene solo i migliori candidati (prima di costruire e fondere le Suggestion, operazione costosa)
            restituendo coppie (indice, punteggio) nell'ordine d'origine.
            """
            if len(scored) > _LOCAL_PRUNE:
                scored = heapq.nlargest(_LOCAL_PRUNE, scored, key=lambda t: (t[0], -t[1]))
            return sorted(((i, sc) for sc, i in scored), key=lambda t: t[0])

        # Se l'utente sta modificando il Titolo e ha già un Artista/Autore valido nel campo
        # contestuale, permettiamo anche query di 0 o 1 carattere (es. svuotare il titolo o scrivere 't' / 'the')
        has_author_ctx = len(ctx_author_norm) >= 2
        if not has_author_ctx and len(q_norm) < 2:
            return []

        q_tokens = q_norm.split() if q_norm else []
        core_q_norm, core_tokens_t, hint_pubs, hint_yr = _extract_query_hints(q_norm, category, lang)
        core_tokens = list(core_tokens_t) if core_tokens_t else q_tokens
        self.seed.ensure_loaded()
        pref_lang = "ita" if lang == "it" else "eng"

        # ------------------------------------------------------------------
        # MODALITÀ CONTESTUALE: L'Artista/Autore è già fissato e l'utente modifica il Titolo
        # ------------------------------------------------------------------
        if has_author_ctx and field_mode == "title":
            scoped_results: Dict[str, Suggestion] = {}

            # A1) Archivio utente filtrato per autore/artista
            scored_a: List[Tuple[float, int]] = []
            for n_it, it in enumerate(archive_items):
                if _stop(n_it):
                    return []
                st = search_fold(it.title)
                sa = search_fold(it.author)
                if not _author_matches(sa, ctx_author_norm):
                    continue
                sc = _score_title_within_author(
                    q_norm,
                    q_tokens,
                    st,
                    popularity=500,
                    is_archive=True,
                    publishers=(it.publisher,) if it.publisher else (),
                    years=(it.year,) if it.year else (),
                    hint_publishers=hint_pubs,
                    hint_year=hint_yr,
                    category=category,
                )
                if sc < 0:
                    continue
                scored_a.append((sc, n_it))
            for n_it, sc in _top(scored_a):
                it = archive_items[n_it]
                self._merge_suggestion(
                    scoped_results,
                    Suggestion(
                        title=it.title,
                        author=it.author,
                        year=it.year,
                        publisher=it.publisher,
                        author_sort=it.author_sort or surname_key(it.author, category),
                        category=category,
                        source="archive",
                        popularity=500,
                        publishers=[it.publisher] if it.publisher else [],
                        years=[it.year] if it.year else [],
                        score=sc,
                    ),
                )

            # B1) Seed Index filtrato per autore/artista
            ctx_tokens = ctx_author_norm.split()
            cand_set = set()
            token_map = self.seed._book_token_map if category == "Libri" else self.seed._music_token_map
            for ct in ctx_tokens:
                if len(ct) >= 3:
                    cand_set.update(token_map.get(ct[:3], []))
                if len(ct) >= 2:
                    cand_set.update(token_map.get(ct[:2], []))
            if not cand_set:
                cand_set = set(range(len(self.seed.books if category == "Libri" else self.seed.music)))

            if category == "Libri":
                scored_b: List[Tuple[float, int]] = []
                for n_it, idx in enumerate(cand_set):
                    if _stop(n_it):
                        return []
                    row = self.seed.books[idx]
                    st, sa = row[8], row[9]
                    if not _author_matches(sa, ctx_author_norm):
                        continue
                    c_pub = _clean_publisher_name(row[5], lang) if row[5] else ""
                    c_yrs = tuple(y for y in (row[6], row[2]) if y)
                    sc = _score_title_within_author(
                        q_norm,
                        q_tokens,
                        st,
                        popularity=row[3],
                        lang_match=(row[7] == pref_lang),
                        is_catalog=True,
                        publishers=(c_pub,) if c_pub else (),
                        years=c_yrs,
                        hint_publishers=hint_pubs,
                        hint_year=hint_yr,
                        category="Libri",
                    )
                    if sc < 0:
                        continue
                    scored_b.append((sc, idx))
                for idx, sc in _top(scored_b):
                    title, author, first_yr, pop, wkey, pub, ed_yr, bk_lang, st, sa = self.seed.books[idx]
                    clean_pub = _clean_publisher_name(pub, lang)
                    best_yr = ed_yr or first_yr
                    yrs = [y for y in (ed_yr, first_yr) if y]
                    self._merge_suggestion(
                        scoped_results,
                        Suggestion(
                            title=title,
                            author=author,
                            year=best_yr,
                            publisher=clean_pub,
                            author_sort=surname_key(author, "Libri"),
                            work_id=wkey,
                            category="Libri",
                            source="catalog",
                            popularity=pop,
                            publishers=[clean_pub] if clean_pub else [],
                            years=list(dict.fromkeys(yrs)),
                            score=sc,
                        ),
                    )
            else:
                scored_m: List[Tuple[float, int]] = []
                for n_it, idx in enumerate(cand_set):
                    if _stop(n_it):
                        return []
                    row = self.seed.music[idx]
                    st, sa = row[5], row[6]
                    if not _author_matches(sa, ctx_author_norm):
                        continue
                    sc = _score_title_within_author(
                        q_norm,
                        q_tokens,
                        st,
                        popularity=row[3],
                        is_catalog=True,
                        years=(row[2],) if row[2] else (),
                        hint_publishers=hint_pubs,
                        hint_year=hint_yr,
                        category=category,
                    )
                    if sc < 0:
                        continue
                    scored_m.append((sc, idx))
                kept_m = _top(scored_m)
                ed_cache_m = self._cached_editions_map(category, [self.seed.music[i][4] for i, _ in kept_m])
                for idx, sc in kept_m:
                    title, artist, yr, pop, mbid, st, sa = self.seed.music[idx]
                    sug = Suggestion(
                        title=title,
                        author=artist,
                        year=yr,
                        publisher="",
                        author_sort=surname_key(artist, category),
                        work_id=mbid,
                        category=category,
                        source="catalog",
                        popularity=pop,
                        publishers=[],
                        years=[yr] if yr else [],
                        score=sc,
                    )
                    cached_ed = ed_cache_m.get(mbid) if mbid else None
                    if cached_ed:
                        sug.publishers = cached_ed.get("publishers") or sug.publishers
                        sug.years = cached_ed.get("years") or sug.years
                        sug.publisher = cached_ed.get("default_publisher") or sug.publisher
                        sug.year = cached_ed.get("default_year") or sug.year
                        sug.author_sort = cached_ed.get("author_sort") or sug.author_sort
                    self._merge_suggestion(scoped_results, sug)

            # C1) Cache SQLite contestuale (e cache autore)
            for ck in (
                f"search:v11:{category}:{lang}:title:{ctx_author_norm}:{q_norm}",
                f"search:v11:{category}:{lang}:author::{ctx_author_norm}",
                f"search:v6:{category}:{lang}:title:{ctx_author_norm}:{q_norm}",
                f"search:v5:{category}:{lang}:title:{ctx_author_norm}:{q_norm}",
                f"search:v6:{category}:{lang}:author::{ctx_author_norm}",
                f"search:v5:{category}:{lang}:author::{ctx_author_norm}",
            ):
                cached_s = self.cache.get(ck)
                if cached_s and isinstance(cached_s.get("items"), list):
                    for row in cached_s["items"]:
                        st = search_fold(row.get("title", ""))
                        sa = search_fold(row.get("author", ""))
                        if not _author_matches(sa, ctx_author_norm):
                            continue
                        r_pubs = tuple(row.get("publishers") or ([row["publisher"]] if row.get("publisher") else []))
                        r_yrs = tuple(row.get("years") or ([row["year"]] if row.get("year") else []))
                        sc = _score_title_within_author(
                            q_norm,
                            q_tokens,
                            st,
                            popularity=int(row.get("popularity") or 100),
                            publishers=r_pubs,
                            years=r_yrs,
                            hint_publishers=hint_pubs,
                            hint_year=hint_yr,
                            category=category,
                        )
                        if sc < 0:
                            continue
                        self._merge_suggestion(
                            scoped_results,
                            Suggestion(
                                title=row.get("title", ""),
                                author=row.get("author", ""),
                                year=row.get("year", ""),
                                publisher=row.get("publisher", ""),
                                author_sort=row.get("author_sort") or surname_key(row.get("author", ""), category),
                                work_id=row.get("work_id", ""),
                                category=category,
                                source="online",
                                popularity=int(row.get("popularity") or 100),
                                publishers=list(r_pubs),
                                years=list(r_yrs),
                                score=sc,
                            ),
                        )

            # C2) Cache SQLite generale per token autore/titolo (copre ricerche con maiuscole o senza autore fisso)
            eff_q_sub = [t for t in core_tokens if t not in SEARCH_STOPWORDS and len(t) >= 2] or core_tokens
            token_q = (ctx_tokens[:2] + eff_q_sub[:2]) if eff_q_sub else ctx_tokens[:2]
            if token_q:
                for row in self.cache.search_cached_items(category, token_q, limit=40):
                    st = search_fold(row.get("title", ""))
                    sa = search_fold(row.get("author", ""))
                    if not _author_matches(sa, ctx_author_norm):
                        continue
                    r_pubs = tuple(row.get("publishers") or ([row["publisher"]] if row.get("publisher") else []))
                    r_yrs = tuple(row.get("years") or ([row["year"]] if row.get("year") else []))
                    sc = _score_title_within_author(
                        q_norm,
                        q_tokens,
                        st,
                        popularity=int(row.get("popularity") or 100),
                        publishers=r_pubs,
                        years=r_yrs,
                        hint_publishers=hint_pubs,
                        hint_year=hint_yr,
                        category=category,
                    )
                    if sc < 0:
                        continue
                    self._merge_suggestion(
                        scoped_results,
                        Suggestion(
                            title=row.get("title", ""),
                            author=row.get("author", ""),
                            year=row.get("year", ""),
                            publisher=row.get("publisher", ""),
                            author_sort=row.get("author_sort") or surname_key(row.get("author", ""), category),
                            work_id=row.get("work_id", ""),
                            category=category,
                            source="online",
                            popularity=int(row.get("popularity") or 100),
                            publishers=list(r_pubs),
                            years=list(r_yrs),
                            score=sc,
                        ),
                    )

            return sorted(scoped_results.values(), key=lambda s: -s.score)[:limit]

        # ------------------------------------------------------------------
        # MODALITÀ STANDARD (O FALLBACK SE L'AUTORE NON HA OPERE CON QUEL TITOLO)
        # ------------------------------------------------------------------
        if len(q_norm) < 2 or not q_tokens:
            return []

        results: Dict[str, Suggestion] = {}
        isbn_code = _is_isbn_or_barcode(query)
        isbn_variants = set(_barcode_variants(isbn_code)) if isbn_code else set()

        # A) Archivio utente (se la query è un ISBN/barcode, confronta solo il codice esatto, mai il titolo!)
        scored_arch: List[Tuple[float, int]] = []
        for n_it, it in enumerate(archive_items):
            if _stop(n_it):
                return []
            if isbn_code:
                it_code = _is_isbn_or_barcode(it.isbn_or_barcode)
                if not it_code or it_code not in isbn_variants:
                    continue
                sc = 9800.0
            else:
                st = search_fold(it.title)
                sa = search_fold(it.author)
                sc = score_suggestion(
                    q_norm,
                    q_tokens,
                    st,
                    sa,
                    popularity=500,
                    field_mode=field_mode,
                    is_archive=True,
                    allow_fuzzy=True,
                    hint_publishers=hint_pubs,
                    hint_year=hint_yr,
                    cand_publishers=(it.publisher,) if it.publisher else (),
                    cand_years=(it.year,) if it.year else (),
                    category=category,
                )
                if sc < 0:
                    continue
                if ctx_title_norm and (st == ctx_title_norm or ctx_title_norm in st):
                    sc += 800.0
            scored_arch.append((sc, n_it))
        for n_it, sc in _top(scored_arch):
            it = archive_items[n_it]
            self._merge_suggestion(
                results,
                Suggestion(
                    title=it.title,
                    author=it.author,
                    year=it.year,
                    publisher=it.publisher,
                    author_sort=it.author_sort or surname_key(it.author, category),
                    category=category,
                    source="archive",
                    popularity=500,
                    publishers=[it.publisher] if it.publisher else [],
                    years=[it.year] if it.year else [],
                    collection=it.collection,
                    from_isbn=bool(isbn_code),
                    score=sc,
                ),
            )

        # B) Seed Index offline: SOLO per ricerche testuali (mai per codici ISBN/EAN,
        #    altrimenti le cifre del codice a barre farebbero match con titoli numerici come '1979' o '1984'!)
        cand_set = set()
        if not isbn_code:
            token_map = self.seed._book_token_map if category == "Libri" else self.seed._music_token_map
            sub_q = [qt for qt in core_tokens if qt not in SEARCH_STOPWORDS and len(qt) >= 2]
            tokens_to_index = sub_q if sub_q else core_tokens
            for qt in tokens_to_index:
                if len(qt) >= 3:
                    cand_set.update(token_map.get(qt[:3], []))
                if len(qt) >= 2:
                    cand_set.update(token_map.get(qt[:2], []))

        if not isbn_code and category == "Libri":
            cand_indices = cand_set if cand_set else (range(len(self.seed.books)) if len(q_norm) >= 3 else [])
            scored_bk: List[Tuple[float, int]] = []
            for n_it, idx in enumerate(cand_indices):
                if _stop(n_it):
                    return []
                row = self.seed.books[idx]
                st = row[8]
                c_pub = _clean_publisher_name(row[5], lang) if row[5] else ""
                c_yrs = tuple(y for y in (row[6], row[2]) if y)
                sc = score_suggestion(
                    q_norm,
                    q_tokens,
                    st,
                    row[9],
                    popularity=row[3],
                    field_mode=field_mode,
                    lang_match=(row[7] == pref_lang),
                    allow_fuzzy=True,
                    is_catalog=True,
                    hint_publishers=hint_pubs,
                    hint_year=hint_yr,
                    cand_publishers=(c_pub,) if c_pub else (),
                    cand_years=c_yrs,
                    category="Libri",
                )
                if sc < 0:
                    continue
                if ctx_title_norm and (st == ctx_title_norm or ctx_title_norm in st):
                    sc += 800.0
                scored_bk.append((sc, idx))
            for idx, sc in _top(scored_bk):
                title, author, first_yr, pop, wkey, pub, ed_yr, bk_lang, st, sa = self.seed.books[idx]
                clean_pub = _clean_publisher_name(pub, lang)
                best_yr = ed_yr or first_yr
                yrs = [y for y in (ed_yr, first_yr) if y]
                self._merge_suggestion(
                    results,
                    Suggestion(
                        title=title,
                        author=author,
                        year=best_yr,
                        publisher=clean_pub,
                        author_sort=surname_key(author, "Libri"),
                        work_id=wkey,
                        category="Libri",
                        source="catalog",
                        popularity=pop,
                        publishers=[clean_pub] if clean_pub else [],
                        years=list(dict.fromkeys(yrs)),
                        score=sc,
                    ),
                )
        elif not isbn_code:
            cand_indices = cand_set if cand_set else (range(len(self.seed.music)) if len(q_norm) >= 3 else [])
            scored_mu: List[Tuple[float, int]] = []
            for n_it, idx in enumerate(cand_indices):
                if _stop(n_it):
                    return []
                row = self.seed.music[idx]
                st = row[5]
                sc = score_suggestion(
                    q_norm,
                    q_tokens,
                    st,
                    row[6],
                    popularity=row[3],
                    field_mode=field_mode,
                    allow_fuzzy=True,
                    is_catalog=True,
                    hint_publishers=hint_pubs,
                    hint_year=hint_yr,
                    cand_years=(row[2],) if row[2] else (),
                    category=category,
                )
                if sc < 0:
                    continue
                if ctx_title_norm and (st == ctx_title_norm or ctx_title_norm in st):
                    sc += 800.0
                scored_mu.append((sc, idx))
            kept_mu = _top(scored_mu)
            ed_cache = self._cached_editions_map(category, [self.seed.music[i][4] for i, _ in kept_mu])
            for idx, sc in kept_mu:
                title, artist, yr, pop, mbid, st, sa = self.seed.music[idx]
                sug = Suggestion(
                    title=title,
                    author=artist,
                    year=yr,
                    publisher="",
                    author_sort=surname_key(artist, category),
                    work_id=mbid,
                    category=category,
                    source="catalog",
                    popularity=pop,
                    publishers=[],
                    years=[yr] if yr else [],
                    score=sc,
                )
                cached_ed = ed_cache.get(mbid) if mbid else None
                if cached_ed:
                    sug.publishers = cached_ed.get("publishers") or sug.publishers
                    sug.years = cached_ed.get("years") or sug.years
                    sug.publisher = cached_ed.get("default_publisher") or sug.publisher
                    sug.year = cached_ed.get("default_year") or sug.year
                    sug.author_sort = cached_ed.get("author_sort") or sug.author_sort
                self._merge_suggestion(results, sug)

        # C) Cache SQLite per questa query
        for ck in (
            f"search:v11:{category}:{lang}:{field_mode}:{ctx_author_norm}:{q_norm}",
            f"search:v10:{category}:{lang}:{field_mode}:{ctx_author_norm}:{q_norm}",
        ):
            cached_search = self.cache.get(ck)
            if cached_search and isinstance(cached_search.get("items"), list):
                for row in cached_search["items"]:
                    st = search_fold(row.get("title", ""))
                    sa = search_fold(row.get("author", ""))
                    r_pubs = tuple(row.get("publishers") or ([row["publisher"]] if row.get("publisher") else []))
                    r_yrs = tuple(row.get("years") or ([row["year"]] if row.get("year") else []))
                    if isbn_code or row.get("from_isbn"):
                        sc = float(row.get("score") or 9000.0)
                    else:
                        sc = score_suggestion(
                            q_norm,
                            q_tokens,
                            st,
                            sa,
                            popularity=int(row.get("popularity") or 100),
                            field_mode=field_mode,
                            allow_fuzzy=True,
                            hint_publishers=hint_pubs,
                            hint_year=hint_yr,
                            cand_publishers=r_pubs,
                            cand_years=r_yrs,
                            category=category,
                        )
                    if sc < 0:
                        continue
                    self._merge_suggestion(
                        results,
                        Suggestion(
                            title=row.get("title", ""),
                            author=row.get("author", ""),
                            year=row.get("year", ""),
                            publisher=row.get("publisher", ""),
                            author_sort=row.get("author_sort") or surname_key(row.get("author", ""), category),
                            work_id=row.get("work_id", ""),
                            category=category,
                            source="online",
                            popularity=int(row.get("popularity") or 100),
                            publishers=list(r_pubs),
                            years=list(r_yrs),
                            collection=row.get("collection", ""),
                            collections=row.get("collections") or [],
                            from_isbn=bool(row.get("from_isbn") or isbn_code),
                            score=sc,
                        ),
                    )

        # C2) Ricerca nei risultati recenti memorizzati in cache SQLite per token significativi (<1ms)
        if not isbn_code and len(q_tokens) >= 2:
            cached_items = self.cache.search_cached_items(category, core_tokens, limit=35)
            for row in cached_items:
                st = search_fold(row.get("title", ""))
                sa = search_fold(row.get("author", ""))
                clean_pub = _clean_publisher_name(row.get("publisher", ""), lang) if category == "Libri" else row.get("publisher", "")
                r_pubs = tuple(row.get("publishers") or ([clean_pub] if clean_pub else []))
                r_yrs = tuple(row.get("years") or ([row["year"]] if row.get("year") else []))
                sc = score_suggestion(
                    q_norm,
                    q_tokens,
                    st,
                    sa,
                    popularity=int(row.get("popularity") or 100),
                    field_mode=field_mode,
                    allow_fuzzy=True,
                    hint_publishers=hint_pubs,
                    hint_year=hint_yr,
                    cand_publishers=r_pubs,
                    cand_years=r_yrs,
                    category=category,
                )
                if sc < 0:
                    continue
                self._merge_suggestion(
                    results,
                    Suggestion(
                        title=row.get("title", ""),
                        author=row.get("author", ""),
                        year=row.get("year", ""),
                        publisher=clean_pub,
                        author_sort=row.get("author_sort") or surname_key(row.get("author", ""), category),
                        work_id=row.get("work_id", ""),
                        category=category,
                        source="online",
                        popularity=int(row.get("popularity") or 100),
                        publishers=list(r_pubs),
                        years=list(r_yrs),
                        collection=row.get("collection", ""),
                        collections=row.get("collections") or [],
                        from_isbn=bool(row.get("from_isbn")),
                        score=sc,
                    ),
                )

        # D) Fallback Fuzzy C++ (`RapidFuzz`) sull'intero catalogo se nessun token ha prefisso in comune
        if len(results) < 3 and len(q_norm) >= 4 and not isbn_code and _rf_fuzz is not None:
            if category == "Libri":
                for n_it, (title, author, first_yr, pop, wkey, pub, ed_yr, bk_lang, st, sa) in enumerate(self.seed.books[:2500]):
                    if _stop(n_it):
                        return []
                    clean_pub = _clean_publisher_name(pub, lang)
                    c_yrs = tuple(y for y in (ed_yr, first_yr) if y)
                    sc = score_suggestion(
                        q_norm,
                        q_tokens,
                        st,
                        sa,
                        popularity=pop,
                        field_mode=field_mode,
                        allow_fuzzy=True,
                        is_catalog=True,
                        hint_publishers=hint_pubs,
                        hint_year=hint_yr,
                        cand_publishers=(clean_pub,) if clean_pub else (),
                        cand_years=c_yrs,
                        category="Libri",
                    )
                    if sc > 0:
                        self._merge_suggestion(
                            results,
                            Suggestion(
                                title=title,
                                author=author,
                                year=ed_yr or first_yr,
                                publisher=clean_pub,
                                author_sort=surname_key(author, "Libri"),
                                work_id=wkey,
                                category="Libri",
                                source="catalog",
                                popularity=pop,
                                publishers=[clean_pub] if clean_pub else [],
                                years=[y for y in (ed_yr, first_yr) if y],
                                score=sc,
                            ),
                        )
            else:
                for n_it, (title, artist, yr, pop, mbid, st, sa) in enumerate(self.seed.music[:2500]):
                    if _stop(n_it):
                        return []
                    sc = score_suggestion(
                        q_norm,
                        q_tokens,
                        st,
                        sa,
                        popularity=pop,
                        field_mode=field_mode,
                        allow_fuzzy=True,
                        is_catalog=True,
                        hint_publishers=hint_pubs,
                        hint_year=hint_yr,
                        cand_years=(yr,) if yr else (),
                        category=category,
                    )
                    if sc > 0:
                        self._merge_suggestion(
                            results,
                            Suggestion(
                                title=title,
                                author=artist,
                                year=yr,
                                publisher="",
                                author_sort=surname_key(artist, category),
                                work_id=mbid,
                                category=category,
                                source="catalog",
                                popularity=pop,
                                years=[yr] if yr else [],
                                score=sc,
                            ),
                        )

        return sorted(results.values(), key=lambda s: -s.score)[:limit]

    # ------------------------------------------------------------------
    # 2. RICERCA ONLINE MULTI-MOTORE IN PARALLELO
    # ------------------------------------------------------------------
    def search_online(
        self,
        category: str,
        query: str,
        lang: str = "it",
        field_mode: str = "title",
        limit: int = 25,
        is_cancelled: Optional[Callable[[], bool]] = None,
        context_author: str = "",
        context_title: str = "",
        on_chunk: Optional[Callable[[List[Suggestion]], None]] = None,
    ) -> List[Suggestion]:
        q_norm = search_fold(query)
        ctx_author_norm = search_fold(context_author) if field_mode == "title" else ""
        if not ctx_author_norm and len(q_norm) < 2:
            return []

        core_q_norm, core_tokens_t, hint_pubs, hint_yr = _extract_query_hints(q_norm, category, lang)
        cache_key = f"search:v11:{category}:{lang}:{field_mode}:{ctx_author_norm}:{q_norm}"
        cached = self.cache.get(cache_key)
        if cached and isinstance(cached.get("items"), list):
            q_tokens_cache = q_norm.split()
            is_code_query = bool(_is_isbn_or_barcode(query))
            out = []
            for row in cached["items"]:
                r_pubs = tuple(row.get("publishers") or ([row["publisher"]] if row.get("publisher") else []))
                r_yrs = tuple(row.get("years") or ([row["year"]] if row.get("year") else []))
                if not is_code_query and not bool(row.get("from_isbn")):
                    st = search_fold(row.get("title", ""))
                    sa = search_fold(row.get("author", ""))
                    pop_val = int(row.get("popularity") or 100)
                    if ctx_author_norm:
                        if not _author_matches(sa, ctx_author_norm):
                            continue
                        chk = _score_title_within_author(
                            q_norm,
                            q_tokens_cache,
                            st,
                            popularity=pop_val,
                            publishers=r_pubs,
                            years=r_yrs,
                            hint_publishers=hint_pubs,
                            hint_year=hint_yr,
                            category=category,
                        )
                    else:
                        chk = score_suggestion(
                            q_norm,
                            q_tokens_cache,
                            st,
                            sa,
                            popularity=pop_val,
                            field_mode=field_mode,
                            allow_fuzzy=True,
                            hint_publishers=hint_pubs,
                            hint_year=hint_yr,
                            cand_publishers=r_pubs,
                            cand_years=r_yrs,
                            category=category,
                        )
                    if chk < 0:
                        continue
                sug = Suggestion(
                    title=row.get("title", ""),
                    author=row.get("author", ""),
                    year=row.get("year", ""),
                    publisher=row.get("publisher", ""),
                    author_sort=row.get("author_sort") or surname_key(row.get("author", ""), category),
                    work_id=row.get("work_id", ""),
                    category=category,
                    source="online",
                    popularity=int(row.get("popularity") or 100),
                    publishers=row.get("publishers") or [],
                    years=row.get("years") or [],
                    collection=row.get("collection", ""),
                    collections=row.get("collections") or [],
                    from_isbn=bool(row.get("from_isbn")),
                    score=float(row.get("score") or 0.0),
                )
                out.append(self._apply_curated_profile(sug))
            if out or not cached["items"]:
                res = out[:limit]
                if on_chunk and not (is_cancelled and is_cancelled()):
                    on_chunk(res)
                return res

        if is_cancelled and is_cancelled():
            return []

        if category == "Libri":
            items = self._search_books_parallel(
                query, q_norm, lang, field_mode, limit, is_cancelled, context_author, ctx_author_norm, on_chunk=on_chunk
            )
        else:
            items = self._search_music_parallel(
                category, query, q_norm, field_mode, limit, is_cancelled, context_author, ctx_author_norm, on_chunk=on_chunk
            )

        # Fallback online rilassato: se il titolo era interrotto con una parola extra non riconosciuta,
        # riprova una sola volta eliminando l'ultima parola (mantenendo lo scoring sulla query originale).
        if not items and not _is_isbn_or_barcode(query) and not (is_cancelled and is_cancelled()):
            base_toks = list(core_tokens_t) if core_tokens_t else q_norm.split()
            relaxed_query: Optional[str] = None
            if len(base_toks) >= 2:
                relaxed_query = " ".join(base_toks[:-1])
            elif ctx_author_norm and len(base_toks) == 1:
                relaxed_query = ""
            if relaxed_query is not None:
                if category == "Libri":
                    items = self._search_books_parallel(
                        relaxed_query,
                        q_norm,
                        lang,
                        field_mode,
                        limit,
                        is_cancelled,
                        context_author,
                        ctx_author_norm,
                        on_chunk=on_chunk,
                    )
                else:
                    items = self._search_music_parallel(
                        category,
                        relaxed_query,
                        q_norm,
                        field_mode,
                        limit,
                        is_cancelled,
                        context_author,
                        ctx_author_norm,
                        on_chunk=on_chunk,
                    )

        if items and not (is_cancelled and is_cancelled()):
            serializable = [
                {
                    "title": s.title,
                    "author": s.author,
                    "year": s.year,
                    "publisher": s.publisher,
                    "author_sort": s.author_sort,
                    "work_id": s.work_id,
                    "popularity": s.popularity,
                    "publishers": s.publishers,
                    "years": s.years,
                    "collection": s.collection,
                    "collections": s.collections,
                    "from_isbn": s.from_isbn,
                    "score": s.score,
                }
                for s in items
            ]
            self.cache.set(cache_key, {"items": serializable})
        return items

    # ------------------------------------------------------------------
    # MOTORI LIBRI IN PARALLELO: GOOGLE BOOKS + OPAC SBN ITALIA + OPEN LIBRARY
    # ------------------------------------------------------------------
    def _search_books_parallel(
        self,
        query: str,
        q_norm: str,
        lang: str,
        field_mode: str,
        limit: int,
        is_cancelled: Optional[Callable[[], bool]],
        context_author: str = "",
        ctx_author_norm: str = "",
        on_chunk: Optional[Callable[[List[Suggestion]], None]] = None,
    ) -> List[Suggestion]:
        results: Dict[str, Suggestion] = {}
        isbn_code = _is_isbn_or_barcode(query)
        effective_query = query
        if not isbn_code and q_norm and search_fold(query) == q_norm:
            core_q_norm, core_tokens_t, _hp, _hy = _extract_query_hints(q_norm, "Libri", lang)
            if core_q_norm and core_q_norm != q_norm and query.strip():
                # Se `query` coincide con `q_norm`, usa i soli token core per i motori remoti
                effective_query = " ".join(core_tokens_t) if core_tokens_t else core_q_norm

        fut_gb = self._search_pool.submit(
            self._search_google_books, effective_query, q_norm, lang, field_mode, is_cancelled, context_author, ctx_author_norm
        )
        fut_sbn = self._search_pool.submit(
            self._search_opac_sbn, effective_query, q_norm, lang, field_mode, is_cancelled, context_author, ctx_author_norm
        )
        fut_ibs = self._search_pool.submit(
            self._search_ibs_text, effective_query, q_norm, lang, field_mode, is_cancelled, context_author, ctx_author_norm
        )
        fut_ol = self._search_pool.submit(
            self._search_openlibrary, effective_query, q_norm, lang, field_mode, limit, is_cancelled, context_author, ctx_author_norm
        )
        primary = {fut_sbn}
        pending = {fut_gb, fut_sbn, fut_ibs, fut_ol}
        if isbn_code:
            fut_hp = self._search_pool.submit(self._search_hoepli_isbn, isbn_code, lang, is_cancelled)
            fut_ibs_isbn = self._search_pool.submit(self._search_ibs_isbn, isbn_code, "Libri", lang, is_cancelled)
            fut_amz = self._search_pool.submit(self._search_amazon_isbn, isbn_code, "Libri", lang, is_cancelled)
            primary.update({fut_hp, fut_ibs_isbn, fut_amz})
            pending.update({fut_hp, fut_ibs_isbn, fut_amz})

        sbn_had_hits = False
        deadline = time.monotonic() + 2.5
        while pending:
            if is_cancelled and is_cancelled():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            done, pending = wait(pending, timeout=min(0.12, remaining), return_when=FIRST_COMPLETED)
            if not done:
                continue
            new_sugs = False
            for fut in done:
                try:
                    res_list = fut.result() or []
                    if fut is fut_sbn and res_list:
                        sbn_had_hits = True
                    for sug in res_list:
                        self._merge_suggestion(results, sug)
                        new_sugs = True
                except Exception:
                    pass

            if new_sugs and on_chunk and not (is_cancelled and is_cancelled()):
                on_chunk(sorted(results.values(), key=lambda s: -s.score)[:limit])

            # Concede fino a 0.55s extra ai motori secondari (Open Library per work_id edizioni, IBS, ecc.)
            # invece di tagliarli dopo soli 120ms quando SBN fallisce velocemente.
            ready_to_finish = (
                not (primary & pending)
                and (bool(isbn_code) or sbn_had_hits or fut_ibs not in pending)
                and len(results) >= 1
                and pending
            )
            if ready_to_finish:
                extra_wait = min(0.55, max(0.0, deadline - time.monotonic()))
                if extra_wait > 0.0:
                    done_extra, pending = wait(pending, timeout=extra_wait)
                    extra_new = False
                    for fut in done_extra:
                        try:
                            for sug in fut.result() or []:
                                self._merge_suggestion(results, sug)
                                extra_new = True
                        except Exception:
                            pass
                    if extra_new and on_chunk and not (is_cancelled and is_cancelled()):
                        on_chunk(sorted(results.values(), key=lambda s: -s.score)[:limit])
                break

        for fut in pending:
            try:
                fut.cancel()
            except Exception:
                pass

        return sorted(results.values(), key=lambda s: -s.score)[:limit]

    def _search_ibs_isbn(
        self,
        isbn_code: str,
        category: str,
        lang: str,
        is_cancelled: Optional[Callable[[], bool]],
    ) -> List[Suggestion]:
        """
        Interroga IBS.it / LaFeltrinelli per codice ISBN/EAN, estraendo il blocco JSON
        strutturato `dataLayer` (che include anche piccoli editori indipendenti italiani 979-12,
        collana, anno di edizione ed editore/etichetta).
        """
        if not isbn_code or (is_cancelled and is_cancelled()):
            return []
        url = f"https://www.ibs.it/search/?ts=as&query={urllib.parse.quote(isbn_code)}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            with urllib.request.urlopen(req, timeout=2.6) as resp:
                raw_html = _read_response_bounded(resp).decode("utf-8", errors="ignore")
        except Exception:
            return []

        if is_cancelled and is_cancelled():
            return []

        import html as _html

        valid_codes = set(_barcode_variants(isbn_code))
        out: List[Suggestion] = []

        # 1) Estrazione dal JSON strutturato dataLayer.push({"ecommerce":...})
        for m_dl in re.finditer(r"dataLayer\.push\((\{\"ecommerce\":.*?\})\)\s*</script>", raw_html, flags=re.DOTALL):
            try:
                dl_obj = json.loads(m_dl.group(1))
                impressions = (dl_obj.get("ecommerce") or {}).get("impression") or []
                for imp_group in impressions:
                    items_list = imp_group if isinstance(imp_group, list) else [imp_group]
                    for it in items_list:
                        if not isinstance(it, dict):
                            continue
                        item_id = re.sub(r"[\s\-]", "", str(it.get("item_id") or "")).strip().upper()
                        if item_id and item_id not in valid_codes:
                            continue
                        raw_title = _html.unescape(str(it.get("item_name") or "")).strip()
                        title = _clean_album_title_suffixes(raw_title) if category != "Libri" else raw_title
                        author = _html.unescape(str(it.get("item_author") or "")).strip()
                        if not title or not author:
                            continue
                        raw_pub = _html.unescape(str(it.get("item_brand") or "")).strip()
                        clean_pub = _clean_publisher_name(raw_pub, lang) if category == "Libri" else raw_pub
                        yr = _extract_year(str(it.get("year_edition") or ""))
                        raw_coll = _html.unescape(str(it.get("item_series") or "")).strip()
                        if raw_coll.lower() in ("null", "none"):
                            raw_coll = ""
                        sug = Suggestion(
                            title=title,
                            author=author,
                            year=yr,
                            publisher=clean_pub,
                            author_sort=surname_key(author, category),
                            work_id="",
                            category=category,
                            source="online",
                            popularity=4800,
                            publishers=[clean_pub] if clean_pub else [],
                            years=[yr] if yr else [],
                            collection=raw_coll,
                            collections=[raw_coll] if raw_coll else [],
                            from_isbn=True,
                            score=9880.0,
                        )
                        out.append(self._apply_curated_profile(sug) if category != "Libri" else sug)
            except Exception:
                pass
        return out

    def _search_ibs_text(
        self,
        query: str,
        q_norm: str,
        lang: str,
        field_mode: str,
        is_cancelled: Optional[Callable[[], bool]],
        context_author: str = "",
        ctx_author_norm: str = "",
    ) -> List[Suggestion]:
        """
        Interroga IBS.it / LaFeltrinelli per titolo / autore, estraendo dal JSON strutturato
        dataLayer le edizioni commerciali italiane reali con anno, editore e collana.
        """
        if is_cancelled and is_cancelled():
            return []
        q_clean = query.strip()
        if context_author.strip() and context_author.strip().lower() not in q_clean.lower():
            search_str = f"{q_clean} {context_author.strip()}".strip()
        else:
            search_str = q_clean
        if len(search_str) < 3:
            return []
        url = f"https://www.ibs.it/search/?ts=as&query={urllib.parse.quote(search_str)}"
        raw_html = ""
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                raw_html = _read_response_bounded(resp).decode("utf-8", errors="ignore")
        except Exception:
            return []

        if is_cancelled and is_cancelled():
            return []

        import html as _html

        out: List[Suggestion] = []
        q_tokens = q_norm.split()
        for m_dl in re.finditer(r"dataLayer\.push\((\{\"ecommerce\":.*?\})\)\s*</script>", raw_html, flags=re.DOTALL):
            try:
                dl_obj = json.loads(m_dl.group(1))
                impressions = (dl_obj.get("ecommerce") or {}).get("impression") or []
                for imp_group in impressions:
                    items_list = imp_group if isinstance(imp_group, list) else [imp_group]
                    for idx, it in enumerate(items_list[:16]):
                        if not isinstance(it, dict):
                            continue
                        cat_str = str(it.get("item_category") or "").lower()
                        if cat_str and "libri" not in cat_str and "ebook" not in cat_str:
                            continue
                        title = _html.unescape(str(it.get("item_name") or "")).strip()
                        author = _html.unescape(str(it.get("item_author") or "")).strip()
                        author = re.sub(r",(?!\s)", ", ", author)
                        if not title or not author or author.lower() in ("none", "null"):
                            continue
                        raw_pub = _html.unescape(str(it.get("item_brand") or "")).strip()
                        clean_pub = _clean_publisher_name(raw_pub, lang)
                        yr = _extract_year(str(it.get("year_edition") or ""))
                        raw_coll = _html.unescape(str(it.get("item_series") or "")).strip()
                        if raw_coll.lower() in ("null", "none"):
                            raw_coll = ""
                        st = search_fold(title)
                        sa = search_fold(author)
                        pop = max(500, (16 - min(idx, 15)) * 260)
                        if ctx_author_norm:
                            if not _author_matches(sa, ctx_author_norm):
                                continue
                            sc = _score_title_within_author(
                                q_norm,
                                q_tokens,
                                st,
                                popularity=pop,
                                lang_match=True,
                                publishers=(clean_pub,) if clean_pub else (),
                                years=(yr,) if yr else (),
                                category="Libri",
                            )
                        else:
                            sc = score_suggestion(
                                q_norm,
                                q_tokens,
                                st,
                                sa,
                                popularity=pop,
                                field_mode=field_mode,
                                lang_match=True,
                                allow_fuzzy=True,
                                cand_publishers=(clean_pub,) if clean_pub else (),
                                cand_years=(yr,) if yr else (),
                                category="Libri",
                            )
                        if sc < 0:
                            continue
                        if (fold(raw_pub) in NON_BOOK_PUBLISHERS_FOLD or fold(clean_pub) in NON_BOOK_PUBLISHERS_FOLD) and not any(
                            w in q_norm for w in ("lecturae", "mustread", "grin", "streetlib")
                        ):
                            sc -= 2500.0
                        out.append(
                            Suggestion(
                                title=title,
                                author=author,
                                year=yr,
                                publisher=clean_pub,
                                author_sort=surname_key(author, "Libri"),
                                work_id="",
                                category="Libri",
                                source="online",
                                popularity=pop,
                                publishers=[clean_pub] if clean_pub else [],
                                years=[yr] if yr else [],
                                collection=raw_coll,
                                collections=[raw_coll] if raw_coll else [],
                                from_isbn=False,
                                score=sc,
                            )
                        )
            except Exception:
                pass
        return out

    def _search_amazon_isbn(
        self,
        isbn_code: str,
        category: str,
        lang: str,
        is_cancelled: Optional[Callable[[], bool]],
    ) -> List[Suggestion]:
        """
        Interroga Amazon.it per codice ISBN / EAN / UPC (fondamentale per libri self-published,
        piccoli editori 979-12, edizioni indipendenti o import disponibili su Amazon).
        """
        if not isbn_code or (is_cancelled and is_cancelled()):
            return []
        url = f"https://www.amazon.it/s?k={urllib.parse.quote(isbn_code)}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                },
            )
            with urllib.request.urlopen(req, timeout=2.6) as resp:
                raw_html = _read_response_bounded(resp).decode("utf-8", errors="ignore")
        except Exception:
            return []

        if is_cancelled and is_cancelled():
            return []
        if "Nessun risultato per" in raw_html or "0 risultati per" in raw_html:
            return []

        import html as _html

        # Considera solo la sezione principale prima di "Altri risultati" o prodotti sponsorizzati non correlati
        main_section = re.split(r"Altri risultati|Risultati correlati| Prodotti sponsorizzati ", raw_html)[0]
        blocks = re.findall(
            r'data-asin="([A-Z0-9]{10})".*?<h2[^>]*>(.*?)</h2>(.*?)(?=data-asin=|$)',
            main_section,
            flags=re.DOTALL,
        )
        out: List[Suggestion] = []
        for idx, (_asin, h2_html, rest_html) in enumerate(blocks[:3]):
            raw_title = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h2_html))).strip()
            title = _clean_album_title_suffixes(raw_title) if category != "Libri" else raw_title
            if not title or title.lower() in ("altri risultati", "cerca"):
                continue
            by_divs = re.findall(
                r'<div class="a-row a-size-base a-color-secondary">(.*?)</div>',
                rest_html,
                flags=re.DOTALL,
            )
            author = ""
            yr = ""
            for b_raw in by_divs:
                b_txt = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", b_raw))).strip()
                if not b_txt or b_txt.lower().startswith(("disponibilità", "consegna", "solo ")):
                    continue
                if not yr:
                    yr = _extract_year(b_txt)
                # Formato tipico Amazon.it: "di Gianfranco Coppola e Marino Niola | 15 mag 2026"
                part_auth = b_txt.split("|")[0].strip()
                if re.match(r"^(?:di|by)\s+", part_auth, flags=re.IGNORECASE):
                    part_auth = re.sub(r"^(?:di|by)\s+", "", part_auth, flags=re.IGNORECASE).strip(" ,;-")
                    part_auth = re.sub(r",?\s*\b(?:e|et)\s+al\.?$", "", part_auth, flags=re.IGNORECASE).strip(" ,;-")
                    part_auth = re.sub(r"\s+e\s+", ", ", part_auth)
                    part_auth = re.sub(r"\s*,\s*", ", ", part_auth).strip(" ,;-")
                    if part_auth and not author:
                        author = part_auth
            if not author:
                continue
            sug = Suggestion(
                title=title,
                author=author,
                year=yr,
                publisher="",
                author_sort=surname_key(author, category),
                work_id="",
                category=category,
                source="online",
                popularity=4200,
                publishers=[],
                years=[yr] if yr else [],
                from_isbn=True,
                score=9820.0 - idx * 30.0,
            )
            out.append(self._apply_curated_profile(sug) if category != "Libri" else sug)
        return out

    def _search_hoepli_isbn(
        self,
        isbn_code: str,
        lang: str,
        is_cancelled: Optional[Callable[[], bool]],
    ) -> List[Suggestion]:
        """
        Interroga il catalogo librario Hoepli per codice ISBN (immunizzato dai blocchi HTTP 429
        di Google Books e aggiornato al giorno d'uscita per tutti i libri in commercio in Italia).
        """
        if not isbn_code or (is_cancelled and is_cancelled()):
            return []
        url = f"https://www.hoepli.it/cerca/libri.aspx?query={urllib.parse.quote(isbn_code)}"
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
                    ),
                    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=2.4) as resp:
                raw_html = _read_response_bounded(resp).decode("utf-8", errors="ignore")
        except Exception:
            return []

        if is_cancelled and is_cancelled():
            return []

        import html as _html

        # Estrai autori dal tag itemprop="author" (su Hoepli sono in formato "cognome nome")
        author_matches = [
            _html.unescape(re.sub(r"<[^>]+>", "", m)).strip()
            for m in re.findall(r'itemprop="author"[^>]*>(.*?)</a>', raw_html, flags=re.IGNORECASE | re.DOTALL)
        ]
        author_matches = [a for a in author_matches if a and len(a) >= 2]
        if not author_matches:
            # Se non c'è autore (es. scheda generica promozionale 'UE Feltrinelli 1+1 (2026)'), lascia priorità a OPAC SBN
            return []

        raw_first_author = author_matches[0].title()
        a_parts = raw_first_author.split()
        if len(a_parts) == 2:
            # Hoepli salva "Orwell George" -> convertiamo in "George Orwell" e "Orwell, George"
            display_author = f"{a_parts[1]} {a_parts[0]}"
            sort_author = f"{a_parts[0]}, {a_parts[1]}"
        else:
            display_author = raw_first_author
            sort_author = surname_key(display_author, "Libri")

        # Estrai titolo dal tag <title> (che conserva le maiuscole/minuscole migliori) o da <h1>
        title_str = ""
        m_title_tag = re.search(r"<title>(.*?)</title>", raw_html, flags=re.IGNORECASE | re.DOTALL)
        if m_title_tag:
            t_full = _html.unescape(m_title_tag.group(1)).strip()
            if " - " in t_full:
                title_str = t_full.split(" - ", 1)[0].strip()
        if not title_str:
            m_h1 = re.search(r'<h1[^>]*class="[^"]*cBlu[^"]*"[^>]*>(.*?)</h1>', raw_html, flags=re.IGNORECASE | re.DOTALL)
            if m_h1:
                title_str = _html.unescape(re.sub(r"<[^>]+>", "", m_h1.group(1))).strip().title()
        if not title_str:
            return []

        # Editore
        raw_pub = ""
        m_pub = re.search(r'Editore:\s*<a[^>]*>.*?<b>([^<]+)</b>', raw_html, flags=re.IGNORECASE | re.DOTALL)
        if m_pub:
            raw_pub = _html.unescape(m_pub.group(1)).strip()
        elif m_title_tag:
            m_p2 = re.search(r"\|\s*Libro\s+(.+?)\s+\d{2}/\d{4}", m_title_tag.group(1), flags=re.IGNORECASE)
            if m_p2:
                raw_pub = _html.unescape(m_p2.group(1)).strip()
        clean_pub = _clean_publisher_name(raw_pub, lang)

        # Anno di pubblicazione
        yr = ""
        m_yr = re.search(r"Pubblicazione:\s*<span[^>]*>([^<]+)</span>", raw_html, flags=re.IGNORECASE)
        if m_yr:
            yr = _extract_year(m_yr.group(1))

        # Collana
        coll = ""
        m_coll = re.search(r"Collana:\s*<span[^>]*>([^<]+)</span>", raw_html, flags=re.IGNORECASE)
        if m_coll:
            raw_c = _html.unescape(m_coll.group(1)).strip(" .,-")
            raw_c = re.sub(r"\.\s*NUOVA\s+SERIE$", "", raw_c, flags=re.IGNORECASE).strip()
            if raw_c:
                coll = raw_c.title()
                for c_known in self.collections_for_publisher(clean_pub, "Libri"):
                    if fold(c_known) == fold(coll):
                        coll = c_known
                        break

        return [
            Suggestion(
                title=title_str,
                author=display_author,
                year=yr,
                publisher=clean_pub,
                author_sort=sort_author,
                work_id="",
                category="Libri",
                source="online",
                popularity=4500,
                publishers=[clean_pub] if clean_pub else [],
                years=[yr] if yr else [],
                collection=coll,
                collections=[coll] if coll else [],
                from_isbn=True,
                score=9850.0,
            )
        ]

    def _search_google_books(
        self,
        query: str,
        q_norm: str,
        lang: str,
        field_mode: str,
        is_cancelled: Optional[Callable[[], bool]],
        context_author: str = "",
        ctx_author_norm: str = "",
    ) -> List[Suggestion]:
        """Interroga Google Books API (~120ms, copertura capillare degli editori italiani)."""
        q_tokens = q_norm.split()
        isbn_code = _is_isbn_or_barcode(query)
        if isbn_code:
            q_expr = f"isbn:{isbn_code}"
        elif field_mode == "author":
            q_expr = f"inauthor:{query.strip()}"
        elif context_author.strip():
            q_expr = f'{query.strip()} inauthor:"{context_author.strip()}"'.strip()
        else:
            q_expr = query.strip()

        lang_restrict = "&langRestrict=it" if (lang == "it" and not isbn_code) else ""
        url = (
            f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(q_expr)}"
            f"{lang_restrict}&printType=books&maxResults=18"
        )
        if time.time() < getattr(self, "_gb_disabled_until", 0.0):
            return []
        data = self._http_get_json(url, timeout=1.8, fast_fail=True, is_cancelled=is_cancelled)
        if not data:
            if self._get_last_http_code() == 429:
                self._gb_disabled_until = time.time() + 300.0
            return []
        if is_cancelled and is_cancelled():
            return []

        out: List[Suggestion] = []
        for idx, item in enumerate(data.get("items") or []):
            vi = item.get("volumeInfo") or {}
            title = str(vi.get("title") or "").strip()
            authors = vi.get("authors") or []
            author = str(authors[0] or "").strip() if authors else ""
            if not title or not author:
                continue
            raw_pub = str(vi.get("publisher") or "").strip()
            clean_pub = _clean_publisher_name(raw_pub, lang)
            yr = _extract_year(vi.get("publishedDate") or "")
            ratings = int(vi.get("ratingsCount") or 0)
            is_major_pub = bool(clean_pub and fold(clean_pub) in ITALIAN_PUBLISHERS_PRIORITY)
            base_pop = (22 - idx) * (350 if is_major_pub else 160) + ratings * 180
            pop = max(400, base_pop)
            is_ita = str(vi.get("language") or "").lower().startswith("it")

            # Estrai eventuale serie/collana da Google Books (seriesInfo o raw_pub se contiene il nome della collana)
            found_colls: List[str] = []
            s_info = vi.get("seriesInfo") or {}
            if isinstance(s_info, dict):
                short_s = str(s_info.get("shortSeriesBookTitle") or "").strip()
                vol_num = str(s_info.get("bookDisplayNumber") or "").strip()
                if short_s:
                    found_colls.append(f"{short_s} #{vol_num}" if vol_num else short_s)
            if clean_pub:
                for c_cand in self.collections_for_publisher(clean_pub, "Libri"):
                    if fold(c_cand) in fold(raw_pub) and c_cand not in found_colls:
                        found_colls.insert(0, c_cand)

            st = search_fold(title)
            sa = search_fold(author)
            if isbn_code:
                sc = 9000.0 - idx * 80.0
            elif ctx_author_norm:
                if not _author_matches(sa, ctx_author_norm):
                    continue
                sc = _score_title_within_author(
                    q_norm,
                    q_tokens,
                    st,
                    popularity=pop,
                    lang_match=is_ita,
                    publishers=(clean_pub,) if clean_pub else (),
                    years=(yr,) if yr else (),
                    category="Libri",
                )
            else:
                sc = score_suggestion(
                    q_norm,
                    q_tokens,
                    st,
                    sa,
                    popularity=pop,
                    field_mode=field_mode,
                    lang_match=is_ita,
                    allow_fuzzy=True,
                    cand_publishers=(clean_pub,) if clean_pub else (),
                    cand_years=(yr,) if yr else (),
                    category="Libri",
                )
            if sc < 0:
                continue
            out.append(
                Suggestion(
                    title=title,
                    author=author,
                    year=yr,
                    publisher=clean_pub,
                    author_sort=surname_key(author, "Libri"),
                    work_id="",
                    category="Libri",
                    source="online",
                    popularity=pop,
                    publishers=[clean_pub] if clean_pub else [],
                    years=[yr] if yr else [],
                    collection=found_colls[0] if (isbn_code and found_colls) else "",
                    collections=found_colls,
                    from_isbn=bool(isbn_code),
                    score=sc,
                )
            )
        return out

    def _search_opac_sbn(
        self,
        query: str,
        q_norm: str,
        lang: str,
        field_mode: str,
        is_cancelled: Optional[Callable[[], bool]],
        context_author: str = "",
        ctx_author_norm: str = "",
    ) -> List[Suggestion]:
        """Interroga OPAC SBN (Servizio Bibliotecario Nazionale Italiano - ICCU)."""
        q_tokens = q_norm.split()
        isbn_code = _is_isbn_or_barcode(query)
        if isbn_code:
            param = f"isbn={urllib.parse.quote(isbn_code)}"
        elif context_author.strip():
            combined_q = f"{query.strip()} {context_author.strip()}".strip()
            param = f"any={urllib.parse.quote(combined_q)}"
        else:
            param = f"any={urllib.parse.quote(query.strip())}"

        rows_cnt = 35 if isbn_code else 15
        url = f"https://opac.sbn.it/opacmobilegw/search.json?{param}&rows={rows_cnt}"
        data = self._http_get_json(url, timeout=2.5, fast_fail=True, is_cancelled=is_cancelled)
        if not data or (is_cancelled and is_cancelled()):
            return []

        brief_list = data.get("briefRecords") or []
        sub_words = [w for w in q_tokens if w not in SEARCH_STOPWORDS and len(w) >= 2]
        if not isbn_code and len(sub_words) >= 2 and len(brief_list) < 2 and any(qt in SEARCH_STOPWORDS for qt in q_tokens):
            # Se la query conteneva preposizioni/articoli che hanno ristretto eccessivamente l'AND di SBN
            # (es. "tra i castagni dell'appennino"), riesegui una ricerca pulita sulle sole parole significative
            sub_q = " ".join(sub_words)
            if context_author.strip():
                sub_q = f"{sub_q} {context_author.strip()}"
            sub_url = f"https://opac.sbn.it/opacmobilegw/search.json?any={urllib.parse.quote(sub_q)}&rows=15"
            sub_data = self._http_get_json(sub_url, timeout=2.0, fast_fail=True, is_cancelled=is_cancelled)
            if sub_data and sub_data.get("briefRecords"):
                existing_bids = {str(r.get("codiceIdentificativo") or "") for r in brief_list}
                for sr in sub_data["briefRecords"]:
                    if str(sr.get("codiceIdentificativo") or "") not in existing_bids:
                        brief_list.append(sr)

        out: List[Suggestion] = []

        # Se e solo se l'utente ha cercato per ISBN, interroghiamo subito `full.json?bid=...`
        # per mostrare immediatamente la Collana ufficiale SBN dell'ISBN nel popup;
        # per le ricerche testuali normali evitiamo le chiamate sequenziali (risparmiando ~500ms),
        # perché `_fetch_book_editions` recupera comunque tutte le collane SBN appena il libro è scelto!
        full_details_by_bid: Dict[str, dict] = {}
        max_full_lookups = 2 if isbn_code else 0
        bids_to_fetch = [
            str(r.get("codiceIdentificativo") or "").strip()
            for r in brief_list[:max_full_lookups]
            if str(r.get("codiceIdentificativo") or "").strip()
        ]
        for bid in bids_to_fetch:
            if is_cancelled and is_cancelled():
                break
            f_url = f"https://opac.sbn.it/opacmobilegw/full.json?bid={urllib.parse.quote(bid)}"
            f_data = self._http_get_json(f_url, timeout=2.0, fast_fail=True, is_cancelled=is_cancelled)
            if f_data and not f_data.get("error"):
                full_details_by_bid[bid] = f_data

        non_trade_markers = (
            "youcanprint", "il mio libro", "lulu", "self-publish", "comune di", "provincia di",
            "regione ", "camera di commercio", "copia", "autografo", "dattiloscritto", "tipografia",
        )

        # Se tutte le prime schede hanno la stessa collana SBN (es. promozione 'Universale economica'),
        # propaghiamola anche alle schede successive dello stesso ISBN
        shared_sbn_coll = ""
        for fd in full_details_by_bid.values():
            rc = str(fd.get("collezione") or "")
            if rc:
                base_c, _ = _parse_sbn_collection(rc)
                if base_c:
                    shared_sbn_coll = base_c
                    break

        for idx, rec in enumerate(brief_list):
            bid = str(rec.get("codiceIdentificativo") or "").strip()
            f_rec = full_details_by_bid.get(bid) or {}
            raw_title = str(rec.get("titolo") or f_rec.get("titolo") or "")
            title = _clean_sbn_title(raw_title)
            raw_author = str(rec.get("autorePrincipale") or f_rec.get("autorePrincipale") or "")
            author, author_sort = _clean_sbn_author(raw_author)
            if not author:
                # Molti record SBN (es. 'Abel / Alessandro Baricco') mettono l'autore dopo ' / ' nel titolo!
                author, author_sort = _extract_sbn_fallback_author(raw_title)
            if not title or not author:
                continue
            pub_str = str(rec.get("pubblicazione") or f_rec.get("pubblicazione") or "")
            clean_pub, yr = _parse_sbn_publication(pub_str, lang)
            pub_low = fold(pub_str)
            is_non_trade = (not clean_pub) or any(m in pub_low for m in non_trade_markers)
            is_major_it = bool(clean_pub and fold(clean_pub) in ITALIAN_PUBLISHERS_PRIORITY)
            if is_major_it:
                pop = max(1400, (18 - min(idx, 17)) * 320)
            elif is_non_trade:
                pop = 35
            else:
                pop = max(250, (16 - min(idx, 15)) * 130)

            # Estrai la Collana ufficiale dal record SBN completo (o dal nome dell'editore se contiene la collana)
            sbn_colls: List[str] = []
            primary_coll = ""
            raw_coll = str(f_rec.get("collezione") or shared_sbn_coll or "")
            if raw_coll:
                base_c, num_c = _parse_sbn_collection(raw_coll)
                if num_c:
                    sbn_colls.append(num_c)
                    primary_coll = num_c
                if base_c and base_c not in sbn_colls:
                    sbn_colls.append(base_c)
                    if not primary_coll:
                        primary_coll = base_c
            if clean_pub:
                for c_cand in self.collections_for_publisher(clean_pub, "Libri"):
                    if fold(c_cand) in fold(pub_str) and c_cand not in sbn_colls:
                        sbn_colls.append(c_cand)
                        if not primary_coll:
                            primary_coll = c_cand

            st = search_fold(title)
            sa = search_fold(author)
            if isbn_code:
                # Se un codice ISBN promozionale (es. Feltrinelli 1+1) è condiviso da più titoli in SBN,
                # diamo una leggera priorità ai titoli/autori più celebri pur restituendoli tutti
                famous_bonus = 0.0
                if f"{st}|{sa}" in CURATED_EDITION_PROFILES:
                    famous_bonus = 250.0
                sc = 9500.0 + famous_bonus - idx * 12.0
            elif ctx_author_norm:
                if not _author_matches(sa, ctx_author_norm):
                    continue
                sc = _score_title_within_author(
                    q_norm,
                    q_tokens,
                    st,
                    popularity=pop,
                    lang_match=True,
                    publishers=(clean_pub,) if clean_pub else (),
                    years=(yr,) if yr else (),
                    category="Libri",
                )
            else:
                sc = score_suggestion(
                    q_norm,
                    q_tokens,
                    st,
                    sa,
                    popularity=pop,
                    field_mode=field_mode,
                    lang_match=True,
                    allow_fuzzy=True,
                    cand_publishers=(clean_pub,) if clean_pub else (),
                    cand_years=(yr,) if yr else (),
                    category="Libri",
                )
            if sc < 0:
                continue
            if is_non_trade and not isbn_code:
                sc -= 1600.0
            out.append(
                Suggestion(
                    title=title,
                    author=author,
                    year=yr,
                    publisher=clean_pub,
                    author_sort=author_sort,
                    work_id="",
                    category="Libri",
                    source="online",
                    popularity=pop,
                    publishers=[clean_pub] if clean_pub else [],
                    years=[yr] if yr else [],
                    collection=primary_coll,
                    collections=sbn_colls,
                    from_isbn=bool(isbn_code),
                    score=sc,
                )
            )
        return out

    def _search_openlibrary(
        self,
        query: str,
        q_norm: str,
        lang: str,
        field_mode: str,
        limit: int,
        is_cancelled: Optional[Callable[[], bool]],
        context_author: str = "",
        ctx_author_norm: str = "",
    ) -> List[Suggestion]:
        q_tokens = q_norm.split()
        fields = (
            "key,title,author_name,first_publish_year,edition_count,"
            "editions,editions.title,editions.publisher,editions.publish_date,editions.language,editions.series"
        )
        lang_code = "it" if lang == "it" else "en"
        isbn_code = _is_isbn_or_barcode(query)
        if isbn_code:
            param = f"isbn={urllib.parse.quote(isbn_code)}"
        elif field_mode == "author":
            param = f"author={urllib.parse.quote(query.strip())}"
        elif context_author.strip():
            if query.strip():
                param = f"title={urllib.parse.quote(query.strip())}&author={urllib.parse.quote(context_author.strip())}"
            else:
                param = f"author={urllib.parse.quote(context_author.strip())}"
        else:
            param = f"q={urllib.parse.quote(query.strip())}"

        url = f"https://openlibrary.org/search.json?{param}&lang={lang_code}&limit=12&fields={fields}"
        data = self._http_get_json(url, timeout=1.8, is_musicbrainz=False, fast_fail=True, is_cancelled=is_cancelled)
        if not data or (is_cancelled and is_cancelled()):
            return []

        pref_lang3 = "ita" if lang == "it" else "eng"
        out: List[Suggestion] = []
        for idx, doc in enumerate(data.get("docs", [])):
            authors = doc.get("author_name") or []
            author = authors[0].strip() if authors else ""
            if not author:
                continue
            wkey = str(doc.get("key") or "").strip()
            ed_docs = ((doc.get("editions") or {}).get("docs")) or []
            ed = ed_docs[0] if ed_docs else {}
            ed_langs = ed.get("language") or []
            is_pref_lang = pref_lang3 in ed_langs

            title = (ed.get("title") if is_pref_lang and ed.get("title") else doc.get("title")) or ""
            title = title.strip()
            if not title:
                continue

            first_yr = str(doc.get("first_publish_year") or "").strip()
            ed_pubs = ed.get("publisher") or []
            raw_pub = ed_pubs[0] if ed_pubs else ""
            clean_pub = _clean_publisher_name(raw_pub, lang)
            ed_dates = ed.get("publish_date") or []
            ed_yr = _extract_year(ed_dates[0]) if ed_dates else ""
            best_yr = ed_yr or first_yr
            ed_count = int(doc.get("edition_count") or 1)
            pop = ed_count * 120

            ol_series = [str(s).strip() for s in (ed.get("series") or []) if str(s).strip()]

            st = search_fold(title)
            sa = search_fold(author)
            if isbn_code:
                sc = 8900.0 - idx * 80.0
            elif ctx_author_norm:
                if not _author_matches(sa, ctx_author_norm):
                    continue
                sc = _score_title_within_author(
                    q_norm,
                    q_tokens,
                    st,
                    popularity=pop,
                    lang_match=is_pref_lang,
                    publishers=(clean_pub,) if clean_pub else (),
                    years=tuple(y for y in (ed_yr, first_yr) if y),
                    category="Libri",
                )
            else:
                sc = score_suggestion(
                    q_norm,
                    q_tokens,
                    st,
                    sa,
                    popularity=pop,
                    field_mode=field_mode,
                    lang_match=is_pref_lang,
                    allow_fuzzy=True,
                    cand_publishers=(clean_pub,) if clean_pub else (),
                    cand_years=tuple(y for y in (ed_yr, first_yr) if y),
                    category="Libri",
                )
            if sc < 0:
                continue

            yrs = [y for y in (ed_yr, first_yr) if y]
            out.append(
                Suggestion(
                    title=title,
                    author=author,
                    year=best_yr,
                    publisher=clean_pub,
                    author_sort=surname_key(author, "Libri"),
                    work_id=wkey,
                    category="Libri",
                    source="online",
                    popularity=pop,
                    publishers=[clean_pub] if clean_pub else [],
                    years=list(dict.fromkeys(yrs)),
                    collection=ol_series[0] if (isbn_code and ol_series) else "",
                    collections=ol_series,
                    from_isbn=bool(isbn_code),
                    score=sc,
                )
            )
        return out

    # ------------------------------------------------------------------
    # MOTORI MUSICA IN PARALLELO: APPLE ITUNES + MUSICBRAINZ + DISCOGS
    # ------------------------------------------------------------------
    def _search_music_parallel(
        self,
        category: str,
        query: str,
        q_norm: str,
        field_mode: str,
        limit: int,
        is_cancelled: Optional[Callable[[], bool]],
        context_author: str = "",
        ctx_author_norm: str = "",
        on_chunk: Optional[Callable[[List[Suggestion]], None]] = None,
    ) -> List[Suggestion]:
        results: Dict[str, Suggestion] = {}
        barcode = _is_isbn_or_barcode(query)
        fut_it = self._search_pool.submit(
            self._search_itunes_music,
            category,
            query,
            q_norm,
            field_mode,
            is_cancelled,
            context_author,
            ctx_author_norm,
        )
        fut_mb = self._mb_pool.submit(
            self._search_musicbrainz,
            category,
            query,
            q_norm,
            field_mode,
            limit,
            is_cancelled,
            context_author,
            ctx_author_norm,
        )
        pending = {fut_it, fut_mb}
        if barcode:
            pending.add(self._search_pool.submit(self._search_ibs_isbn, barcode, category, "it", is_cancelled))
            pending.add(self._search_pool.submit(self._search_amazon_isbn, barcode, category, "it", is_cancelled))
        if self.discogs_token:
            pending.add(
                self._search_pool.submit(
                    self._search_discogs,
                    category,
                    query,
                    q_norm,
                    field_mode,
                    is_cancelled,
                    context_author,
                    ctx_author_norm,
                )
            )

        deadline = time.monotonic() + 3.8
        while pending:
            if is_cancelled and is_cancelled():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            done, pending = wait(pending, timeout=min(0.12, remaining), return_when=FIRST_COMPLETED)
            if not done:
                continue
            new_sugs = False
            for fut in done:
                try:
                    for sug in fut.result() or []:
                        self._merge_suggestion(results, sug)
                        new_sugs = True
                except Exception:
                    pass

            if new_sugs and on_chunk and not (is_cancelled and is_cancelled()):
                on_chunk(sorted(results.values(), key=lambda s: -s.score)[:limit])

        for fut in pending:
            try:
                fut.cancel()
            except Exception:
                pass

        ranked = sorted(results.values(), key=lambda s: -s.score)[:limit]
        if barcode and ranked and not (is_cancelled and is_cancelled()):
            top = ranked[0]
            top_tf = search_fold(_clean_album_title_suffixes(top.title))
            for sib in ranked[1:]:
                sib_tf = search_fold(_clean_album_title_suffixes(sib.title))
                if sib_tf == top_tf or (top_tf and top_tf in sib_tf) or (sib_tf and sib_tf in top_tf):
                    if not top.publisher and sib.publisher:
                        top.publisher = sib.publisher
                    for p in sib.publishers or []:
                        if p and p not in top.publishers:
                            top.publishers.append(p)
                    if not top.year and sib.year:
                        top.year = sib.year
                    for y in sib.years or []:
                        if y and y not in top.years:
                            top.years.append(y)
                    if not top.work_id and sib.work_id:
                        top.work_id = sib.work_id
                    if sib.author_sort and sort_key_matches_author(sib.author_sort, top.author):
                        top.author_sort = sib.author_sort
            if not top.publisher or not top.year:
                try:
                    ed = self.fetch_editions(category, top.work_id, top.title, top.author, is_cancelled=is_cancelled)
                    if not top.publisher:
                        top.publisher = ed.default_publisher or (ed.publishers[0] if ed.publishers else "")
                    for p in ed.publishers or []:
                        if p and p not in top.publishers:
                            top.publishers.append(p)
                    if not top.year:
                        top.year = ed.default_year or (ed.years[0] if ed.years else "")
                    for y in ed.years or []:
                        if y and y not in top.years:
                            top.years.append(y)
                    if ed.author_sort and sort_key_matches_author(ed.author_sort, top.author):
                        top.author_sort = ed.author_sort
                except Exception:
                    pass
        return ranked

    def _search_itunes_music(
        self,
        category: str,
        query: str,
        q_norm: str,
        field_mode: str,
        is_cancelled: Optional[Callable[[], bool]],
        context_author: str = "",
        ctx_author_norm: str = "",
    ) -> List[Suggestion]:
        """Interroga Apple iTunes Search API (~80ms su CDN Apple, incluso lookup diretto per codice UPC/EAN)."""
        barcode = _is_isbn_or_barcode(query)
        q_tokens = q_norm.split()
        rows_raw: list = []

        if barcode:
            for variant in _barcode_variants(barcode)[:2]:
                if is_cancelled and is_cancelled():
                    return []
                u_upc = f"https://itunes.apple.com/lookup?upc={urllib.parse.quote(variant)}&entity=album&country=IT"
                d_upc = self._http_get_json(u_upc, timeout=1.8, fast_fail=True, is_cancelled=is_cancelled)
                if d_upc and d_upc.get("results"):
                    rows_raw = [r for r in d_upc["results"] if r.get("wrapperType") == "collection"]
                    if rows_raw:
                        break
        else:
            attr = "&attribute=artistTerm" if field_mode == "author" else ""
            term_str = f"{context_author.strip()} {query.strip()}".strip() if context_author.strip() else query.strip()
            url = (
                f"https://itunes.apple.com/search?term={urllib.parse.quote(term_str)}"
                f"&media=music&entity=album&country=IT&limit=25{attr}"
            )
            data = self._http_get_json(url, timeout=1.8, fast_fail=True, is_cancelled=is_cancelled)
            if not data or (is_cancelled and is_cancelled()):
                return []
            rows_raw = data.get("results") or []

        out: List[Suggestion] = []
        for idx, row in enumerate(rows_raw):
            raw_title = str(row.get("collectionName") or "").strip()
            title = _clean_album_title_suffixes(raw_title) or raw_title
            artist = str(row.get("artistName") or "").strip()
            if not title or not artist:
                continue
            yr = _extract_year(row.get("releaseDate") or "")
            if category == "CD" and yr and yr.isdigit() and int(yr) < 1982:
                disp_yr = ""
            else:
                disp_yr = yr

            label = _extract_itunes_label(str(row.get("copyright") or ""))
            st = search_fold(title)
            sa = search_fold(artist)
            if barcode:
                pop = 12000
                sc = 9200.0 - idx * 80.0
            else:
                all_tok_match = all(t in f"{st} {sa}" for t in q_tokens)
                pop = max(800, (25 - idx) * 1400) if all_tok_match else max(80, (25 - idx) * 90)
                if ctx_author_norm:
                    if not _author_matches(sa, ctx_author_norm):
                        continue
                    sc = _score_title_within_author(q_norm, q_tokens, st, popularity=pop, category=category)
                else:
                    sc = score_suggestion(
                        q_norm, q_tokens, st, sa, popularity=pop, field_mode=field_mode, allow_fuzzy=True, category=category
                    )
                if sc < 0:
                    continue

            sug = Suggestion(
                title=title,
                author=artist,
                year=disp_yr,
                publisher=label,
                author_sort=surname_key(artist, category),
                work_id="",
                category=category,
                source="online",
                popularity=pop,
                publishers=[label] if label else [],
                years=[disp_yr] if disp_yr else [],
                from_isbn=bool(barcode),
                score=sc,
            )
            out.append(self._apply_curated_profile(sug))
        return out

    def _search_musicbrainz(
        self,
        category: str,
        query: str,
        q_norm: str,
        field_mode: str,
        limit: int,
        is_cancelled: Optional[Callable[[], bool]],
        context_author: str = "",
        ctx_author_norm: str = "",
    ) -> List[Suggestion]:
        barcode = _is_isbn_or_barcode(query)
        if barcode:
            # Ricerca diretta per codice a barre EAN-13 / UPC-12 sull'indice Release di MusicBrainz
            variants = _barcode_variants(barcode)
            q_bar = " OR ".join(f"barcode:{v}" for v in variants if v)
            u_bar = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(q_bar)}&fmt=json&limit=10"
            d_bar = self._http_get_json(u_bar, timeout=2.5, is_musicbrainz=True, fast_fail=True, is_cancelled=is_cancelled)
            if not d_bar or (is_cancelled and is_cancelled()):
                return []
            out_bar: List[Suggestion] = []
            for idx, rel in enumerate(d_bar.get("releases") or []):
                raw_t = str(rel.get("title") or "").strip()
                title = _clean_album_title_suffixes(raw_t) or raw_t
                credits = rel.get("artist-credit") or []
                artist_parts = []
                first_sort = ""
                for c in credits:
                    name_part = c.get("name") or (c.get("artist") or {}).get("name") or ""
                    join_part = c.get("joinphrase") or ""
                    artist_parts.append(name_part + join_part)
                    if not first_sort and isinstance(c.get("artist"), dict):
                        first_sort = str(c["artist"].get("sort-name") or "").strip()
                artist = "".join(artist_parts).strip()
                if not title or not artist:
                    continue
                yr = _extract_year(str(rel.get("date") or ""))
                if category == "CD" and yr and yr.isdigit() and int(yr) < 1982:
                    yr = ""
                labels: List[str] = []
                for li in rel.get("label-info") or []:
                    lbl_obj = li.get("label") if isinstance(li, dict) else None
                    if isinstance(lbl_obj, dict):
                        lname = _clean_publisher_name(str(lbl_obj.get("name") or ""), "en")
                        if lname and lname not in labels:
                            labels.append(lname)
                media_fmts = [str(m.get("format") or "") for m in (rel.get("media") or []) if isinstance(m, dict)]
                fmt_match = any(
                    (_is_cd_format(f) if category == "CD" else _is_vinyl_format(f)) for f in media_fmts
                )
                rg_obj = rel.get("release-group") if isinstance(rel.get("release-group"), dict) else {}
                rgid = str(rg_obj.get("id") or "").strip()
                sc = (9750.0 if fmt_match else 9350.0) - idx * 60.0
                sug = Suggestion(
                    title=title,
                    author=artist,
                    year=yr,
                    publisher=labels[0] if labels else "",
                    author_sort=first_sort or surname_key(artist, category),
                    work_id=rgid,
                    category=category,
                    source="online",
                    popularity=15000,
                    publishers=labels,
                    years=[yr] if yr else [],
                    from_isbn=True,
                    score=sc,
                )
                out_bar.append(self._apply_curated_profile(sug))
            return out_bar

        query_clean = query.strip()
        q_tokens = q_norm.split()
        esc_tokens = [re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", t) for t in query_clean.split() if t]
        escaped = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", query_clean)
        query_has_live = "live" in q_norm or "concerto" in q_norm
        live_filter = "" if query_has_live else " NOT secondarytype:live"

        if field_mode == "author":
            if len(esc_tokens) <= 1:
                lucene = f'artist:({escaped}*) AND (primarytype:album OR primarytype:ep){live_filter}'
            else:
                tok_clauses = [
                    f'(artist:{t} OR artist:{t}~1 OR artist:{t}*)' if len(t) >= 4 else f'artist:{t}*'
                    for t in esc_tokens
                ]
                lucene = f'({" AND ".join(tok_clauses)}) AND (primarytype:album OR primarytype:ep){live_filter}'
        elif context_author.strip():
            esc_auth = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", context_author.strip())
            auth_tokens = [
                re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", t)
                for t in context_author.strip().split()
                if t
            ]
            auth_clause = " AND ".join(
                f'(artist:{t} OR artist:{t}~1 OR artist:{t}*)' if len(t) >= 4 else f'artist:{t}*'
                for t in auth_tokens
            )
            if esc_tokens:
                tok_clauses = [
                    f'(releasegroup:{t} OR releasegroup:{t}~1 OR releasegroup:{t}*)' if len(t) >= 4 else f'releasegroup:{t}*'
                    for t in esc_tokens
                ]
                rg_clause = " AND ".join(tok_clauses)
                lucene = f'(releasegroup:("{escaped}")^6 OR ({rg_clause})) AND ({auth_clause}) AND (primarytype:album OR primarytype:ep){live_filter}'
            else:
                lucene = f'({auth_clause}) AND (primarytype:album OR primarytype:ep){live_filter}'
        else:
            # Ricerca generale su titolo, artista o combinazione titolo + artista
            if len(esc_tokens) >= 2:
                tok_clauses = [
                    (
                        f'(releasegroup:{t} OR releasegroup:{t}~1 OR releasegroup:{t}* '
                        f'OR artist:{t} OR artist:{t}~1 OR artist:{t}*)'
                    )
                    if len(t) >= 4
                    else f'(releasegroup:{t}* OR artist:{t}*)'
                    for t in esc_tokens
                ]
            else:
                tok_clauses = [
                    f'({t} OR {t}~1 OR {t}*)' if len(t) >= 4 else f'{t}*'
                    for t in esc_tokens
                ]
            all_toks = " AND ".join(tok_clauses)
            lucene = (
                f'(releasegroup:("{escaped}")^8 OR artist:("{escaped}")^8 OR ({all_toks})) '
                f'AND (primarytype:album OR primarytype:ep){live_filter}'
            )

        url = f"https://musicbrainz.org/ws/2/release-group/?query={urllib.parse.quote(lucene)}&fmt=json&limit=22"
        data = self._http_get_json(url, timeout=3.8, is_musicbrainz=True, is_cancelled=is_cancelled)
        if not data or (is_cancelled and is_cancelled()):
            return []

        rgs = data.get("release-groups") or []
        mbids = [rg.get("id") for rg in rgs if rg.get("id")]

        pop_map: Dict[str, int] = {}
        if mbids and not (is_cancelled and is_cancelled()):
            lb_data = self._http_post_json(
                "https://api.listenbrainz.org/1/popularity/release-group",
                {"release_group_mbids": mbids[:22]},
                timeout=1.4,
            )
            if isinstance(lb_data, list):
                for row in lb_data:
                    rid = row.get("release_group_mbid")
                    if rid:
                        pop_map[rid] = int(row.get("total_listen_count") or 0)

        out: List[Suggestion] = []
        for rg in rgs:
            sec = [s.lower() for s in (rg.get("secondary-types") or [])]
            if not query_has_live and "live" in sec:
                continue
            if any(s in sec for s in ("interview", "audiobook", "spokenword", "dj-mix", "demo")):
                continue
            rgid = str(rg.get("id") or "").strip()
            title = str(rg.get("title") or "").strip()
            credits = rg.get("artist-credit") or []
            artist_parts = []
            first_sort_name = ""
            for c in credits:
                name_part = c.get("name") or (c.get("artist") or {}).get("name") or ""
                join_part = c.get("joinphrase") or ""
                artist_parts.append(name_part + join_part)
                if not first_sort_name and isinstance(c.get("artist"), dict):
                    first_sort_name = str(c["artist"].get("sort-name") or "").strip()
            artist = "".join(artist_parts).strip()
            if not title or not artist:
                continue

            yr = _extract_year(rg.get("first-release-date") or "")
            if category == "CD" and yr and yr.isdigit() and int(yr) < 1982:
                yr = ""
            rel_count = int(rg.get("count") or 1)
            lb_pop = pop_map.get(rgid, 0)
            if lb_pop == 0 and rel_count <= 2:
                effective_pop = 40
            else:
                effective_pop = max(lb_pop, rel_count * 450)

            st = search_fold(title)
            sa = search_fold(artist)
            if ctx_author_norm:
                if not _author_matches(sa, ctx_author_norm):
                    continue
                sc = _score_title_within_author(q_norm, q_tokens, st, popularity=effective_pop, category=category)
            else:
                sc = score_suggestion(
                    q_norm, q_tokens, st, sa, popularity=effective_pop, field_mode=field_mode, allow_fuzzy=True, category=category
                )
            if sc < 0:
                continue

            sug = Suggestion(
                title=title,
                author=artist,
                year=yr,
                publisher="",
                author_sort=first_sort_name or surname_key(artist, category),
                work_id=rgid,
                category=category,
                source="online",
                popularity=effective_pop,
                publishers=[],
                years=[yr] if yr else [],
                score=sc,
            )
            if rgid:
                cached_ed = self.cache.get(f"editions:v10:{category}:{rgid}")
                if cached_ed:
                    sug.publishers = cached_ed.get("publishers") or sug.publishers
                    sug.years = cached_ed.get("years") or sug.years
                    sug.publisher = cached_ed.get("default_publisher") or sug.publisher
                    sug.year = cached_ed.get("default_year") or sug.year
                    sug.author_sort = cached_ed.get("author_sort") or sug.author_sort
            out.append(self._apply_curated_profile(sug))
        return out

    def _search_discogs(
        self,
        category: str,
        query: str,
        q_norm: str,
        field_mode: str,
        is_cancelled: Optional[Callable[[], bool]],
        context_author: str = "",
        ctx_author_norm: str = "",
    ) -> List[Suggestion]:
        """Interroga Discogs API filtrando per supporto fisico (CD vs Vinyl) o codice a barre EAN/UPC."""
        if not self.discogs_token:
            return []
        barcode = _is_isbn_or_barcode(query)
        q_tokens = q_norm.split()
        fmt_filter = "CD" if category == "CD" else "Vinyl"
        if barcode:
            url = (
                f"https://api.discogs.com/database/search?barcode={urllib.parse.quote(barcode)}"
                f"&type=release&format={fmt_filter}&per_page=10"
            )
        else:
            q_str = query.strip()
            a_str = context_author.strip()
            if q_str and a_str:
                url = (
                    f"https://api.discogs.com/database/search?q={urllib.parse.quote(q_str)}"
                    f"&artist={urllib.parse.quote(a_str)}&type=release&format={fmt_filter}&per_page=15"
                )
            elif a_str:
                url = (
                    f"https://api.discogs.com/database/search?artist={urllib.parse.quote(a_str)}"
                    f"&type=release&format={fmt_filter}&per_page=15"
                )
            else:
                url = (
                    f"https://api.discogs.com/database/search?q={urllib.parse.quote(q_str)}"
                    f"&type=release&format={fmt_filter}&per_page=15"
                )
        clean_discogs = str(self.discogs_token or "").strip()
        if not clean_discogs or "\r" in clean_discogs or "\n" in clean_discogs:
            return []
        data = self._http_get_json(
            url,
            timeout=2.4,
            extra_headers={"Authorization": f"Discogs token={clean_discogs}"},
            fast_fail=True,
            is_cancelled=is_cancelled,
        )
        if not data or (is_cancelled and is_cancelled()):
            return []

        out: List[Suggestion] = []
        for idx, row in enumerate(data.get("results") or []):
            full_title = str(row.get("title") or "").strip()
            if " - " in full_title:
                artist, raw_t = [p.strip() for p in full_title.split(" - ", 1)]
            else:
                raw_t, artist = full_title, ""
            title = _clean_album_title_suffixes(raw_t) or raw_t
            artist = re.sub(r"\s*(\(\d+\)|\*)\s*$", "", artist).rstrip("* ").strip()  # Rimuove disambiguazioni e asterischi Discogs
            if not title or not artist:
                continue
            yr = _extract_year(str(row.get("year") or ""))
            if category == "CD" and yr and yr.isdigit() and int(yr) < 1982:
                yr = ""
            labels = [
                re.sub(r"\s*(\(\d+\)|\*)\s*$", "", str(l).strip()).rstrip("* ").strip()
                for l in (row.get("label") or [])
                if str(l).strip() and str(l).strip().lower() not in ("not on label", "none")
            ]
            pub = labels[0] if labels else ""
            comm = row.get("community") or {}
            have = int(comm.get("have") or 0)
            pop = max(500, have * 5 + (15 - idx) * 200)

            st = search_fold(title)
            sa = search_fold(artist)
            if barcode:
                sc = 9550.0 - idx * 60.0
            elif ctx_author_norm:
                if not _author_matches(sa, ctx_author_norm):
                    continue
                sc = _score_title_within_author(q_norm, q_tokens, st, popularity=pop, category=category)
            else:
                sc = score_suggestion(
                    q_norm, q_tokens, st, sa, popularity=pop, field_mode=field_mode, allow_fuzzy=True, category=category
                )
            if sc < 0:
                continue
            sug = Suggestion(
                title=title,
                author=artist,
                year=yr,
                publisher=pub,
                author_sort=surname_key(artist, category),
                work_id="",
                category=category,
                source="online",
                popularity=pop,
                publishers=list(dict.fromkeys(labels[:10])),
                years=[yr] if yr else [],
                from_isbn=bool(barcode),
                score=sc,
            )
            out.append(self._apply_curated_profile(sug))
        return out

    # ------------------------------------------------------------------
    # 3. RECUPERO EDIZIONI SPECIFICHE PER FORMATO (Libri vs CD vs Vinili)
    # ------------------------------------------------------------------
    def fetch_editions(
        self,
        category: str,
        work_id: str,
        title: str,
        author: str,
        lang: str = "it",
        is_cancelled: Optional[Callable[[], bool]] = None,
    ) -> EditionBundle:
        eff_title = _clean_album_title_suffixes(title) if category != "Libri" else title
        norm_key = f"{search_fold(eff_title)}|{search_fold(author)}"
        cache_id = work_id or norm_key
        cache_key = f"editions:v10:{category}:{cache_id}"

        cached = self.cache.get(cache_key)
        if cached and cached.get("publishers"):
            return EditionBundle(
                category=category,
                work_id=work_id,
                title=title,
                author=author,
                author_sort=cached.get("author_sort") or surname_key(author, category),
                default_publisher=cached.get("default_publisher") or "",
                default_year=cached.get("default_year") or "",
                publishers=cached.get("publishers") or [],
                years=cached.get("years") or [],
                collections=cached.get("collections") or [],
                default_collection=cached.get("default_collection") or "",
                collection_meta=cached.get("collection_meta") or {},
                publisher_years=cached.get("publisher_years") or {},
                year_publishers=cached.get("year_publishers") or {},
                year_collections=cached.get("year_collections") or {},
                publisher_collections=cached.get("publisher_collections") or {},
                publisher_all_years=cached.get("publisher_all_years") or {},
                year_all_publishers=cached.get("year_all_publishers") or {},
                pair_collections=cached.get("pair_collections") or {},
                edition_count=int(cached.get("edition_count") or len(cached.get("publishers") or [])),
                from_cache=True,
            )

        if category == "Libri":
            bundle = self._fetch_book_editions(work_id, title, author, lang, is_cancelled)
        else:
            bundle = self._fetch_music_editions(category, work_id, title, author, is_cancelled)

        if bundle and (bundle.publishers or bundle.years) and not (is_cancelled and is_cancelled()):
            self.cache.set(
                cache_key,
                {
                    "author_sort": bundle.author_sort,
                    "default_publisher": bundle.default_publisher,
                    "default_year": bundle.default_year,
                    "publishers": bundle.publishers,
                    "years": bundle.years,
                    "collections": bundle.collections,
                    "default_collection": bundle.default_collection,
                    "collection_meta": bundle.collection_meta,
                    "publisher_years": bundle.publisher_years,
                    "year_publishers": bundle.year_publishers,
                    "year_collections": bundle.year_collections,
                    "publisher_collections": bundle.publisher_collections,
                    "publisher_all_years": bundle.publisher_all_years,
                    "year_all_publishers": bundle.year_all_publishers,
                    "pair_collections": bundle.pair_collections,
                    "edition_count": bundle.edition_count,
                },
            )
        return bundle

    def _fetch_book_editions(
        self,
        work_id: str,
        title: str,
        author: str,
        lang: str,
        is_cancelled: Optional[Callable[[], bool]],
    ) -> EditionBundle:
        """
        Recupera in parallelo tutte le edizioni librarie e le relative Collane da:
        1. Google Books API (`intitle` + `inauthor`)
        2. OPAC SBN Italia (`any` + `full.json` per la Collana ufficiale SBN)
        3. Open Library (`/search.json` + `/works/.../editions.json` incluso `series`)
        4. AbeBooks (edizioni storiche/fuori catalogo italiane e internazionali)
        5. IBS / LaFeltrinelli (catalogo commerciale italiano corrente con collana)
        6. Internet Archive (archivio bibliografico storico)
        """
        norm_key = f"{search_fold(title)}|{search_fold(author)}"
        curated = CURATED_EDITION_PROFILES.get(norm_key, {})
        curated_cat = curated.get("Libri", {})

        pub_counts_it: Dict[str, int] = {}
        pub_counts_all: Dict[str, int] = {}
        coll_counts: Dict[str, int] = {}
        coll_canon: Dict[str, str] = {}
        for _plist in PUBLISHER_COLLECTIONS_IT.values():
            for _kc in _plist:
                coll_canon.setdefault(fold(_kc), _kc)
        collection_meta: Dict[str, Dict[str, str]] = {}
        publisher_years: Dict[str, str] = {}
        year_publishers: Dict[str, str] = {}
        year_collections: Dict[str, str] = {}
        publisher_collections: Dict[str, str] = {}
        pub_years_sets: Dict[str, set[str]] = {}
        year_pubs_sets: Dict[str, set[str]] = {}
        pair_collections: Dict[str, List[str]] = {}
        years_set: set[str] = set()
        first_year = ""
        ed_total = 0
        lock = threading.Lock()
        a_fold = search_fold(author)

        for p in curated_cat.get("publishers", []):
            pub_counts_it[p] = pub_counts_it.get(p, 0) + 15
        for y in curated_cat.get("years", []):
            years_set.add(y)

        def add_pub_year(raw_p: str, yr_str: str, is_ita_hint: bool = False) -> Tuple[str, str]:
            cp = _clean_publisher_name(raw_p, lang)
            if cp and fold(cp) in NON_BOOK_PUBLISHERS_FOLD:
                return "", ""
            yr = _extract_year(yr_str)
            with lock:
                if cp:
                    is_major_it = fold(cp) in ITALIAN_PUBLISHERS_PRIORITY
                    pub_counts_all[cp] = pub_counts_all.get(cp, 0) + (8 if is_major_it else 1)
                    if is_ita_hint or is_major_it:
                        pub_counts_it[cp] = pub_counts_it.get(cp, 0) + (18 if is_major_it else 3)
                    if yr:
                        pub_years_sets.setdefault(cp, set()).add(yr)
                        year_pubs_sets.setdefault(yr, set()).add(cp)
                if yr:
                    years_set.add(yr)
            return cp, yr

        def add_coll(c_name: str, weight: int = 5, pub_name: str = "", yr_val: str = "") -> None:
            c = c_name.strip(" ,.;:/-")
            if not c or len(c) < 2 or re.fullmatch(r"[\d\s#.\-/]+", c):
                return
            cf = fold(c)
            with lock:
                canon = coll_canon.get(cf)
                if canon is None:
                    canon = c
                    coll_canon[cf] = canon
                elif sum(1 for ch in c if ch.isupper()) > sum(1 for ch in canon if ch.isupper()) and canon not in coll_counts:
                    canon = c
                    coll_canon[cf] = canon
                coll_counts[canon] = coll_counts.get(canon, 0) + weight
                if pub_name or yr_val:
                    existing_m = collection_meta.get(canon) or {}
                    merged_m = {
                        "publisher": existing_m.get("publisher") or pub_name,
                        "year": existing_m.get("year") or yr_val,
                    }
                    collection_meta[canon] = merged_m
                    collection_meta[cf] = merged_m
                if pub_name and yr_val:
                    pk = f"{fold(pub_name)}|{yr_val}"
                    lst = pair_collections.setdefault(pk, [])
                    if all(fold(x) != cf for x in lst):
                        lst.append(canon)
                if yr_val and yr_val not in year_collections:
                    year_collections[yr_val] = canon
                if pub_name:
                    if pub_name not in publisher_collections:
                        publisher_collections[pub_name] = canon
                        publisher_collections[fold(pub_name)] = canon

        def task_google() -> None:
            if is_cancelled and is_cancelled():
                return
            if time.time() < getattr(self, "_gb_disabled_until", 0.0):
                return
            q = f'intitle:"{title}" inauthor:"{author}"'
            u = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(q)}&maxResults=35"
            d = self._http_get_json(u, timeout=2.5, fast_fail=True, is_cancelled=is_cancelled)
            if not d:
                if self._get_last_http_code() == 429:
                    self._gb_disabled_until = time.time() + 300.0
                return
            for it in d.get("items") or []:
                vi = it.get("volumeInfo") or {}
                raw_vt = str(vi.get("title") or "")
                if not _book_edition_title_matches(raw_vt, title):
                    continue
                if a_fold:
                    v_authors = [search_fold(str(a)) for a in (vi.get("authors") or [])]
                    if v_authors and not any(_author_matches(va, a_fold) for va in v_authors):
                        continue

                is_ita = str(vi.get("language") or "").lower().startswith("it")
                raw_p = str(vi.get("publisher") or "")
                cp, yr = add_pub_year(raw_p, str(vi.get("publishedDate") or ""), is_ita)
                s_info = vi.get("seriesInfo") or {}
                if isinstance(s_info, dict):
                    short_s = str(s_info.get("shortSeriesBookTitle") or "").strip()
                    if short_s:
                        add_coll(short_s, 6, cp, yr)

        def task_sbn() -> None:
            if is_cancelled and is_cancelled():
                return
            combined_q = f"{title} {author}".strip()
            u = (
                "https://opac.sbn.it/opacmobilegw/search.json?"
                f"any={urllib.parse.quote(combined_q)}&rows=25"
            )
            d = self._http_get_json(u, timeout=2.6, fast_fail=True, is_cancelled=is_cancelled)
            if not d or d.get("error"):
                return
            brief_recs = d.get("briefRecords") or []
            matched_bids = []
            for rec in brief_recs:
                raw_rec_tit = str(rec.get("titolo") or "")
                r_tit = _clean_sbn_title(raw_rec_tit)
                if not _book_edition_title_matches(r_tit, title):
                    continue
                raw_rec_auth = str(rec.get("autorePrincipale") or "")
                if raw_rec_auth and a_fold:
                    disp_a, _ = _clean_sbn_author(raw_rec_auth)
                    if disp_a and not _author_matches(search_fold(disp_a), a_fold):
                        continue
                # Escludi formati non librari (video, dvd, spartiti musicali, registrazioni audio)
                natura = str(rec.get("natura") or "").lower()
                tipo = str(rec.get("tipoMateriale") or "").lower()
                if any(x in natura or x in tipo for x in ("video", "dvd", "musica manoscritta", "registrazione sonora", "documento proiettabile")):
                    continue

                pub_raw = str(rec.get("pubblicazione") or "")
                p_name, yr = _parse_sbn_publication(pub_raw, lang)
                if p_name:
                    add_pub_year(p_name, yr, True)
                    for c_cand in self.collections_for_publisher(p_name, "Libri"):
                        if fold(c_cand) in fold(pub_raw):
                            add_coll(c_cand, 8, p_name, yr)

                bid = str(rec.get("codiceIdentificativo") or "").strip()
                if bid:
                    matched_bids.append(bid)

            # Ispeziona `full.json` sui primi record SBN filtrati per estrarre le Collane reali
            for bid in matched_bids[:4]:
                if is_cancelled and is_cancelled():
                    break
                fu = f"https://opac.sbn.it/opacmobilegw/full.json?bid={urllib.parse.quote(bid)}"
                fd = self._http_get_json(fu, timeout=2.0, fast_fail=True, is_cancelled=is_cancelled)
                if not fd or fd.get("error"):
                    continue
                p_name2, yr2 = "", ""
                if fd.get("pubblicazione"):
                    p_name2, yr2 = _parse_sbn_publication(str(fd.get("pubblicazione") or ""), lang)
                    if p_name2:
                        add_pub_year(p_name2, yr2, True)
                raw_coll = str(fd.get("collezione") or "")
                if raw_coll:
                    base_c, num_c = _parse_sbn_collection(raw_coll)
                    if base_c:
                        add_coll(base_c, 12, p_name2, yr2)
                    if num_c:
                        add_coll(num_c, 9, p_name2, yr2)

        def _ingest_ol_work(wk: str) -> None:
            nonlocal ed_total
            if not wk or not re.fullmatch(r"/works/OL\d+W", wk) or (is_cancelled and is_cancelled()):
                return
            u = f"https://openlibrary.org{wk}/editions.json?limit=80"
            d = self._http_get_json(u, timeout=2.8, fast_fail=True, is_cancelled=is_cancelled)
            if d and isinstance(d.get("entries"), list):
                with lock:
                    ed_total = max(ed_total, int(d.get("size") or len(d["entries"])))
                for ed in d["entries"]:
                    langs = [str(l.get("key") or "") for l in (ed.get("languages") or []) if isinstance(l, dict)]
                    is_ita = any(l.endswith("/ita") for l in langs)
                    yr = _extract_year(ed.get("publish_date") or "")
                    pubs = ed.get("publishers") or [""]
                    first_cp = ""
                    for rp in pubs:
                        cp, _ = add_pub_year(str(rp), yr, is_ita)
                        if cp and not first_cp:
                            first_cp = cp
                    for ser in ed.get("series") or []:
                        s_clean = str(ser).strip()
                        if s_clean:
                            add_coll(s_clean, 8 if is_ita else 3, first_cp, yr)

        def task_openlib() -> None:
            nonlocal first_year
            if is_cancelled and is_cancelled():
                return

            work_candidates: List[Tuple[int, str]] = []
            seen_wkeys: set[str] = set()
            if work_id and work_id.startswith("/works/"):
                seen_wkeys.add(work_id)

            # Cerca sempre su OpenLibrary search.json per coprire anche suggerimenti senza work_id
            # o edizioni storiche registrate su opere gemelle (es. 'Ossi di seppia' vs 'Ossi di seppia, 1920-1927')
            search_author = author.strip()
            for attempt_author in (search_author, search_fold(search_author).split()[-1] if search_fold(search_author) else ""):
                if not attempt_author or (is_cancelled and is_cancelled()):
                    break
                su = (
                    "https://openlibrary.org/search.json?"
                    f"title={urllib.parse.quote(title.strip())}&author={urllib.parse.quote(attempt_author)}"
                    "&limit=15&fields=key,title,author_name,publisher,publish_year,first_publish_year,edition_count"
                )
                sd = self._http_get_json(su, timeout=2.7, fast_fail=True, is_cancelled=is_cancelled)
                docs = (sd or {}).get("docs") or []
                if not docs:
                    continue
                for doc in docs:
                    d_title = str(doc.get("title") or "").strip()
                    if not _book_edition_title_matches(d_title, title):
                        continue
                    d_authors = [search_fold(str(a)) for a in (doc.get("author_name") or [])]
                    if a_fold and d_authors and not any(_author_matches(da, a_fold) for da in d_authors):
                        continue
                    wk = str(doc.get("key") or "").strip()
                    ec = int(doc.get("edition_count") or 1)
                    if wk.startswith("/works/") and wk not in seen_wkeys:
                        seen_wkeys.add(wk)
                        work_candidates.append((ec, wk))
                    fy = _extract_year(str(doc.get("first_publish_year") or ""))
                    if fy:
                        with lock:
                            if not first_year or fy < first_year:
                                first_year = fy
                            years_set.add(fy)
                    doc_pubs = [str(p).strip() for p in (doc.get("publisher") or []) if str(p).strip()]
                    doc_yrs = [
                        _extract_year(str(y))
                        for y in (doc.get("publish_year") or [])
                        if _extract_year(str(y))
                    ]
                    if len(doc_pubs) == 1 and len(doc_yrs) == 1:
                        cp_one = _clean_publisher_name(doc_pubs[0], lang)
                        add_pub_year(doc_pubs[0], doc_yrs[0], bool(cp_one and fold(cp_one) in ITALIAN_PUBLISHERS_PRIORITY))
                    else:
                        for rp in doc_pubs[:14]:
                            cp_doc = _clean_publisher_name(rp, lang)
                            add_pub_year(rp, "", bool(cp_doc and fold(cp_doc) in ITALIAN_PUBLISHERS_PRIORITY))
                        with lock:
                            for y_val in doc_yrs[:18]:
                                years_set.add(y_val)
                break

            work_candidates.sort(key=lambda item: -item[0])
            if work_candidates and not (is_cancelled and is_cancelled()):
                _ingest_ol_work(work_candidates[0][1])

        def task_abebooks() -> None:
            if is_cancelled and is_cancelled():
                return
            domain = "www.abebooks.it" if lang == "it" else "www.abebooks.com"
            url = (
                f"https://{domain}/servlet/SearchResults?"
                f"tn={urllib.parse.quote(title)}&an={urllib.parse.quote(author)}&ds=50"
            )
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                        ),
                        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                )
                with urllib.request.urlopen(req, timeout=2.8) as resp:
                    raw_html = _read_response_bounded(resp).decode("utf-8", errors="ignore")
            except Exception:
                return
            if not raw_html or (is_cancelled and is_cancelled()):
                return

            import html as _html

            chunks = raw_html.split('data-test-id="listing-title"')
            for ch in chunks[1:51]:
                if is_cancelled and is_cancelled():
                    return
                m_t = re.search(r">([^<]+)</h2>", ch)
                if not m_t:
                    continue
                card_title = _html.unescape(m_t.group(1)).strip()
                if not _book_edition_title_matches(card_title, title):
                    continue
                if lang == "it":
                    ct_low = search_fold(card_title)
                    if any(tr_tok in ct_low for tr_tok in ("siirler", "gedichte", "poemes", "edicion")) and ct_low not in search_fold(title):
                        continue
                m_a = re.search(r'data-test-id="listing-author-link"[^>]*>([^<]+)<', ch)
                if m_a and a_fold:
                    card_author = _html.unescape(m_a.group(1)).strip()
                    if card_author and not _author_matches(search_fold(card_author), a_fold):
                        continue
                m_p = re.search(r'data-test-id="publisher-\d+"[^>]*>(.*?)</p>', ch, flags=re.DOTALL)
                if not m_p:
                    continue
                raw_pub = _html.unescape(re.sub(r"<[^>]+>", "", m_p.group(1))).strip()
                cp, yr, coll = _parse_abebooks_publisher_field(raw_pub, lang)
                if not cp and not yr:
                    continue
                m_lang = re.search(r'data-test-id="language-\d+"[^>]*>(.*?)</p>', ch, flags=re.DOTALL)
                lang_txt = _html.unescape(re.sub(r"<[^>]+>", "", m_lang.group(1))).lower() if m_lang else ""
                is_ita_lang = ("italian" in lang_txt) or ("italiano" in lang_txt)
                if lang_txt and not is_ita_lang and lang == "it" and cp and fold(cp) not in ITALIAN_PUBLISHERS_PRIORITY:
                    # Evita che traduzioni straniere scavalchino gli editori italiani
                    continue
                cp_final, yr_final = add_pub_year(cp, yr, is_ita_lang or (bool(cp) and fold(cp) in ITALIAN_PUBLISHERS_PRIORITY))
                if coll:
                    add_coll(coll, 8 if is_ita_lang else 4, cp_final, yr_final)
                # Rileva eventuale collana nelle parentesi del titolo (es. "(Oscar classici moderni)")
                for paren in re.findall(r"\(([^()]{3,45})\)", card_title):
                    pf = fold(paren.strip())
                    if pf in COLLECTION_TO_PUBLISHER_IT or any(
                        pf.startswith(prefix)
                        for prefix in ("oscar ", "lo specchio", "i meridiani", "bur ", "supercoralli", "et ", "biblioteca adelphi")
                    ):
                        add_coll(paren.strip(), 7, cp_final, yr_final)

        def task_ibs() -> None:
            if is_cancelled and is_cancelled():
                return
            search_str = f"{title} {author}".strip()
            if len(search_str) < 3:
                return
            url = f"https://www.ibs.it/search/?ts=as&query={urllib.parse.quote(search_str)}"
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                        ),
                        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                )
                with urllib.request.urlopen(req, timeout=2.5) as resp:
                    raw_html = _read_response_bounded(resp).decode("utf-8", errors="ignore")
            except Exception:
                return
            if not raw_html or (is_cancelled and is_cancelled()):
                return

            import html as _html

            for m_dl in re.finditer(r"dataLayer\.push\((\{\"ecommerce\":.*?\})\)\s*</script>", raw_html, flags=re.DOTALL):
                try:
                    dl_obj = json.loads(m_dl.group(1))
                    impressions = (dl_obj.get("ecommerce") or {}).get("impression") or []
                    for imp_group in impressions:
                        items_list = imp_group if isinstance(imp_group, list) else [imp_group]
                        for it in items_list[:20]:
                            if not isinstance(it, dict):
                                continue
                            cat_str = str(it.get("item_category") or "").lower()
                            if cat_str and "libri" not in cat_str and "ebook" not in cat_str:
                                continue
                            it_title = _html.unescape(str(it.get("item_name") or "")).strip()
                            it_author = _html.unescape(str(it.get("item_author") or "")).strip()
                            it_author = re.sub(r",(?!\s)", ", ", it_author)
                            if not it_title or not it_author or it_author.lower() in ("none", "null"):
                                continue
                            if not _book_edition_title_matches(it_title, title):
                                continue
                            if a_fold:
                                first_author = it_author.split(",")[0].strip()
                                if not (_author_matches(search_fold(it_author), a_fold) or _author_matches(search_fold(first_author), a_fold)):
                                    continue
                            raw_pub = _html.unescape(str(it.get("item_brand") or "")).strip()
                            if fold(raw_pub) in NON_BOOK_PUBLISHERS_FOLD:
                                continue
                            yr = _extract_year(str(it.get("year_edition") or ""))
                            cp, yr_clean = add_pub_year(raw_pub, yr, True)
                            raw_coll = _html.unescape(str(it.get("item_series") or "")).strip()
                            if raw_coll and raw_coll.lower() not in ("null", "none"):
                                add_coll(raw_coll, 10, cp, yr_clean)
                except Exception:
                    pass

        def task_internet_archive() -> None:
            if is_cancelled and is_cancelled():
                return
            a_toks = a_fold.split() if a_fold else []
            creator_q = a_toks[-1] if a_toks else author.strip()
            if not creator_q:
                return
            q_expr = f'title:("{title.strip()}") AND creator:({creator_q}) AND mediatype:texts'
            url = (
                "https://archive.org/advancedsearch.php?"
                f"q={urllib.parse.quote(q_expr)}&fl[]=title&fl[]=creator&fl[]=publisher&fl[]=year&rows=25&output=json"
            )
            d = self._http_get_json(url, timeout=2.6, fast_fail=True, is_cancelled=is_cancelled)
            if not d:
                return
            for doc in (d.get("response") or {}).get("docs") or []:
                doc_title = str(doc.get("title") or "").strip()
                if not _book_edition_title_matches(doc_title, title):
                    continue
                doc_creator = doc.get("creator") or ""
                if isinstance(doc_creator, list):
                    doc_creator = " ".join(str(x) for x in doc_creator)
                if a_fold and str(doc_creator).strip() and not _author_matches(search_fold(str(doc_creator)), a_fold):
                    continue
                raw_pub = str(doc.get("publisher") or "").strip()
                if ":" in raw_pub:
                    raw_pub = raw_pub.split(":", 1)[1].strip()
                yr = _extract_year(str(doc.get("year") or ""))
                cp_ia = _clean_publisher_name(raw_pub, lang)
                add_pub_year(raw_pub, yr, bool(cp_ia and fold(cp_ia) in ITALIAN_PUBLISHERS_PRIORITY))

        pool = ThreadPoolExecutor(max_workers=7)
        futs = [
            pool.submit(task_google),
            pool.submit(task_sbn),
            pool.submit(task_openlib),
            pool.submit(task_abebooks),
            pool.submit(task_ibs),
            pool.submit(task_internet_archive),
        ]
        if work_id and re.fullmatch(r"/works/OL\d+W", work_id):
            futs.append(pool.submit(_ingest_ol_work, work_id))
        try:
            deadline = time.monotonic() + 3.0
            pending = set(futs)
            while pending:
                if is_cancelled and is_cancelled():
                    break
                rem = deadline - time.monotonic()
                if rem <= 0.0:
                    break
                done, pending = wait(pending, timeout=min(0.2, rem))
                for f in done:
                    try:
                        f.result()
                    except Exception:
                        pass
        finally:
            for f in futs:
                try:
                    f.cancel()
                except Exception:
                    pass
            pool.shutdown(wait=False, cancel_futures=True)

        with lock:
            pub_counts_it = dict(pub_counts_it)
            pub_counts_all = dict(pub_counts_all)
            years_set = set(years_set)
            pub_years_sets = {k: set(v) for k, v in pub_years_sets.items()}
            year_pubs_sets = {k: set(v) for k, v in year_pubs_sets.items()}
            coll_counts = dict(coll_counts)
            collection_meta = dict(collection_meta)
            publisher_years = dict(publisher_years)
            year_publishers = dict(year_publishers)
            pair_collections = {k: list(v) for k, v in pair_collections.items()}

        ranked_pubs: List[str] = []
        # Inserisci ESCLUSIVAMENTE gli editori effettivamente riscontrati per quest'opera
        if lang == "it" and pub_counts_it:
            # 1) Prima gli editori italiani attestati con maggiore frequenza per quest'opera
            for p, _ in sorted(pub_counts_it.items(), key=lambda kv: -kv[1]):
                if p not in ranked_pubs:
                    ranked_pubs.append(p)
        # 2) Poi gli altri editori internazionali o non-italiani attestati per il volume
        for p, _ in sorted(pub_counts_all.items(), key=lambda kv: -kv[1]):
            if p not in ranked_pubs:
                ranked_pubs.append(p)

        sorted_years = sorted(years_set)
        publisher_all_years: Dict[str, List[str]] = {}
        for p in ranked_pubs:
            yrs_for_p = sorted(pub_years_sets.get(p, set()))
            if yrs_for_p:
                publisher_all_years[p] = yrs_for_p
                publisher_years[p] = yrs_for_p[0]
                publisher_years[fold(p)] = yrs_for_p[0]
        for p, yrs_s in pub_years_sets.items():
            if p not in publisher_all_years and yrs_s:
                yrs_for_p = sorted(yrs_s)
                publisher_all_years[p] = yrs_for_p
                publisher_years[p] = yrs_for_p[0]
                publisher_years[fold(p)] = yrs_for_p[0]

        pub_rank_idx = {p: idx for idx, p in enumerate(ranked_pubs)}
        year_all_publishers: Dict[str, List[str]] = {}
        for yr, pubs_s in year_pubs_sets.items():
            ordered_pubs = sorted(pubs_s, key=lambda p: (pub_rank_idx.get(p, 9999), p))
            if ordered_pubs:
                year_all_publishers[yr] = ordered_pubs
                year_publishers[yr] = ordered_pubs[0]

        # Ordina le collane: solo quelle trovate direttamente per il libro su SBN/OpenLibrary;
        # usa le collane dell'editore principale solo come fallback se nessuna collana reale è emersa.
        ranked_colls: List[str] = [c for c, _ in sorted(coll_counts.items(), key=lambda kv: -kv[1])]
        if not ranked_colls:
            for p in ranked_pubs[:3]:
                p_yr = publisher_years.get(p, "")
                for c_pub in self.collections_for_publisher(p, "Libri"):
                    if c_pub not in ranked_colls:
                        ranked_colls.append(c_pub)
                    if c_pub not in collection_meta:
                        collection_meta[c_pub] = {"publisher": p, "year": p_yr}
                        collection_meta[fold(c_pub)] = {"publisher": p, "year": p_yr}

        default_pub = curated_cat.get("default_publisher") or (ranked_pubs[0] if ranked_pubs else "")
        default_yr = curated_cat.get("default_year") or ""
        if not default_yr and default_pub and publisher_all_years.get(default_pub):
            def_yrs = publisher_all_years[default_pub]
            default_yr = first_year if (first_year and first_year in def_yrs) else def_yrs[0]
        if not default_yr:
            default_yr = first_year or (sorted_years[0] if sorted_years else "")
        if default_yr and default_yr in sorted_years:
            sorted_years = [default_yr] + [y for y in sorted_years if y != default_yr]
        if default_pub and default_yr:
            if default_pub not in publisher_all_years:
                publisher_all_years[default_pub] = [default_yr]
            if default_pub not in publisher_years:
                publisher_years[default_pub] = default_yr
                publisher_years[fold(default_pub)] = default_yr
            if default_yr not in year_publishers:
                year_publishers[default_yr] = default_pub
            if default_yr not in year_all_publishers:
                year_all_publishers[default_yr] = [default_pub]

        return EditionBundle(
            category="Libri",
            work_id=work_id,
            title=title,
            author=author,
            author_sort=curated.get("author_sort") or surname_key(author, "Libri"),
            default_publisher=default_pub,
            default_year=default_yr,
            publishers=ranked_pubs[:30],
            years=sorted_years[:40],
            collections=ranked_colls[:35],
            collection_meta=collection_meta,
            publisher_years=publisher_years,
            year_publishers=year_publishers,
            year_collections=year_collections,
            publisher_collections=publisher_collections,
            publisher_all_years=publisher_all_years,
            year_all_publishers=year_all_publishers,
            pair_collections=pair_collections,
            edition_count=max(ed_total, len(ranked_pubs)),
        )

    def _fetch_music_editions(
        self,
        category: str,
        work_id: str,
        title: str,
        author: str,
        is_cancelled: Optional[Callable[[], bool]],
    ) -> EditionBundle:
        """
        Recupera le etichette discografiche, le collezioni/box set e gli anni di stampa filtrando
        rigorosamente per formato fisico:
        - CD: solo stampe CD / SACD / HDCD / SHM-CD (anni >= 1982)
        - Vinili: solo stampe LP / 12" Vinyl / 7" Vinyl / Vinyl
        """
        clean_title = _clean_album_title_suffixes(title)
        alt_title = re.sub(r"\s*\(live\)\s*$", "", clean_title, flags=re.IGNORECASE).strip()
        norm_key = f"{search_fold(clean_title)}|{search_fold(author)}"
        alt_key = f"{search_fold(alt_title)}|{search_fold(author)}"
        curated = CURATED_EDITION_PROFILES.get(norm_key) or CURATED_EDITION_PROFILES.get(alt_key) or {}
        curated_cat = curated.get(category, {})
        artist_sort = curated.get("artist_sort") or surname_key(author, category)

        target_t_raw = search_fold(clean_title)
        target_t_fold = search_fold(alt_title or clean_title)
        target_a_fold = search_fold(author)
        target_t_toks = [t for t in target_t_fold.split() if len(t) >= 3]
        target_a_toks = [t for t in target_a_fold.split() if len(t) >= 3]

        def _release_matches_target(rel_title: str, rel_artist: str) -> bool:
            rt_clean = _clean_album_title_suffixes(rel_title)
            rt_fold = search_fold(rt_clean)
            rt_no_live = search_fold(re.sub(r"\s*\(live\)\s*$", "", rt_clean, flags=re.IGNORECASE).strip())
            if not rt_fold:
                return False
            title_ok = False
            for tgt in ({target_t_raw, target_t_fold} - {""}):
                if rt_fold == tgt or rt_no_live == tgt or tgt in rt_fold or rt_fold in tgt:
                    title_ok = True
                    break
            if not title_ok and target_t_toks:
                rt_toks = set(rt_fold.split())
                title_ok = all(t in rt_toks for t in target_t_toks)
            if not title_ok:
                return False
            if rel_artist.strip() and target_a_fold:
                ra_fold = search_fold(rel_artist)
                if target_a_fold in ra_fold or ra_fold in target_a_fold:
                    return True
                ra_toks = set(ra_fold.split())
                if target_a_toks and not any(t in ra_toks or t in ra_fold for t in target_a_toks):
                    return False
            return True

        label_counts: Dict[str, int] = {}
        format_years: set[str] = set()
        fallback_labels: Dict[str, int] = {}
        fallback_years: set[str] = set()
        publisher_years: Dict[str, str] = {}
        year_publishers: Dict[str, str] = {}
        year_collections: Dict[str, str] = {}
        publisher_collections: Dict[str, str] = {}
        pub_years_sets: Dict[str, set[str]] = {}
        year_pubs_sets: Dict[str, set[str]] = {}
        fallback_pub_years_sets: Dict[str, set[str]] = {}
        fallback_year_pubs_sets: Dict[str, set[str]] = {}
        pair_collections: Dict[str, List[str]] = {}
        collection_meta: Dict[str, Dict[str, str]] = {}
        discovered_colls: List[str] = []
        matched_releases = 0

        for p in curated_cat.get("publishers", []):
            label_counts[p] = label_counts.get(p, 0) + 15
        for y in curated_cat.get("years", []):
            format_years.add(y)

        # Pre-popola le collezioni specifiche per l'artista e il formato (CD vs Vinili)
        for c_name in self.collections_for_publisher("", category, author):
            pub_c, yr_c = self.resolve_collection_metadata(c_name, category, author)
            if pub_c or yr_c:
                collection_meta[c_name] = {"publisher": pub_c, "year": yr_c}
                collection_meta[fold(c_name)] = {"publisher": pub_c, "year": yr_c}
                if pub_c and yr_c:
                    pk = f"{fold(pub_c)}|{yr_c}"
                    lst = pair_collections.setdefault(pk, [])
                    if all(fold(x) != fold(c_name) for x in lst):
                        lst.append(c_name)

        # 1) Query MusicBrainz Release con fallback automatico se rgid non trova stampe
        sort_from_mb = False

        def ingest_mb_releases(releases_list: list, strict_match: bool = False) -> None:
            nonlocal artist_sort, matched_releases, sort_from_mb
            for rel in releases_list:
                credits = rel.get("artist-credit") or []
                rel_artist_parts = []
                for c in credits:
                    np = c.get("name") or (c.get("artist") or {}).get("name") or ""
                    jp = c.get("joinphrase") or ""
                    rel_artist_parts.append(np + jp)
                rel_artist = "".join(rel_artist_parts).strip()
                rel_title = str(rel.get("title") or "").strip()
                if strict_match and not _release_matches_target(rel_title, rel_artist):
                    continue
                if credits and isinstance(credits[0].get("artist"), dict) and not sort_from_mb:
                    sn = str(credits[0]["artist"].get("sort-name") or "").strip()
                    if sn and sort_key_matches_author(sn, author):
                        artist_sort = sn
                        sort_from_mb = True
                media_list = rel.get("media") or []
                formats = [str(m.get("format") or "") for m in media_list if isinstance(m, dict)]
                if category == "CD":
                    is_match = any(_is_cd_format(f) for f in formats)
                else:
                    is_match = any(_is_vinyl_format(f) for f in formats)

                yr = _extract_year(rel.get("date") or "")
                is_valid_cd_yr = not (category == "CD" and yr and yr.isdigit() and int(yr) < 1982)

                labels_in_rel = []
                for li in rel.get("label-info") or []:
                    if isinstance(li, dict) and isinstance(li.get("label"), dict):
                        lname = str(li["label"].get("name") or "").strip()
                        if lname and lname.lower() not in ("[no label]", "not on label", "none"):
                            labels_in_rel.append(lname)

                disamb = str(rel.get("disambiguation") or "").strip()

                if is_match:
                    matched_releases += 1
                    if yr and is_valid_cd_yr:
                        format_years.add(yr)
                    for lname in labels_in_rel:
                        label_counts[lname] = label_counts.get(lname, 0) + 3
                        if yr and is_valid_cd_yr:
                            pub_years_sets.setdefault(lname, set()).add(yr)
                            year_pubs_sets.setdefault(yr, set()).add(lname)
                    # Se la disambiguazione di MusicBrainz indica una ristampa/box/edizione speciale, registrala!
                    if disamb and len(disamb) <= 42:
                        d_low = disamb.lower()
                        if any(
                            kw in d_low
                            for kw in (
                                "edition", "remaster", "box", "deluxe", "collector",
                                "anniversary", "discovery", "experience", "immersion",
                                "deagostini", "de agostini", "shm-cd", "sacd", "180g", "half-speed",
                            )
                        ):
                            c_title = disamb[0].upper() + disamb[1:]
                            if c_title not in discovered_colls:
                                discovered_colls.append(c_title)
                            first_lbl = labels_in_rel[0] if labels_in_rel else ""
                            if yr and is_valid_cd_yr and yr not in year_collections:
                                year_collections[yr] = c_title
                            if first_lbl:
                                if first_lbl not in publisher_collections:
                                    publisher_collections[first_lbl] = c_title
                                    publisher_collections[fold(first_lbl)] = c_title
                            if first_lbl and yr and is_valid_cd_yr:
                                pk = f"{fold(first_lbl)}|{yr}"
                                lst = pair_collections.setdefault(pk, [])
                                if all(fold(x) != fold(c_title) for x in lst):
                                    lst.append(c_title)
                            if first_lbl or (yr and is_valid_cd_yr):
                                meta = {"publisher": first_lbl, "year": yr if is_valid_cd_yr else ""}
                                collection_meta.setdefault(c_title, meta)
                                collection_meta.setdefault(fold(c_title), meta)
                else:
                    if yr and is_valid_cd_yr:
                        fallback_years.add(yr)
                    for lname in labels_in_rel:
                        fallback_labels[lname] = fallback_labels.get(lname, 0) + 1
                        if yr and is_valid_cd_yr:
                            fallback_pub_years_sets.setdefault(lname, set()).add(yr)
                            fallback_year_pubs_sets.setdefault(yr, set()).add(lname)

        norm_title = clean_title.replace("’", "'").replace("‘", "'").replace("`", "'").strip()
        norm_author = author.replace("’", "'").replace("‘", "'").replace("`", "'").strip()

        # 1A) Apple iTunes lookup veloce (~80ms) per garantire sempre almeno l'etichetta principale e l'anno
        if not (is_cancelled and is_cancelled()):
            it_url = (
                f"https://itunes.apple.com/search?term={urllib.parse.quote(norm_title + ' ' + norm_author)}"
                "&media=music&entity=album&country=IT&limit=8"
            )
            it_data = self._http_get_json(
                it_url, timeout=2.0, is_musicbrainz=False, fast_fail=True, is_cancelled=is_cancelled
            )
            if it_data and isinstance(it_data.get("results"), list):
                for row in it_data["results"]:
                    rt = _clean_album_title_suffixes(str(row.get("collectionName") or ""))
                    ra = str(row.get("artistName") or "").strip()
                    if _release_matches_target(rt, ra):
                        lbl = _extract_itunes_label(str(row.get("copyright") or ""))
                        yr = _extract_year(str(row.get("releaseDate") or ""))
                        valid_yr = yr if not (category == "CD" and yr.isdigit() and int(yr) < 1982) else ""
                        if lbl:
                            fallback_labels[lbl] = fallback_labels.get(lbl, 0) + 2
                            if valid_yr:
                                fallback_pub_years_sets.setdefault(lbl, set()).add(valid_yr)
                                fallback_year_pubs_sets.setdefault(valid_yr, set()).add(lbl)
                        if valid_yr:
                            fallback_years.add(valid_yr)

        # 1B) MusicBrainz Release Inspector: prova rgid (se presente), poi fallback automatico su titolo+artista
        if not (is_cancelled and is_cancelled()):
            if work_id and re.fullmatch(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                work_id,
            ):
                rg_url = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(f'rgid:{work_id}')}&fmt=json&limit=85"
                sdata = self._http_get_json(rg_url, timeout=4.2, is_musicbrainz=True, is_cancelled=is_cancelled)
                if sdata and isinstance(sdata.get("releases"), list):
                    ingest_mb_releases(sdata["releases"], strict_match=False)

            # Se work_id era assente o non ha restuito stampe con etichetta, cerca per titolo + artista!
            if (matched_releases < 2 or not label_counts) and not (is_cancelled and is_cancelled()):
                esc_t = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", norm_title)
                esc_a = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", norm_author)
                rel_q = f'release:"{esc_t}" AND artist:"{esc_a}"'
                rel_url = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(rel_q)}&fmt=json&limit=85"
                sdata2 = self._http_get_json(rel_url, timeout=4.2, is_musicbrainz=True, is_cancelled=is_cancelled)
                if sdata2 and isinstance(sdata2.get("releases"), list) and sdata2["releases"]:
                    ingest_mb_releases(sdata2["releases"], strict_match=True)
                if (matched_releases < 1 or not label_counts) and not (is_cancelled and is_cancelled()):
                    # Fallback tokenizzato con AND esplicito su tutte le parole (evita OR implicito Lucene)
                    t_words = [w for w in search_fold(alt_title or norm_title).split() if w]
                    a_words = [w for w in search_fold(norm_author).split() if w]
                    if t_words and a_words:
                        rel_q3 = f'release:({" AND ".join(t_words)}) AND artist:({" AND ".join(a_words)})'
                        rel_url3 = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(rel_q3)}&fmt=json&limit=65"
                        sdata3 = self._http_get_json(rel_url3, timeout=4.2, is_musicbrainz=True, is_cancelled=is_cancelled)
                        if sdata3 and isinstance(sdata3.get("releases"), list):
                            ingest_mb_releases(sdata3["releases"], strict_match=True)

        # 2) Se abbiamo anche Discogs Token, arricchisci con le stampe fisiche Discogs
        clean_discogs = str(self.discogs_token or "").strip()
        if clean_discogs and "\r" not in clean_discogs and "\n" not in clean_discogs and not (is_cancelled and is_cancelled()):
            fmt_filter = "CD" if category == "CD" else "Vinyl"
            d_url = (
                "https://api.discogs.com/database/search?"
                f"release_title={urllib.parse.quote(norm_title)}&artist={urllib.parse.quote(norm_author)}"
                f"&type=release&format={fmt_filter}&per_page=35"
            )
            d_data = self._http_get_json(
                d_url,
                timeout=3.5,
                extra_headers={"Authorization": f"Discogs token={clean_discogs}"},
                is_cancelled=is_cancelled,
            )
            if d_data and isinstance(d_data.get("results"), list):
                for r in d_data["results"]:
                    raw_full = str(r.get("title") or "").strip()
                    if " - " in raw_full:
                        d_artist, d_title = [p.strip() for p in raw_full.split(" - ", 1)]
                        d_artist = re.sub(r"\s*(\(\d+\)|\*)\s*$", "", d_artist).rstrip("* ").strip()
                    else:
                        d_artist, d_title = "", raw_full
                    if d_title and not _release_matches_target(d_title, d_artist):
                        continue
                    matched_releases += 1
                    yr = _extract_year(str(r.get("year") or ""))
                    if category == "CD" and yr and yr.isdigit() and int(yr) < 1982:
                        yr = ""
                    if yr:
                        format_years.add(yr)
                    for raw_l in (r.get("label") or [])[:4]:
                        lname = re.sub(r"\s*\(\d+\)\s*$", "", str(raw_l).strip())
                        if lname and lname.lower() not in ("not on label", "none"):
                            label_counts[lname] = label_counts.get(lname, 0) + 2
                            if yr:
                                pub_years_sets.setdefault(lname, set()).add(yr)
                                year_pubs_sets.setdefault(yr, set()).add(lname)

        ranked_labels = [k for k, _ in sorted(label_counts.items(), key=lambda kv: -kv[1])]
        if not ranked_labels:
            ranked_labels = [k for k, _ in sorted(fallback_labels.items(), key=lambda kv: -kv[1])]
            for k, yrs_s in fallback_pub_years_sets.items():
                pub_years_sets.setdefault(k, set()).update(yrs_s)
            for yr, pubs_s in fallback_year_pubs_sets.items():
                year_pubs_sets.setdefault(yr, set()).update(pubs_s)
        else:
            for lbl in ranked_labels:
                if not pub_years_sets.get(lbl) and fallback_pub_years_sets.get(lbl):
                    pub_years_sets.setdefault(lbl, set()).update(fallback_pub_years_sets[lbl])

        sorted_years = sorted(format_years) if format_years else sorted(fallback_years)
        for yr in sorted_years:
            if not year_pubs_sets.get(yr) and fallback_year_pubs_sets.get(yr):
                year_pubs_sets.setdefault(yr, set()).update(fallback_year_pubs_sets[yr])

        publisher_all_years: Dict[str, List[str]] = {}
        for lbl in ranked_labels:
            yrs_for_l = sorted(pub_years_sets.get(lbl, set()))
            if yrs_for_l:
                publisher_all_years[lbl] = yrs_for_l
                publisher_years[lbl] = yrs_for_l[0]
                publisher_years[fold(lbl)] = yrs_for_l[0]
        for lbl, yrs_s in pub_years_sets.items():
            if lbl not in publisher_all_years and yrs_s:
                yrs_for_l = sorted(yrs_s)
                publisher_all_years[lbl] = yrs_for_l
                publisher_years[lbl] = yrs_for_l[0]
                publisher_years[fold(lbl)] = yrs_for_l[0]

        lbl_rank_idx = {lbl: idx for idx, lbl in enumerate(ranked_labels)}
        year_all_publishers: Dict[str, List[str]] = {}
        for yr, pubs_s in year_pubs_sets.items():
            ordered_pubs = sorted(pubs_s, key=lambda p: (lbl_rank_idx.get(p, 9999), p))
            if ordered_pubs:
                year_all_publishers[yr] = ordered_pubs
                year_publishers[yr] = ordered_pubs[0]

        default_label = curated_cat.get("default_publisher") or (ranked_labels[0] if ranked_labels else "")
        default_yr = curated_cat.get("default_year") or ""
        if not default_yr and default_label and publisher_all_years.get(default_label):
            default_yr = publisher_all_years[default_label][0]
        if not default_yr and sorted_years:
            default_yr = sorted_years[0]
        if default_yr and default_yr in sorted_years:
            sorted_years = [default_yr] + [y for y in sorted_years if y != default_yr]
        if default_label and default_yr:
            if default_label not in publisher_all_years:
                publisher_all_years[default_label] = [default_yr]
            if default_label not in publisher_years:
                publisher_years[default_label] = default_yr
                publisher_years[fold(default_label)] = default_yr
            if default_yr not in year_publishers:
                year_publishers[default_yr] = default_label
            if default_yr not in year_all_publishers:
                year_all_publishers[default_yr] = [default_label]

        # Unisce le collezioni specifiche dell'artista/formato con quelle rilevate da MusicBrainz
        final_colls: List[str] = []
        for c in self.collections_for_publisher(default_label, category, author) + discovered_colls:
            if c and c not in final_colls:
                final_colls.append(c)

        return EditionBundle(
            category=category,
            work_id=work_id,
            title=title,
            author=author,
            author_sort=artist_sort,
            default_publisher=default_label,
            default_year=default_yr,
            publishers=ranked_labels[:30],
            years=sorted_years[:40],
            collections=final_colls[:30],
            collection_meta=collection_meta,
            publisher_years=publisher_years,
            year_publishers=year_publishers,
            year_collections=year_collections,
            publisher_collections=publisher_collections,
            publisher_all_years=publisher_all_years,
            year_all_publishers=year_all_publishers,
            pair_collections=pair_collections,
            edition_count=matched_releases or len(ranked_labels),
        )
