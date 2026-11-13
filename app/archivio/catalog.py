"""
Motore di catalogo e autocompletamento multi-fonte in parallelo (Open Source Edition).

Architettura ibrida a 3 livelli:
1. Locale Istantaneo (< 2 ms):
   - Elementi già in archivio + Seed Index in RAM (6.600+ libri, 3.400+ album) + SQLite Cache
   - Ricerca esatta per prefisso + Ricerca Fuzzy tollerante agli errori di battitura (`RapidFuzz` C++)
2. Ricerca Online Parallela Multi-Motore (~100-350 ms):
   - LIBRI: Google Books API + OPAC SBN (Servizio Bibliotecario Nazionale Italiano) + Open Library
   - CD & VINILI: Apple iTunes Search API + MusicBrainz + ListenBrainz + Discogs API (opzionale)
3. Ispettore Edizioni Specifico per Supporto (Libri vs CD vs Vinili):
   - LIBRI: Aggrega tutte le case editrici italiane/internazionali e gli anni di ristampa da Google Books, OPAC SBN e Open Library.
   - CD vs VINILI: Interroga MusicBrainz (e opzionalmente Discogs) filtrando rigorosamente per formato fisico (`CD/SACD/HDCD` dal 1982+ vs `Vinyl/LP/12"/7"`).
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
import heapq
from typing import Callable, Dict, List, Optional, Tuple

try:
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process
except ImportError:
    _rf_fuzz = None
    _rf_process = None
import difflib


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

from .config import cache_path
from .store import Item
from .textutil import fold, search_fold, sort_key_matches_author, surname_key

# Numero massimo di candidati locali per fonte da trasformare in suggerimenti (gli altri non entrerebbero nei primi 25)
_LOCAL_PRUNE = 200

USER_AGENT = "Archivio/1.0.0 (OpenSource; https://github.com/a-bit-are/archivio)"
CACHE_TTL_SECONDS = 30 * 86400  # 30 giorni

SEARCH_STOPWORDS = {
    "il", "lo", "la", "i", "gli", "le", "l", "gl",
    "un", "uno", "una",
    "di", "a", "da", "in", "con", "su", "per", "tra", "fra", "d",
    "del", "dello", "della", "dei", "degli", "delle", "dell",
    "al", "allo", "alla", "ai", "agli", "alle", "all",
    "dal", "dallo", "dalla", "dai", "dagli", "dalle", "dall",
    "nel", "nello", "nella", "nei", "negli", "nelle", "nell",
    "sul", "sullo", "sulla", "sui", "sugli", "sulle", "sull",
    "col", "coi", "coll", "e", "ed", "o", "od",
    "the", "a", "an", "of", "and", "in", "to", "for", "on", "at", "by", "with",
}

ITALIAN_PUBLISHERS_PRIORITY = {
    "mondadori": "Mondadori",
    "arnoldo mondadori": "Mondadori",
    "arnoldo mondadori editore": "Mondadori",
    "oscar mondadori": "Mondadori",
    "strade blu mondadori": "Mondadori",
    "einaudi": "Einaudi",
    "giulio einaudi": "Einaudi",
    "giulio einaudi editore": "Einaudi",
    "einaudi tascabili": "Einaudi",
    "bompiani": "Bompiani",
    "tascabili bompiani": "Bompiani",
    "rcs libri": "RCS Libri",
    "feltrinelli": "Feltrinelli",
    "giangiacomo feltrinelli": "Feltrinelli",
    "universale economica feltrinelli": "Feltrinelli",
    "adelphi": "Adelphi",
    "adelphi edizioni": "Adelphi",
    "rizzoli": "Rizzoli",
    "bur": "BUR Rizzoli",
    "bur rizzoli": "BUR Rizzoli",
    "biblioteca universale rizzoli": "BUR Rizzoli",
    "garzanti": "Garzanti",
    "garzanti libri": "Garzanti",
    "sellerio": "Sellerio",
    "sellerio editore": "Sellerio",
    "sellerio editore palermo": "Sellerio",
    "laterza": "Laterza",
    "editori laterza": "Laterza",
    "gius. laterza & figli": "Laterza",
    "guanda": "Guanda",
    "ugo guanda editore": "Guanda",
    "longanesi": "Longanesi",
    "neri pozza": "Neri Pozza",
    "marsilio": "Marsilio",
    "minimum fax": "Minimum Fax",
    "fazi": "Fazi",
    "fazi editore": "Fazi",
    "e/o": "Edizioni E/O",
    "edizioni e/o": "Edizioni E/O",
    "salani": "Salani",
    "adriano salani editore": "Salani",
    "giunti": "Giunti",
    "giunti editore": "Giunti",
    "tea": "TEA",
    "bollati boringhieri": "Bollati Boringhieri",
    "il mulino": "Il Mulino",
    "societa editrice il mulino": "Il Mulino",
    "newton compton": "Newton Compton",
    "newton compton editori": "Newton Compton",
    "zanichelli": "Zanichelli",
    "piemme": "Piemme",
    "edizioni piemme": "Piemme",
    "sperling & kupfer": "Sperling & Kupfer",
    "rusconi": "Rusconi",
    "sur": "SUR",
    "edizioni sur": "SUR",
    "iperborea": "Iperborea",
    "la nave di teseo": "La Nave di Teseo",
    "fanucci": "Fanucci",
    "chiarelettere": "Chiarelettere",
    "saggiatore": "Il Saggiatore",
    "il saggiatore": "Il Saggiatore",
    "corbaccio": "Corbaccio",
    "ponte alle grazie": "Ponte alle Grazie",
    "treves": "Fratelli Treves",
    "fratelli treves": "Fratelli Treves",
    "vallecchi": "Vallecchi",
    "sansoni": "Sansoni",
    "utef": "UTET",
    "utet": "UTET",
    "hoepli": "Hoepli",
}

# Collane storiche degli editori italiani (proposte automaticamente in base all'Editore selezionato)
PUBLISHER_COLLECTIONS_IT: Dict[str, List[str]] = {
    "Mondadori": [
        "I Meridiani",
        "Oscar moderni",
        "Oscar classici",
        "Oscar assoluti",
        "Oscar bestsellers",
        "Oscar saggi",
        "Scrittori italiani e stranieri",
        "Strade blu",
        "Lo Specchio",
        "Urania",
        "Il Giallo Mondadori",
    ],
    "Einaudi": [
        "Supercoralli",
        "I Coralli",
        "ET Scrittori",
        "ET Classici",
        "ET Saggi",
        "ET Biblioteca",
        "Gli Struzzi",
        "I Millenni",
        "Stile Libero Big",
        "Piccola Biblioteca Einaudi",
        "Collezione di poesia",
        "Nuovi Coralli",
    ],
    "Adelphi": [
        "Biblioteca Adelphi",
        "Piccola Biblioteca Adelphi",
        "Gli Adelphi",
        "Fabula",
        "Classici Adelphi",
        "La collana dei casi",
        "I periscopi",
    ],
    "Feltrinelli": [
        "Universale Economica Feltrinelli",
        "Universale Economica. I Classici",
        "I Narratori",
        "Campi del sapere",
        "Varia",
        "Serie Bianca",
    ],
    "Bompiani": [
        "Tascabili Bompiani",
        "I grandi tascabili",
        "Classici Bompiani",
        "Il pensiero occidentale",
        "Narratori italiani",
        "Letteraria",
        "Overlook",
    ],
    "BUR Rizzoli": [
        "BUR Classici",
        "BUR Contemporanea",
        "BUR Saggi",
        "BUR Grandi Romanzi",
        "BUR Teatro",
        "BUR Scienza",
    ],
    "Rizzoli": [
        "La Scala",
        "BUR Classici",
        "BUR Contemporanea",
        "Classici dell'arte",
        "24/7",
    ],
    "Sellerio": [
        "La memoria",
        "Il contesto",
        "Il divano",
        "La nuova diagonale",
    ],
    "Garzanti": [
        "I grandi libri Garzanti",
        "Gli Elefanti",
        "Narratori moderni",
        "Saggi",
    ],
    "UTET": [
        "Classici UTET",
        "Classici della filosofia",
        "Classici della storia",
        "Classici italiani",
        "Scrittori del mondo",
    ],
    "Newton Compton": [
        "I Mammut",
        "Grandi Tascabili Economici",
        "MiniMammut",
        "Live",
        "Nuova narrativa Newton",
    ],
    "Laterza": [
        "Economica Laterza",
        "I Robinson",
        "Biblioteca Universale Laterza",
        "Storia e Società",
        "Scrittori d'Italia",
    ],
    "Guanda": [
        "Biblioteca della Fenice",
        "Narratori della Fenice",
        "Le Fenici Tascabili",
        "Prosa contemporanea",
    ],
    "Il Mulino": [
        "Universale Paperbacks Il Mulino",
        "Biblioteca storica",
        "Collezione di testi e di studi",
        "Saggi",
        "Farsi un'idea",
    ],
    "Neri Pozza": [
        "I narratori delle tavole",
        "Biblioteca Neri Pozza",
        "Bloom",
        "Le tavole d'oro",
    ],
    "Iperborea": [
        "Iperborea",
        "I Miniborei",
        "The Passenger",
    ],
    "Minimum Fax": [
        "Minimum Classics",
        "Sotterranei",
        "Nichel",
        " Indi",
    ],
    "Marsilio": [
        "Farfalle",
        "Tascabili Marsilio",
        "Letteratura universale Marsilio",
        "Gli specchi",
    ],
    "La Nave di Teseo": [
        "Oceani",
        "I Delfini",
        "Le Onde",
        "I Fari",
    ],
    "Edizioni E/O": [
        "Dal mondo",
        "Tascabili E/O",
        "Sabot/age",
    ],
    "Fazi": [
        "Le strade",
        "Tascabili",
        " Lain",
    ],
    "Salani": [
        "Gl'Istrici",
        "I Cammelli",
        "Romanzi Salani",
    ],
    "Bollati Boringhieri": [
        "Universale Bollati Boringhieri",
        "Varianti",
        "Saggi",
    ],
    "Il Saggiatore": [
        "La Cultura",
        "Tascabili",
    ],
    "TEA": [
        "TEAdue",
        "TEA Classici",
    ],
    "Longanesi": [
        "La Gaja Scienza",
        "Il Cammeo",
    ],
    "Fanucci": [
        "Immaginario",
        "TIF Extra",
        "Collezione Dick",
    ],
    "SUR": [
        "BigSUR",
        "SUR",
    ],
}

MUSIC_COLLECTIONS_CD: Dict[str, Tuple[str, str]] = {
    "Original Album Classics": ("Sony Music", "2010"),
    "Original Album Series": ("Warner Music", "2010"),
    "Mobile Fidelity Original Master Recording": ("Mobile Fidelity Sound Lab", ""),
    "SHM-CD Japanese Edition": ("Universal Music Japan", ""),
    "Collector's Edition": ("", ""),
    "Deluxe Edition": ("", ""),
    "Anniversary Edition": ("", ""),
    "Sorrisi e Canzoni Collana": ("TV Sorrisi e Canzoni", ""),
}

MUSIC_COLLECTIONS_VINYL: Dict[str, Tuple[str, str]] = {
    "DeAgostini Jazz in Vinile": ("DeAgostini", "2016"),
    "DeAgostini Rock in Vinile": ("DeAgostini", "2017"),
    "Blue Note Tone Poet Series": ("Blue Note", "2019"),
    "Blue Note Classic Vinyl Series": ("Blue Note", "2020"),
    "Acoustic Sounds Series": ("Verve / Impulse!", "2020"),
    "Mobile Fidelity Original Master Recording": ("Mobile Fidelity Sound Lab", ""),
    "Half-Speed Mastered": ("Abbey Road / UMC", ""),
    "180g Remastered Vinyl": ("", ""),
    "Anniversary Edition": ("", ""),
}

# Collezioni / Box Set specifici per Artista e Formato -> (Etichetta, Anno della collezione/ristampa)
ARTIST_COLLECTION_META: Dict[str, Dict[str, Dict[str, Tuple[str, str]]]] = {
    "pink floyd": {
        "CD": {
            "Discovery Edition": ("EMI", "2011"),
            "Experience Edition": ("EMI", "2011"),
            "Immersion Box Set": ("EMI", "2011"),
            "Pink Floyd Records Remastered": ("Pink Floyd Records", "2016"),
            "Shine On (Box Set)": ("EMI", "1992"),
            "Oh, by the Way (Box Set)": ("EMI", "2007"),
        },
        "Vinili": {
            "Pink Floyd Records Remastered (180g)": ("Pink Floyd Records", "2016"),
            "Back to Black (180g)": ("EMI", "2011"),
            "DeAgostini Rock in Vinile": ("DeAgostini", "2017"),
        },
    },
    "the beatles": {
        "CD": {
            "The Beatles in Stereo": ("Apple Records", "2009"),
            "The Beatles in Mono": ("Apple Records", "2009"),
        },
        "Vinili": {
            "The Beatles in Stereo (180g)": ("Apple Records", "2012"),
            "The Beatles in Mono (Vinyl Box)": ("Apple Records", "2014"),
            "DeAgostini The Beatles Vinyl Collection": ("DeAgostini", "2017"),
        },
    },
    "led zeppelin": {
        "CD": {
            "Deluxe Edition Remastered": ("Atlantic", "2014"),
        },
        "Vinili": {
            "Deluxe Edition Remastered (180g)": ("Atlantic", "2014"),
            "DeAgostini Rock in Vinile": ("DeAgostini", "2017"),
        },
    },
    "miles davis": {
        "CD": {
            "The Complete Columbia Album Collection": ("Columbia", "2009"),
            "Original Album Classics": ("Columbia / Sony Music", "2010"),
        },
        "Vinili": {
            "DeAgostini Jazz in Vinile": ("DeAgostini", "2016"),
            "Mobile Fidelity Original Master Recording": ("Mobile Fidelity Sound Lab", "2015"),
        },
    },
    "john coltrane": {
        "CD": {
            "The Classic Quartet": ("Impulse!", "1998"),
        },
        "Vinili": {
            "Acoustic Sounds Series": ("Impulse!", "2020"),
            "DeAgostini Jazz in Vinile": ("DeAgostini", "2016"),
        },
    },
    "fabrizio de andre": {
        "CD": {
            "Edizione Rimasterizzata 24-bit": ("Sony Music / Ricordi", "2009"),
        },
        "Vinili": {
            "DeAgostini Fabrizio De André in Vinile": ("DeAgostini / Sony Music", "2017"),
        },
    },
}

MUSIC_COLLECTIONS_COMMON: List[str] = list(MUSIC_COLLECTIONS_CD.keys())

# Mappa inversa Collana -> Editore per i Libri (costruita automaticamente + alias comuni)
COLLECTION_TO_PUBLISHER_IT: Dict[str, str] = {}
for _pub_name, _coll_list in PUBLISHER_COLLECTIONS_IT.items():
    for _c_item in _coll_list:
        _c_fold = fold(_c_item)
        if _c_fold and _c_fold not in COLLECTION_TO_PUBLISHER_IT:
            COLLECTION_TO_PUBLISHER_IT[_c_fold] = _pub_name

for _alias_k, _alias_pub in (
    ("oscar", "Mondadori"),
    ("oscar mondadori", "Mondadori"),
    ("i meridiani", "Mondadori"),
    ("meridiani", "Mondadori"),
    ("urania", "Mondadori"),
    ("il giallo mondadori", "Mondadori"),
    ("strade blu", "Mondadori"),
    ("lo specchio", "Mondadori"),
    ("supercoralli", "Einaudi"),
    ("i coralli", "Einaudi"),
    ("nuovi coralli", "Einaudi"),
    ("et", "Einaudi"),
    ("einaudi tascabili", "Einaudi"),
    ("gli struzzi", "Einaudi"),
    ("i millenni", "Einaudi"),
    ("piccola biblioteca einaudi", "Einaudi"),
    ("stile libero", "Einaudi"),
    ("biblioteca adelphi", "Adelphi"),
    ("piccola biblioteca adelphi", "Adelphi"),
    ("gli adelphi", "Adelphi"),
    ("fabula", "Adelphi"),
    ("classici adelphi", "Adelphi"),
    ("universale economica", "Feltrinelli"),
    ("universale economica feltrinelli", "Feltrinelli"),
    ("i narratori", "Feltrinelli"),
    ("tascabili bompiani", "Bompiani"),
    ("i grandi tascabili", "Bompiani"),
    ("il pensiero occidentale", "Bompiani"),
    ("bur", "BUR Rizzoli"),
    ("biblioteca universale rizzoli", "BUR Rizzoli"),
    ("la memoria", "Sellerio"),
    ("il contesto", "Sellerio"),
    ("il divano", "Sellerio"),
    ("i grandi libri", "Garzanti"),
    ("gli elefanti", "Garzanti"),
    ("i mammut", "Newton Compton"),
    ("minimammut", "Newton Compton"),
    ("grandi tascabili economici", "Newton Compton"),
    ("i robinson", "Laterza"),
    ("economica laterza", "Laterza"),
    ("biblioteca universale laterza", "Laterza"),
    ("oceani", "La Nave di Teseo"),
    ("i delfini", "La Nave di Teseo"),
):
    COLLECTION_TO_PUBLISHER_IT[_alias_k] = _alias_pub

NOISE_TITLE_WORDS = (
    "karaoke", "tribute", "lullaby", "8-bit", "ringtone", "instrumental version",
    "in the style of", "made famous by", "backing track", "cover version",
)

CD_FORMAT_KEYWORDS = ("cd", "sacd", "hdcd", "dualdisc", "shm-cd", "blu-spec")
VINYL_FORMAT_KEYWORDS = ("vinyl", "lp", '12"', '7"', '10"', "flexi-disc", "shellac")

_ALBUM_SUFFIX_CLEAN_RE = re.compile(
    r"\s*[\(\[]"
    r"(?:remaster(?:ed)?(?:\s+\d{4})?|\d{4}\s+remaster(?:ed)?|deluxe(?:\s+edition|\s+version)?|"
    r"expanded(?:\s+edition|\s+version)?|special\s+edition|anniversary\s+edition|"
    r"bonus\s+track\s+version|collector'?s\s+edition|standard\s+edition)"
    r"[\)\]]\s*$",
    re.IGNORECASE,
)


# Profili edizioni pre-verificati per risposta 0ms anche offline sui grandi classici
CURATED_EDITION_PROFILES: Dict[str, dict] = {
    "the dark side of the moon|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["EMI", "Harvest", "Capitol Records", "Parlophone", "Pink Floyd Records", "Sony Music", "Mobile Fidelity Sound Lab"],
            "years": ["1983", "1984", "1992", "1994", "2003", "2011", "2016", "2023", "1973"],
            "default_publisher": "EMI",
            "default_year": "1983",
        },
        "Vinili": {
            "publishers": ["Harvest", "EMI", "Capitol Records", "Pink Floyd Records", "Mobile Fidelity Sound Lab", "Parlophone", "Odeon"],
            "years": ["1973", "1974", "1979", "1983", "1993", "2003", "2011", "2016", "2023"],
            "default_publisher": "Harvest",
            "default_year": "1973",
        },
    },
    "the wall|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["EMI", "Harvest", "Columbia", "Capitol Records", "Pink Floyd Records"],
            "years": ["1984", "1985", "1994", "2000", "2011", "2016", "1979"],
            "default_publisher": "EMI",
            "default_year": "1984",
        },
        "Vinili": {
            "publishers": ["Harvest", "Columbia", "EMI", "Pink Floyd Records", "Capitol Records"],
            "years": ["1979", "1980", "1982", "1997", "2012", "2016"],
            "default_publisher": "Harvest",
            "default_year": "1979",
        },
    },
    "wish you were here|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["EMI", "Harvest", "Columbia", "Capitol Records", "Pink Floyd Records", "Sony Music"],
            "years": ["1983", "1984", "1992", "1994", "2011", "2016", "1975"],
            "default_publisher": "EMI",
            "default_year": "1983",
        },
        "Vinili": {
            "publishers": ["Harvest", "Columbia", "EMI", "Pink Floyd Records", "CBS"],
            "years": ["1975", "1976", "1980", "1997", "2011", "2016"],
            "default_publisher": "Harvest",
            "default_year": "1975",
        },
    },
    "animals|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["EMI", "Harvest", "Columbia", "Capitol Records", "Pink Floyd Records"],
            "years": ["1985", "1994", "2000", "2011", "2016", "2022", "1977"],
            "default_publisher": "EMI",
            "default_year": "1985",
        },
        "Vinili": {
            "publishers": ["Harvest", "Columbia", "EMI", "Pink Floyd Records"],
            "years": ["1977", "1978", "1985", "2016", "2022"],
            "default_publisher": "Harvest",
            "default_year": "1977",
        },
    },
    "the final cut|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["Harvest", "EMI", "Columbia", "Capitol Records", "Pink Floyd Records"],
            "years": ["1983", "1986", "1994", "2004", "2011", "2017"],
            "default_publisher": "Harvest",
            "default_year": "1983",
        },
        "Vinili": {
            "publishers": ["Harvest", "Columbia", "EMI", "Pink Floyd Records"],
            "years": ["1983", "2017"],
            "default_publisher": "Harvest",
            "default_year": "1983",
        },
    },
    "the division bell|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["EMI", "Columbia", "Parlophone", "Pink Floyd Records"],
            "years": ["1994", "2011", "2014", "2016"],
            "default_publisher": "EMI",
            "default_year": "1994",
        },
        "Vinili": {
            "publishers": ["EMI", "Columbia", "Parlophone", "Pink Floyd Records"],
            "years": ["1994", "2014", "2016", "2019"],
            "default_publisher": "EMI",
            "default_year": "1994",
        },
    },
    "meddle|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["EMI", "Harvest", "Capitol Records", "Mobile Fidelity Sound Lab", "Pink Floyd Records"],
            "years": ["1984", "1989", "1992", "2011", "2016", "1971"],
            "default_publisher": "EMI",
            "default_year": "1984",
        },
        "Vinili": {
            "publishers": ["Harvest", "EMI", "Capitol Records", "Mobile Fidelity Sound Lab", "Pink Floyd Records"],
            "years": ["1971", "1973", "1984", "2016"],
            "default_publisher": "Harvest",
            "default_year": "1971",
        },
    },
    "atom heart mother|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["EMI", "Harvest", "Capitol Records", "Pink Floyd Records"],
            "years": ["1985", "1994", "2011", "2016", "1970"],
            "default_publisher": "EMI",
            "default_year": "1985",
        },
        "Vinili": {
            "publishers": ["Harvest", "EMI", "Capitol Records", "Pink Floyd Records"],
            "years": ["1970", "1975", "1979", "2016"],
            "default_publisher": "Harvest",
            "default_year": "1970",
        },
    },
    "the piper at the gates of dawn|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["EMI", "Columbia", "Capitol Records", "Parlophone", "Pink Floyd Records"],
            "years": ["1985", "1994", "1997", "2007", "2011", "2016", "1967"],
            "default_publisher": "EMI",
            "default_year": "1985",
        },
        "Vinili": {
            "publishers": ["Columbia", "Tower", "Harvest", "EMI", "Pink Floyd Records"],
            "years": ["1967", "1973", "1997", "2016", "2018", "2022"],
            "default_publisher": "Columbia",
            "default_year": "1967",
        },
    },
    "a momentary lapse of reason|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["EMI", "Columbia", "Capitol Records", "Pink Floyd Records"],
            "years": ["1987", "1994", "2011", "2016", "2021"],
            "default_publisher": "EMI",
            "default_year": "1987",
        },
        "Vinili": {
            "publishers": ["EMI", "Columbia", "Pink Floyd Records"],
            "years": ["1987", "2017", "2021"],
            "default_publisher": "EMI",
            "default_year": "1987",
        },
    },
    "the endless river|pink floyd": {
        "artist_sort": "Pink Floyd",
        "CD": {
            "publishers": ["Parlophone", "Columbia", "Pink Floyd Records"],
            "years": ["2014", "2016"],
            "default_publisher": "Parlophone",
            "default_year": "2014",
        },
        "Vinili": {
            "publishers": ["Parlophone", "Columbia", "Pink Floyd Records"],
            "years": ["2014"],
            "default_publisher": "Parlophone",
            "default_year": "2014",
        },
    },
    "ten summoners tales|sting": {
        "artist_sort": "Sting",
        "CD": {
            "publishers": ["A&M Records", "Polydor", "Universal Music"],
            "years": ["1993", "1998", "2004", "2013", "2023"],
            "default_publisher": "A&M Records",
            "default_year": "1993",
        },
        "Vinili": {
            "publishers": ["A&M Records", "Universal Music"],
            "years": ["1993", "2016", "2017", "2023"],
            "default_publisher": "A&M Records",
            "default_year": "1993",
        },
    },
    "abbey road|the beatles": {
        "artist_sort": "Beatles, The",
        "CD": {
            "publishers": ["Parlophone", "Apple Records", "EMI", "Capitol Records", "Universal Music"],
            "years": ["1983", "1987", "2009", "2019", "1969"],
            "default_publisher": "Parlophone",
            "default_year": "1987",
        },
        "Vinili": {
            "publishers": ["Apple Records", "Parlophone", "PCS", "Capitol Records", "Mobile Fidelity Sound Lab"],
            "years": ["1969", "1970", "1976", "1982", "2012", "2019"],
            "default_publisher": "Apple Records",
            "default_year": "1969",
        },
    },
    "creuza de ma|fabrizio de andre": {
        "artist_sort": "De André, Fabrizio",
        "CD": {
            "publishers": ["Ricordi", "BMG Ricordi", "Sony Music", "RCA"],
            "years": ["1984", "1991", "2002", "2009", "2014"],
            "default_publisher": "Ricordi",
            "default_year": "1984",
        },
        "Vinili": {
            "publishers": ["Ricordi", "Sony Music", "BMG"],
            "years": ["1984", "2010", "2014", "2021"],
            "default_publisher": "Ricordi",
            "default_year": "1984",
        },
    },
    "la voce del padrone|franco battiato": {
        "artist_sort": "Battiato, Franco",
        "CD": {
            "publishers": ["EMI Italiana", "EMI", "Universal Music", "Capitol"],
            "years": ["1983", "1985", "1995", "2008", "2015", "2021", "1981"],
            "default_publisher": "EMI Italiana",
            "default_year": "1983",
        },
        "Vinili": {
            "publishers": ["EMI Italiana", "EMI", "Universal Music"],
            "years": ["1981", "1982", "2011", "2016", "2021"],
            "default_publisher": "EMI Italiana",
            "default_year": "1981",
        },
    },
    "il nome della rosa|umberto eco": {
        "author_sort": "Eco, Umberto",
        "Libri": {
            "publishers": ["Bompiani", "La Nave di Teseo", "Fabbri", "Mondadori", "RCS Libri", "Harcourt"],
            "years": ["1980", "1984", "1989", "2000", "2012", "2020"],
            "default_publisher": "Bompiani",
            "default_year": "1980",
        },
    },
    "il pendolo di foucault|umberto eco": {
        "author_sort": "Eco, Umberto",
        "Libri": {
            "publishers": ["Bompiani", "La Nave di Teseo", "RCS Libri"],
            "years": ["1988", "1991", "2001", "2014", "2020"],
            "default_publisher": "Bompiani",
            "default_year": "1988",
        },
    },
    "se questo e un uomo|primo levi": {
        "author_sort": "Levi, Primo",
        "Libri": {
            "publishers": ["Einaudi", "De Silva", "Mondadori"],
            "years": ["1947", "1958", "1989", "2005", "2014"],
            "default_publisher": "Einaudi",
            "default_year": "1958",
        },
    },
    "i promessi sposi|alessandro manzoni": {
        "author_sort": "Manzoni, Alessandro",
        "Libri": {
            "publishers": ["Mondadori", "Einaudi", "BUR Rizzoli", "Garzanti", "Feltrinelli", "Laterza", "Zanichelli"],
            "years": ["1827", "1840", "1985", "1995", "2006", "2014", "2021"],
            "default_publisher": "Mondadori",
            "default_year": "1840",
        },
    },
    "1984|george orwell": {
        "author_sort": "Orwell, George",
        "Libri": {
            "publishers": ["Mondadori", "Einaudi", "Feltrinelli", "BUR Rizzoli", "Garzanti", "Sellerio", "Newton Compton", "Penguin"],
            "years": ["1949", "1950", "1984", "2000", "2016", "2021"],
            "default_publisher": "Mondadori",
            "default_year": "1949",
        },
    },
    "la fattoria degli animali|george orwell": {
        "author_sort": "Orwell, George",
        "Libri": {
            "publishers": ["Mondadori", "Feltrinelli", "Einaudi", "Bompiani", "BUR Rizzoli", "Garzanti"],
            "years": ["1945", "1947", "1995", "2010", "2021"],
            "default_publisher": "Mondadori",
            "default_year": "1945",
        },
    },
    "il signore degli anelli|j r r tolkien": {
        "author_sort": "Tolkien, J.R.R.",
        "Libri": {
            "publishers": ["Bompiani", "Rusconi", "Astrolabio", "Mondadori", "HarperCollins"],
            "years": ["1954", "1970", "2000", "2003", "2020"],
            "default_publisher": "Bompiani",
            "default_year": "1954",
        },
    },
    "il gattopardo|giuseppe tomasi di lampedusa": {
        "author_sort": "Tomasi di Lampedusa, Giuseppe",
        "Libri": {
            "publishers": ["Feltrinelli", "Mondadori", "Einaudi"],
            "years": ["1958", "1969", "1995", "2002", "2013", "2020"],
            "default_publisher": "Feltrinelli",
            "default_year": "1958",
        },
    },
    "il barone rampante|italo calvino": {
        "author_sort": "Calvino, Italo",
        "Libri": {
            "publishers": ["Einaudi", "Mondadori", "Garzanti"],
            "years": ["1957", "1965", "1993", "2002", "2016"],
            "default_publisher": "Einaudi",
            "default_year": "1957",
        },
    },
    "le citta invisibili|italo calvino": {
        "author_sort": "Calvino, Italo",
        "Libri": {
            "publishers": ["Einaudi", "Mondadori"],
            "years": ["1972", "1993", "2002", "2016"],
            "default_publisher": "Einaudi",
            "default_year": "1972",
        },
    },
    "la coscienza di zeno|italo svevo": {
        "author_sort": "Svevo, Italo",
        "Libri": {
            "publishers": ["Mondadori", "Einaudi", "Feltrinelli", "Garzanti", "BUR Rizzoli", "Cappelli"],
            "years": ["1923", "1985", "1995", "2008", "2019"],
            "default_publisher": "Mondadori",
            "default_year": "1923",
        },
    },
    "il fu mattia pascal|luigi pirandello": {
        "author_sort": "Pirandello, Luigi",
        "Libri": {
            "publishers": ["Mondadori", "Einaudi", "Feltrinelli", "Garzanti", "BUR Rizzoli"],
            "years": ["1904", "1988", "1993", "2006", "2018"],
            "default_publisher": "Mondadori",
            "default_year": "1904",
        },
    },
}

CURATED_BOOKS_SEED = [
    ["Il nome della rosa", "Umberto Eco", "1980", 9500, "/works/OL262421W", "Bompiani", "1980", "ita"],
    ["Il pendolo di Foucault", "Umberto Eco", "1988", 6200, "/works/OL262427W", "Bompiani", "1988", "ita"],
    ["L'isola del giorno prima", "Umberto Eco", "1994", 4800, "/works/OL262419W", "Bompiani", "1994", "ita"],
    ["Baudolino", "Umberto Eco", "2000", 4900, "/works/OL262403W", "Bompiani", "2000", "ita"],
    ["Il cimitero di Praga", "Umberto Eco", "2010", 5100, "/works/OL15442254W", "Bompiani", "2010", "ita"],
    ["Numero zero", "Umberto Eco", "2015", 4300, "", "Bompiani", "2015", "ita"],
    ["Se questo è un uomo", "Primo Levi", "1947", 9200, "/works/OL819090W", "Einaudi", "1958", "ita"],
    ["La tregua", "Primo Levi", "1963", 7800, "/works/OL819101W", "Einaudi", "1963", "ita"],
    ["Il sistema periodico", "Primo Levi", "1975", 7400, "/works/OL819094W", "Einaudi", "1975", "ita"],
    ["I sommersi e i salvati", "Primo Levi", "1986", 7100, "/works/OL819092W", "Einaudi", "1986", "ita"],
    ["I promessi sposi", "Alessandro Manzoni", "1827", 9400, "/works/OL1109965W", "Mondadori", "1840", "ita"],
    ["Il Gattopardo", "Giuseppe Tomasi di Lampedusa", "1958", 8800, "/works/OL1067617W", "Feltrinelli", "1958", "ita"],
    ["Il barone rampante", "Italo Calvino", "1957", 8600, "/works/OL15022W", "Einaudi", "1957", "ita"],
    ["Il visconte dimezzato", "Italo Calvino", "1952", 8200, "/works/OL15035W", "Einaudi", "1952", "ita"],
    ["Il cavaliere inesistente", "Italo Calvino", "1959", 8100, "/works/OL15024W", "Einaudi", "1959", "ita"],
    ["Le città invisibili", "Italo Calvino", "1972", 8700, "/works/OL15027W", "Einaudi", "1972", "ita"],
    ["Se una notte d'inverno un viaggiatore", "Italo Calvino", "1979", 8400, "/works/OL15031W", "Einaudi", "1979", "ita"],
    ["Il sentiero dei nidi di ragno", "Italo Calvino", "1947", 7900, "/works/OL15030W", "Einaudi", "1947", "ita"],
    ["Marcovaldo", "Italo Calvino", "1963", 7800, "/works/OL15028W", "Einaudi", "1963", "ita"],
    ["Lezioni americane", "Italo Calvino", "1988", 7600, "/works/OL15026W", "Garzanti", "1988", "ita"],
    ["La coscienza di Zeno", "Italo Svevo", "1923", 8900, "/works/OL1181939W", "Mondadori", "1923", "ita"],
    ["Senilità", "Italo Svevo", "1898", 6800, "", "Einaudi", "1898", "ita"],
    ["Il fu Mattia Pascal", "Luigi Pirandello", "1904", 9000, "/works/OL91897W", "Mondadori", "1904", "ita"],
    ["Uno, nessuno e centomila", "Luigi Pirandello", "1926", 8800, "/works/OL91905W", "Mondadori", "1926", "ita"],
    ["Sei personaggi in cerca d'autore", "Luigi Pirandello", "1921", 8300, "/works/OL91902W", "Mondadori", "1921", "ita"],
    ["I Malavoglia", "Giovanni Verga", "1881", 8700, "/works/OL152940W", "Mondadori", "1881", "ita"],
    ["Mastro-don Gesualdo", "Giovanni Verga", "1889", 7900, "/works/OL152944W", "Mondadori", "1889", "ita"],
    ["Il piacere", "Gabriele D'Annunzio", "1889", 8200, "", "Mondadori", "1889", "ita"],
    ["Canne al vento", "Grazia Deledda", "1913", 7600, "", "Mondadori", "1913", "ita"],
    ["Lessico famigliare", "Natalia Ginzburg", "1963", 8000, "", "Einaudi", "1963", "ita"],
    ["La luna e i falò", "Cesare Pavese", "1950", 8300, "", "Einaudi", "1950", "ita"],
    ["Il mestiere di vivere", "Cesare Pavese", "1952", 7500, "", "Einaudi", "1952", "ita"],
    ["Una questione privata", "Beppe Fenoglio", "1963", 7900, "", "Einaudi", "1963", "ita"],
    ["Il partigiano Johnny", "Beppe Fenoglio", "1968", 7700, "", "Einaudi", "1968", "ita"],
    ["Cristo si è fermato a Eboli", "Carlo Levi", "1945", 8100, "", "Einaudi", "1945", "ita"],
    ["Il giardino dei Finzi-Contini", "Giorgio Bassani", "1962", 8200, "", "Einaudi", "1962", "ita"],
    ["La Storia", "Elsa Morante", "1974", 8300, "", "Einaudi", "1974", "ita"],
    ["L'isola di Arturo", "Elsa Morante", "1957", 8000, "", "Einaudi", "1957", "ita"],
    ["Menzogna e sortilegio", "Elsa Morante", "1948", 7400, "", "Einaudi", "1948", "ita"],
    ["Gli indifferenti", "Alberto Moravia", "1929", 8100, "", "Bompiani", "1929", "ita"],
    ["La ciociara", "Alberto Moravia", "1957", 7900, "", "Bompiani", "1957", "ita"],
    ["La noia", "Alberto Moravia", "1960", 7700, "", "Bompiani", "1960", "ita"],
    ["Il conformista", "Alberto Moravia", "1951", 7500, "", "Bompiani", "1951", "ita"],
    ["Il giorno della civetta", "Leonardo Sciascia", "1961", 8400, "", "Adelphi", "1961", "ita"],
    ["A ciascuno il suo", "Leonardo Sciascia", "1966", 7900, "", "Adelphi", "1966", "ita"],
    ["Todo modo", "Leonardo Sciascia", "1974", 7500, "", "Adelphi", "1974", "ita"],
    ["Quer pasticciaccio brutto de via Merulana", "Carlo Emilio Gadda", "1957", 7800, "", "Garzanti", "1957", "ita"],
    ["Il deserto dei Tartari", "Dino Buzzati", "1940", 8600, "", "Mondadori", "1940", "ita"],
    ["La boutique del mistero", "Dino Buzzati", "1968", 7600, "", "Mondadori", "1968", "ita"],
    ["Sostiene Pereira", "Antonio Tabucchi", "1994", 8200, "", "Feltrinelli", "1994", "ita"],
    ["Oceano mare", "Alessandro Baricco", "1993", 8000, "", "Rizzoli", "1993", "ita"],
    ["Novecento", "Alessandro Baricco", "1994", 8300, "", "Feltrinelli", "1994", "ita"],
    ["Seta", "Alessandro Baricco", "1996", 7900, "", "Rizzoli", "1996", "ita"],
    ["Io non ho paura", "Niccolò Ammaniti", "2001", 7900, "", "Einaudi", "2001", "ita"],
    ["La solitudine dei numeri primi", "Paolo Giordano", "2008", 7800, "", "Mondadori", "2008", "ita"],
    ["Gomorra", "Roberto Saviano", "2006", 8100, "", "Mondadori", "2006", "ita"],
    ["L'amica geniale", "Elena Ferrante", "2011", 8600, "", "Edizioni E/O", "2011", "ita"],
    ["Storia del nuovo cognome", "Elena Ferrante", "2012", 8000, "", "Edizioni E/O", "2012", "ita"],
    ["La forma dell'acqua", "Andrea Camilleri", "1994", 8000, "", "Sellerio", "1994", "ita"],
    ["Il cane di terracotta", "Andrea Camilleri", "1996", 7900, "", "Sellerio", "1996", "ita"],
    ["Il ladro di merendine", "Andrea Camilleri", "1996", 7900, "", "Sellerio", "1996", "ita"],
    ["Montedidio", "Erri De Luca", "2001", 7200, "", "Feltrinelli", "2001", "ita"],
    ["Il peso della farfalla", "Erri De Luca", "2009", 7100, "", "Feltrinelli", "2009", "ita"],
    ["1984", "George Orwell", "1949", 9800, "/works/OL1168007W", "Mondadori", "1949", "ita"],
    ["La fattoria degli animali", "George Orwell", "1945", 9400, "/works/OL1168083W", "Mondadori", "1945", "ita"],
    ["Il Signore degli Anelli", "J.R.R. Tolkien", "1954", 9700, "/works/OL27448W", "Bompiani", "1954", "ita"],
    ["Lo Hobbit", "J.R.R. Tolkien", "1937", 9500, "/works/OL27482W", "Bompiani", "1937", "ita"],
    ["Il Silmarillion", "J.R.R. Tolkien", "1977", 8500, "/works/OL27495W", "Bompiani", "1977", "ita"],
    ["Orgoglio e pregiudizio", "Jane Austen", "1813", 9500, "/works/OL66554W", "Einaudi", "1813", "ita"],
    ["Il grande Gatsby", "Francis Scott Fitzgerald", "1925", 9300, "/works/OL468431W", "Mondadori", "1925", "ita"],
    ["Il giovane Holden", "J.D. Salinger", "1951", 9200, "/works/OL3335245W", "Einaudi", "1951", "ita"],
    ["Il vecchio e il mare", "Ernest Hemingway", "1952", 9100, "/works/OL62994W", "Mondadori", "1952", "ita"],
    ["Per chi suona la campana", "Ernest Hemingway", "1940", 8600, "/works/OL62987W", "Mondadori", "1940", "ita"],
    ["Addio alle armi", "Ernest Hemingway", "1929", 8500, "/works/OL62979W", "Mondadori", "1929", "ita"],
    ["Cent'anni di solitudine", "Gabriel García Márquez", "1967", 9600, "/works/OL57645W", "Mondadori", "1967", "ita"],
    ["L'amore ai tempi del colera", "Gabriel García Márquez", "1985", 8900, "/works/OL57638W", "Mondadori", "1985", "ita"],
    ["Cronaca di una morte annunciata", "Gabriel García Márquez", "1981", 8600, "/works/OL57627W", "Mondadori", "1981", "ita"],
    ["Delitto e castigo", "Fëdor Dostoevskij", "1866", 9500, "/works/OL166896W", "Einaudi", "1866", "ita"],
    ["I fratelli Karamazov", "Fëdor Dostoevskij", "1880", 9300, "/works/OL166905W", "Einaudi", "1880", "ita"],
    ["L'idiota", "Fëdor Dostoevskij", "1869", 8900, "/works/OL166899W", "Einaudi", "1869", "ita"],
    ["Guerra e pace", "Lev Tolstoj", "1869", 9400, "/works/OL267171W", "Einaudi", "1869", "ita"],
    ["Anna Karenina", "Lev Tolstoj", "1877", 9300, "/works/OL267155W", "Einaudi", "1877", "ita"],
    ["Il Maestro e Margherita", "Michail Bulgakov", "1967", 9100, "/works/OL1071525W", "Einaudi", "1967", "ita"],
    ["Il processo", "Franz Kafka", "1925", 9100, "/works/OL43123W", "Adelphi", "1925", "ita"],
    ["La metamorfosi", "Franz Kafka", "1915", 9200, "/works/OL43118W", "Adelphi", "1915", "ita"],
    ["Il castello", "Franz Kafka", "1926", 8400, "/works/OL43120W", "Adelphi", "1926", "ita"],
    ["Siddhartha", "Hermann Hesse", "1922", 9000, "/works/OL79264W", "Adelphi", "1922", "ita"],
    ["Il lupo della steppa", "Hermann Hesse", "1927", 8600, "/works/OL79248W", "Adelphi", "1927", "ita"],
    ["Narciso e Boccadoro", "Hermann Hesse", "1930", 8500, "/works/OL79256W", "Adelphi", "1930", "ita"],
    ["Lo straniero", "Albert Camus", "1942", 9100, "/works/OL1231676W", "Bompiani", "1942", "ita"],
    ["La peste", "Albert Camus", "1947", 8900, "/works/OL1231670W", "Bompiani", "1947", "ita"],
    ["L'insostenibile leggerezza dell'essere", "Milan Kundera", "1984", 9000, "/works/OL68077W", "Adelphi", "1984", "ita"],
    ["Il Piccolo Principe", "Antoine de Saint-Exupéry", "1943", 9600, "/works/OL1112050W", "Bompiani", "1943", "ita"],
    ["Fahrenheit 451", "Ray Bradbury", "1953", 9100, "/works/OL103123W", "Mondadori", "1953", "ita"],
    ["Il mondo nuovo", "Aldous Huxley", "1932", 9000, "/works/OL64400W", "Mondadori", "1932", "ita"],
    ["Dune", "Frank Herbert", "1965", 9100, "/works/OL893415W", "Fanucci", "1965", "ita"],
    ["Fondazione", "Isaac Asimov", "1951", 8900, "/works/OL46125W", "Mondadori", "1951", "ita"],
    ["Io, robot", "Isaac Asimov", "1950", 8700, "/works/OL46148W", "Mondadori", "1950", "ita"],
    ["Guida galattica per gli autostoppisti", "Douglas Adams", "1979", 8900, "/works/OL2163649W", "Mondadori", "1979", "ita"],
    ["Harry Potter e la pietra filosofale", "J.K. Rowling", "1997", 9600, "/works/OL82563W", "Salani", "1998", "ita"],
    ["Il codice Da Vinci", "Dan Brown", "2003", 8800, "/works/OL76837W", "Mondadori", "2003", "ita"],
    ["I pilastri della Terra", "Ken Follett", "1989", 8900, "/works/OL1922536W", "Mondadori", "1989", "ita"],
    ["L'ombra del vento", "Carlos Ruiz Zafón", "2001", 8800, "/works/OL5730286W", "Mondadori", "2001", "ita"],
    ["Il cacciatore di aquiloni", "Khaled Hosseini", "2003", 8800, "/works/OL5781992W", "Piemme", "2003", "ita"],
    ["Dieci piccoli indiani", "Agatha Christie", "1939", 9100, "/works/OL471574W", "Mondadori", "1939", "ita"],
    ["Assassinio sull'Orient Express", "Agatha Christie", "1934", 9000, "/works/OL471715W", "Mondadori", "1934", "ita"],
    ["Shining", "Stephen King", "1977", 8900, "/works/OL81633W", "Bompiani", "1977", "ita"],
    ["It", "Stephen King", "1986", 9000, "/works/OL81613W", "Sperling & Kupfer", "1986", "ita"],
    ["Sapiens. Da animali a dèi", "Yuval Noah Harari", "2011", 8800, "", "Bompiani", "2014", "ita"],
]

DEFAULT_BOOK_PUBLISHERS_IT = [
    "Mondadori",
    "Einaudi",
    "BUR Rizzoli",
    "Adelphi",
    "Feltrinelli",
    "Garzanti",
    "Bompiani",
    "Rizzoli",
    "Newton Compton",
    "Laterza",
    "UTET",
    "Sellerio",
    "Marsilio",
    "Giunti",
    "Il Mulino",
]


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
    edition_count: int = 0
    from_cache: bool = False

    def __post_init__(self) -> None:
        if not self.year_publishers and self.publisher_years:
            for p, y in self.publisher_years.items():
                if y and y not in self.year_publishers:
                    self.year_publishers[y] = p
        if not self.year_collections and self.collection_meta:
            for c, m in self.collection_meta.items():
                y = m.get("year")
                if y and y not in self.year_collections:
                    self.year_collections[y] = c
        if not self.publisher_collections and self.collection_meta:
            for c, m in self.collection_meta.items():
                p = m.get("publisher")
                if p and p not in self.publisher_collections:
                    self.publisher_collections[p] = c
                    self.publisher_collections[fold(p)] = c


class PersistentCache:
    """Cache SQLite persistente su disco per ricerche ed edizioni (con connessione riutilizzata e pulizia TTL)."""

    def __init__(self, db_file: Optional[str] = None):
        self.db_file = db_file or cache_path()
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._writes_since_prune = 0
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            os.makedirs(os.path.dirname(self.db_file), exist_ok=True)
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
                    os.remove(path)
            self._writes_since_prune = 0
            self._get_conn()

    def get(self, key: str, max_age: int = CACHE_TTL_SECONDS) -> Optional[dict]:
        now = int(time.time())
        with self._lock:
            try:
                conn = self._get_conn()
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

        conditions = ["k LIKE ?"]
        params = [f"search:v11:{category}:%"]
        sub_conds = []
        for tok in sub_tokens[:4]:
            p = tok[:4] if len(tok) >= 4 else tok
            sub_conds.append("v LIKE ?")
            params.append(f"%{p}%")
        if sub_conds:
            # Con 2 o più parole significative usiamo AND per precisione ed estrema velocità (<1ms)
            joiner = " AND " if len(sub_conds) >= 2 else " OR "
            conditions.append("(" + joiner.join(sub_conds) + ")")

        where_clause = " AND ".join(conditions)
        sql = f"SELECT v FROM kv_cache WHERE {where_clause} ORDER BY ts DESC LIMIT {limit}"

        items: List[dict] = []
        seen = set()
        with self._lock:
            try:
                conn = self._get_conn()
                rows = conn.execute(sql, params).fetchall()
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
            self._load_music(os.path.join(data_dir, "seed_music.json"))
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

    def _load_music(self, path: str) -> None:
        if not os.path.exists(path):
            return
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
                pop = max(raw_pop, 4500)
                mbid = str(row[4] or "").strip() if len(row) > 4 else ""
                st = search_fold(title)
                sa = search_fold(artist)
                m_idx = len(self.music)
                self.music.append((title, artist, year, pop, mbid, st, sa))
                for tok in set((st + " " + sa).split()):
                    if len(tok) >= 2:
                        p = tok[:3]
                        self._music_token_map.setdefault(p, []).append(m_idx)
                        if p != tok[:2]:
                            self._music_token_map.setdefault(tok[:2], []).append(m_idx)
        except Exception:
            pass


def _clean_publisher_name(pub: str, lang: str = "it") -> str:
    p = re.sub(r"\s+", " ", (pub or "").strip()).strip(",.;:/-[]()! ")
    if not p or len(p) < 2:
        return ""
    low = fold(p)
    if (
        low in ("s.n.", "s.n", "sn", "s.l.", "s.l", "s.n.t", "s.n.t.", "s. n", "s.e.", "unknown", "no label", "not on label")
        or re.fullmatch(r"s\.?\s*[nlte]\.?(?:\s*t\.?)?!?", low)
        or any(arc in low for arc in ("stamperia", "tipografia", "tip.", "presso ", "pe' torchi", "in casa de"))
    ):
        return ""
    if lang == "it":
        if low in ("tascabili economici newton", "roma ten", "ten"):
            return "Newton Compton"
        if low in ("unione tipografico editrice torinese", "unione tipografico-editrice torinese"):
            return "UTET"
        for k, canonical in ITALIAN_PUBLISHERS_PRIORITY.items():
            ks = k.strip()
            if ks == low or low.startswith(ks + " ") or low.endswith(" " + ks):
                return canonical
    return p


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


def _barcode_variants(code: str) -> List[str]:
    """
    Restituisce le varianti equivalenti GS1 tra UPC-A (12 cifre) ed EAN-13 (13 cifre con 0 iniziale),
    così un codice UPC americano viene trovato anche se archiviato come EAN-13 europeo e viceversa.
    """
    c = re.sub(r"[\s\-]", "", (code or "").strip()).upper()
    if not c:
        return []
    out: List[str] = [c]
    if c.isdigit():
        stripped = c.lstrip("0")
        for cand in (c.zfill(12), c.zfill(13), stripped):
            if cand and len(cand) >= 8 and cand not in out:
                out.append(cand)
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


def _score_title_within_author(
    query_norm: str,
    query_tokens: List[str],
    title_norm: str,
    popularity: int,
    is_archive: bool = False,
    lang_match: bool = False,
    is_catalog: bool = False,
) -> float:
    """
    Calcola il punteggio di un titolo quando l'autore/artista è già stato fissato nel campo Artista/Autore.
    Supporta query vuota (mostra tutta la discografia/bibliografia dell'artista), prefissi brevi ('t', 'the')
    e tolleranza agli errori di battitura ('the divion bell', 'teh final cut').
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
    score = 0.0

    if title_norm == query_norm:
        score += 6000.0
    elif title_norm.startswith(query_norm):
        score += 4200.0
    elif (" " + query_norm) in (" " + title_norm):
        score += 2800.0
    elif len(query_norm) >= 4 and query_norm in title_norm:
        score += 1400.0

    all_tokens_ok = True
    for qt in query_tokens:
        if any(w.startswith(qt) for w in title_words):
            score += 350.0
        elif len(qt) >= 4 and qt in title_norm:
            score += 180.0
        else:
            all_tokens_ok = False
            break

    if not all_tokens_ok:
        if len(query_norm) < 3:
            return -1.0
        best_r = _fuzzy_score_cut(query_norm, title_norm, 76.0)
        if best_r < 76.0:
            return -1.0
        score += (best_r - 70.0) * 70.0

    score += _popularity_bonus(popularity, is_catalog=is_catalog)

    for nw in NOISE_TITLE_WORDS:
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
) -> float:
    """Calcola un punteggio di rilevanza combinando match testuale, fuzzy C++ e popolarità reale."""
    target = title_norm if field_mode in ("title", "both") else author_norm
    secondary = author_norm if field_mode in ("title", "both") else title_norm
    # Estrai il titolo/autore principale separato da sottotitoli (: , - , — , . , / )
    main_target = target.split(":")[0].split(" - ")[0].split(" — ")[0].split(". ")[0].split("/")[0].strip()
    main_secondary = secondary.split(":")[0].split(" - ")[0].split(" — ")[0].split(". ")[0].split("/")[0].strip()

    score = 0.0
    combined = f"{title_norm} {author_norm}"
    combined_rev = f"{author_norm} {title_norm}"
    # Versioni "incorniciate" da spazi: `" " + parola in padded` equivale a "esiste un token che
    # inizia con `parola`" ma viene valutato in C, senza generatori Python per ogni candidato.
    target_toks = target.split()
    combined_toks = combined.split()
    pad_target = " " + " ".join(target_toks) + " "
    pad_combined = " " + " ".join(combined_toks) + " "

    # 1. Match testuale esatto / prefisso / contenimento di frase
    if target == query_norm or main_target == query_norm or combined == query_norm or combined_rev == query_norm:
        if (target == query_norm or main_target == query_norm) and popularity < 1200 and not is_catalog and not is_archive:
            score += 8500.0
        else:
            score += 12000.0
    elif title_norm and author_norm and title_norm in query_norm and author_norm in query_norm:
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

        both_contained = bool(title_norm and author_norm and (title_norm in query_norm or author_norm in query_norm))
        t_words_sub = [w for w in title_norm.split() if len(w) >= 3]
        title_in_q = bool(t_words_sub and sum(1 for w in t_words_sub if w in query_norm) >= max(1, len(t_words_sub) * 0.65))
        lenient = both_contained or title_in_q or query_norm in combined

        if not allow_fuzzy or len(query_norm) < 4:
            if not lenient:
                return -1.0
        else:
            # Calcolo Fuzzy C++ ultra-rapido per tollerare errori di battitura (es. "punk floyd", "umberto ecco")
            fuzzy_targets = (target, main_target, combined, secondary, main_secondary)
            if lenient:
                best_r = max(_fuzzy_score(query_norm, ft) for ft in fuzzy_targets)
            else:
                # Sotto l'80% il candidato verrebbe scartato: con la soglia RapidFuzz esce subito
                best_r = max(_fuzzy_score_cut(query_norm, ft, 80.0) for ft in fuzzy_targets)
                if best_r < 80.0:
                    return -1.0
            direct_target_r = max(
                _direct_ratio_cut(query_norm, target, 82.0),
                _direct_ratio_cut(query_norm, main_target, 82.0),
            )
            score += (best_r - 75.0) * 65.0
            if direct_target_r >= 82.0:
                score += (direct_target_r - 78.0) * 75.0

    score += _popularity_bonus(popularity, is_catalog=is_catalog)

    for nw in NOISE_TITLE_WORDS:
        if nw in title_norm and nw not in query_norm:
            score -= 1800.0

    if is_archive:
        score += 550.0
    if lang_match:
        score += 400.0

    score -= min(len(title_norm), 120) * 1.5
    return score


class CatalogService:
    """
    Servizio centrale di autocompletamento e recupero edizioni multi-motore (Libri, CD, Vinili).
    """

    def __init__(self, cache: Optional[PersistentCache] = None, discogs_token: str = ""):
        self.cache = cache or PersistentCache()
        self.seed = SeedIndex()
        self.discogs_token = discogs_token
        self._mb_lock = threading.Lock()
        self._http_lock = threading.Lock()
        self._last_mb_call = 0.0
        self._gb_disabled_until = 0.0
        self._last_http_code = 0
        self._thread_local = threading.local()
        self._search_pool = ThreadPoolExecutor(max_workers=12)
        threading.Thread(target=self.seed.ensure_loaded, daemon=True).start()

    def close(self) -> None:
        """Chiude il pool HTTP e la cache SQLite associata al catalogo."""
        try:
            self._search_pool.shutdown(wait=False, cancel_futures=True)
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
    ) -> Optional[dict]:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        attempts = 1 if (fast_fail and not is_musicbrainz) else 2
        for attempt in range(attempts):
            if is_musicbrainz:
                with self._mb_lock:
                    elapsed = time.time() - self._last_mb_call
                    if elapsed < 1.05:
                        time.sleep(1.05 - elapsed)
                    self._last_mb_call = time.time()
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    self._set_last_http_code(200)
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                code = getattr(exc, "code", None)
                self._set_last_http_code(int(code) if code else 0)
                if code in (400, 401, 403, 404) or attempt == attempts - 1:
                    return None
                time.sleep(1.2 * (attempt + 1) if is_musicbrainz else 0.25)
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
                return json.loads(resp.read().decode("utf-8"))
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
                    # Cerca prefisso più lungo (es. 'oscar moderni classici' -> 'Mondadori', 'et scrittori' -> 'Einaudi')
                    for k_coll, pub_target in sorted(COLLECTION_TO_PUBLISHER_IT.items(), key=lambda kv: -len(kv[0])):
                        if c_base_fold.startswith(k_coll + " ") or c_base_fold.endswith(" " + k_coll) or k_coll in c_base_fold:
                            resolved_pub = pub_target
                            break

        # 4) Se abbiamo individuato l'Editore/Etichetta ma non ancora l'anno specifico della collana,
        #    usa l'anno di pubblicazione registrato per quell'editore/etichetta nel bundle dell'opera!
        if resolved_pub and not resolved_yr and bundle and bundle.publisher_years:
            resolved_yr = (
                bundle.publisher_years.get(resolved_pub)
                or bundle.publisher_years.get(fold(resolved_pub))
                or ""
            )

        return resolved_pub, resolved_yr

    def resolve_publisher_metadata(
        self,
        publisher: str,
        category: str = "Libri",
        author: str = "",
        bundle: Optional[EditionBundle] = None,
        current_collection: str = "",
    ) -> Tuple[str, str, bool]:
        """
        Dato un Editore / Etichetta (es. 'Mondadori', 'Einaudi', 'Adelphi', 'EMI', 'Harvest'):
        Restituisce (anno_risolto, collana_risolta, collana_incompatibile_da_togliere).
        - anno_risolto: l'anno registrato per quell'editore nel bundle dell'opera.
        - collana_risolta: eventuale collana specifica dell'edizione di quell'editore.
        - collana_incompatibile_da_togliere: True se l'attuale current_collection appartiene a un ALTRO editore
          diverso da quello selezionato, e quindi va rimossa.
        """
        p_raw = (publisher or "").strip()
        if not p_raw:
            return "", "", False

        p_fold = fold(p_raw)
        p_sfold = search_fold(p_raw)

        # 1) Risolvi l'anno associato a questo editore per l'opera corrente
        resolved_yr = ""
        if bundle and bundle.publisher_years:
            resolved_yr = (
                bundle.publisher_years.get(p_raw)
                or bundle.publisher_years.get(p_fold)
                or bundle.publisher_years.get(p_sfold)
                or ""
            )
            if not resolved_yr:
                for k_pub, yr_v in bundle.publisher_years.items():
                    kf = fold(k_pub)
                    if kf and yr_v and (kf == p_fold or kf in p_fold or p_fold in kf):
                        resolved_yr = yr_v
                        break

        # Se non trovato nel bundle, controlla collection_meta del bundle
        if not resolved_yr and bundle and bundle.collection_meta:
            for meta in bundle.collection_meta.values():
                mp = meta.get("publisher") or ""
                if mp and (fold(mp) == p_fold or fold(mp) in p_fold or p_fold in fold(mp)):
                    if meta.get("year"):
                        resolved_yr = meta.get("year")
                        break

        # 2) Risolvi eventuale collana specifica per questo editore
        resolved_coll = ""
        if bundle and bundle.publisher_collections:
            resolved_coll = (
                bundle.publisher_collections.get(p_raw)
                or bundle.publisher_collections.get(p_fold)
                or bundle.publisher_collections.get(p_sfold)
                or ""
            )
            if not resolved_coll:
                for k_pub, c_val in bundle.publisher_collections.items():
                    kf = fold(k_pub)
                    if kf and c_val and (kf == p_fold or kf in p_fold or p_fold in kf):
                        resolved_coll = c_val
                        break

        if not resolved_coll and bundle and bundle.collection_meta:
            for c_name, meta in bundle.collection_meta.items():
                mp = meta.get("publisher") or ""
                if mp and (fold(mp) == p_fold or fold(mp) in p_fold or p_fold in fold(mp)):
                    resolved_coll = c_name
                    break

        # 3) Verifica se l'attuale current_collection è incompatibile e va tolta
        should_clear_coll = False
        cur_c = (current_collection or "").strip()
        if cur_c:
            col_pub, _ = self.resolve_collection_metadata(cur_c, category, author, bundle)
            if col_pub:
                cp_fold = fold(col_pub)
                cp_sfold = search_fold(col_pub)
                is_compat = (
                    cp_fold == p_fold
                    or cp_sfold == p_sfold
                    or cp_sfold in p_sfold
                    or p_sfold in cp_sfold
                )
                if not is_compat:
                    should_clear_coll = True
            else:
                c_base = re.sub(r"\s*(?:#|n\.?\s*|vol\.?\s*)\d+\s*$", "", cur_c, flags=re.IGNORECASE).strip()
                other_pub = (
                    COLLECTION_TO_PUBLISHER_IT.get(fold(c_base))
                    or COLLECTION_TO_PUBLISHER_IT.get(fold(cur_c))
                )
                if other_pub:
                    op_fold = fold(other_pub)
                    op_sfold = search_fold(other_pub)
                    if not (op_fold == p_fold or op_sfold == p_sfold or op_sfold in p_sfold or p_sfold in op_sfold):
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
    ) -> Tuple[str, str, bool]:
        """
        Dato un Anno inserito o selezionato dall'utente (es. '1980', '1995'):
        Restituisce (editore_risolto, collana_risolta, collana_incompatibile_da_togliere).
        - editore_risolto: l'editore che ha pubblicato l'opera in quell'anno.
        - collana_risolta: eventuale collana dell'edizione di quell'anno.
        - collana_incompatibile_da_togliere: True se l'attuale current_collection apparteneva a un'altra
          edizione/anno/editore diversa da quella selezionata, e quindi va rimossa.
        """
        y_clean = _extract_year(str(year_str or ""))
        if not y_clean:
            return "", "", False

        # 1) Risolvi l'editore per quest'anno
        resolved_pub = ""
        if bundle:
            if bundle.year_publishers and y_clean in bundle.year_publishers:
                resolved_pub = bundle.year_publishers[y_clean]
            if not resolved_pub and bundle.publisher_years:
                for p_name, yr_v in bundle.publisher_years.items():
                    if yr_v == y_clean:
                        resolved_pub = p_name
                        break
            if not resolved_pub and bundle.collection_meta:
                for meta in bundle.collection_meta.values():
                    if meta.get("year") == y_clean and meta.get("publisher"):
                        resolved_pub = meta.get("publisher")
                        break

        # 2) Risolvi eventuale collana per quest'anno
        resolved_coll = ""
        if bundle:
            if bundle.year_collections and y_clean in bundle.year_collections:
                resolved_coll = bundle.year_collections[y_clean]
            if not resolved_coll and bundle.collection_meta:
                for c_name, meta in bundle.collection_meta.items():
                    if meta.get("year") == y_clean:
                        resolved_coll = c_name
                        break

        # 3) Verifica se l'attuale current_collection è incompatibile e va tolta
        should_clear_coll = False
        cur_c = (current_collection or "").strip()
        if cur_c:
            col_pub, col_yr = self.resolve_collection_metadata(cur_c, category, author, bundle)
            if col_yr and col_yr != y_clean:
                should_clear_coll = True
            elif resolved_pub and col_pub:
                cp_sfold = search_fold(col_pub)
                rp_sfold = search_fold(resolved_pub)
                if not (cp_sfold == rp_sfold or cp_sfold in rp_sfold or rp_sfold in cp_sfold):
                    should_clear_coll = True

        return resolved_pub, resolved_coll, should_clear_coll

    def _apply_curated_profile(self, sug: Suggestion) -> Suggestion:
        key = f"{search_fold(sug.title)}|{search_fold(sug.author)}"
        prof = CURATED_EDITION_PROFILES.get(key)
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
            if sug.from_isbn:
                # Se proviene da un codice ISBN/EAN/UPC specifico, preserva l'etichetta/editore
                # e l'anno esatti di quel codice a barre come principali!
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
        """Recupera in un'unica query SQLite le edizioni già in cache per un gruppo di MBID (v9 con fallback v6/v5)."""
        ids = [m for m in dict.fromkeys(mbids) if m]
        if not ids:
            return {}
        found = self.cache.get_many(
            [f"editions:v9:{category}:{m}" for m in ids]
            + [f"editions:v6:{category}:{m}" for m in ids]
            + [f"editions:v5:{category}:{m}" for m in ids]
        )
        out: Dict[str, dict] = {}
        for m in ids:
            ed = (
                found.get(f"editions:v9:{category}:{m}")
                or found.get(f"editions:v6:{category}:{m}")
                or found.get(f"editions:v5:{category}:{m}")
            )
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
                sc = _score_title_within_author(q_norm, q_tokens, st, popularity=500, is_archive=True)
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
                    st, sa = self.seed.books[idx][8], self.seed.books[idx][9]
                    if not _author_matches(sa, ctx_author_norm):
                        continue
                    sc = _score_title_within_author(
                        q_norm,
                        q_tokens,
                        st,
                        popularity=self.seed.books[idx][3],
                        lang_match=(self.seed.books[idx][7] == pref_lang),
                        is_catalog=True,
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
                    st, sa = self.seed.music[idx][5], self.seed.music[idx][6]
                    if not _author_matches(sa, ctx_author_norm):
                        continue
                    sc = _score_title_within_author(
                        q_norm, q_tokens, st, popularity=self.seed.music[idx][3], is_catalog=True
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
                        sc = _score_title_within_author(
                            q_norm, q_tokens, st, popularity=int(row.get("popularity") or 100)
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
                                publishers=row.get("publishers") or ([row["publisher"]] if row.get("publisher") else []),
                                years=row.get("years") or ([row["year"]] if row.get("year") else []),
                                score=sc,
                            ),
                        )

            if scoped_results or len(q_norm) < 3:
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
                    q_norm, q_tokens, st, sa, popularity=500, field_mode=field_mode, is_archive=True, allow_fuzzy=True
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
            sub_q = [qt for qt in q_tokens if qt not in SEARCH_STOPWORDS and len(qt) >= 2]
            tokens_to_index = sub_q if sub_q else q_tokens
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
                    q_norm, q_tokens, st, row[6], popularity=row[3], field_mode=field_mode, allow_fuzzy=True, is_catalog=True
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
                            publishers=row.get("publishers") or ([row["publisher"]] if row.get("publisher") else []),
                            years=row.get("years") or ([row["year"]] if row.get("year") else []),
                            collection=row.get("collection", ""),
                            collections=row.get("collections") or [],
                            from_isbn=bool(row.get("from_isbn") or isbn_code),
                            score=sc,
                        ),
                    )

        # C2) Ricerca nei risultati recenti memorizzati in cache SQLite per token significativi (<1ms)
        if not isbn_code and len(q_tokens) >= 2:
            cached_items = self.cache.search_cached_items(category, q_tokens, limit=35)
            for row in cached_items:
                st = search_fold(row.get("title", ""))
                sa = search_fold(row.get("author", ""))
                sc = score_suggestion(
                    q_norm,
                    q_tokens,
                    st,
                    sa,
                    popularity=int(row.get("popularity") or 100),
                    field_mode=field_mode,
                    allow_fuzzy=True,
                )
                if sc < 0:
                    continue
                clean_pub = _clean_publisher_name(row.get("publisher", ""), lang) if category == "Libri" else row.get("publisher", "")
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
                        publishers=row.get("publishers") or ([clean_pub] if clean_pub else []),
                        years=row.get("years") or ([row["year"]] if row.get("year") else []),
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
                    sc = score_suggestion(
                        q_norm, q_tokens, st, sa, popularity=pop, field_mode=field_mode, allow_fuzzy=True, is_catalog=True
                    )
                    if sc > 0:
                        clean_pub = _clean_publisher_name(pub, lang)
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
                        q_norm, q_tokens, st, sa, popularity=pop, field_mode=field_mode, allow_fuzzy=True, is_catalog=True
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

        cache_key = f"search:v11:{category}:{lang}:{field_mode}:{ctx_author_norm}:{q_norm}"
        cached = self.cache.get(cache_key)
        if cached and isinstance(cached.get("items"), list):
            out = []
            for row in cached["items"]:
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
        fut_gb = self._search_pool.submit(
            self._search_google_books, query, q_norm, lang, field_mode, is_cancelled, context_author, ctx_author_norm
        )
        fut_sbn = self._search_pool.submit(
            self._search_opac_sbn, query, q_norm, lang, field_mode, is_cancelled, context_author, ctx_author_norm
        )
        fut_ibs = self._search_pool.submit(
            self._search_ibs_text, query, q_norm, lang, field_mode, is_cancelled, context_author, ctx_author_norm
        )
        fut_ol = self._search_pool.submit(
            self._search_openlibrary, query, q_norm, lang, field_mode, limit, is_cancelled, context_author, ctx_author_norm
        )
        primary = {fut_sbn}
        pending = {fut_gb, fut_sbn, fut_ibs, fut_ol}
        if isbn_code:
            fut_hp = self._search_pool.submit(self._search_hoepli_isbn, isbn_code, lang, is_cancelled)
            fut_ibs_isbn = self._search_pool.submit(self._search_ibs_isbn, isbn_code, "Libri", lang, is_cancelled)
            fut_amz = self._search_pool.submit(self._search_amazon_isbn, isbn_code, "Libri", lang, is_cancelled)
            primary.update({fut_hp, fut_ibs_isbn, fut_amz})
            pending.update({fut_hp, fut_ibs_isbn, fut_amz})

        while pending:
            done, pending = wait(pending, timeout=1.8, return_when=FIRST_COMPLETED)
            if not done:
                break
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

            # Quando il motore principale (OPAC SBN in ~180ms per testo, o ISBN per barcode) ha risposto
            # e abbiamo già risultati validi, concediamo al massimo 120ms extra ai motori secondari
            # per non rallentare l'autocompletamento reattivo dell'interfaccia
            if not (primary & pending) and len(results) >= 1 and pending:
                done_extra, pending = wait(pending, timeout=0.12)
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
                raw_html = resp.read().decode("utf-8", errors="ignore")
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
                        item_id = re.sub(r"[\s\-]", "", str(it.get("item_id") or "")).strip()
                        if item_id and item_id not in valid_codes:
                            continue
                        title = _html.unescape(str(it.get("item_name") or "")).strip()
                        author = _html.unescape(str(it.get("item_author") or "")).strip()
                        if not title or not author:
                            continue
                        raw_pub = _html.unescape(str(it.get("item_brand") or "")).strip()
                        clean_pub = _clean_publisher_name(raw_pub, lang) if category == "Libri" else raw_pub
                        yr = _extract_year(str(it.get("year_edition") or ""))
                        raw_coll = _html.unescape(str(it.get("item_series") or "")).strip()
                        if raw_coll.lower() in ("null", "none"):
                            raw_coll = ""
                        out.append(
                            Suggestion(
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
                        )
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
                raw_html = resp.read().decode("utf-8", errors="ignore")
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
                        sc = score_suggestion(q_norm, q_tokens, st, sa, popularity=pop, field_mode=field_mode, lang_match=True, allow_fuzzy=True)
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
                raw_html = resp.read().decode("utf-8", errors="ignore")
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
            title = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h2_html))).strip()
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
            out.append(
                Suggestion(
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
            )
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
                raw_html = resp.read().decode("utf-8", errors="ignore")
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
        data = self._http_get_json(url, timeout=1.8, fast_fail=True)
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
                sc = _score_title_within_author(q_norm, q_tokens, st, popularity=pop, lang_match=is_ita)
            else:
                sc = score_suggestion(
                    q_norm, q_tokens, st, sa, popularity=pop, field_mode=field_mode, lang_match=is_ita, allow_fuzzy=True
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
        data = self._http_get_json(url, timeout=2.5, fast_fail=True)
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
            sub_data = self._http_get_json(sub_url, timeout=2.0, fast_fail=True)
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
            f_data = self._http_get_json(f_url, timeout=2.0, fast_fail=True)
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
                sc = _score_title_within_author(q_norm, q_tokens, st, popularity=pop, lang_match=True)
            else:
                sc = score_suggestion(
                    q_norm, q_tokens, st, sa, popularity=pop, field_mode=field_mode, lang_match=True, allow_fuzzy=True
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
        data = self._http_get_json(url, timeout=1.8, is_musicbrainz=False, fast_fail=True)
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
                sc = _score_title_within_author(q_norm, q_tokens, st, popularity=pop, lang_match=is_pref_lang)
            else:
                sc = score_suggestion(
                    q_norm,
                    q_tokens,
                    st,
                    sa,
                    popularity=pop,
                    field_mode=field_mode,
                    lang_match=is_pref_lang,
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
        fut_mb = self._search_pool.submit(
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

        while pending:
            if is_cancelled and is_cancelled():
                break
            done, pending = wait(pending, timeout=2.5, return_when=FIRST_COMPLETED)
            if not done:
                break
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

        return sorted(results.values(), key=lambda s: -s.score)[:limit]

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
                d_upc = self._http_get_json(u_upc, timeout=1.8, fast_fail=True)
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
            data = self._http_get_json(url, timeout=1.8, fast_fail=True)
            if not data or (is_cancelled and is_cancelled()):
                return []
            rows_raw = data.get("results") or []

        out: List[Suggestion] = []
        for idx, row in enumerate(rows_raw):
            raw_title = str(row.get("collectionName") or "").strip()
            title = _ALBUM_SUFFIX_CLEAN_RE.sub("", raw_title).strip() or raw_title
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
                    sc = _score_title_within_author(q_norm, q_tokens, st, popularity=pop)
                else:
                    sc = score_suggestion(
                        q_norm, q_tokens, st, sa, popularity=pop, field_mode=field_mode, allow_fuzzy=True
                    )
                if sc < 0:
                    continue

            sug = Suggestion(
                title=title,
                author=artist,
                year=disp_yr or yr,
                publisher=label,
                author_sort=surname_key(artist, category),
                work_id="",
                category=category,
                source="online",
                popularity=pop,
                publishers=[label] if label else [],
                years=[disp_yr] if disp_yr else ([yr] if yr else []),
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
            d_bar = self._http_get_json(u_bar, timeout=2.5, is_musicbrainz=True, fast_fail=True)
            if not d_bar or (is_cancelled and is_cancelled()):
                return []
            out_bar: List[Suggestion] = []
            for idx, rel in enumerate(d_bar.get("releases") or []):
                raw_t = str(rel.get("title") or "").strip()
                title = _ALBUM_SUFFIX_CLEAN_RE.sub("", raw_t).strip() or raw_t
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
        q_tokens = [re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", t) for t in query_clean.split() if t]
        escaped = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", query_clean)
        query_has_live = "live" in q_norm or "concerto" in q_norm
        live_filter = "" if query_has_live else " NOT secondarytype:live"

        if field_mode == "author":
            if len(q_tokens) == 1:
                lucene = f'artist:({escaped}*) AND (primarytype:album OR primarytype:ep){live_filter}'
            else:
                tok_clauses = [
                    f'(artist:{t} OR artist:{t}~1 OR artist:{t}*)' if len(t) >= 4 else f'artist:{t}*'
                    for t in q_tokens
                ]
                lucene = f'({" AND ".join(tok_clauses)}) AND (primarytype:album OR primarytype:ep){live_filter}'
        elif context_author.strip():
            esc_auth = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", context_author.strip())
            auth_tokens = [re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", t) for t in esc_auth.split() if t]
            auth_clause = " AND ".join(
                f'(artist:{t} OR artist:{t}~1 OR artist:{t}*)' if len(t) >= 4 else f'artist:{t}*'
                for t in auth_tokens
            )
            if q_tokens:
                tok_clauses = [
                    f'(releasegroup:{t} OR releasegroup:{t}~1 OR releasegroup:{t}*)' if len(t) >= 4 else f'releasegroup:{t}*'
                    for t in q_tokens
                ]
                rg_clause = " AND ".join(tok_clauses)
                lucene = f'(releasegroup:("{escaped}")^6 OR ({rg_clause})) AND ({auth_clause}) AND (primarytype:album OR primarytype:ep){live_filter}'
            else:
                lucene = f'({auth_clause}) AND (primarytype:album OR primarytype:ep){live_filter}'
        else:
            # Ricerca generale su titolo, artista o combinazione titolo + artista
            tok_clauses = [
                f'({t} OR {t}~1 OR {t}*)' if len(t) >= 4 else f'{t}*'
                for t in q_tokens
            ]
            all_toks = " AND ".join(tok_clauses)
            lucene = (
                f'(releasegroup:("{escaped}")^8 OR artist:("{escaped}")^8 OR ({all_toks})) '
                f'AND (primarytype:album OR primarytype:ep){live_filter}'
            )

        url = f"https://musicbrainz.org/ws/2/release-group/?query={urllib.parse.quote(lucene)}&fmt=json&limit=22"
        data = self._http_get_json(url, timeout=3.8, is_musicbrainz=True)
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
                sc = _score_title_within_author(q_norm, q_tokens, st, popularity=effective_pop)
            else:
                sc = score_suggestion(
                    q_norm, q_tokens, st, sa, popularity=effective_pop, field_mode=field_mode, allow_fuzzy=True
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
                cached_ed = (
                    self.cache.get(f"editions:v9:{category}:{rgid}")
                    or self.cache.get(f"editions:v6:{category}:{rgid}")
                    or self.cache.get(f"editions:v5:{category}:{rgid}")
                )
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
        data = self._http_get_json(
            url,
            timeout=2.4,
            extra_headers={"Authorization": f"Discogs token={self.discogs_token}"},
            fast_fail=True,
        )
        if not data or (is_cancelled and is_cancelled()):
            return []

        out: List[Suggestion] = []
        for idx, row in enumerate(data.get("results") or []):
            full_title = str(row.get("title") or "").strip()
            if " - " in full_title:
                artist, title = [p.strip() for p in full_title.split(" - ", 1)]
            else:
                title, artist = full_title, ""
            artist = re.sub(r"\s*(\(\d+\)|\*)\s*$", "", artist).rstrip("* ").strip()  # Rimuove disambiguazioni e asterischi Discogs
            if not title or not artist:
                continue
            yr = _extract_year(str(row.get("year") or ""))
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
                sc = _score_title_within_author(q_norm, q_tokens, st, popularity=pop)
            else:
                sc = score_suggestion(q_norm, q_tokens, st, sa, popularity=pop, field_mode=field_mode, allow_fuzzy=True)
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
        norm_key = f"{search_fold(title)}|{search_fold(author)}"
        cache_id = work_id or norm_key
        cache_key = f"editions:v9:{category}:{cache_id}"

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
        3. Open Library (`/works/.../editions.json` incluso `series`)
        """
        norm_key = f"{search_fold(title)}|{search_fold(author)}"
        curated = CURATED_EDITION_PROFILES.get(norm_key, {})
        curated_cat = curated.get("Libri", {})

        pub_counts_it: Dict[str, int] = {}
        pub_counts_all: Dict[str, int] = {}
        coll_counts: Dict[str, int] = {}
        collection_meta: Dict[str, Dict[str, str]] = {}
        publisher_years: Dict[str, str] = {}
        year_publishers: Dict[str, str] = {}
        year_collections: Dict[str, str] = {}
        publisher_collections: Dict[str, str] = {}
        years_set: set[str] = set()
        first_year = ""
        ed_total = 0
        lock = threading.Lock()

        for p in curated_cat.get("publishers", []):
            pub_counts_it[p] = pub_counts_it.get(p, 0) + 15
        for y in curated_cat.get("years", []):
            years_set.add(y)

        def add_pub_year(raw_p: str, yr_str: str, is_ita_hint: bool = False) -> Tuple[str, str]:
            cp = _clean_publisher_name(raw_p, lang)
            yr = _extract_year(yr_str)
            with lock:
                if cp:
                    is_major_it = fold(cp) in ITALIAN_PUBLISHERS_PRIORITY
                    pub_counts_all[cp] = pub_counts_all.get(cp, 0) + (8 if is_major_it else 1)
                    if is_ita_hint or is_major_it:
                        pub_counts_it[cp] = pub_counts_it.get(cp, 0) + (18 if is_major_it else 3)
                    if yr:
                        if cp not in publisher_years:
                            publisher_years[cp] = yr
                            publisher_years[fold(cp)] = yr
                        if yr not in year_publishers:
                            year_publishers[yr] = cp
                if yr:
                    years_set.add(yr)
            return cp, yr

        def add_coll(c_name: str, weight: int = 5, pub_name: str = "", yr_val: str = "") -> None:
            c = c_name.strip(" ,.;:/-")
            if not c or len(c) < 2:
                return
            with lock:
                coll_counts[c] = coll_counts.get(c, 0) + weight
                if pub_name or yr_val:
                    existing_m = collection_meta.get(c) or {}
                    merged_m = {
                        "publisher": existing_m.get("publisher") or pub_name,
                        "year": existing_m.get("year") or yr_val,
                    }
                    collection_meta[c] = merged_m
                    collection_meta[fold(c)] = merged_m
                if yr_val and yr_val not in year_collections:
                    year_collections[yr_val] = c
                if pub_name:
                    if pub_name not in publisher_collections:
                        publisher_collections[pub_name] = c
                        publisher_collections[fold(pub_name)] = c

        def task_google() -> None:
            if is_cancelled and is_cancelled():
                return
            q = f'intitle:"{title}" inauthor:"{author}"'
            u = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(q)}&maxResults=35"
            d = self._http_get_json(u, timeout=3.2)
            if not d:
                return
            t_fold = search_fold(title)
            a_fold = search_fold(author)
            t_words = [w for w in t_fold.split() if len(w) > 2]
            for it in d.get("items") or []:
                vi = it.get("volumeInfo") or {}
                vt = search_fold(str(vi.get("title") or ""))
                # Se il titolo non corrisponde all'opera ricercata, ignora il volume
                if t_words and not all(w in vt for w in t_words):
                    if t_fold not in vt and vt not in t_fold:
                        continue
                if a_fold:
                    v_authors = [search_fold(str(a)) for a in (vi.get("authors") or [])]
                    if v_authors and not any(a_fold in va or va in a_fold for va in v_authors):
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
            d = self._http_get_json(u, timeout=3.2)
            if not d:
                return
            brief_recs = d.get("briefRecords") or []
            t_fold = search_fold(title)
            t_words = [w for w in t_fold.split() if len(w) > 2]
            matched_bids = []
            for rec in brief_recs:
                r_tit = search_fold(str(rec.get("titolo") or ""))
                if t_words and not all(w in r_tit for w in t_words):
                    if t_fold not in r_tit and r_tit not in t_fold:
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
                fd = self._http_get_json(fu, timeout=2.0, fast_fail=True)
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

        def task_openlib() -> None:
            nonlocal ed_total
            if is_cancelled and is_cancelled():
                return
            if work_id and work_id.startswith("/works/"):
                u = f"https://openlibrary.org{work_id}/editions.json?limit=80"
                d = self._http_get_json(u, timeout=3.8)
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

        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [pool.submit(task_google), pool.submit(task_sbn), pool.submit(task_openlib)]
            for f in as_completed(futs):
                try:
                    f.result()
                except Exception:
                    pass

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

        sorted_years = sorted(years_set)
        default_pub = curated_cat.get("default_publisher") or (ranked_pubs[0] if ranked_pubs else "")
        default_yr = curated_cat.get("default_year") or first_year or (sorted_years[0] if sorted_years else "")
        if default_yr and default_yr in sorted_years:
            sorted_years = [default_yr] + [y for y in sorted_years if y != default_yr]
        if default_pub and default_yr and default_pub not in publisher_years:
            publisher_years[default_pub] = default_yr
            publisher_years[fold(default_pub)] = default_yr

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
        norm_key = f"{search_fold(title)}|{search_fold(author)}"
        curated = CURATED_EDITION_PROFILES.get(norm_key, {})
        curated_cat = curated.get(category, {})
        artist_sort = curated.get("artist_sort") or surname_key(author, category)

        label_counts: Dict[str, int] = {}
        format_years: set[str] = set()
        fallback_labels: Dict[str, int] = {}
        fallback_years: set[str] = set()
        publisher_years: Dict[str, str] = {}
        year_publishers: Dict[str, str] = {}
        year_collections: Dict[str, str] = {}
        publisher_collections: Dict[str, str] = {}
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

        # 1) Query MusicBrainz Release con fallback automatico se rgid non trova stampe
        sort_from_mb = False

        def ingest_mb_releases(releases_list: list) -> None:
            nonlocal artist_sort, matched_releases, sort_from_mb
            for rel in releases_list:
                credits = rel.get("artist-credit") or []
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
                            if lname not in publisher_years:
                                publisher_years[lname] = yr
                                publisher_years[fold(lname)] = yr
                            if yr not in year_publishers:
                                year_publishers[yr] = lname
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
                            if first_lbl or (yr and is_valid_cd_yr):
                                meta = {"publisher": first_lbl, "year": yr if is_valid_cd_yr else ""}
                                collection_meta.setdefault(c_title, meta)
                                collection_meta.setdefault(fold(c_title), meta)
                else:
                    if yr and is_valid_cd_yr:
                        fallback_years.add(yr)
                    for lname in labels_in_rel:
                        fallback_labels[lname] = fallback_labels.get(lname, 0) + 1

        norm_title = title.replace("’", "'").replace("‘", "'").replace("`", "'").strip()
        norm_author = author.replace("’", "'").replace("‘", "'").replace("`", "'").strip()

        # 1A) Apple iTunes lookup veloce (~80ms) per garantire sempre almeno l'etichetta principale e l'anno
        if not (is_cancelled and is_cancelled()):
            it_url = (
                f"https://itunes.apple.com/search?term={urllib.parse.quote(norm_title + ' ' + norm_author)}"
                "&media=music&entity=album&country=IT&limit=8"
            )
            it_data = self._http_get_json(it_url, timeout=2.0, is_musicbrainz=False, fast_fail=True)
            if it_data and isinstance(it_data.get("results"), list):
                st_target = search_fold(title)
                sa_target = search_fold(author)
                for row in it_data["results"]:
                    rt = _ALBUM_SUFFIX_CLEAN_RE.sub("", str(row.get("collectionName") or "")).strip()
                    ra = str(row.get("artistName") or "").strip()
                    if search_fold(rt) == st_target or st_target in search_fold(rt):
                        if sa_target in search_fold(ra) or search_fold(ra) in sa_target:
                            lbl = _extract_itunes_label(str(row.get("copyright") or ""))
                            yr = _extract_year(str(row.get("releaseDate") or ""))
                            valid_yr = yr if not (category == "CD" and yr.isdigit() and int(yr) < 1982) else ""
                            if lbl:
                                fallback_labels[lbl] = fallback_labels.get(lbl, 0) + 2
                                if valid_yr:
                                    if lbl not in publisher_years:
                                        publisher_years[lbl] = valid_yr
                                        publisher_years[fold(lbl)] = valid_yr
                                    if valid_yr not in year_publishers:
                                        year_publishers[valid_yr] = lbl
                            if valid_yr:
                                fallback_years.add(valid_yr)

        # 1B) MusicBrainz Release Inspector: prova rgid (se presente), poi fallback automatico su titolo+artista
        if not (is_cancelled and is_cancelled()):
            if work_id:
                rg_url = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(f'rgid:{work_id}')}&fmt=json&limit=85"
                sdata = self._http_get_json(rg_url, timeout=4.2, is_musicbrainz=True)
                if sdata and isinstance(sdata.get("releases"), list):
                    ingest_mb_releases(sdata["releases"])

            # Se work_id era assente o non ha restituito stampe con etichetta, cerca per titolo + artista!
            if (matched_releases < 2 or not label_counts) and not (is_cancelled and is_cancelled()):
                esc_t = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", norm_title)
                esc_a = re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", norm_author)
                rel_q = f'release:"{esc_t}" AND artist:"{esc_a}"'
                rel_url = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(rel_q)}&fmt=json&limit=85"
                sdata2 = self._http_get_json(rel_url, timeout=4.2, is_musicbrainz=True)
                if sdata2 and isinstance(sdata2.get("releases"), list) and sdata2["releases"]:
                    ingest_mb_releases(sdata2["releases"])
                elif not (is_cancelled and is_cancelled()):
                    # Fallback tokenizzato (senza virgolette né punteggiatura)
                    tok_t = search_fold(norm_title)
                    tok_a = search_fold(norm_author)
                    if tok_t and tok_a:
                        rel_q3 = f"release:({tok_t}) AND artist:({tok_a})"
                        rel_url3 = f"https://musicbrainz.org/ws/2/release/?query={urllib.parse.quote(rel_q3)}&fmt=json&limit=65"
                        sdata3 = self._http_get_json(rel_url3, timeout=4.2, is_musicbrainz=True)
                        if sdata3 and isinstance(sdata3.get("releases"), list):
                            ingest_mb_releases(sdata3["releases"])

        # 2) Se abbiamo anche Discogs Token, arricchisci con le stampe fisiche Discogs
        if self.discogs_token and not (is_cancelled and is_cancelled()):
            fmt_filter = "CD" if category == "CD" else "Vinyl"
            d_url = (
                "https://api.discogs.com/database/search?"
                f"release_title={urllib.parse.quote(title)}&artist={urllib.parse.quote(author)}"
                f"&type=release&format={fmt_filter}&per_page=35"
            )
            d_data = self._http_get_json(
                d_url,
                timeout=3.5,
                extra_headers={"Authorization": f"Discogs token={self.discogs_token}"},
            )
            if d_data and isinstance(d_data.get("results"), list):
                for r in d_data["results"]:
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
                                if lname not in publisher_years:
                                    publisher_years[lname] = yr
                                    publisher_years[fold(lname)] = yr
                                if yr not in year_publishers:
                                    year_publishers[yr] = lname

        ranked_labels = [k for k, _ in sorted(label_counts.items(), key=lambda kv: -kv[1])]
        if not ranked_labels:
            ranked_labels = [k for k, _ in sorted(fallback_labels.items(), key=lambda kv: -kv[1])]

        sorted_years = sorted(format_years) if format_years else sorted(fallback_years)
        default_label = curated_cat.get("default_publisher") or (ranked_labels[0] if ranked_labels else "")
        default_yr = curated_cat.get("default_year") or (sorted_years[0] if sorted_years else "")
        if default_yr and default_yr in sorted_years:
            sorted_years = [default_yr] + [y for y in sorted_years if y != default_yr]
        if default_label and default_yr:
            if default_label not in publisher_years:
                publisher_years[default_label] = default_yr
                publisher_years[fold(default_label)] = default_yr
            if default_yr not in year_publishers:
                year_publishers[default_yr] = default_label

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
            edition_count=matched_releases or len(ranked_labels),
        )
