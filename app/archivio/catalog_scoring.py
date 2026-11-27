"""Funzioni di normalizzazione, parsing metadati, codici ISBN/EAN e scoring per il catalogo."""
from __future__ import annotations

import difflib
from functools import lru_cache
import math
import re
from typing import Dict, List, Optional, Tuple

from .catalog_data import (
    CD_FORMAT_KEYWORDS,
    ITALIAN_PUBLISHERS_PRIORITY,
    NOISE_BOOK_WORDS,
    NOISE_TITLE_WORDS,
    NON_BOOK_PUBLISHERS_FOLD,
    PUBLISHER_COLLECTIONS_IT,
    SEARCH_STOPWORDS,
    VINYL_FORMAT_KEYWORDS,
    _CITY_ONLY_PUBLISHERS_FOLD,
    _ITALIAN_PUBS_BY_LEN,
    _PUBLISHER_HINT_ENTRIES,
)
from .textutil import fold, search_fold, surname_key


try:
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process
except ImportError:
    _rf_fuzz = None
    _rf_process = None
import difflib


def _read_response_bounded(resp, max_bytes: int = 4 * 1024 * 1024) -> bytes:
    """Legge la risposta HTTP limitando i byte massimi in memoria."""
    try:
        data = resp.read(max_bytes + 1)
    except TypeError:
        data = resp.read()
    if len(data) > max_bytes:
        raise ValueError(f"HTTP response exceeds {max_bytes} bytes")
    return data


def _fuzzy_score(a: str, b: str) -> float:
    """Restituisce un indice di similarità 0..100 tra due stringhe usando RapidFuzz C++ (o difflib come fallback)."""
    if not a or not b:
        return 0.0
    if _rf_fuzz is not None:
        # Usa ratio e token_sort_ratio; evita WRatio perché causa falsi positivi (score 85.5)
        # su stringhe che condividono solo articoli/stopword brevissime (es. "il", "re")
        r = float(_rf_fuzz.ratio(a, b))
        ts = float(_rf_fuzz.token_sort_ratio(a, b))
        return max(r, ts)
    r_full = difflib.SequenceMatcher(None, a, b).ratio() * 100.0
    if len(a) < len(b) and len(a) >= 4:
        # Calcola anche similarità sul prefisso di pari lunghezza
        r_pref = difflib.SequenceMatcher(None, a, b[: len(a) + 2]).ratio() * 100.0
        return max(r_full, r_pref)
    return r_full


def _direct_ratio(a: str, b: str) -> float:
    """Similarità carattere-per-carattere (0..100) sull'intera stringa per premiare i refusi diretti."""
    if not a or not b:
        return 0.0
    if _rf_fuzz is not None:
        return float(_rf_fuzz.ratio(a, b))
    return difflib.SequenceMatcher(None, a, b).ratio() * 100.0


def _direct_ratio_cut(a: str, b: str, cutoff: float) -> float:
    """
    Come `_direct_ratio`, ma con soglia: sopra `cutoff` restituisce il valore esatto,
    sotto restituisce 0.0 (RapidFuzz scarta subito le coppie troppo diverse, molto più veloce).
    """
    if not a or not b:
        return 0.0
    if _rf_fuzz is not None:
        # Limite teorico: ratio <= 200*min/(la+lb). Se le lunghezze sono troppo diverse è inutile calcolarla
        la, lb = len(a), len(b)
        if 200.0 * min(la, lb) < cutoff * (la + lb):
            return 0.0
        return float(_rf_fuzz.ratio(a, b, score_cutoff=cutoff))
    r = difflib.SequenceMatcher(None, a, b).ratio() * 100.0
    return r if r >= cutoff else 0.0


def _fuzzy_score_cut(a: str, b: str, cutoff: float) -> float:
    """Come `_fuzzy_score`, ma con soglia: sotto `cutoff` restituisce 0.0, altrimenti il valore esatto."""
    if not a or not b:
        return 0.0
    if _rf_fuzz is not None:
        la, lb = len(a), len(b)
        if 200.0 * min(la, lb) < cutoff * (la + lb):
            return 0.0  # lunghezze troppo diverse: né ratio né token_sort_ratio possono raggiungere la soglia
        r = float(_rf_fuzz.ratio(a, b, score_cutoff=cutoff))
        ts = float(_rf_fuzz.token_sort_ratio(a, b, score_cutoff=cutoff))
        return max(r, ts)
    r = _fuzzy_score(a, b)
    return r if r >= cutoff else 0.0
def _parse_abebooks_publisher_field(raw_str: str, lang: str = "it") -> Tuple[str, str, str]:
    """
    Estrae (editore_pulito, anno, collana) da una stringa editore di AbeBooks
    (es. ', Milano : Mondadori, 1971' -> ('Mondadori', '1971', ''),
    'Torino, Giulio Einaudi, Editore 1943., 1943' -> ('Einaudi', '1943', ''),
    'MONDADORI 1976, Xxiii Ed. . Collana: Lo Specchio, 1976' -> ('Mondadori', '1976', 'Lo Specchio')).
    """
    s = re.sub(r"^(?:Editore|Publisher)\s*:\s*", "", raw_str or "", flags=re.IGNORECASE).strip()
    if not s:
        return "", "", ""

    yr = _extract_year(s)
    coll = ""
    m_coll = re.search(r"\b(?:collana|collezione|serie|series)\s*:\s*([^,;]+)", s, flags=re.IGNORECASE)
    if m_coll:
        coll = m_coll.group(1).strip(" .,-;:/")
        s = (s[: m_coll.start()] + " " + s[m_coll.end() :]).strip()

    if coll:
        c_fold = fold(coll)
        for _p_cands in PUBLISHER_COLLECTIONS_IT.values():
            for _kc in _p_cands:
                if fold(_kc) == c_fold:
                    coll = _kc
                    break

    s_clean = re.sub(r"\b(?:\d+[ªa°]?|[ivxlcdm]+)\s+ed(?:izione|\.)?\b", " ", s, flags=re.IGNORECASE)
    s_clean = re.sub(r"\b(?:edizioni?\s+italiana|italian\s+edition|ristampa|anni\s+'?\d{2})\b", " ", s_clean, flags=re.IGNORECASE)
    s_clean = re.sub(
        r"\b(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\b",
        " ",
        s_clean,
        flags=re.IGNORECASE,
    )
    s_clean = re.sub(r"\b(1[4-9]\d{2}|20\d{2})\b", " ", s_clean)

    best_pub = ""
    best_is_priority = False
    for seg in re.split(r"\s*(?:[,;:/|]+|\s+-\s+)\s*", s_clean):
        seg_c = seg.strip(" .,-()[]'\"")
        if not seg_c or len(seg_c) < 2:
            continue
        seg_fold = fold(seg_c)
        if re.fullmatch(r"[\W_\d]+", seg_fold) or seg_fold in _CITY_ONLY_PUBLISHERS_FOLD:
            continue
        if seg_fold in NON_BOOK_PUBLISHERS_FOLD:
            return "", "", ""
        cp = _clean_publisher_name(seg_c, lang)
        if not cp:
            continue
        cp_fold = fold(cp)
        if cp_fold in NON_BOOK_PUBLISHERS_FOLD:
            return "", "", ""
        if cp_fold in _CITY_ONLY_PUBLISHERS_FOLD:
            continue
        is_prio = cp_fold in ITALIAN_PUBLISHERS_PRIORITY
        if not best_pub or (is_prio and not best_is_priority):
            best_pub = cp
            best_is_priority = is_prio
            if is_prio:
                break

    if best_pub and not coll:
        s_fold = fold(s)
        p_fold = fold(best_pub)
        for known_c in sorted(PUBLISHER_COLLECTIONS_IT.get(best_pub, []), key=len, reverse=True):
            kc_fold = fold(known_c)
            if kc_fold and kc_fold != p_fold and kc_fold in s_fold:
                coll = known_c
                break

    return best_pub, yr, coll


def _book_edition_title_matches(cand_title: str, target_title: str) -> bool:
    """
    Verifica che il titolo candidato si riferisca effettivamente all'opera cercata
    escludendo guide alla lettura, analisi critiche, riassunti o biografie derivate.
    """
    c_raw = _clean_sbn_text(cand_title or "")
    if " / " in c_raw:
        c_raw = c_raw.split(" / ", 1)[0].strip()
    c_fold = search_fold(c_raw)
    t_fold = search_fold(target_title or "")
    if not c_fold or not t_fold:
        return False

    for nw in NOISE_BOOK_WORDS:
        if nw in c_fold and nw not in t_fold:
            return False
    for extra_noise in (
        "e i suoi", "100 anni dopo", "uno alla volta", "da colorare",
        "trascritti con amore", "interpretazione fotografica", "centenario",
        "quaderni ", "concordanza", "antologia critica",
    ):
        if extra_noise in c_fold and extra_noise not in t_fold:
            return False

    c_base = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", c_raw)
    c_base_fold = search_fold(c_base) or c_fold
    c_main = c_base_fold.split(":")[0].split(" - ")[0].strip() or c_base_fold

    if len(t_fold) <= 3:
        return c_main == t_fold or c_base_fold == t_fold or c_fold == t_fold

    t_words = [w for w in t_fold.split() if w not in SEARCH_STOPWORDS and len(w) > 2]
    if not t_words:
        t_words = [w for w in t_fold.split() if w]

    c_words = set(c_fold.split())
    if t_words and all((w in c_words) or (len(w) >= 4 and w in c_fold) for w in t_words):
        return True
    if t_fold in c_fold or c_main == t_fold:
        return True
    return False


def _clean_publisher_name(pub: str, lang: str = "it") -> str:
    p = re.sub(r"\s+", " ", (pub or "").strip()).strip(",.;:/-[]()! ")
    if not p or len(p) < 2:
        return ""
    low = fold(p)
    if (
        re.fullmatch(r"[\W_\d]+", low)
        or low in _CITY_ONLY_PUBLISHERS_FOLD
        or low in ("s.n.", "s.n", "sn", "s.l.", "s.l", "s.n.t", "s.n.t.", "s. n", "s.e.", "unknown", "no label", "not on label")
        or re.fullmatch(r"s\.?\s*[nlte]\.?(?:\s*t\.?)?!?", low)
        or any(arc in low for arc in ("stamperia", "tipografia", "tip.", "presso ", "pe' torchi", "in casa de"))
    ):
        return ""
    if lang == "it":
        if low in ("tascabili economici newton", "roma ten", "ten"):
            return "Newton Compton"
        if low in ("unione tipografico editrice torinese", "unione tipografico-editrice torinese"):
            return "UTET"
        if low in ITALIAN_PUBLISHERS_PRIORITY:
            return ITALIAN_PUBLISHERS_PRIORITY[low]
        for k, canonical in _ITALIAN_PUBS_BY_LEN:
            ks = k.strip()
            if ks == low or low.startswith(ks + " ") or low.endswith(" " + ks):
                return canonical
    return p


def _publishers_match(a: str, b: str) -> bool:
    """
    True se i due nomi di editore/etichetta si riferiscono allo stesso editore
    tramite uguaglianza normalizzata o inclusione di parole intere (mai sottostringhe arbitrarie).
    """
    fa = fold(a)
    fb = fold(b)
    sa = search_fold(a)
    sb = search_fold(b)
    if not sa or not sb:
        return False
    if fa == fb or sa == sb:
        return True
    ca = re.sub(r"\s+", "", sa)
    cb = re.sub(r"\s+", "", sb)
    if len(ca) >= 3 and ca == cb:
        return True
    ta = [t for t in sa.split() if t]
    tb = [t for t in sb.split() if t]
    if not ta or not tb:
        return False
    short, long_list = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    long_set = set(long_list)
    if not all(t in long_set for t in short):
        return False
    if any(len(t) >= 3 for t in short):
        return True
    if len(short) >= 2 and sum(len(t) for t in short) >= 3:
        return True
    if len(short) >= 2 and f" {' '.join(short)} " in f" {' '.join(long_list)} ":
        return True
    return False


def _extract_year(text: str) -> str:
    if not text:
        return ""
    m = re.search(r"\b(1[4-9]\d{2}|20\d{2})\b", str(text))
    return m.group(1) if m else ""


def _parse_sbn_publication(pub_str: str, lang: str = "it") -> Tuple[str, str]:
    """
    Estrae editore e anno da una stringa SBN (es. 'Milano : Bompiani, 1980' -> ('Bompiani', '1980')).
    """
    if not pub_str:
        return "", ""
    yr = _extract_year(pub_str)
    s = pub_str
    if ":" in s:
        s = s.split(":", 1)[1]
    if "," in s:
        s = s.split(",", 1)[0]
    s = re.sub(r"\b(1[4-9]\d{2}|20\d{2})\b", "", s)
    s = re.sub(r"\[.*?\]", "", s).strip(" ,.;:/-")
    return _clean_publisher_name(s, lang), yr


def _clean_sbn_author(raw_author: str) -> Tuple[str, str]:
    """
    Converte un autore SBN (es. 'Eco, Umberto <1932-2016>') in ('Umberto Eco', 'Eco, Umberto').
    """
    a = re.sub(r"<[^>]*>", "", raw_author or "").strip(" ,.;:")
    if not a:
        return "", ""
    if "," in a:
        parts = [p.strip() for p in a.split(",", 1)]
        if len(parts) == 2 and parts[0] and parts[1]:
            display = f"{parts[1]} {parts[0]}"
            sort_k = f"{parts[0]}, {parts[1]}"
            return display, sort_k
    return a, surname_key(a, "Libri")


def _is_isbn_or_barcode(query: str) -> str:
    """
    Riconosce se una query è un codice ISBN-10, ISBN-13, EAN-13 (13 cifre),
    UPC-A (12 cifre, standard USA/Canada per CD e Vinili) o EAN-8 (8 cifre),
    ignorando trattini e spazi.
    Restituisce la stringa normalizzata oppure '' se non è un codice numerico/ISBN.
    """
    cleaned = re.sub(r"[\s\-]", "", (query or "").strip()).upper()
    if len(cleaned) in (8, 12, 13) and cleaned.isdigit():
        return cleaned
    if len(cleaned) == 10 and cleaned[:9].isdigit() and (cleaned[9].isdigit() or cleaned[9] == "X"):
        return cleaned
    return ""


def _isbn10_to_13(isbn10: str) -> str:
    if len(isbn10) != 10 or not isbn10[:9].isdigit():
        return ""
    core = "978" + isbn10[:9]
    total = sum(int(ch) * (1 if idx % 2 == 0 else 3) for idx, ch in enumerate(core))
    check = (10 - (total % 10)) % 10
    return f"{core}{check}"


def _isbn13_to_10(isbn13: str) -> str:
    if len(isbn13) != 13 or not isbn13.isdigit() or not isbn13.startswith("978"):
        return ""
    core = isbn13[3:12]
    total = sum(int(ch) * (10 - idx) for idx, ch in enumerate(core))
    rem = (11 - (total % 11)) % 11
    check = "X" if rem == 10 else str(rem)
    return f"{core}{check}"


def _barcode_variants(code: str) -> List[str]:
    """
    Restituisce le varianti equivalenti GS1/ISBN:
    - conversione bidirezionale ISBN-10 <-> ISBN-13 (prefisso 978)
    - conversione UPC-A (12 cifre) <-> EAN-13 (13 cifre con 0 iniziale)
    """
    c = re.sub(r"[\s\-]", "", (code or "").strip()).upper()
    if not c:
        return []
    out: List[str] = [c]
    if len(c) == 10 and c[:9].isdigit() and (c[9].isdigit() or c[9] == "X"):
        i13 = _isbn10_to_13(c)
        if i13 and i13 not in out:
            out.append(i13)
    elif len(c) == 13 and c.isdigit():
        if c.startswith("978"):
            i10 = _isbn13_to_10(c)
            if i10 and i10 not in out:
                out.append(i10)
        if c.startswith("0"):
            upc = c[1:]
            if upc not in out:
                out.append(upc)
    elif len(c) == 12 and c.isdigit():
        ean13 = "0" + c
        if ean13 not in out:
            out.append(ean13)
    return out


def _clean_sbn_text(raw: str) -> str:
    """Rimuove marcatori UNIMARC di non-ordinamento (ˆ, ‰, \\x88, \\x89, <<, >>) dalle stringhe OPAC SBN."""
    if not raw:
        return ""
    s = re.sub(r"[\x88\x89\u02c6\u2030ˆ‰«»]", "", str(raw))
    return re.sub(r"\s+", " ", s).strip()


def _clean_sbn_title(raw_title: str) -> str:
    """Pulisce il titolo ISBD SBN (es. 'ˆIl ‰nome della rosa / Umberto Eco' -> 'Il nome della rosa')."""
    t = _clean_sbn_text(raw_title)
    if " / " in t:
        t = t.split(" / ", 1)[0].strip()
    if " ; " in t:
        t = t.split(" ; ", 1)[0].strip()
    # Rimuove prefissi numerici di sottocollana tipo '[1]: Nulla per caso' o '11: Il figlio sbagliato'
    t = re.sub(r"^\[\d+\]\s*:\s*", "", t).strip()
    t = re.sub(r"^\d{1,2}\s*:\s+(?=[A-ZÀ-Ž])", "", t).strip()
    if " : " in t:
        main_t, sub_t = t.split(" : ", 1)
        sub_low = sub_t.strip().lower()
        if len(main_t.strip()) >= 3 and (
            "autore:" in sub_low
            or len(sub_t) > 35
            or sub_low.startswith(("romanzo", "racconto", "racconti", "poesie", "commedia", "tragedia", "dramma"))
        ):
            t = main_t.strip()
        else:
            t = f"{main_t.strip()}: {sub_t.strip()}"
    return t.strip(" /:;")


def _extract_sbn_fallback_author(raw_title: str) -> Tuple[str, str]:
    """
    Quando `autorePrincipale` è vuoto in SBN (es. 'Abel : un western metafisico / Alessandro Baricco'
    oppure 'Sharon e mia suocera / Suad Amiry ; a cura di Maria Nadotti'),
    estrae l'autore dalla responsabilità dopo ' / ' nel campo titolo ISBD.
    """
    t = _clean_sbn_text(raw_title)
    if " / " not in t:
        return "", ""
    after_slash = t.split(" / ", 1)[1].strip()
    first_stmt = after_slash.split(" ; ", 1)[0].strip()
    first_stmt = re.sub(
        r"^(?:di|by|a cura di|curato da|edited by|scritto da|testi di)\s+",
        "",
        first_stmt,
        flags=re.IGNORECASE,
    ).strip(" ,.;:[]()")
    if not first_stmt or len(first_stmt) < 3 or len(first_stmt) > 65:
        return "", ""
    # Se contiene più autori separati da virgola o ' e ', prende il primo
    if " , " in first_stmt:
        first_stmt = first_stmt.split(" , ", 1)[0].strip()
    return _clean_sbn_author(first_stmt)


def _parse_sbn_collection(raw_coll: str) -> Tuple[str, str]:
    """
    Estrae il nome della collana e l'eventuale versione numerata da una stringa SBN
    (es. 'Piccola biblioteca Adelphi ; 12' -> ('Piccola biblioteca Adelphi', 'Piccola biblioteca Adelphi #12')).
    """
    c = _clean_sbn_text(raw_coll)
    if not c:
        return "", ""
    if " / " in c:
        c = c.split(" / ", 1)[0].strip()
    num_part = ""
    if " ; " in c:
        base, right = c.split(" ; ", 1)
        base = base.strip(" ,.;:/-")
        m_num = re.search(r"\b(\d{1,5})\b", right)
        if m_num:
            num_part = m_num.group(1)
        c = base
    else:
        c = c.strip(" ,.;:/-")
    if not c or len(c) < 2 or fold(c) in ("scheda di lettura", "varia", "nessuna"):
        return "", ""
    # Normalizza 'BUR. Scienza' -> 'BUR Scienza'
    c = re.sub(r"\.\s+", " ", c).strip()
    numbered = f"{c} #{num_part}" if num_part else ""
    return c, numbered


def _extract_itunes_label(copyright_str: str) -> str:
    """Estrae l'etichetta discografica dal campo copyright di Apple iTunes."""
    if not copyright_str:
        return ""
    s = re.sub(r"^[℗©\s\d/\-]+", "", copyright_str).strip()
    # Se contiene 'under exclusive license to X' o 'marketed by X', o 'a division of'
    for marker in ("under exclusive license to ", "marketed and distributed by ", "distributed by "):
        if marker in s.lower():
            idx = s.lower().index(marker) + len(marker)
            s = s[idx:].strip()
            break
    s = s.split(",")[0].split(";")[0].strip()
    s = re.sub(
        r"\b(Records?\s+Ltd\.?|Ltd\.?|Inc\.?|LLC|GmbH|S\.?p\.?A\.?|S\.?r\.?l\.?|Holdings?|Entertainment|Music\s+Group)\b\.?",
        "",
        s,
        flags=re.IGNORECASE,
    ).strip(" ,.-/")
    s = re.sub(r"^(?:an|a)\s+", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s+releases?$", "", s, flags=re.IGNORECASE).strip(" ,.-/")
    if len(s) > 38 or len(s) < 2:
        return ""
    return s


def _is_cd_format(fmt: str) -> bool:
    f = (fmt or "").lower()
    if "vinyl" in f or "cassette" in f or "digital" in f:
        return False
    return any(k in f for k in CD_FORMAT_KEYWORDS)


def _is_vinyl_format(fmt: str) -> bool:
    f = (fmt or "").lower()
    return any(k in f for k in VINYL_FORMAT_KEYWORDS)


@lru_cache(maxsize=262144)
def _author_matches(cand_author_norm: str, ctx_author_norm: str) -> bool:
    """
    Verifica se l'autore/artista candidato corrisponde all'autore/artista già
    presente nel campo contestuale, tollerando cognomi singoli e refusi (es. 'punk floyd').

    Funzione pura sulle due stringhe normalizzate: il risultato è memorizzato in cache, quindi mentre
    l'utente scrive il titolo (autore fisso) ogni coppia viene valutata una sola volta.
    """
    if not cand_author_norm or not ctx_author_norm:
        return False
    if cand_author_norm == ctx_author_norm:
        return True
    cand_words = cand_author_norm.split()
    ctx_words = ctx_author_norm.split()
    if ctx_words and all(
        any(w == cw or (len(cw) >= 3 and w.startswith(cw)) for w in cand_words)
        for cw in ctx_words
    ):
        return True
    if len(ctx_author_norm) >= 4 and (
        ctx_author_norm in cand_author_norm or cand_author_norm in ctx_author_norm
    ):
        return True
    if len(ctx_author_norm) >= 4:
        if _fuzzy_score_cut(ctx_author_norm, cand_author_norm, 82.0) >= 82.0:
            return True
    return False


def _popularity_bonus(popularity: int, is_catalog: bool = False) -> float:
    """
    Curva di popolarità calibrata per premiare nettamente i grandi classici e gli album/libri
    più famosi rispetto a pubblicazioni minori o omonime oscure.
    """
    bonus = 0.0
    if popularity > 0:
        bonus += math.log10(max(popularity, 1) + 1) * 950.0
        if popularity >= 25000:
            bonus += 1100.0
        elif popularity >= 8000:
            bonus += 650.0
        elif popularity >= 2500:
            bonus += 300.0
    if is_catalog:
        bonus += 1150.0
    return bonus


@lru_cache(maxsize=4096)
def _extract_query_hints(
    query_norm: str,
    category: str = "Libri",
    lang: str = "it",
) -> Tuple[str, Tuple[str, ...], Tuple[str, ...], str]:
    """
    Estrae dalla query eventuali suggerimenti impliciti di editore (es. 'ossi di sep cde' -> 'cde' -> 'CDE')
    o anno (es. 'ossi di seppia 1948') lasciando intatta la parte principale di ricerca,
    solo quando rimane almeno una parola significativa del titolo/autore.
    """
    toks = [t for t in (query_norm or "").split() if t]
    if len(toks) < 2:
        return (query_norm or "").strip(), tuple(toks), (), ""

    used = [False] * len(toks)
    hint_pubs: List[str] = []
    hint_year = ""

    def _has_substantive_remaining(cand_used: List[bool]) -> bool:
        return any(
            not cand_used[idx] and toks[idx] not in SEARCH_STOPWORDS and len(toks[idx]) >= 2
            for idx in range(len(toks))
        )

    if category in ("", "Libri"):
        for _pf, p_toks, canonical in _PUBLISHER_HINT_ENTRIES:
            plen = len(p_toks)
            if plen >= len(toks):
                continue
            for start in range(len(toks) - plen, -1, -1):
                if any(used[start + k] for k in range(plen)):
                    continue
                if tuple(toks[start : start + plen]) == p_toks:
                    trial = list(used)
                    for k in range(plen):
                        trial[start + k] = True
                    if _has_substantive_remaining(trial):
                        used = trial
                        if canonical not in hint_pubs:
                            hint_pubs.append(canonical)
                        break

    # Estrai un eventuale anno finale/secondario (1400-2099), mai in posizione 0 (es. "1984 orwell")
    for idx in range(len(toks) - 1, 0, -1):
        if used[idx]:
            continue
        t = toks[idx]
        if len(t) == 4 and t.isdigit():
            yr_n = int(t)
            if 1400 <= yr_n <= 2099:
                trial = list(used)
                trial[idx] = True
                if any(
                    not trial[j] and toks[j] not in SEARCH_STOPWORDS and len(toks[j]) >= 2 and not toks[j].isdigit()
                    for j in range(len(toks))
                ):
                    used = trial
                    hint_year = t
                    break

    core_toks = tuple(toks[i] for i in range(len(toks)) if not used[i])
    if not core_toks:
        return (query_norm or "").strip(), tuple(toks), (), ""
    return " ".join(core_toks), core_toks, tuple(hint_pubs), hint_year


def _score_title_within_author(
    query_norm: str,
    query_tokens: List[str],
    title_norm: str,
    popularity: int,
    is_archive: bool = False,
    lang_match: bool = False,
    is_catalog: bool = False,
    publishers: Tuple[str, ...] = (),
    years: Tuple[str, ...] = (),
    hint_publishers: Tuple[str, ...] = (),
    hint_year: str = "",
    category: str = "Libri",
) -> float:
    """
    Calcola il punteggio di un titolo quando l'autore/artista è già stato fissato nel campo Artista/Autore.
    Supporta query vuota (mostra tutta la discografia/bibliografia dell'artista), prefissi brevi ('t', 'the'),
    titoli interrotti con parole extra o editore/anno in coda ('ossi di sep cde') e tolleranza agli errori di battitura.
    """
    if not title_norm:
        return -1.0

    # Se il campo Titolo è vuoto ma l'Artista/Autore è compilato, restituisci tutte le opere dell'artista
    # ordinate dalla più popolare alla meno popolare
    if not query_norm:
        score = 2000.0 + _popularity_bonus(popularity, is_catalog=is_catalog)
        if is_archive:
            score += 600.0
        if lang_match:
            score += 450.0
        return score

    title_words = title_norm.split()
    main_title = re.sub(r"\s+(?:1[4-9]\d{2}|20\d{2})(?:\s+(?:1[4-9]\d{2}|20\d{2}))?\s*$", "", title_norm).strip()
    main_title = main_title.split(":")[0].split(" - ")[0].split(" — ")[0].split(". ")[0].split("/")[0].strip() or title_norm
    title_alpha_sub = [w for w in main_title.split() if w not in SEARCH_STOPWORDS and len(w) >= 2 and not w.isdigit()]

    def _eval_title(eff_q: str, eff_tokens: List[str]) -> float:
        if not eff_q or not eff_tokens:
            return -1.0
        sc = 0.0
        if title_norm == eff_q or main_title == eff_q:
            sc += 6000.0
        elif title_norm.startswith(eff_q) or main_title.startswith(eff_q):
            sc += 4200.0
        elif (" " + eff_q) in (" " + title_norm) or (" " + eff_q) in (" " + main_title):
            sc += 2800.0
        elif len(eff_q) >= 4 and eff_q in title_norm:
            sc += 1400.0

        all_tokens_ok = True
        for qt in eff_tokens:
            if any(w.startswith(qt) for w in title_words):
                sc += 350.0
            elif len(qt) >= 4 and qt in title_norm:
                sc += 180.0
            elif len(qt) >= 4 and any(_direct_ratio_cut(qt, w, 80.0) >= 80.0 for w in title_words if len(w) >= 3):
                sc += 260.0
            else:
                all_tokens_ok = False

        if not all_tokens_ok:
            sub_q = [t for t in eff_tokens if t not in SEARCH_STOPWORDS and len(t) >= 2]
            if not sub_q:
                sub_q = eff_tokens
            matched_sub_list: List[str] = []
            for qt in sub_q:
                if any(w.startswith(qt) for w in title_words):
                    matched_sub_list.append(qt)
                elif len(qt) >= 4 and qt in title_norm:
                    matched_sub_list.append(qt)
                elif len(qt) >= 4 and any(_direct_ratio_cut(qt, w, 80.0) >= 80.0 for w in title_words if len(w) >= 3):
                    matched_sub_list.append(qt)
            matched_sub = len(matched_sub_list)
            covered_title_sub = sum(
                1
                for w in title_alpha_sub
                if any(
                    w.startswith(qt) or (len(qt) >= 4 and _direct_ratio_cut(qt, w, 80.0) >= 80.0)
                    for qt in sub_q
                )
            )
            has_partial_coverage = (
                (matched_sub >= 2 and matched_sub / max(len(sub_q), 1) >= 0.50)
                or (
                    len(title_alpha_sub) >= 2
                    and covered_title_sub >= len(title_alpha_sub)
                    and matched_sub >= 1
                    and sum(len(qt) for qt in matched_sub_list) >= 4
                )
                or (
                    len(title_alpha_sub) == 1
                    and covered_title_sub == 1
                    and len(sub_q) <= 3
                    and any(len(qt) >= 4 for qt in matched_sub_list)
                )
            )
            if has_partial_coverage:
                if covered_title_sub >= len(title_alpha_sub) and title_alpha_sub:
                    sc += 2400.0
                else:
                    sc += 1100.0
                sc -= (len(sub_q) - matched_sub) * 550.0
            else:
                if len(eff_q) < 3:
                    return -1.0
                best_r = max(
                    _fuzzy_score_cut(eff_q, title_norm, 76.0),
                    _fuzzy_score_cut(eff_q, main_title, 76.0),
                )
                if best_r < 76.0:
                    return -1.0
                sc += (best_r - 70.0) * 70.0

        sc += _popularity_bonus(popularity, is_catalog=is_catalog)

        for nw in NOISE_TITLE_WORDS:
            if nw in title_norm and nw not in eff_q:
                sc -= 1800.0
        if category in ("", "Libri"):
            for nw in NOISE_BOOK_WORDS:
                if nw in title_norm and nw not in eff_q:
                    sc -= 1800.0

        if is_archive:
            sc += 550.0
        if lang_match:
            sc += 400.0

        sc -= min(len(title_norm), 120) * 1.5
        return sc

    best_sc = _eval_title(query_norm, query_tokens)

    eff_hint_pubs = hint_publishers
    eff_hint_yr = hint_year
    if not eff_hint_pubs and not eff_hint_yr and category in ("", "Libri"):
        core_q, core_toks_t, eff_hint_pubs, eff_hint_yr = _extract_query_hints(query_norm, category or "Libri")
    else:
        core_q, core_toks_t, _, _ = _extract_query_hints(query_norm, category or "Libri")

    if core_q and core_q != query_norm and (eff_hint_pubs or eff_hint_yr):
        core_sc = _eval_title(core_q, list(core_toks_t))
        if core_sc >= 0.0:
            core_sc -= 140.0
            if eff_hint_pubs and publishers and any(
                _publishers_match(cp, hp) for cp in publishers for hp in eff_hint_pubs
            ):
                core_sc += 450.0
            if eff_hint_yr and years and eff_hint_yr in years:
                core_sc += 200.0
            if core_sc > best_sc:
                best_sc = core_sc

    return best_sc


def _score_suggestion_core(
    query_norm: str,
    query_tokens: List[str],
    title_norm: str,
    author_norm: str,
    target: str,
    secondary: str,
    main_target: str,
    main_secondary: str,
    target_toks: List[str],
    combined: str,
    combined_rev: str,
    combined_toks: List[str],
    pad_target: str,
    pad_combined: str,
    clean_t_norm: str,
    clean_a_norm: str,
    popularity: int,
    field_mode: str,
    is_archive: bool,
    lang_match: bool,
    allow_fuzzy: bool,
    is_catalog: bool,
    category: str,
) -> float:
    """Nucleo di scoring testuale/fuzzy/popolarità per una singola forma normalizzata della query."""
    score = 0.0
    pad_q = f" {query_norm} "

    # 1. Match testuale esatto / prefisso / contenimento di frase
    if target == query_norm or main_target == query_norm or combined == query_norm or combined_rev == query_norm:
        if (target == query_norm or main_target == query_norm) and popularity < 1200 and not is_catalog and not is_archive:
            score += 8500.0
        else:
            score += 12000.0
    elif (
        title_norm
        and author_norm
        and (
            (len(clean_t_norm) >= 2 and f" {clean_t_norm} " in pad_q)
            or (len(title_norm) >= 2 and f" {title_norm} " in pad_q)
        )
        and (
            (len(clean_a_norm) >= 2 and f" {clean_a_norm} " in pad_q)
            or (len(author_norm) >= 2 and f" {author_norm} " in pad_q)
        )
    ):
        score += 11000.0
    elif field_mode == "title" and (author_norm == query_norm or (len(query_norm) >= 4 and author_norm.startswith(query_norm))) and popularity >= 3000:
        # Se l'utente scrive il nome di un autore/artista celebre nel campo Titolo, proponi subito i suoi capolavori
        score += 9000.0 if author_norm == query_norm else 7500.0
    elif target.startswith(query_norm) or main_target.startswith(query_norm) or combined.startswith(query_norm):
        score += 9500.0
    elif (" " + query_norm) in (" " + target) or (" " + query_norm) in (" " + main_target) or (" " + query_norm) in (" " + combined):
        score += 8200.0
    elif query_norm in target or query_norm in main_target or query_norm in combined:
        score += 6500.0
    elif secondary == query_norm or main_secondary == query_norm:
        score += 6000.0
    elif secondary.startswith(query_norm) or main_secondary.startswith(query_norm):
        score += 4500.0
    elif (" " + query_norm) in (" " + secondary) or (" " + query_norm) in (" " + main_secondary):
        score += 3500.0

    # 2. Token overlap & copertura di parole significative (anti-stopword leak)
    sub_tokens = [tok for tok in query_tokens if tok not in SEARCH_STOPWORDS and len(tok) >= 2]
    if not sub_tokens:
        sub_tokens = query_tokens

    def _close_token(word: str, toks: List[str]) -> bool:
        """True se almeno un token (>= 3 lettere) è un refuso vicino (>= 82%) della parola."""
        cands = [tt for tt in toks if len(tt) >= 3]
        if not cands:
            return False
        if _rf_process is not None:
            # Un'unica chiamata C++ su tutti i token invece di un ciclo Python
            return _rf_process.extractOne(word, cands, scorer=_rf_fuzz.ratio, score_cutoff=82.0) is not None
        return any(_direct_ratio(word, tt) >= 82.0 for tt in cands)

    matched_sub_count = 0
    for st in sub_tokens:
        if (" " + st) in pad_combined:
            matched_sub_count += 1
        elif len(st) >= 4 and _close_token(st, combined_toks):
            matched_sub_count += 1

    all_tokens_present = True
    for qt in query_tokens:
        if (" " + qt) in pad_target:
            score += 350.0
        elif len(qt) >= 4 and _close_token(qt, target_toks):
            score += 320.0
        elif (" " + qt) in pad_combined:
            score += 200.0
        elif len(qt) >= 4 and _close_token(qt, combined_toks):
            score += 180.0
        else:
            all_tokens_present = False

    if all_tokens_present and len(query_tokens) >= 2:
        score += 3500.0

    if not all_tokens_present:
        # Se la query contiene almeno due parole significative, imponi una copertura minima sostanziale
        if len(sub_tokens) >= 2:
            min_required = max(1, int(len(sub_tokens) * 0.55))
            if matched_sub_count < min_required and query_norm not in combined:
                if not (
                    _direct_ratio_cut(query_norm, target, 80.0) >= 80.0
                    or _direct_ratio_cut(query_norm, main_target, 80.0) >= 80.0
                    or _direct_ratio_cut(query_norm, combined, 80.0) >= 80.0
                ):
                    return -1.0

        # Titolo interrotto con parole extra finali (es. "ossi di sep pippo", "il nome della ros xyz")
        matched_target_sub_tokens: List[str] = []
        for st in sub_tokens:
            if len(st) >= 3 and (" " + st) in pad_target:
                matched_target_sub_tokens.append(st)
            elif len(st) == 2 and f" {st} " in pad_target:
                matched_target_sub_tokens.append(st)
            elif len(st) >= 4 and _close_token(st, target_toks):
                matched_target_sub_tokens.append(st)

        main_target_clean = re.sub(r"\s+(?:1[4-9]\d{2}|20\d{2})(?:\s+(?:1[4-9]\d{2}|20\d{2}))?\s*$", "", main_target).strip() or main_target
        main_target_alpha_sub = [
            w for w in main_target_clean.split() if w not in SEARCH_STOPWORDS and len(w) >= 2 and not w.isdigit()
        ]
        covered_main_target = sum(
            1
            for w in main_target_alpha_sub
            if any(
                (len(st) >= 3 and w.startswith(st))
                or (len(st) == 2 and w == st)
                or (len(st) >= 4 and _direct_ratio_cut(st, w, 82.0) >= 82.0)
                for st in sub_tokens
            )
        )
        min_interrupted = max(2, int(len(sub_tokens) * 0.55))
        interrupted_title_ok = (
            len(matched_target_sub_tokens) >= 2
            and matched_sub_count >= min_interrupted
            and (
                any(len(t) >= 4 for t in matched_target_sub_tokens)
                or (
                    len(main_target_alpha_sub) >= 2
                    and covered_main_target >= len(main_target_alpha_sub)
                    and sum(len(t) for t in matched_target_sub_tokens) >= 6
                )
            )
        ) or (
            len(main_target_alpha_sub) == 1
            and covered_main_target == 1
            and len(matched_target_sub_tokens) == 1
            and len(matched_target_sub_tokens[0]) >= 5
            and len(sub_tokens) == 2
        )

        if interrupted_title_ok:
            if covered_main_target >= len(main_target_alpha_sub) and main_target_alpha_sub:
                score += 2600.0
            else:
                score += 1400.0
            unmatched_sub = max(0, len(sub_tokens) - matched_sub_count)
            score -= unmatched_sub * 650.0
        else:
            pad_q_sub = f" {query_norm} "
            clean_t_sub = re.sub(r"^(?:the|a|an|il|lo|la|i|gli|le|l)\s+", "", title_norm).strip() or title_norm
            clean_a_sub = re.sub(r"^(?:the|a|an|il|lo|la|i|gli|le|l)\s+", "", author_norm).strip() or author_norm
            t_contained = len(clean_t_sub) >= 3 and (f" {clean_t_sub} " in pad_q_sub or f" {title_norm} " in pad_q_sub)
            a_contained = len(clean_a_sub) >= 3 and (f" {clean_a_sub} " in pad_q_sub or f" {author_norm} " in pad_q_sub)
            both_contained = bool(title_norm and author_norm and (t_contained or a_contained))
            t_words_sub = [w for w in clean_t_sub.split() if len(w) >= 3 and w not in SEARCH_STOPWORDS]
            if not t_words_sub:
                t_words_sub = [w for w in clean_t_sub.split() if len(w) >= 3]
            title_in_q = bool(
                len(t_words_sub) >= 2
                and sum(1 for w in t_words_sub if f" {w} " in pad_q_sub or (len(w) >= 4 and w in query_norm))
                >= max(2, int(len(t_words_sub) * 0.65))
            )
            lenient = both_contained or title_in_q or (len(query_norm) >= 4 and query_norm in combined)

            if not allow_fuzzy or len(query_norm) < 4:
                if not lenient:
                    return -1.0
            else:
                # Calcolo Fuzzy C++ ultra-rapido per tollerare errori di battitura (es. "punk floyd", "umberto ecco")
                fuzzy_targets = (target, main_target, combined, secondary, main_secondary)
                if lenient:
                    best_r = max(_fuzzy_score(query_norm, ft) for ft in fuzzy_targets)
                    if best_r < 75.0 and not both_contained:
                        return -1.0
                else:
                    # Sotto l'80% il candidato verrebbe scartato: con la soglia RapidFuzz esce subito
                    best_r = max(_fuzzy_score_cut(query_norm, ft, 80.0) for ft in fuzzy_targets)
                    if best_r < 80.0:
                        return -1.0
                direct_target_r = max(
                    _direct_ratio_cut(query_norm, target, 82.0),
                    _direct_ratio_cut(query_norm, main_target, 82.0),
                )
                if best_r >= 75.0:
                    score += (best_r - 75.0) * 65.0
                if direct_target_r >= 82.0:
                    score += (direct_target_r - 78.0) * 75.0

    if score <= 0.0:
        return -1.0

    score += _popularity_bonus(popularity, is_catalog=is_catalog)

    for nw in NOISE_TITLE_WORDS:
        if nw in title_norm and nw not in query_norm:
            score -= 1800.0
    if category in ("", "Libri"):
        for nw in NOISE_BOOK_WORDS:
            if nw in title_norm and nw not in query_norm:
                score -= 1800.0

    if is_archive:
        score += 550.0
    if lang_match:
        score += 400.0

    score -= min(len(title_norm), 120) * 1.5
    return score


def score_suggestion(
    query_norm: str,
    query_tokens: List[str],
    title_norm: str,
    author_norm: str,
    popularity: int,
    field_mode: str = "title",
    is_archive: bool = False,
    lang_match: bool = False,
    allow_fuzzy: bool = False,
    is_catalog: bool = False,
    hint_publishers: Tuple[str, ...] = (),
    hint_year: str = "",
    cand_publishers: Tuple[str, ...] = (),
    cand_years: Tuple[str, ...] = (),
    category: str = "",
) -> float:
    """Calcola un punteggio di rilevanza combinando match testuale, fuzzy C++, hint editore/anno e popolarità reale."""
    target = title_norm if field_mode in ("title", "both") else author_norm
    secondary = author_norm if field_mode in ("title", "both") else title_norm
    main_target = target.split(":")[0].split(" - ")[0].split(" — ")[0].split(". ")[0].split("/")[0].strip()
    main_secondary = secondary.split(":")[0].split(" - ")[0].split(" — ")[0].split(". ")[0].split("/")[0].strip()

    combined = f"{title_norm} {author_norm}"
    combined_rev = f"{author_norm} {title_norm}"
    clean_t_norm = re.sub(r"^(?:the|a|an|il|lo|la|i|gli|le|l)\s+", "", title_norm).strip() or title_norm
    clean_a_norm = re.sub(r"^(?:the|a|an|il|lo|la|i|gli|le|l)\s+", "", author_norm).strip() or author_norm
    target_toks = target.split()
    combined_toks = combined.split()
    pad_target = " " + " ".join(target_toks) + " "
    pad_combined = " " + " ".join(combined_toks) + " "

    best_sc = _score_suggestion_core(
        query_norm=query_norm,
        query_tokens=query_tokens,
        title_norm=title_norm,
        author_norm=author_norm,
        target=target,
        secondary=secondary,
        main_target=main_target,
        main_secondary=main_secondary,
        target_toks=target_toks,
        combined=combined,
        combined_rev=combined_rev,
        combined_toks=combined_toks,
        pad_target=pad_target,
        pad_combined=pad_combined,
        clean_t_norm=clean_t_norm,
        clean_a_norm=clean_a_norm,
        popularity=popularity,
        field_mode=field_mode,
        is_archive=is_archive,
        lang_match=lang_match,
        allow_fuzzy=allow_fuzzy,
        is_catalog=is_catalog,
        category=category,
    )

    eff_hint_pubs = hint_publishers
    eff_hint_yr = hint_year
    if category in ("", "Libri") and field_mode in ("title", "both"):
        if not eff_hint_pubs and not eff_hint_yr:
            core_q, core_toks_t, eff_hint_pubs, eff_hint_yr = _extract_query_hints(query_norm, category or "Libri")
        else:
            core_q, core_toks_t, _, _ = _extract_query_hints(query_norm, category or "Libri")
        if core_q and core_q != query_norm and (eff_hint_pubs or eff_hint_yr):
            hint_sc = _score_suggestion_core(
                query_norm=core_q,
                query_tokens=list(core_toks_t),
                title_norm=title_norm,
                author_norm=author_norm,
                target=target,
                secondary=secondary,
                main_target=main_target,
                main_secondary=main_secondary,
                target_toks=target_toks,
                combined=combined,
                combined_rev=combined_rev,
                combined_toks=combined_toks,
                pad_target=pad_target,
                pad_combined=pad_combined,
                clean_t_norm=clean_t_norm,
                clean_a_norm=clean_a_norm,
                popularity=popularity,
                field_mode=field_mode,
                is_archive=is_archive,
                lang_match=lang_match,
                allow_fuzzy=allow_fuzzy,
                is_catalog=is_catalog,
                category=category,
            )
            if hint_sc >= 0.0:
                hint_sc -= 160.0
                if eff_hint_pubs and cand_publishers and any(
                    _publishers_match(cp, hp) for cp in cand_publishers for hp in eff_hint_pubs
                ):
                    hint_sc += 500.0
                if eff_hint_yr and cand_years and eff_hint_yr in cand_years:
                    hint_sc += 250.0
                if hint_sc > best_sc:
                    best_sc = hint_sc

    return best_sc
