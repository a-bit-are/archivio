#!/usr/bin/env python3
"""
Costruisce l'indice visivo delle copertine (`app/archivio/data/seed_covers.json`)
per il riconoscimento da fotocamera di album privi di testo o con grafica iconica
(es. Pink Floyd — Atom Heart Mother, Wish You Were Here, Dark Side of the Moon, Animals, ecc.).

Per ciascun album:
1. Scarica la copertina ufficiale (iTunes Search API / Artist Lookup, con fallback Cover Art Archive).
2. Calcola il vettore semantico a 768 dimensioni tramite Apple Vision
   (`VNGenerateImageFeaturePrintRequest` con `usesCPUOnly = true`).
3. Quantizza il vettore in `Int16` little-endian codificato in Base64 (`1536 byte` per copertina).
4. Calcola e salva anche il vettore multipiattaforma a `256` dimensioni (`xp_b64`, `Int8` Base64 da `256 byte`)
   usato da Windows, Linux e fallback macOS.

Uso (richiede macOS e connessione di rete):
    python3 tools/build_cover_index.py
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
DATA_DIR = os.path.join(APP_DIR, "archivio", "data")
SEED_MUSIC_PATH = os.path.join(DATA_DIR, "seed_music.json")
OUT_PATH = os.path.join(DATA_DIR, "seed_covers.json")
CACHE_IMG_DIR = os.path.join(tempfile.gettempdir(), "archivio_cover_artwork_cache")

UA = "Archivio/1.0.0 (OpenSource; https://github.com/a-bit-are/archivio)"

# Discografia Pink Floyd + grandi classici (specialmente copertine senza testo o con loghi iconici)
PRIORITY_ALBUMS: List[Tuple[str, str, str, str]] = [
    # Pink Floyd (tutti gli album in studio e live principali)
    ("Atom Heart Mother", "Pink Floyd", "1970", ""),
    ("The Dark Side of the Moon", "Pink Floyd", "1973", "f5093c06-23e3-404f-aeaa-40f72885ee3a"),
    ("Wish You Were Here", "Pink Floyd", "1975", "9386b868-f265-3b7f-a226-31f7216228e2"),
    ("Animals", "Pink Floyd", "1977", "202f1b43-095b-3a14-a90b-4445b51695b1"),
    ("The Wall", "Pink Floyd", "1979", "f20263c7-57b3-3030-9549-838b53238e53"),
    ("Meddle", "Pink Floyd", "1971", "9f10b724-f815-315d-903a-730d8ba59d39"),
    ("The Piper at the Gates of Dawn", "Pink Floyd", "1967", "49cd8f4e-6939-3029-95f8-e51911303f14"),
    ("A Saucerful of Secrets", "Pink Floyd", "1968", ""),
    ("Ummagumma", "Pink Floyd", "1969", ""),
    ("Obscured by Clouds", "Pink Floyd", "1972", ""),
    ("More", "Pink Floyd", "1969", ""),
    ("The Final Cut", "Pink Floyd", "1983", ""),
    ("A Momentary Lapse of Reason", "Pink Floyd", "1987", "4940b8aa-898b-3e60-9992-94e073bab6f6"),
    ("The Division Bell", "Pink Floyd", "1994", "5b4b1f84-b610-332b-9f1f-8f4d43a81e7f"),
    ("The Endless River", "Pink Floyd", "2014", ""),
    ("Pulse", "Pink Floyd", "1995", ""),
    ("Delicate Sound of Thunder", "Pink Floyd", "1988", ""),
    ("Relics", "Pink Floyd", "1971", ""),
    # Classici internazionali celebri (molti privi di testo frontale o con copertine iconiche)
    ("Abbey Road", "The Beatles", "1969", "9162580e-5df4-32de-80cc-f45a8d8a9b1d"),
    ("Sgt. Pepper’s Lonely Hearts Club Band", "The Beatles", "1967", ""),
    ("Revolver", "The Beatles", "1966", ""),
    ("Led Zeppelin IV", "Led Zeppelin", "1971", ""),
    ("Physical Graffiti", "Led Zeppelin", "1975", ""),
    ("Houses of the Holy", "Led Zeppelin", "1973", ""),
    ("Unknown Pleasures", "Joy Division", "1979", ""),
    ("Closer", "Joy Division", "1980", ""),
    ("In the Court of the Crimson King", "King Crimson", "1969", ""),
    ("The Velvet Underground & Nico", "The Velvet Underground", "1967", ""),
    ("Sticky Fingers", "The Rolling Stones", "1971", ""),
    ("Let It Bleed", "The Rolling Stones", "1969", ""),
    ("Nevermind", "Nirvana", "1991", ""),
    ("In Utero", "Nirvana", "1993", ""),
    ("OK Computer", "Radiohead", "1997", ""),
    ("Kid A", "Radiohead", "2000", ""),
    ("In Rainbows", "Radiohead", "2007", ""),
    ("Random Access Memories", "Daft Punk", "2013", ""),
    ("Discovery", "Daft Punk", "2001", ""),
    ("Kind of Blue", "Miles Davis", "1959", ""),
    ("Bitches Brew", "Miles Davis", "1970", ""),
    ("A Love Supreme", "John Coltrane", "1965", ""),
    ("Selling England by the Pound", "Genesis", "1973", ""),
    ("Foxtrot", "Genesis", "1972", ""),
    ("Fragile", "Yes", "1971", ""),
    ("Close to the Edge", "Yes", "1972", ""),
    ("Machine Head", "Deep Purple", "1972", ""),
    ("Deep Purple in Rock", "Deep Purple", "1970", ""),
    ("Tubular Bells", "Mike Oldfield", "1973", ""),
    ("Oxygène", "Jean-Michel Jarre", "1976", ""),
    ("Violator", "Depeche Mode", "1990", ""),
    ("Power, Corruption & Lies", "New Order", "1983", ""),
    ("Disintegration", "The Cure", "1989", ""),
    ("Remain in Light", "Talking Heads", "1980", ""),
    ("Mezzanine", "Massive Attack", "1998", ""),
    ("Dummy", "Portishead", "1994", ""),
    ("Abraxas", "Santana", "1970", ""),
    ("Electric Ladyland", "Jimi Hendrix", "1968", ""),
    ("Are You Experienced", "Jimi Hendrix", "1967", ""),
    ("Breakfast in America", "Supertramp", "1979", ""),
    ("Hotel California", "Eagles", "1976", ""),
    ("Rumours", "Fleetwood Mac", "1977", ""),
    ("What's Going On", "Marvin Gaye", "1971", ""),
    ("Songs in the Key of Life", "Stevie Wonder", "1976", ""),
    ("Love over Gold", "Dire Straits", "1982", ""),
    ("Brothers in Arms", "Dire Straits", "1985", ""),
    ("Blackstar", "David Bowie", "2016", ""),
    ("Heroes", "David Bowie", "1977", ""),
    ("A Night at the Opera", "Queen", "1975", ""),
    ("News of the World", "Queen", "1977", ""),
    ("Currents", "Tame Impala", "2015", ""),
    ("Demon Days", "Gorillaz", "2005", ""),
    ("AM", "Arctic Monkeys", "2013", ""),
    # Classici italiani
    ("Crêuza de mä", "Fabrizio De André", "1984", "c7a6e068-1404-3925-801a-89f788904a6c"),
    ("Anime salve", "Fabrizio De André", "1996", ""),
    ("La buona novella", "Fabrizio De André", "1970", ""),
    ("Non al denaro non all'amore né al cielo", "Fabrizio De André", "1971", ""),
    ("Storia di un impiegato", "Fabrizio De André", "1973", ""),
    ("La voce del padrone", "Franco Battiato", "1981", ""),
    ("L'era del cinghiale bianco", "Franco Battiato", "1979", ""),
    ("Storia di un minuto", "Premiata Forneria Marconi", "1972", ""),
    ("Per un amico", "Premiata Forneria Marconi", "1972", ""),
    ("Darwin!", "Banco del Mutuo Soccorso", "1972", ""),
    ("Felona e Sorona", "Le Orme", "1973", ""),
    ("Arbeit macht frei", "Area", "1973", ""),
    ("Profondo rosso", "Goblin", "1975", ""),
    ("Come è profondo il mare", "Lucio Dalla", "1977", ""),
    ("Rimmel", "Francesco De Gregori", "1975", ""),
    ("Nero a metà", "Pino Daniele", "1980", ""),
    ("Anima latina", "Lucio Battisti", "1974", ""),
    ("Il mio canto libero", "Lucio Battisti", "1972", ""),
    # Album da sessione reale e classici anni 70-2020
    ("The Manhattan Transfer", "The Manhattan Transfer", "1975", ""),
    ("Coming Out", "The Manhattan Transfer", "1976", ""),
    ("La boum", "Vladimir Cosma", "1980", ""),
    ("Paris", "Supertramp", "1980", ""),
    ("Fuse", "Everything but the Girl", "2023", ""),
    ("A Day at the Races", "Queen", "1976", ""),
    ("Appetite for Destruction", "Guns N' Roses", "1987", ""),
    ("Amnesiac", "Radiohead", "2001", ""),
    ("Buon Natale, Mondo!", "Angelo Di Mario", "2010", ""),
]


def _strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm_text(s: str) -> str:
    t = _strip_accents(s).lower()
    t = t.replace("’", "'").replace("‘", "'").replace("`", "'").replace("&", " and ")
    t = re.sub(r"[^a-z0-9']+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


_EDITION_PARENS_RE = re.compile(
    r"\s*[\(\[]"
    r"(?:[^)\]]*(?:remaster(?:ed)?|deluxe|anniversary|edition|version|mix|remix|"
    r"soundtrack|original|expanded|mono|stereo|live|180g|\d{4}))"
    r"[\)\]]",
    re.IGNORECASE,
)


def _clean_album_title(s: str) -> str:
    prev = s or ""
    for _ in range(3):
        nxt = _EDITION_PARENS_RE.sub("", prev).strip()
        if nxt == prev:
            break
        prev = nxt
    prev = re.sub(r"\s*-\s*(?:EP|Single|Remastered.*)$", "", prev, flags=re.IGNORECASE).strip()
    return _norm_text(prev)


def _norm_artist(s: str) -> str:
    t = _norm_text(s)
    if t.startswith("the "):
        t = t[4:]
    return t


def _http_get_json(url: str, timeout: int = 12, retries: int = 4) -> Optional[dict]:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503):
                time.sleep(2.5 * (attempt + 1))
                continue
            return None
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return None


def _download_file(url: str, dest_path: str, timeout: int = 15) -> bool:
    if os.path.isfile(dest_path) and os.path.getsize(dest_path) > 1000:
        return True
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) > 1000:
                with open(dest_path, "wb") as f:
                    f.write(data)
                return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            time.sleep(1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    return False


def _score_itunes_match(
    row: dict, target_title: str, target_artist: str
) -> Tuple[int, str]:
    """Restituisce (punteggio, artwork_url_600) oppure (-1, '') se non è l'album giusto."""
    if row.get("wrapperType") and row.get("wrapperType") != "collection":
        return -1, ""
    art_100 = str(row.get("artworkUrl100") or "").strip()
    if not art_100:
        return -1, ""

    col_name = str(row.get("collectionName") or "").strip()
    art_name = str(row.get("artistName") or "").strip()
    if not col_name or not art_name:
        return -1, ""

    na_target = _norm_artist(target_artist)
    na_row = _norm_artist(art_name)
    if na_target != na_row:
        # Tollera collaborazioni o suffissi minori (es. "Mina & Adriano Celentano" / "The Jimi Hendrix Experience")
        toks_t = set(na_target.split())
        toks_r = set(na_row.split())
        if not (toks_t and (toks_t.issubset(toks_r) or toks_r.issubset(toks_t))):
            return -1, ""

    nt_target_raw = _norm_text(target_title)
    nt_target = _clean_album_title(target_title)
    nt_row_raw = _norm_text(col_name)
    nt_row = _clean_album_title(col_name)

    # Scarta singoli / compilation / cofanetti estranei
    low_col = col_name.lower()
    if any(bad in low_col for bad in (" - single", "karaoke", "tribute to", "in the style of", "box set", "early years", "later years", "documentary")):
        if not any(bad in target_title.lower() for bad in ("single", "box", "early years", "later years")):
            return -1, ""

    score = 0
    if nt_row_raw == nt_target_raw:
        score = 100
    elif nt_row == nt_target and nt_target:
        score = 88
    else:
        return -1, ""

    # Penalizza lievemente varianti live/remix/anniversary se il target non lo richiede
    if "live" in low_col and "live" not in target_title.lower() and nt_target != "pulse":
        score -= 12
    if "remix" in low_col and "remix" not in target_title.lower():
        score -= 10
    if "anniversary" in low_col or "deluxe" in low_col:
        score -= 6
    if " - ep" in low_col and "ep" not in target_title.lower():
        score -= 15

    art_600 = re.sub(r"/\d+x\d+bb\.", "/600x600bb.", art_100)
    return score, art_600


def _score_deezer_match(row: dict, target_title: str, target_artist: str) -> Tuple[int, str]:
    """Valuta un risultato dell'API pubblica Deezer (`/search/album`) e restituisce (score, cover_big_url)."""
    cover_url = str(row.get("cover_big") or row.get("cover_xl") or row.get("cover_medium") or "").strip()
    if not cover_url:
        return -1, ""
    col_name = str(row.get("title") or "").strip()
    art_obj = row.get("artist") if isinstance(row.get("artist"), dict) else {}
    art_name = str(art_obj.get("name") or "").strip()
    if not col_name or not art_name:
        return -1, ""
    na_target = _norm_artist(target_artist)
    na_row = _norm_artist(art_name)
    if na_target != na_row:
        toks_t = set(na_target.split())
        toks_r = set(na_row.split())
        if not (toks_t and (toks_t.issubset(toks_r) or toks_r.issubset(toks_t))):
            return -1, ""
    nt_target = _clean_album_title(target_title)
    nt_row_raw = _norm_text(col_name)
    nt_row_clean = _clean_album_title(col_name)
    score = -1
    if nt_row_raw == nt_target:
        score = 100
    elif nt_row_clean == nt_target:
        score = 90
    elif nt_target and (nt_row_clean.startswith(nt_target + " ") or nt_row_clean.endswith(" " + nt_target)):
        score = 70
    elif nt_target and len(nt_target) >= 6 and nt_target in nt_row_clean:
        score = 50
    if score < 0:
        return -1, ""
    low_col = col_name.lower()
    if "live" in low_col and "live" not in target_title.lower() and nt_target not in ("pulse", "paris"):
        score -= 12
    if "remix" in low_col and "remix" not in target_title.lower():
        score -= 10
    if "deluxe" in low_col or "anniversary" in low_col:
        score -= 6
    return score, cover_url


def _collect_target_albums(max_total: int = 1280) -> List[Tuple[str, str, str, str]]:
    """Raccoglie gli album da indicizzare evitando duplicati."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    sys.path.insert(0, APP_DIR)
    import build_seed_index as bsi  # type: ignore
    from archivio.catalog import CURATED_EDITION_PROFILES  # type: ignore

    seen = set()
    out: List[Tuple[str, str, str, str]] = []

    def _add(title: str, artist: str, year: str, mbid: str) -> None:
        key = (_clean_album_title(title) or _norm_text(title), _norm_artist(artist))
        if not key[0] or not key[1] or key in seen:
            return
        seen.add(key)
        out.append((title.strip(), artist.strip(), str(year or "").strip(), str(mbid or "").strip()))

    # 1. Lista prioritaria (tutti i Pink Floyd + vinili iconici senza testo)
    for t, a, y, m in PRIORITY_ALBUMS:
        _add(t, a, y, m)

    # 2. Tutti i profili musicali curati di catalog.py
    for key_str, prof in CURATED_EDITION_PROFILES.items():
        if "Vinili" not in prof and "CD" not in prof:
            continue
        if "|" not in key_str:
            continue
        t_part, a_part = key_str.split("|", 1)
        years = (prof.get("Vinili") or prof.get("CD") or {}).get("years") or []
        _add(t_part.title(), prof.get("artist_sort") or a_part.title(), years[0] if years else "", "")

    # 3. Tutti gli album curati di build_seed_index.py
    for row in bsi.CURATED_ALBUMS:
        mbid = row[4] if len(row) > 4 and not str(row[4]).startswith(("8224c8c9", "9224c8c9")) else ""
        _add(row[0], row[1], row[2], mbid)

    # 4. Album più popolari da seed_music.json (senza soglia stretta a 260, fino a max_total)
    if os.path.isfile(SEED_MUSIC_PATH):
        with open(SEED_MUSIC_PATH, "r", encoding="utf-8") as f:
            seed_items = json.load(f).get("items") or []
        for row in seed_items:
            if len(out) >= max_total:
                break
            if len(row) < 4:
                continue
            title, artist, yr, _pop = row[0], row[1], row[2], int(row[3] or 0)
            mbid = row[4] if len(row) > 4 else ""
            _add(title, artist, yr, mbid)

    return out[:max_total]


def _fetch_artwork_urls(
    albums: List[Tuple[str, str, str, str]],
    skip_indices: Optional[set] = None,
) -> Dict[int, str]:
    """
    Risolve in parallelo gli URL delle copertine usando:
    1. Deezer Search API (veloce e ad alta concorrenza)
    2. iTunes artist/album lookup come integrazione/fallback
    3. Cover Art Archive tramite MBID
    """
    skip = skip_indices or set()
    urls: Dict[int, str] = {}
    todo = [idx for idx in range(len(albums)) if idx not in skip]
    print(f"== Ricerca URL copertine (Deezer/iTunes/CAA) per {len(todo)} nuovi album (già in cache: {len(skip)}) ==", flush=True)

    def _resolve_one(idx: int) -> Tuple[int, str]:
        title, artist, _yr, mbid = albums[idx]
        best_url = ""
        # 1) Deezer API (generous rate limit, direct 500x500 JPG)
        q_dz = urllib.parse.quote(f"{artist} {title}")
        dz_data = _http_get_json(f"https://api.deezer.com/search/album?q={q_dz}&limit=8", timeout=10, retries=3)
        if dz_data and isinstance(dz_data.get("data"), list):
            best_sc = -1
            for row in dz_data["data"]:
                sc, cu = _score_deezer_match(row, title, artist)
                if sc > best_sc:
                    best_sc = sc
                    best_url = cu
        # 2) Cover Art Archive (se abbiamo un MBID reale)
        if not best_url and mbid and len(mbid) == 36:
            best_url = f"https://coverartarchive.org/release-group/{mbid}/front-500"
        return idx, best_url

    with ThreadPoolExecutor(max_workers=10) as pool:
        futs = [pool.submit(_resolve_one, idx) for idx in todo]
        done_cnt = 0
        for fut in as_completed(futs):
            done_cnt += 1
            idx, u = fut.result()
            if u:
                urls[idx] = u
            if done_cnt % 100 == 0 or done_cnt == len(todo):
                print(f"  Deezer/CAA risolti: {done_cnt}/{len(todo)} (trovati {len(urls)})", flush=True)

    # 3) Fallback iTunes sequenziale gentile per eventuali album rimasti senza URL (es. priority/italiani)
    missing = [idx for idx in todo if idx not in urls]
    if missing:
        print(f"== Fallback iTunes per {len(missing)} album senza URL Deezer/CAA ==", flush=True)
        for n_m, idx in enumerate(missing[:180], 1):
            title, artist, _yr, _mbid = albums[idx]
            q = urllib.parse.quote(f"{artist} {title}")
            d = _http_get_json(f"https://itunes.apple.com/search?term={q}&entity=album&limit=10", timeout=10, retries=2)
            time.sleep(0.18)
            best_sc = -1
            best_url = ""
            if d:
                for row in d.get("results", []):
                    sc, art_url = _score_itunes_match(row, title, artist)
                    if sc > best_sc:
                        best_sc = sc
                        best_url = art_url
            if best_url:
                urls[idx] = best_url
            if n_m % 30 == 0:
                print(f"  iTunes fallback: {n_m}/{min(len(missing), 180)}", flush=True)

    return urls


def _compute_featureprints_batch(image_paths: List[str]) -> Dict[str, str]:
    """
    Compila ed esegue un piccolo helper Swift per calcolare in batch i vettori
    `VNGenerateImageFeaturePrintRequest` (`usesCPUOnly = true`) quantizzati in Int16 LE Base64.
    """
    swift_code = r"""
import Foundation
import Vision
import AppKit

let ctx = CIContext(options: nil)
var out: [String: String] = [:]

while let line = readLine() {
    let path = line.trimmingCharacters(in: .whitespacesAndNewlines)
    if path.isEmpty { continue }
    autoreleasepool {
        let url = URL(fileURLWithPath: path)
        guard let ci = CIImage(contentsOf: url, options: [.applyOrientationProperty: true]),
              let cg = ctx.createCGImage(ci, from: ci.extent) else {
            return
        }
        let req = VNGenerateImageFeaturePrintRequest()
        if #available(macOS 14.0, *) {
            if let stages = try? req.supportedComputeStageDevices {
                for (stage, devs) in stages {
                    for dev in devs {
                        if case .cpu = dev {
                            req.setComputeDevice(dev, for: stage)
                            break
                        }
                    }
                }
            }
        } else {
            req.usesCPUOnly = true
        }
        let handler = VNImageRequestHandler(cgImage: cg, options: [:])
        do {
            try handler.perform([req])
        } catch {
            return
        }
        guard let obs = req.results?.first as? VNFeaturePrintObservation,
              obs.elementCount == 768 else {
            return
        }
        let floats: [Float] = obs.data.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
        if floats.count != 768 { return }
        var rawBytes = Data(capacity: 768 * 2)
        for f in floats {
            let clamped = max(-1.0 as Float, min(1.0 as Float, f))
            var q = Int16((clamped * 32767.0).rounded()).littleEndian
            withUnsafeBytes(of: &q) { rawBytes.append(contentsOf: $0) }
        }
        out[path] = rawBytes.base64EncodedString()
    }
}

if let data = try? JSONSerialization.data(withJSONObject: out, options: []),
   let jsonStr = String(data: data, encoding: .utf8) {
    print(jsonStr)
}
"""
    tmp_dir = tempfile.mkdtemp(prefix="archivio_fp_helper_")
    try:
        swift_file = os.path.join(tmp_dir, "fp_batch.swift")
        bin_file = os.path.join(tmp_dir, "fp_batch")
        with open(swift_file, "w", encoding="utf-8") as f:
            f.write(swift_code)
        subprocess.run(["swiftc", "-O", swift_file, "-o", bin_file], check=True)
        payload = "\n".join(image_paths) + "\n"
        res = subprocess.run(
            [bin_file],
            input=payload,
            capture_output=True,
            text=True,
            timeout=300,
            check=True,
        )
        return json.loads(res.stdout.strip() or "{}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def build_cover_index() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("build_cover_index.py richiede macOS per Apple Vision VNGenerateImageFeaturePrintRequest.")

    os.makedirs(CACHE_IMG_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    # Indicizza i file JPEG già presenti in CACHE_IMG_DIR per riusarli senza riscaricare
    cached_by_suffix: Dict[str, str] = {}
    for fname in sorted(os.listdir(CACHE_IMG_DIR)):
        if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
            continue
        fpath = os.path.join(CACHE_IMG_DIR, fname)
        if os.path.getsize(fpath) <= 1000:
            continue
        suffix = re.sub(r"^\d+_", "", fname)
        cached_by_suffix.setdefault(suffix, fpath)

    albums = _collect_target_albums(max_total=1650)
    downloaded_map: Dict[int, str] = {}
    for idx, (title, artist, _, _) in enumerate(albums):
        safe_name = re.sub(r"[^a-z0-9_]+", "_", f"{artist}_{title}".lower()).strip("_")[:90]
        cached_path = cached_by_suffix.get(f"{safe_name}.jpg")
        if cached_path:
            downloaded_map[idx] = cached_path

    urls = _fetch_artwork_urls(albums, skip_indices=set(downloaded_map.keys()))
    print(f"== Download parallelo di {len(urls)} nuove copertine in {CACHE_IMG_DIR} ==", flush=True)

    def _dl_one(item: Tuple[int, str]) -> Optional[Tuple[int, str]]:
        idx, url = item
        title, artist, _, _ = albums[idx]
        safe_name = re.sub(r"[^a-z0-9_]+", "_", f"{artist}_{title}".lower()).strip("_")[:90]
        dest = os.path.join(CACHE_IMG_DIR, f"{idx:04d}_{safe_name}.jpg")
        if _download_file(url, dest):
            return idx, dest
        return None

    with ThreadPoolExecutor(max_workers=12) as dl_pool:
        dl_futs = [dl_pool.submit(_dl_one, it) for it in urls.items()]
        dl_done = 0
        for fut in as_completed(dl_futs):
            dl_done += 1
            res = fut.result()
            if res is not None:
                downloaded_map[res[0]] = res[1]
            if dl_done % 100 == 0 or dl_done == len(urls):
                print(f"  scaricate: {dl_done}/{len(urls)} (totale disponibili: {len(downloaded_map)})", flush=True)

    downloaded: List[Tuple[int, str]] = sorted(downloaded_map.items(), key=lambda kv: kv[0])

    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    from PIL import Image, ImageOps
    from archivio.remote_scanner import extract_cover_vec256, quantize_vec256_b64  # type: ignore

    print(f"== Calcolo FeaturePrint Apple Vision per {len(downloaded)} immagini ==", flush=True)
    fp_map = _compute_featureprints_batch([p for _, p in downloaded])

    items_out: List[List[str]] = []
    for idx, img_path in downloaded:
        fp_b64 = fp_map.get(img_path, "")
        if not fp_b64:
            continue
        # Verifica esatta dimensione (1536 byte decodificati = 768 Int16)
        if len(base64.b64decode(fp_b64)) != 1536:
            continue
        xp_b64 = ""
        try:
            with Image.open(img_path) as im_raw:
                vec256 = extract_cover_vec256(ImageOps.exif_transpose(im_raw).convert("RGB"))
                xp_b64 = quantize_vec256_b64(vec256)
        except Exception:
            xp_b64 = ""
        if not xp_b64 or len(base64.b64decode(xp_b64)) != 256:
            continue
        title, artist, yr, mbid = albums[idx]
        items_out.append([title, artist, yr, mbid, fp_b64, xp_b64])

    payload = {
        "source": "Apple Vision FeaturePrint + Cross-Platform 256D (iTunes / Cover Art Archive)",
        "built": time.strftime("%Y-%m-%d"),
        "dim": 768,
        "dtype": "int16_le_b64",
        "scale": 32767,
        "xp_dim": 256,
        "xp_dtype": "int8_b64",
        "xp_scale": 127,
        "fields": ["title", "artist", "year", "mbid", "fp_b64", "xp_b64"],
        "items": items_out,
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(OUT_PATH) / 1024.0
    print(f"✓ Salvate {len(items_out)} copertine in {OUT_PATH} ({size_kb:.1f} KB)", flush=True)


def update_xp_vectors_from_cache() -> None:
    """Aggiorna `xp_b64` in `seed_covers.json` dalle copertine già in cache senza riscaricare da iTunes."""
    if not os.path.isfile(OUT_PATH):
        raise FileNotFoundError(f"Manca {OUT_PATH}")
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    from PIL import Image, ImageOps
    from archivio.remote_scanner import extract_cover_vec256, quantize_vec256_b64  # type: ignore

    with open(OUT_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)

    cache_by_suffix: Dict[str, str] = {}
    if os.path.isdir(CACHE_IMG_DIR):
        for fname in sorted(os.listdir(CACHE_IMG_DIR)):
            if not fname.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            # I file sono salvati come `<idx>_<safe_name>.jpg`
            parts = fname.split("_", 1)
            suffix = parts[1] if len(parts) == 2 else fname
            cache_by_suffix[suffix] = os.path.join(CACHE_IMG_DIR, fname)

    updated = 0
    items_out: List[List[str]] = []
    for row in payload.get("items") or []:
        title, artist, yr, mbid, fp_b64 = row[0], row[1], row[2], row[3], row[4]
        safe_name = re.sub(r"[^a-z0-9_]+", "_", f"{artist}_{title}".lower()).strip("_")[:90] + ".jpg"
        img_path = cache_by_suffix.get(safe_name, "")
        xp_b64 = row[5] if len(row) > 5 else ""
        if img_path and os.path.isfile(img_path):
            with Image.open(img_path) as im_raw:
                vec256 = extract_cover_vec256(ImageOps.exif_transpose(im_raw).convert("RGB"))
                xp_new = quantize_vec256_b64(vec256)
                if xp_new:
                    xp_b64 = xp_new
                    updated += 1
        items_out.append([title, artist, yr, mbid, fp_b64, xp_b64])

    payload["source"] = "Apple Vision FeaturePrint + Cross-Platform 256D (iTunes / Cover Art Archive)"
    payload["xp_dim"] = 256
    payload["xp_dtype"] = "int8_b64"
    payload["xp_scale"] = 127
    payload["fields"] = ["title", "artist", "year", "mbid", "fp_b64", "xp_b64"]
    payload["items"] = items_out
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(OUT_PATH) / 1024.0
    print(f"✓ Aggiornati {updated}/{len(items_out)} vettori multipiattaforma in {OUT_PATH} ({size_kb:.1f} KB)", flush=True)


if __name__ == "__main__":
    if "--update-xp" in sys.argv[1:]:
        update_xp_vectors_from_cache()
    else:
        build_cover_index()
