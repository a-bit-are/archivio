"""Dati statici, editori prioritari, collane e profili curati per CatalogService."""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

from .textutil import fold, search_fold


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
    "a. mondadori": "Mondadori",
    "a mondadori": "Mondadori",
    "mondadori libri": "Mondadori",
    "mondadori italian": "Mondadori",
    "oscar mondadori": "Mondadori",
    "strade blu mondadori": "Mondadori",
    "einaudi": "Einaudi",
    "giulio einaudi": "Einaudi",
    "giulio einaudi editore": "Einaudi",
    "einaudi tascabili": "Einaudi",
    "einaudi ragazzi": "Einaudi Ragazzi",
    "bompiani": "Bompiani",
    "tascabili bompiani": "Bompiani",
    "rcs libri": "RCS Libri",
    "r.c.s.": "RCS Libri",
    "feltrinelli": "Feltrinelli",
    "giangiacomo feltrinelli": "Feltrinelli",
    "universale economica feltrinelli": "Feltrinelli",
    "adelphi": "Adelphi",
    "adelphi edizioni": "Adelphi",
    "rizzoli": "Rizzoli",
    "bur": "BUR Rizzoli",
    "bur rizzoli": "BUR Rizzoli",
    "bur biblioteca univ. rizzoli": "BUR Rizzoli",
    "bur biblioteca univ rizzoli": "BUR Rizzoli",
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
    "mulino": "Il Mulino",
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
    "hoepli editore": "Hoepli",
    "cde": "CDE",
    "c.d.e.": "CDE",
    "edizioni cde": "CDE",
    "cde s.p.a.": "CDE",
    "cde spa": "CDE",
    "club degli editori": "Club degli Editori",
    "euroclub": "Euroclub",
    "mondolibri": "Mondolibri",
    "edizioni di storia e letteratura": "Edizioni di Storia e Letteratura",
    "storia e letteratura": "Edizioni di Storia e Letteratura",
    "piero gobetti": "Piero Gobetti",
    "gobetti": "Piero Gobetti",
    "fratelli ribet": "Fratelli Ribet",
    "ribet": "Fratelli Ribet",
    "carabba": "Carabba",
    "giuseppe carabba": "Carabba",
    "alberto tallone": "Alberto Tallone",
    "tallone": "Alberto Tallone",
    "tallone editore": "Alberto Tallone",
    "fabbri": "Fabbri",
    "fabbri editori": "Fabbri",
    "fratelli fabbri": "Fabbri",
    "de agostini": "DeAgostini",
    "deagostini": "DeAgostini",
    "la biblioteca di repubblica": "La Biblioteca di Repubblica",
    "biblioteca di repubblica": "La Biblioteca di Repubblica",
    "gruppo editoriale l'espresso": "La Biblioteca di Repubblica",
    "repubblica": "La Biblioteca di Repubblica",
    "corriere della sera": "Corriere della Sera",
    "demetra": "Demetra",
    "carocci": "Carocci",
    "mursia": "Mursia",
    "u. mursia": "Mursia",
    "sei": "SEI",
    "loescher": "Loescher",
    "le monnier": "Le Monnier",
    "la nuova italia": "La Nuova Italia",
    "electa": "Electa",
    "quodlibet": "Quodlibet",
    "marcos y marcos": "Marcos y Marcos",
    "keller": "Keller",
    "frassinelli": "Frassinelli",
    "sonzogno": "Sonzogno",
    "cappelli": "Cappelli",
    "castelvecchi": "Castelvecchi",
    "donzelli": "Donzelli",
    "rubbettino": "Rubbettino",
    "famiglia cristiana": "Famiglia Cristiana",
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
        "Indi",
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
        "Lain",
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

_COLLECTION_TO_PUBLISHER_SORTED = sorted(
    COLLECTION_TO_PUBLISHER_IT.items(), key=lambda kv: -len(kv[0])
)

NOISE_TITLE_WORDS = (
    "karaoke", "tribute", "lullaby", "8-bit", "ringtone", "instrumental version",
    "in the style of", "made famous by", "backing track", "cover version",
)

NOISE_BOOK_WORDS = (
    "analisi del libro", "riassunto", "guida alla lettura", "guida alla letteratura",
    "study guide", "book summary", "analisi e commento", "schede di lettura",
    "cliffnotes", "sparknotes", "lecturae", "mustread", "lezioni su", "studio su",
    "commento a",
)

NON_BOOK_PUBLISHERS_FOLD = {
    "audible", "audible studios", "storytel", "emons audiolibri",
    "lecturae.it", "lecturae", "mustread", "mustread (it)",
    "grin verlag", "streetlib", "auto 11 motors",
}

_CITY_ONLY_PUBLISHERS_FOLD = {
    "milano", "torino", "roma", "firenze", "bologna", "venezia", "napoli",
    "palermo", "bari", "genova", "verona", "padova", "parma", "trieste",
    "catania", "bergamo", "brescia", "modena", "pisa", "siena", "perugia",
    "lecce", "italia", "italy", "london", "new york", "paris", "berlin",
    "madrid", "barcelona", "ankara", "istanbul", "tokyo", "lisboa", "wien",
    "zurich", "geneve", "bruxelles", "amsterdam", "munchen", "stuttgart",
    "frankfurt", "oxford", "cambridge",
    "editore", "edizioni", "editori", "libri", "books", "press",
}

_AMBIGUOUS_PUB_HINT_WORDS = {
    "tea", "sei", "sur", "repubblica", "mulino", "storia e letteratura",
}

_ITALIAN_PUBS_BY_LEN = sorted(ITALIAN_PUBLISHERS_PRIORITY.items(), key=lambda kv: -len(kv[0]))

_PUBLISHER_HINT_ENTRIES: List[Tuple[str, Tuple[str, ...], str]] = []
for _pk, _pcanon in _ITALIAN_PUBS_BY_LEN:
    _pf = search_fold(_pk)
    if not _pf or _pf in _AMBIGUOUS_PUB_HINT_WORDS:
        continue
    _ptoks = tuple(_pf.split())
    if _ptoks:
        _PUBLISHER_HINT_ENTRIES.append((_pf, _ptoks, _pcanon))
_PUBLISHER_HINT_ENTRIES.sort(key=lambda item: (-len(item[1]), -len(item[0])))

CD_FORMAT_KEYWORDS = ("cd", "sacd", "hdcd", "dualdisc", "shm-cd", "blu-spec")
VINYL_FORMAT_KEYWORDS = ("vinyl", "lp", '12"', '7"', '10"', "flexi-disc", "shellac")

_ALBUM_SUFFIX_CLEAN_RE = re.compile(
    r"\s*(?:"
    r"\[(?:import|vinyl|lp|2lp|gatefold|limited(?:\s+edition)?)\]"
    r"|[\(\[]\s*(?:"
    r"remaster(?:ed)?(?:\s+\d{4})?|\d{4}\s+remaster(?:ed)?|"
    r"deluxe(?:\s+edition|\s+version)?|expanded(?:\s+edition|\s+version)?|"
    r"special\s+edition|anniversary\s+edition|bonus\s+track\s+version|"
    r"collector'?s\s+edition|standard\s+edition|super\s+deluxe|"
    r"import(?:\s+edition)?|"
    r"limited(?:\s+edition)?(?:\s*[-–]\s*[^\)\]]*)?|"
    r"(?:green|red|blue|white|clear|gold|silver|yellow|orange|purple|pink|marbled|splatter|picture|colou?red)\s+vinyl|"
    r"180\s*g(?:r|rams?)?(?:\s+vinyl)?|gatefold(?:\s+sleeve)?|"
    r"record\s+store\s+day|rsd(?:\s+\d{4})?|"
    r"vinyl|lp|2lp|2x?lp"
    r")\s*[\)\]]"
    r")\s*$",
    re.IGNORECASE,
)


def _clean_album_title_suffixes(raw_title: str) -> str:
    """Rimuove ripetutamente suffissi commerciali/formato ([Import], Limited Edition, Vinyl, Remastered...)."""
    cur = (raw_title or "").strip()
    while cur:
        nxt = _ALBUM_SUFFIX_CLEAN_RE.sub("", cur).strip()
        if nxt == cur or not nxt:
            break
        cur = nxt
    return cur or (raw_title or "").strip()


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
    "paris|supertramp": {
        "artist_sort": "Supertramp",
        "CD": {
            "publishers": ["A&M Records", "A&M", "Universal Music"],
            "years": ["1987", "2002", "1980"],
            "default_publisher": "A&M",
            "default_year": "1987",
        },
        "Vinili": {
            "publishers": ["A&M", "A&M Records", "Universal Music"],
            "years": ["1980", "2015", "2025"],
            "default_publisher": "A&M",
            "default_year": "1980",
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
    "love you live|the rolling stones": {
        "artist_sort": "Rolling Stones, The",
        "Vinili": {
            "publishers": ["Rolling Stones Records", "CBS", "Virgin", "Polydor", "Universal Music"],
            "years": ["1977", "1986", "2011", "2020"],
            "default_publisher": "Rolling Stones Records",
            "default_year": "1977",
        },
        "CD": {
            "publishers": ["Rolling Stones Records", "CBS", "Virgin", "Universal Music"],
            "years": ["1986", "1990", "1998", "2009", "1977"],
            "default_publisher": "Rolling Stones Records",
            "default_year": "1986",
        },
    },
    "love you live|rolling stones": {
        "artist_sort": "Rolling Stones, The",
        "Vinili": {
            "publishers": ["Rolling Stones Records", "CBS", "Virgin", "Polydor", "Universal Music"],
            "years": ["1977", "1986", "2011", "2020"],
            "default_publisher": "Rolling Stones Records",
            "default_year": "1977",
        },
        "CD": {
            "publishers": ["Rolling Stones Records", "CBS", "Virgin", "Universal Music"],
            "years": ["1986", "1990", "1998", "2009", "1977"],
            "default_publisher": "Rolling Stones Records",
            "default_year": "1986",
        },
    },
    "immagine italiana|gianni morandi": {
        "artist_sort": "Morandi, Gianni",
        "Vinili": {
            "publishers": ["RCA", "RCA Italiana", "Sony Music"],
            "years": ["1984"],
            "default_publisher": "RCA",
            "default_year": "1984",
        },
        "CD": {
            "publishers": ["RCA", "Sony Music"],
            "years": ["1984", "1999"],
            "default_publisher": "RCA",
            "default_year": "1984",
        },
    },
    "immagine italiana|morandi": {
        "artist_sort": "Morandi, Gianni",
        "Vinili": {
            "publishers": ["RCA", "RCA Italiana", "Sony Music"],
            "years": ["1984"],
            "default_publisher": "RCA",
            "default_year": "1984",
        },
        "CD": {
            "publishers": ["RCA", "Sony Music"],
            "years": ["1984", "1999"],
            "default_publisher": "RCA",
            "default_year": "1984",
        },
    },
    "la faccia delle donne|stadio": {
        "artist_sort": "Stadio",
        "Vinili": {
            "publishers": ["RCA", "RCA Italiana", "Sony Music"],
            "years": ["1984"],
            "default_publisher": "RCA",
            "default_year": "1984",
        },
        "CD": {
            "publishers": ["RCA", "Sony Music"],
            "years": ["1984", "1998"],
            "default_publisher": "RCA",
            "default_year": "1984",
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

CURATED_MUSIC_SEED = [
    ["Love You Live", "The Rolling Stones", "1977", 9500, "09291c11-4b3d-31a5-bcbf-9b03ce365174"],
    ["Immagine italiana", "Gianni Morandi", "1984", 8200, "f68e1bc2-f420-4e8f-a294-c7feed8c41e9"],
    ["La faccia delle donne", "Stadio", "1984", 8200, "23a0a0eb-e109-35c2-8d6b-3e38815cce6d"],
    ["Il dono", "Renato Zero", "2005", 8200, ""],
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
