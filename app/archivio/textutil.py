"""
Normalizzazione del testo e chiavi di ordinamento bibliografico.

- fold():            minuscolo + senza accenti, per ricerche e confronti
- surname_key():     "Cognome, Nome" per l'ordinamento per autore/artista
- title_sort_key():  titolo senza articolo iniziale (Il, La, The…)
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional
import unicodedata

_PUNCT_RE = re.compile(r"[^\w\s,]", re.UNICODE)
_SPACES_RE = re.compile(r"\s+")
_YEAR_RE = re.compile(r"(1[4-9]\d\d|20\d\d)")


@lru_cache(maxsize=131072)
def fold(text: str) -> str:
    """Minuscolo, senza diacritici, apostrofi tipografici uniformati (risultati in cache: le stringhe sono immutabili)."""
    if not text:
        return ""
    text = text.replace("’", "'").replace("‘", "'").replace("`", "'")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.casefold().strip()


@lru_cache(maxsize=131072)
def search_fold(text: str) -> str:
    """Come fold() ma rimuove la punteggiatura: usata per l'indice di ricerca (risultati in cache)."""
    text = fold(text).replace("'", " ")
    text = _PUNCT_RE.sub(" ", text).replace(",", " ")
    return _SPACES_RE.sub(" ", text).strip()


def tokens(text: str) -> list:
    return search_fold(text).split()


def sort_key_matches_author(sort_key: str, author: str) -> bool:
    """
    True se la chiave "Cognome, Nome" proposta da una fonte esterna è plausibilmente quella dell'autore/artista
    indicato (condivide almeno una parola significativa). Scarta gli abbinamenti sbagliati di cataloghi online
    (es. «Mozart, Wolfgang Amadeus» proposto per «Cesare Cremonini»).
    """
    sk = set(w for w in search_fold(sort_key).split() if len(w) >= 2)
    au = set(w for w in search_fold(author).split() if len(w) >= 2)
    if not sk or not au:
        return True  # niente da confrontare: non scartare
    if sk & au:
        return True
    # tolleranza per piccole varianti ortografiche (es. «Bashō» / «Basho» già normalizzate, refusi di una lettera)
    return any(a[:4] == s_[:4] and len(a) >= 4 and len(s_) >= 4 for a in au for s_ in sk)


def year_of(value) -> str:
    m = _YEAR_RE.search(str(value or ""))
    return m.group(1) if m else ""


# --------------------------------------------------------------------------
# Titoli
# --------------------------------------------------------------------------
_TITLE_ARTICLES = ("il ", "lo ", "la ", "i ", "gli ", "le ", "un ", "uno ", "una ",
                   "the ", "a ", "an ", "el ", "los ", "las ", "der ", "die ", "das ")
_TITLE_ELIDED = ("l'", "un'", "gl'")


_TITLE_VOL_RE = re.compile(
    r"\b(vol(?:ume)?|tomo|parte|libro|n|nr|num)\s+(\d+|[ivxlc]+)\b",
    re.IGNORECASE,
)


def title_sort_key(title: str) -> str:
    t = fold(title).lstrip("\"'«“([¿¡ ")
    for el in _TITLE_ELIDED:
        if t.startswith(el) and len(t) > len(el):
            t = t[len(el):]
            break
    else:
        for art in _TITLE_ARTICLES:
            if t.startswith(art) and len(t) > len(art):
                t = t[len(art):]
                break
    sf = search_fold(t)

    def _repl_vol(m: re.Match) -> str:
        prefix = m.group(1).lower()
        num_val = _parse_vol_number(m.group(2))
        if num_val is not None:
            return f"{prefix} {num_val:06d}"
        return m.group(0)

    return _TITLE_VOL_RE.sub(_repl_vol, sf)


# --------------------------------------------------------------------------
# Cognomi
# --------------------------------------------------------------------------
COMPOUND_PREFIXES = ["von und zu", "van der", "van den", "van de", "van het", "de la", "de las",
                     "de los", "de li", "von der", "de lo", "da la", "della", "dalla", "des"]

SIMPLE_PREFIXES = {
    "de", "del", "della", "delle", "dello", "degli", "dei", "de'", "d'",
    "di", "da", "dal", "dalla", "dalle", "dallo", "dai", "dagli",
    "lo", "la", "li", "le", "van", "von", "vom", "zu", "der", "den", "ten", "ter",
    "du", "des", "dos", "das", "do", "mc", "mac", "o'", "fitz", "san", "santa", "santo", "sant'", "st.",
    "al", "el", "bin", "ibn", "ben",
}

HONORIFICS = {"don", "padre", "fra", "frate", "suor", "suora", "sir", "dame", "lord", "lady",
              "dott.", "dott", "prof.", "prof", "dr.", "dr", "mons.", "papa", "san", "santa", "beato"}

SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "junior", "senior"}

BAND_ARTICLES = {"the", "i", "gli", "le", "il", "la", "lo", "die", "les", "los", "las", "el"}

BAND_WORDS = {
    "band", "orchestra", "orchestre", "quartet", "quartetto", "quintet", "trio", "ensemble", "choir",
    "coro", "brothers", "sisters", "boys", "girls", "club", "project", "experience", "group", "gruppo",
    "crew", "gang", "posse", "family", "singers", "players", "philharmonic", "filarmonica", "symphony",
    "sinfonica", "collective", "society", "company", "machine", "kids", "men", "allstars", "all-stars",
    "jazz", "soccorso", "storie", "tese", "rockets", "stones", "beatles", "doors",
}

KNOWN_GROUPS = {
    "pink floyd", "led zeppelin", "dire straits", "deep purple", "iron maiden", "black sabbath",
    "fleetwood mac", "depeche mode", "pearl jam", "foo fighters", "talking heads", "radiohead",
    "coldplay", "nirvana", "queen", "u2", "ac/dc", "metallica", "guns n' roses", "red hot chili peppers",
    "rem", "r.e.m.", "abba", "genesis", "yes", "toto", "kiss", "eagles", "journey", "muse", "oasis",
    "blur", "pulp", "suede", "travis", "keane", "placebo", "garbage", "portishead", "massive attack",
    "daft punk", "linkin park", "green day", "duran duran", "simple minds", "tears for fears",
    "spandau ballet", "pet shop boys", "erasure", "new order", "joy division", "the cure",
    "king crimson", "jethro tull", "emerson, lake & palmer", "van halen", "def leppard", "bon jovi",
    "maroon 5", "arctic monkeys", "imagine dragons", "twenty one pilots", "gorillaz", "kraftwerk",
    "premiata forneria marconi", "pfm", "banco del mutuo soccorso", "elio e le storie tese",
    "marlene kuntz", "subsonica", "afterhours", "verdena", "baustelle", "litfiba", "matia bazar",
    "nomadi", "pooh", "negramaro", "modà", "måneskin", "maneskin", "articolo 31", "883", "lunapop",
    "lùnapop", "tiromancino", "bluvertigo", "stadio", "area", "goblin", "new trolls", "le orme",
    "almamegretta", "99 posse", "club dogo", "thegiornalisti", "ricchi e poveri", "eiffel 65",
    "righeira", "planet funk", "dik dik", "equipe 84", "camaleonti", "giganti", "formula 3",
    "simon & garfunkel", "crosby, stills, nash & young", "earth, wind & fire", "sly & the family stone",
    "steely dan", "thin lizzy", "status quo", "uriah heep", "motörhead", "motorhead", "slayer",
    "megadeth", "anthrax", "pantera", "system of a down", "rage against the machine", "soundgarden",
    "alice in chains", "stone temple pilots", "smashing pumpkins", "sonic youth", "pixies",
    "beastie boys", "run-dmc", "public enemy", "wu-tang clan", "outkast", "black eyed peas",
    "destiny's child", "spice girls", "take that", "backstreet boys", "nsync", "one direction",
    "bee gees", "boney m.", "village people", "chic", "air", "justice", "moderat",
}

GIVEN_NAMES = {
    # Italiani
    "giovanni", "mario", "luigi", "paolo", "francesco", "alessandro", "giuseppe", "andrea", "antonio",
    "marco", "roberto", "carlo", "stefano", "angelo", "enrico", "davide", "luca", "matteo", "simone",
    "federico", "claudio", "massimo", "giorgio", "alberto", "michele", "salvatore", "daniele", "pietro",
    "vincenzo", "giancarlo", "fabrizio", "filippo", "gianni", "edoardo", "domenico", "lorenzo", "riccardo",
    "maria", "anna", "laura", "elena", "chiara", "giulia", "sara", "francesca", "silvia", "monica",
    "simona", "paola", "cristina", "alessandra", "roberta", "alfio", "alfredo", "cesare", "dacia", "diego",
    "erri", "italo", "primo", "umberto", "corrado", "stefania", "gabriele", "silvio", "valentina",
    "edmondo", "maurizio", "pino", "lucio", "vasco", "zucchero", "fabio", "franco", "gino", "nino",
    "renato", "renzo", "sergio", "bruno", "aldo", "carla", "elsa", "natalia", "oriana", "grazia",
    "giacomo", "ugo", "dante", "gabriella", "niccolo", "niccolò", "nicola", "tiziano", "ivano", "eros",
    "biagio", "gigi", "max", "nek", "irene", "fiorella", "ornella", "loredana", "gianna", "mia", "rino",
    "adriano", "lucia", "beppe", "pier", "pierpaolo", "pier paolo", "giosue", "giosuè", "leonardo",
    "ludovico", "ennio", "enzo", "tullio", "raffaella", "patty", "emma", "elisa", "giorgia", "carmen",
    "samuele", "vinicio", "cesare", "tommaso", "marracash", "mahmood", "achille", "ermal", "arisa",
    "andrea", "ilaria", "marta", "beatrice", "veronica", "rita", "teresa", "rosa", "angela", "lina",
    "michela", "donatella", "susanna", "margherita", "camilla", "caterina", "federica", "elisabetta",
    "giuliano", "giampaolo", "gianluca", "gianfranco", "gianmarco", "pierluigi", "piero", "raffaele",
    "rocco", "sandro", "sebastiano", "valerio", "vittorio", "walter", "arturo", "attilio", "dario",
    "emilio", "ettore", "giulio", "guido", "lino", "manuel", "mirko", "ottavio", "rodolfo", "romano",
    "ruggero", "settimio", "tito", "carlo emilio", "elio", "aldo", "alda", "amelia", "goliarda",
    # Internazionali
    "agatha", "albert", "alex", "alexander", "alexandre", "alice", "arthur", "charles", "anne", "john",
    "paul", "george", "ringo", "david", "michael", "bruce", "bob", "eric", "freddie", "elvis", "roger",
    "richard", "robert", "james", "william", "thomas", "peter", "mark", "stephen", "steven", "kevin",
    "brian", "jimi", "jimmy", "jim", "joe", "joni", "neil", "nick", "frank", "billy", "bill", "ray",
    "stevie", "marvin", "aretha", "whitney", "mariah", "madonna", "prince", "kate", "taylor", "ed",
    "adele", "amy", "lana", "billie", "harry", "louis", "miles", "herbie", "chet", "ella", "nina",
    "sarah", "jane", "emily", "charlotte", "virginia", "ernest", "f.", "scott", "mary", "edgar", "allan",
    "oscar", "leo", "lev", "fyodor", "fedor", "anton", "gabriel", "isabel", "jorge", "julio", "mario",
    "pablo", "federico", "miguel", "carlos", "jose", "josé", "juan", "luis", "haruki", "kazuo", "umberto",
    "hermann", "thomas", "franz", "johann", "wolfgang", "ludwig", "friedrich", "karl", "victor", "honore",
    "honoré", "gustave", "emile", "émile", "jules", "marcel", "albert", "simone", "jean", "jean-paul",
    "milan", "gunter", "günter", "patrick", "salman", "zadie", "toni", "maya", "margaret", "doris",
    "sylvia", "ursula", "philip", "isaac", "ray", "terry", "neil", "douglas", "j.", "j.r.r.", "c.s.",
    "dan", "ken", "lee", "tom", "tim", "jack", "chris", "christopher", "daniel", "matthew", "andrew",
    "anthony", "joseph", "donald", "ronald", "edward", "jason", "jeff", "jeffrey", "ryan", "gary",
    "nicholas", "eric", "jonathan", "justin", "brandon", "benjamin", "samuel", "gregory", "frank",
    "patricia", "jennifer", "linda", "elizabeth", "barbara", "susan", "jessica", "karen", "nancy",
    "lisa", "betty", "dorothy", "sandra", "ashley", "kimberly", "donna", "carol", "michelle", "amanda",
    "melissa", "deborah", "stephanie", "rebecca", "laura", "helen", "sharon", "cynthia", "kathleen",
    "ruth", "anna", "shirley", "angela", "brenda", "pamela", "nicole", "katherine", "samantha",
    "christine", "rachel", "carolyn", "janet", "catherine", "heather", "diane", "julie", "joyce",
    "victoria", "kelly", "christina", "joan", "evelyn", "lauren", "judith", "olivia", "martha",
    "cheryl", "megan", "andrea", "hannah", "jacqueline", "ann", "gloria", "jean", "kathryn", "alice",
    "teresa", "doris", "sara", "janice", "julia", "marie", "madison", "grace", "judy", "theresa",
    "beverly", "denise", "marilyn", "amber", "danielle", "abigail", "brittany", "rose", "diana",
    "natalie", "sophia", "alexis", "lori", "kayla", "jane", "kendrick", "kanye", "drake", "rihanna",
    "beyonce", "beyoncé", "shakira", "celine", "céline", "norah", "diana", "tina", "janis", "patti",
    "lou", "iggy", "tom", "leonard", "johnny", "willie", "dolly", "garth", "kenny", "lionel", "phil",
    "sting", "elton", "rod", "van", "cat", "art", "gil", "otis", "sam", "ben", "bonnie", "carole",
    "cyndi", "annie", "sinead", "sinéad", "bjork", "björk", "dua", "ariana", "justin", "bruno", "post",
}

COMMON_FIRST_WORDS_BANDS = {
    "pink", "led", "deep", "dire", "iron", "black", "daft", "green", "pearl", "red", "blue", "white",
    "simple", "massive", "arctic", "foo", "linkin", "imagine", "twenty", "king", "dead", "grateful",
    "rolling", "sex", "velvet", "talking", "stone", "smashing", "sonic", "public", "beastie", "new",
    "joy", "pet", "tears", "duran", "spandau", "def", "thin", "status", "system", "rage", "alice",
    "steely", "jethro", "fleetwood", "depeche", "nine", "modest", "vampire", "fleet", "tame", "kings",
    "franz", "bloc", "mumford", "florence", "marlene", "planet", "club", "almamegretta",
}


def _join(cognome, nome, appellativo=""):
    res = f"{cognome}, {nome}" if nome else cognome
    return f"{res} ({appellativo})" if appellativo else res


def _first_person(text: str, is_music: bool) -> str:
    """Restituisce il primo autore di un elenco (es. 'A. Calvisi, G. Caserza' → 'A. Calvisi')."""
    t = text.strip()
    if ";" in t:
        t = t.split(";")[0].strip()
    if not is_music:
        for sep in (" e ", " & ", " and ", " / ", " con ", " with "):
            low = t.lower()
            if sep in low:
                idx = low.index(sep)
                t = t[:idx].strip()
                break
    return t


def surname_key(author: str, is_music: bool | str = False) -> str:
    """
    Chiave "Cognome, Nome" ad alta precisione (offline).

    `is_music` può essere un booleano oppure il nome della categoria ("Libri", "CD", "Vinili").
    Gestisce prefissi (De, Del, Di, Da, D', van, von, de la, van der…), elenchi di
    autori, iniziali, appellativi (don, padre…), suffissi (Jr.), inversioni già presenti
    ("Eco, Umberto" o "De Sanctis Antonio"), mononimi (Sting), gruppi musicali
    ("The Beatles" → "Beatles, The", "Pink Floyd" resta "Pink Floyd").
    """
    if isinstance(is_music, str):
        is_music = is_music.strip().lower() in ("cd", "vinili", "vinile", "vinyl", "music")
    a = _SPACES_RE.sub(" ", (author or "").strip())
    if not a:
        return ""
    low = fold(a)

    if is_music:
        if low in KNOWN_GROUPS or low.replace("the ", "", 1) in KNOWN_GROUPS:
            words = a.split()
            if words[0].lower() in BAND_ARTICLES and len(words) > 1:
                return f"{' '.join(words[1:])}, {words[0]}"
            return a
        if any(sep in f" {low} " for sep in (" & ", " + ", " feat ", " feat. ", " ft. ", " vs ", " vs. ", " x ")):
            return a

    # Elenco "Cognome, Nome" già invertito oppure elenco di autori separati da virgola
    if "," in a:
        parts = [p.strip() for p in a.split(",") if p.strip()]
        if len(parts) >= 2:
            first_words = parts[0].split()
            looks_inverted = (
                "." not in parts[0]
                and (len(first_words) == 1 or first_words[0].lower() in SIMPLE_PREFIXES)
                and len(parts[1].split()) <= 3
            )
            if looks_inverted and len(parts) == 2:
                return f"{parts[0]}, {parts[1]}"
            a = parts[0]
        else:
            a = parts[0] if parts else a

    a = _first_person(a, is_music)
    words = a.split()
    if not words:
        return ""
    if len(words) == 1:
        return words[0]

    # Gruppi musicali
    if is_music:
        if words[0].lower() in BAND_ARTICLES:
            return f"{' '.join(words[1:])}, {words[0]}"
        if any(w.lower().strip(".,") in BAND_WORDS for w in words):
            return a
        if len(words) >= 2 and words[0].lower() in COMMON_FIRST_WORDS_BANDS and words[0].lower() not in GIVEN_NAMES:
            return a
        if len(words) >= 4:
            return a

    appellativo = ""
    if words[0].lower() in HONORIFICS and len(words) > 2:
        appellativo = words[0]
        words = words[1:]

    suffix = ""
    if len(words) > 2 and words[-1].lower() in SUFFIXES:
        suffix = words[-1]
        words = words[:-1]

    def finish(cognome, nome):
        if suffix:
            cognome = f"{cognome} {suffix}"
        return _join(cognome, nome, appellativo)

    # Prefissi composti (van der, de la…)
    for pref in COMPOUND_PREFIXES:
        n = len(pref.split())
        if len(words) > n + 0 and len(words) >= n + 1:
            cand = " ".join(w.lower() for w in words[-n - 1:-1])
            if cand == pref and len(words) > n + 1:
                return finish(" ".join(words[-n - 1:]), " ".join(words[:-n - 1]))

    # Prefissi semplici (De Sanctis, Da Vinci, van Beethoven, D' Annunzio)
    if len(words) >= 3:
        w0 = words[0].lower()
        w_last = words[-1].lower()
        # Caso invertito con particella iniziale: "De Sanctis Antonio" -> "De Sanctis, Antonio"
        if (w0 in SIMPLE_PREFIXES or w0.endswith(("'", "’"))) and w_last in GIVEN_NAMES:
            return finish(" ".join(words[:-1]), words[-1])
        pen = words[-2].lower()
        if pen in SIMPLE_PREFIXES or pen.endswith("'") or pen.endswith("’"):
            # Caso "Giuseppe Tomasi di Lampedusa" -> "Tomasi di Lampedusa, Giuseppe"
            if (
                len(words) >= 4
                and words[0].lower() in GIVEN_NAMES
                and words[-3].lower() not in GIVEN_NAMES
                and not words[-3].endswith(".")
            ):
                return finish(" ".join(words[-3:]), " ".join(words[:-3]))
            return finish(" ".join(words[-2:]), " ".join(words[:-2]))
        if len(words) >= 4 and words[-3].lower() in SIMPLE_PREFIXES:
            return finish(" ".join(words[-3:]), " ".join(words[:-3]))
        # Doppi cognomi ispanici/internazionali celebri (es. "Gabriel García Márquez", "Mario Vargas Llosa", "Arthur Conan Doyle")
        if len(words) == 3 and words[0].lower() in GIVEN_NAMES and fold(words[1]) in {
            "garcia", "vargas", "conan", "perez", "sanchez", "gomez", "lopez",
            "fernandez", "rodriguez", "martinez", "gonzalez", "diaz", "ramirez",
            "ruiz", "alvarez", "jimenez", "moreno", "munoz", "ortega", "delgado",
            "castro", "ortiz", "marquez", "herrera", "cabrera", "flores", "vega",
            "fuentes", "aguilar", "ibanez", "benitez", "rojas", "llosa", "bioy",
        }:
            return finish(" ".join(words[1:]), words[0])

    # Due parole: rileva l'ordine "Cognome Nome" (es. "Allevi Giovanni")
    if len(words) == 2:
        w1, w2 = words[0].lower(), words[1].lower()
        if w1.startswith(("d'", "d’", "de'", "de’")) and len(w1) > 2:
            return finish(words[0], words[1])
        if w1 in SIMPLE_PREFIXES:
            return finish(" ".join(words), "")
        w1_name = w1 in GIVEN_NAMES or (len(w1) <= 3 and w1.endswith("."))
        w2_name = w2 in GIVEN_NAMES or (len(w2) <= 3 and w2.endswith("."))
        if w2_name and not w1_name:
            return finish(words[0], words[1])
        return finish(words[1], words[0])

    return finish(words[-1], " ".join(words[:-1]))


_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_ROMAN_RE = re.compile(r"^[IVXLC]+$", re.IGNORECASE)


def _parse_vol_number(token: str) -> Optional[int]:
    t = (token or "").strip()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    if _ROMAN_RE.match(t):
        u = t.upper()
        total = 0
        prev = 0
        for ch in reversed(u):
            val = _ROMAN_VALUES.get(ch, 0)
            if val < prev:
                total -= val
            else:
                total += val
                prev = val
        if 1 <= total <= 200:
            return total
    return None


_COLL_VOL_RE = re.compile(
    r"^(?P<base>.+?)"
    r"(?:[\s,\-–—:/]+(?:vol(?:ume)?\.?|n\.?|nr\.?|num\.?|tomo|#)\s*(?P<num1>\d+|[IVXLC]+)"
    r"|\s+#(?P<num2>\d+|[IVXLC]+)"
    r"|\s+(?P<num3>\d+))\s*$",
    re.IGNORECASE,
)


def collection_sort_parts(collection: str) -> tuple[str, int, str]:
    """
    Estrae da una stringa di Collana / Collezione:
      1. `base_key`: chiave normalizzata del nome della collana (senza eventuale numero di volume finale)
      2. `vol_num`: numero di volume intero (arabo o romano: es. 1, 2, I, II, 10...) oppure 999999 se assente
      3. `base_display`: nome pulito della collana (per raggruppamento e menu a tendina)

    Esempi:
      - "I Meridiani"            -> ("i meridiani", 999999, "I Meridiani")
      - "Grandi Classici #1"     -> ("grandi classici", 1, "Grandi Classici")
      - "Grandi Classici Vol. II"-> ("grandi classici", 2, "Grandi Classici")
    """
    raw = _SPACES_RE.sub(" ", (collection or "").strip())
    if not raw:
        return ("", 999999, "")
    m = _COLL_VOL_RE.match(raw)
    if m:
        base_disp = m.group("base").strip(" -–—:,;/#")
        num_str = m.group("num1") or m.group("num2") or m.group("num3") or ""
        parsed_num = _parse_vol_number(num_str)
        if base_disp and parsed_num is not None:
            return (search_fold(base_disp), parsed_num, base_disp)
    return (search_fold(raw), 999999, raw)
