"""Motore di visione, OCR, codici a barre e matching visivo delle copertine."""
from __future__ import annotations

import base64
import io
import json
import math
import operator
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
from typing import Any, Dict, List, Optional, Tuple

from .catalog import (
    COLLECTION_TO_PUBLISHER_IT,
    ITALIAN_PUBLISHERS_PRIORITY,
    PUBLISHER_COLLECTIONS_IT,
    _direct_ratio,
)
from .config import resource_path
from .textutil import fold, search_fold


KNOWN_ISSN_SERIES: Dict[str, Tuple[str, str]] = {
    "9771120528": ("Urania", "Mondadori"),
    "9771120498": ("I Classici del Giallo Mondadori", "Mondadori"),
    "9770017007": ("Il Giallo Mondadori", "Mondadori"),
    "9771128608": ("La Biblioteca di Repubblica", "La Repubblica"),
    "9771129085": ("Classici dell'Arte", "Rizzoli / Skira"),
    "9772038084": ("Jazz in Vinile DeAgostini", "DeAgostini"),
    "9772531510": ("Prog Rock Italiano in Vinile", "DeAgostini"),
    "9772421265": ("Blues in Vinile", "DeAgostini"),
}

OCR_NOISE_WORDS = {
    "romanzo", "racconti", "poesie", "saggi", "edizione", "edizioni", "integrale",
    "bestseller", "premio", "strega", "nobel", "traduzione", "prefazione", "cura",
    "introduzione", "postfazione", "testo", "fronte", "latino", "greco", "originale",
    "stereo", "mono", "digital", "remaster", "remastered", "33rpm", "45rpm", "hifi", "hi-fi",
    "siae", "biem", "gema", "ascap", "made in italy", "made in eu", "compact disc", "digital audio",
    "all rights reserved", "vietato il noleggio", "prezzo", "euro", "lire",
    "inclus", "including", "featuring", "contains", "gatefold", "180g", "2lp",
}

OCR_NOISE_PHRASES = (
    "a cura di", "introduzione di", "prefazione di", "traduzione di", "postfazione di",
    "testo latino a fronte", "testo greco a fronte", "testo originale a fronte",
    "con testo a fronte", "edizione integrale", "nuova edizione", "premio strega",
    "premio nobel", "all rights reserved", "compact disc", "digital audio",
    "bande originale du film", "bande originale", "original motion picture soundtrack",
    "original soundtrack", "colonna sonora originale", "dialogues de", "parental advisory",
    "explicit content", "explicit lyrics", "vietato il noleggio", "made in italy",
    "made in eu", "printed in", "pat pending",
)

_RECORD_LABEL_NOISE_TOKENS = {
    "emi", "harvest", "virgin", "polydor", "columbia", "sony", "warner", "universal",
    "decca", "philips", "capitol", "rca", "atlantic", "elektra", "parlophone", "island",
    "geffen", "interscope", "arista", "epic", "verve", "impulse", "deutsche", "grammophon",
    "fonit", "cetra", "ricordi", "cgd", "mondadori", "einaudi", "rizzoli", "feltrinelli",
    "adelphi", "bompiani", "siae", "biem", "gema", "ascap", "stereo", "mono", "33rpm",
    "45rpm", "hifi", "digital", "remaster", "remastered", "inclus", "including", "bande",
    "originale", "film", "dialogues", "musique",
}

_SOUNDTRACK_CREDIT_PREFIX_RE = re.compile(
    r"^(?:musique\s+d[eu]?\s+|music\s+by\s+|musiche\s+di\s+|conducted\s+by\s+|"
    r"performed\s+by\s+|lyrics\s+by\s+|arranged\s+by\s+|produced\s+by\s+|"
    r"dialogues\s+de\s+|directed\s+by\s+|a\s+film\s+by\s+|un\s+film\s+d[ei]\s+|"
    r"musique\s+de(?=[A-Za-zÀ-ÿ]{3,}))",
    re.IGNORECASE,
)


def _clean_soundtrack_credit_prefix(text: str) -> str:
    """Rimuove prefissi di credito cinematografico/colonna sonora dalle righe OCR."""
    cleaned = _SOUNDTRACK_CREDIT_PREFIX_RE.sub("", text).strip(" :;-")
    return cleaned if len(cleaned) >= 2 else text


_SOUNDTRACK_MUSIC_CREDIT_RE = re.compile(
    r"^(?:musique\s+d[eu]?\s+|music\s+by\s+|musiche\s+di\s+|composed\s+by\s+|"
    r"conducted\s+by\s+|score\s+by\s+|musique\s+de(?=[A-Za-zÀ-ÿ]{3,}))(.+)$",
    re.IGNORECASE,
)


def _extract_soundtrack_hints(lines: List[Dict[str, Any]]) -> Tuple[List[str], set]:
    """Estrae compositori da crediti espliciti ('Musique de...') e token di brani teaser ('inclus ...')."""
    credit_artists: List[str] = []
    teaser_tokens: set = set()
    ordered = sorted(
        [x for x in (lines or []) if isinstance(x, dict) and str(x.get("text") or "").strip()],
        key=lambda x: -float(x.get("y") or 0.5),
    )
    teaser_next = False
    teaser_prev_y = 0.0
    for item in ordered:
        raw = str(item.get("text") or "").strip()
        y_pos = float(item.get("y") or 0.5)
        f_raw = fold(raw)
        m_cred = _SOUNDTRACK_MUSIC_CREDIT_RE.match(raw)
        if m_cred:
            art = m_cred.group(1).strip(" :;-")
            if len(art) >= 3 and art not in credit_artists:
                credit_artists.append(art)
        if f_raw in ("inclus", "including", "includes", "contiene", "featuring", "feat"):
            teaser_next = True
            teaser_prev_y = y_pos
            continue
        if teaser_next and abs(teaser_prev_y - y_pos) <= 0.14:
            for tok in _extract_meaningful_tokens(raw):
                teaser_tokens.add(tok)
        elif f_raw.startswith(("inclus ", "including ", "includes ", "contiene ", "featuring ")):
            rest = raw.split(None, 1)[1] if " " in raw else ""
            for tok in _extract_meaningful_tokens(rest):
                teaser_tokens.add(tok)
        teaser_next = False
    return credit_artists, teaser_tokens


def _deinterleave_mixed_case_line(text: str) -> List[str]:
    """
    Separa righe grafiche in cui Apple Vision fonde testo TitleCase e UPPERCASE sovrapposti
    (es. 'The COMINE Manhattan OUT Transfer' -> ['The Manhattan Transfer', 'COMINE OUT']).
    """
    tokens = text.split()
    if len(tokens) < 4:
        return []
    kinds: List[str] = []
    for tok in tokens:
        alpha = "".join(ch for ch in tok if ch.isalpha())
        if len(alpha) < 2:
            kinds.append("neutral")
        else:
            upper_cnt = sum(1 for ch in alpha if ch.isupper())
            lower_cnt = len(alpha) - upper_cnt
            is_title_like = alpha[0].isupper() and alpha[1:].islower()
            if lower_cnt == 0 or (upper_cnt >= max(2, int(len(alpha) * 0.65)) and not is_title_like):
                kinds.append("upper")
            elif upper_cnt > 0 and lower_cnt > 0:
                kinds.append("mixed")
            else:
                kinds.append("neutral")
    seq = [k for k in kinds if k in ("upper", "mixed")]
    if seq.count("upper") < 2 or seq.count("mixed") < 2:
        return []
    transitions = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i - 1])
    if transitions < 2:
        return []
    upper_toks = [t for t, k in zip(tokens, kinds) if k == "upper"]
    mixed_toks = [t for t, k in zip(tokens, kinds) if k == "mixed"]
    out: List[str] = []
    if len(mixed_toks) >= 2:
        out.append(" ".join(mixed_toks))
    if len(upper_toks) >= 2:
        out.append(" ".join(upper_toks))
    return out


_TRIVIAL_STOPWORDS = {
    "di", "da", "in", "su", "il", "lo", "la", "le", "ed", "al", "ai", "ad",
    "un", "una", "uno", "del", "dei", "degli", "delle", "che", "non", "con", "per",
    "the", "and", "for", "of", "by", "to", "in", "on", "at", "les", "des", "une",
    "und", "der", "die", "das", "you", "are", "all",
}


def _extract_meaningful_tokens(text: str) -> List[str]:
    words = [w for w in search_fold(text).split() if len(w) >= 2]
    filtered = [w for w in words if w not in _TRIVIAL_STOPWORDS]
    return filtered if filtered else [w for w in words if len(w) >= 3]


def _token_matches_set(tok: str, c_tokens: set) -> bool:
    if tok in c_tokens:
        return True
    if len(tok) <= 2:
        return False
    for ct in c_tokens:
        if len(ct) >= 3:
            if min(len(tok), len(ct)) >= 0.72 * max(len(tok), len(ct)) and (tok.startswith(ct) or ct.startswith(tok)):
                return True
            if abs(len(tok) - len(ct)) <= 1 and _direct_ratio(tok, ct) >= 80.0:
                return True
    return False


def _is_visual_match_trusted(
    vm: Dict[str, Any],
    lines: Optional[List[Dict[str, Any]]],
    cover_tokens: set,
) -> bool:
    """
    Valuta se un match visivo di copertina è coerente col testo OCR eventualmente letto.
    Evita che falsi positivi visivi (copertine fuori indice) sovrascrivano OCR affidabile.
    """
    if not isinstance(vm, dict):
        return False
    title = str(vm.get("title") or "").strip()
    artist = str(vm.get("artist") or "").strip()
    if not title or not artist:
        return False
    try:
        dist = float(vm.get("distance", 9.9))
    except (TypeError, ValueError):
        return False
    if dist > 0.72:
        return False

    sig_ocr = {
        tok for tok in (cover_tokens or set())
        if len(tok) >= 3 and tok not in _RECORD_LABEL_NOISE_TOKENS and tok not in _TRIVIAL_STOPWORDS
    }
    if not sig_ocr:
        return dist <= 0.65

    vm_toks = _extract_meaningful_tokens(f"{title} {artist}")
    overlap = sum(1 for t in vm_toks if _token_matches_set(t, sig_ocr))
    if overlap >= 1:
        return True

    max_line_h = max(
        (float(x.get("height") or 0.0) for x in (lines or []) if isinstance(x, dict)),
        default=0.0,
    )
    if len(sig_ocr) >= 2 or max_line_h >= 0.025:
        return dist <= 0.36
    return dist <= 0.56


def normalize_barcode_input(raw: str) -> Tuple[str, bool, str, str]:
    """
    Normalizza un codice a barre scansionato.
    Restituisce: (codice_pulito, is_issn_977, nome_collana_edicola, editore_edicola)
    - Se è un codice a 18 cifre (EAN-13 + supplemento 5 cifre del prezzo), estrae le prime 13 cifre.
    - Se inizia con 977 (13 cifre), lo identifica come codice periodico da edicola (ISSN).
    """
    cleaned = re.sub(r"[^0-9Xx]", "", (raw or "").strip()).upper()
    if len(cleaned) == 18 and cleaned[:3] in ("978", "979", "977"):
        cleaned = cleaned[:13]
    elif len(cleaned) == 15 and cleaned[:3] in ("978", "979", "977"):
        cleaned = cleaned[:13]

    if len(cleaned) >= 13 and cleaned.startswith("977"):
        prefix10 = cleaned[:10]
        series_name, pub_name = KNOWN_ISSN_SERIES.get(prefix10, ("Edizione Periodica da Edicola", ""))
        return cleaned[:13], True, series_name, pub_name

    return cleaned, False, "", ""


def _vision_binary_path() -> str:
    candidates = [
        resource_path("data", "vision_scanner"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vision_scanner"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return ""


def generate_qr_png(url: str, out_path: str) -> bool:
    """Genera un'immagine PNG del QR Code (nativo Apple CoreImage su macOS, libreria qrcode su Windows e Linux)."""
    bin_path = _vision_binary_path()
    if bin_path:
        try:
            res = subprocess.run(
                [bin_path, "qr", url, out_path],
                capture_output=True,
                text=True,
                timeout=4,
            )
            if res.returncode == 0 and os.path.isfile(out_path):
                return True
        except Exception:
            pass

    # Fallback multi-piattaforma con modulo qrcode (Windows, Linux, macOS fallback)
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        img.save(out_path)
        return os.path.isfile(out_path)
    except Exception:
        pass
    return False


def _normalize_retail_barcode(raw_txt: str) -> str:
    """Estrae un codice ISBN / EAN / UPC valido (8, 10, 12 o 13 cifre) ignorando URL o QR code."""
    s = (raw_txt or "").strip()
    if not s or "://" in s or "/" in s:
        return ""
    # Rimuovi eventuale prefisso simbologia GS1 (es. ]E0)
    if s.startswith("]") and len(s) > 3:
        s = s[3:].strip()
    cleaned = re.sub(r"[^0-9Xx]", "", s).upper()
    if len(cleaned) in (8, 12, 13) and cleaned.isdigit():
        return cleaned
    if len(cleaned) == 10 and cleaned[:9].isdigit() and (cleaned[9].isdigit() or cleaned[9] == "X"):
        return cleaned
    return ""


def _decode_barcodes_cross_platform(img_or_gray) -> List[str]:
    """Decodifica codici a barre 1D/ISBN tramite zxing-cpp C++ su Windows e Linux."""
    try:
        import zxingcpp  # type: ignore
    except ImportError:
        return []
    out: List[str] = []
    try:
        results = zxingcpp.read_barcodes(
            img_or_gray,
            try_rotate=True,
            try_downscale=True,
            try_invert=True,
        )
        for r in results:
            fmt_str = str(getattr(r, "format", "")).upper()
            if "QR" in fmt_str and not str(getattr(r, "text", "")).strip().isdigit():
                continue
            code = _normalize_retail_barcode(str(getattr(r, "text", "") or ""))
            if code and code not in out:
                out.append(code)
    except Exception:
        pass
    return out


def _ocr_lines_windows(image_path: str) -> List[Dict[str, Any]]:
    """Esegue il motore OCR nativo di Windows 10/11 (`Windows.Media.Ocr`) senza finestre console."""
    abs_img = os.path.abspath(image_path)
    if not os.path.isfile(abs_img):
        return []
    ps_script = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$imgPath = $env:ARCHIVIO_OCR_IMG
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    return $netTask.Result
}

$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imgPath)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $imgH = [double]$bitmap.PixelHeight
    if ($imgH -le 0) { $imgH = 1.0 }

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {
        $lang = New-Object Windows.Globalization.Language('en-US')
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
    }
    if ($null -eq $engine) {
        Write-Output '{"lines":[]}'
        exit 0
    }
    $res = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines = @()
    foreach ($line in $res.Lines) {
        $minY = 999999.0
        $maxY = 0.0
        foreach ($w in $line.Words) {
            $r = $w.BoundingRect
            if ($r.Y -lt $minY) { $minY = [double]$r.Y }
            if (($r.Y + $r.Height) -gt $maxY) { $maxY = [double]($r.Y + $r.Height) }
        }
        $hNorm = ($maxY - $minY) / $imgH
        if ($hNorm -le 0 -or $hNorm -gt 1.0) { $hNorm = 0.05 }
        $yCenter = ($minY + $maxY) * 0.5
        $yVision = 1.0 - ($yCenter / $imgH)
        $lines += @{
            text = [string]$line.Text
            height = [math]::Round($hNorm, 4)
            y = [math]::Round($yVision, 4)
            confidence = 0.92
        }
    }
    @{ lines = @($lines) } | ConvertTo-Json -Depth 4 -Compress
} finally {
    if ($null -ne $stream) { $stream.Dispose() }
}
"""
    env = os.environ.copy()
    env["ARCHIVIO_OCR_IMG"] = abs_img
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=10,
            creationflags=no_win,
        )
        if res.returncode == 0 and res.stdout.strip():
            raw_out = res.stdout.strip()
            idx = raw_out.find("{")
            if idx >= 0:
                parsed = json.loads(raw_out[idx:])
                if isinstance(parsed, dict) and isinstance(parsed.get("lines"), list):
                    return parsed["lines"]
    except Exception:
        pass
    return []


def _find_tesseract_bin() -> Optional[str]:
    """Individua l'eseguibile `tesseract` (incluso in AppImage o installato nel sistema)."""
    import shutil as _shutil

    candidates: List[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "tesseract"))
        candidates.append(os.path.join(meipass, "tesseract.exe"))
    appdir = os.environ.get("APPDIR")
    if appdir:
        candidates.append(os.path.join(appdir, "usr", "bin", "tesseract"))
    found = _shutil.which("tesseract")
    if found:
        candidates.append(found)
    if sys.platform == "win32":
        candidates.append(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
        candidates.append(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _ocr_lines_tesseract(image_path: str) -> List[Dict[str, Any]]:
    """Esegue OCR tramite Tesseract TSV restituendo righe compatibili con il formato Vision (`height`, `y`)."""
    tess = _find_tesseract_bin()
    if not tess or not os.path.isfile(image_path):
        return []
    img_h = 1000.0
    try:
        from PIL import Image
        with Image.open(image_path) as im:
            img_h = float(max(im.size[1], 1))
    except Exception:
        pass

    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    tsv_text = ""
    for lang_args in (["-l", "ita+eng"], ["-l", "eng"], []):
        try:
            res = subprocess.run(
                [tess, image_path, "stdout", *lang_args, "--psm", "3", "tsv"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=no_win,
            )
            if res.returncode == 0 and res.stdout.strip():
                tsv_text = res.stdout
                break
        except Exception:
            continue

    if not tsv_text:
        return []

    grouped: Dict[Tuple[str, str, str, str], List[Tuple[int, int, float, str]]] = {}
    for idx, raw_ln in enumerate(tsv_text.splitlines()):
        if idx == 0 or not raw_ln.strip():
            continue
        cols = raw_ln.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        word = cols[11].strip()
        if not word:
            continue
        try:
            conf = float(cols[10])
            top = int(cols[7])
            height = int(cols[9])
        except ValueError:
            continue
        if conf < 25.0:
            continue
        key = (cols[1], cols[2], cols[3], cols[4])
        grouped.setdefault(key, []).append((top, top + max(height, 1), conf, word))

    lines_out: List[Dict[str, Any]] = []
    for words in grouped.values():
        txt = " ".join(w[3] for w in words).strip()
        if len(txt) < 2:
            continue
        min_t = min(w[0] for w in words)
        max_b = max(w[1] for w in words)
        avg_conf = sum(w[2] for w in words) / (100.0 * len(words))
        h_norm = max(0.01, min(1.0, (max_b - min_t) / img_h))
        y_center = (min_t + max_b) * 0.5
        y_vision = max(0.0, min(1.0, 1.0 - (y_center / img_h)))
        lines_out.append(
            {
                "text": txt,
                "height": round(h_norm, 4),
                "y": round(y_vision, 4),
                "confidence": round(max(0.35, min(1.0, avg_conf)), 3),
            }
        )
    return lines_out
_XP_COVER_CACHE_LOCK = threading.Lock()
_XP_COVER_CACHE: Optional[List[Tuple[str, str, str, str, List[float]]]] = None


def extract_cover_vec256(im: Any) -> List[float]:
    """Estrae un descrittore visivo L2-normalizzato a 256 dimensioni (griglia RGB 8x8 + gradienti 8x8)."""
    from PIL import Image

    w, h = im.size
    if w < 16 or h < 16:
        return []
    if w > 20 and h > 20:
        mx, my = int(w * 0.05), int(h * 0.05)
        im = im.crop((mx, my, w - mx, h - my))
    im64 = im.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
    gray = im64.convert("L").tobytes()
    if not gray or (max(gray) - min(gray)) < 16:
        return []
    g_mean_px = sum(gray) / 4096.0
    g_std_px = math.sqrt(sum((p - g_mean_px) ** 2 for p in gray) / 4096.0)
    if g_std_px < 3.5:
        return []

    im8 = im64.resize((8, 8), Image.Resampling.BOX)
    raw8 = im8.tobytes()
    vec: List[float] = []
    for ch in range(3):
        vals = [raw8[i * 3 + ch] for i in range(64)]
        mean = sum(vals) / 64.0
        var = sum((v - mean) ** 2 for v in vals) / 64.0
        std = max(math.sqrt(var), 14.0)
        vec.extend((v - mean) / std for v in vals)

    g_cells = [0.0] * 64
    for y in range(1, 63):
        row = y * 64
        by = (y // 8) * 8
        for x in range(1, 63):
            dx = gray[row + x + 1] - gray[row + x - 1]
            dy = gray[row + 64 + x] - gray[row - 64 + x]
            g_cells[by + (x // 8)] += math.hypot(dx, dy)
    g_mean = sum(g_cells) / 64.0
    g_std = max(math.sqrt(sum((v - g_mean) ** 2 for v in g_cells) / 64.0), 20.0)
    vec.extend(((v - g_mean) / g_std) * 0.65 for v in g_cells)

    norm = math.sqrt(sum(map(operator.mul, vec, vec)))
    if norm < 1e-6:
        return []
    inv = 1.0 / norm
    return [v * inv for v in vec]


def quantize_vec256_b64(vec: List[float]) -> str:
    """Quantizza il vettore unitario a 256 float in Int8 Base64 (256 byte)."""
    if len(vec) != 256:
        return ""
    ints = [max(-127, min(127, int(round(v * 127.0)))) for v in vec]
    return base64.b64encode(struct.pack("256b", *ints)).decode("ascii")


def _resolve_seed_covers_path() -> str:
    candidates = [
        os.environ.get("ARCHIVIO_SEED_COVERS_PATH", "").strip(),
        resource_path("data", "seed_covers.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "seed_covers.json"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return ""


def _load_cross_platform_seed_covers() -> List[Tuple[str, str, str, str, List[float]]]:
    global _XP_COVER_CACHE
    with _XP_COVER_CACHE_LOCK:
        if _XP_COVER_CACHE is not None:
            return _XP_COVER_CACHE
        path = _resolve_seed_covers_path()
        if not path:
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            dim = int(data.get("xp_dim") or 256)
            scale = float(data.get("xp_scale") or 127.0)
            if dim != 256 or scale <= 0:
                return []
            out: List[Tuple[str, str, str, str, List[float]]] = []
            for row in data.get("items") or []:
                if not isinstance(row, list) or len(row) < 6 or not row[5]:
                    continue
                raw = base64.b64decode(str(row[5]))
                if len(raw) != 256:
                    continue
                vals = [v / scale for v in struct.unpack("256b", raw)]
                norm = math.sqrt(sum(map(operator.mul, vals, vals)))
                if norm < 1e-6:
                    continue
                inv = 1.0 / norm
                out.append((
                    str(row[0] or ""),
                    str(row[1] or ""),
                    str(row[2] or ""),
                    str(row[3] or ""),
                    [v * inv for v in vals],
                ))
            if out:
                _XP_COVER_CACHE = out
            return out
        except Exception:
            return []


def _candidate_cover_crops(im: Any) -> List[Any]:
    from PIL import Image

    w, h = im.size
    if w < 32 or h < 32:
        return []
    crops: List[Any] = [im]
    min_s = min(w, h)
    cx, cy = w / 2.0, h / 2.0

    # 1. Rilevamento contorno foreground rispetto al bordo (copertina inquadrata davanti a sfondo neutro)
    small = im.resize((64, 64), Image.Resampling.BILINEAR)
    sraw = small.tobytes()

    def _px(x: int, y: int) -> Tuple[int, int, int]:
        idx = (y * 64 + x) * 3
        return sraw[idx], sraw[idx + 1], sraw[idx + 2]

    border = (
        [_px(i, 0) for i in range(64)]
        + [_px(i, 63) for i in range(64)]
        + [_px(0, y) for y in range(64)]
        + [_px(63, y) for y in range(64)]
    )
    mid = len(border) // 2
    br = sorted(p[0] for p in border)[mid]
    bg = sorted(p[1] for p in border)[mid]
    bb = sorted(p[2] for p in border)[mid]
    xs: List[int] = []
    ys: List[int] = []
    for y in range(4, 60):
        for x in range(4, 60):
            r, g, b = _px(x, y)
            if abs(r - br) + abs(g - bg) + abs(b - bb) > 48:
                xs.append(x)
                ys.append(y)
    if len(xs) >= 30:
        x0_f, x1_f = min(xs) / 64.0, (max(xs) + 1) / 64.0
        y0_f, y1_f = min(ys) / 64.0, (max(ys) + 1) / 64.0
        bw, bh = (x1_f - x0_f) * w, (y1_f - y0_f) * h
        if bw >= 40 and bh >= 40:
            crops.append(im.crop((int(x0_f * w), int(y0_f * h), int(x1_f * w), int(y1_f * h))))
            bcx, bcy = (x0_f + x1_f) * 0.5 * w, (y0_f + y1_f) * 0.5 * h
            bside = max(bw, bh)
            for s in (1.0, 0.88, 0.76):
                hs = bside * s * 0.5
                crops.append(
                    im.crop((max(0, int(bcx - hs)), max(0, int(bcy - hs)), min(w, int(bcx + hs)), min(h, int(bcy + hs))))
                )

    # 2. Se OpenCV è presente (Windows/Linux), individua il contorno rettangolare principale
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        arr = np.array(im)
        gray_cv = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(cv2.GaussianBlur(gray_cv, (5, 5), 0), 40, 140)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(w * h)
        for cnt in sorted(contours, key=cv2.contourArea, reverse=True)[:4]:
            rx, ry, rw, rh = cv2.boundingRect(cnt)
            if (rw * rh) < frame_area * 0.08 or rw < 40 or rh < 40:
                continue
            aspect = rw / max(1.0, float(rh))
            if 0.55 <= aspect <= 1.8:
                crops.append(im.crop((rx, ry, rx + rw, ry + rh)))
                break
    except Exception:
        pass

    # 3. Ritagli quadrati multi-scala centrati e decentrati (inclusa altezza piena e spostamento laterale a mano)
    for scale in (1.0, 0.88, 0.74, 0.60):
        side = min_s * scale
        for ox in (-0.14, 0.0, 0.14):
            for oy in (-0.05, 0.0, 0.05):
                if scale >= 0.98 and oy != 0.0:
                    continue
                scx = cx + ox * w
                scy = cy + oy * h
                x0 = max(0, int(scx - side / 2.0))
                y0 = max(0, int(scy - side / 2.0))
                x1 = min(w, int(scx + side / 2.0))
                y1 = min(h, int(scy + side / 2.0))
                if (x1 - x0) >= 32 and (y1 - y0) >= 32:
                    crops.append(im.crop((x0, y0, x1, y1)))
    return crops


def _match_cover_pil_image(raw_im: Any) -> List[Dict[str, Any]]:
    """Esegue il confronto visivo multi-scala e specchiato su un'immagine PIL già aperta."""
    db = _load_cross_platform_seed_covers()
    if not db or raw_im is None:
        return []
    try:
        from PIL import Image, ImageOps

        im = ImageOps.exif_transpose(raw_im).convert("RGB")
        full_gray = im.resize((64, 64), Image.Resampling.BILINEAR).convert("L").tobytes()
        if not full_gray or (max(full_gray) - min(full_gray)) < 16:
            return []

        crops = list(_candidate_cover_crops(im))
        best_by_idx: Dict[int, float] = {}
        best_direct_d = 9.9
        for mirrored in (False, True):
            for crop in crops:
                variant = ImageOps.mirror(crop) if mirrored else crop
                vec = extract_cover_vec256(variant)
                if not vec:
                    continue
                best1_idx = -1
                best1_d = 9.9
                best2_d = 9.9
                for idx, (_, _, _, _, db_vec) in enumerate(db):
                    dot = sum(map(operator.mul, vec, db_vec))
                    dist = math.sqrt(max(0.0, 2.0 - 2.0 * dot))
                    if dist < best_direct_d:
                        best_direct_d = dist
                    if dist <= 0.65:
                        prev = best_by_idx.get(idx)
                        if prev is None or dist < prev:
                            best_by_idx[idx] = dist
                    if dist < best1_d:
                        best2_d = best1_d
                        best1_d = dist
                        best1_idx = idx
                    elif dist < best2_d:
                        best2_d = dist
                margin = best2_d - best1_d
                if best1_idx >= 0 and 0.65 < best1_d <= 0.72 and margin >= 0.085:
                    eff_d = min(0.62, best1_d * 0.85)
                    prev = best_by_idx.get(best1_idx)
                    if prev is None or eff_d < prev:
                        best_by_idx[best1_idx] = eff_d
            if best_direct_d <= 0.50:
                break
    except Exception:
        return []

    ranked = sorted(best_by_idx.items(), key=lambda kv: kv[1])[:5]
    return [
        {
            "title": db[idx][0],
            "artist": db[idx][1],
            "year": db[idx][2],
            "mbid": db[idx][3],
            "distance": round(dist, 4),
        }
        for idx, dist in ranked
    ]


def match_cover_artwork_cross_platform(image_path: str) -> List[Dict[str, Any]]:
    """
    Riconosce visivamente copertine di dischi/album su qualsiasi OS (Windows, Linux, fallback macOS)
    confrontando ritagli multi-scala normali e specchiati contro `seed_covers.json`.
    """
    if not image_path or not os.path.isfile(image_path):
        return []
    try:
        from PIL import Image

        with Image.open(image_path) as raw_im:
            return _match_cover_pil_image(raw_im)
    except Exception:
        return []


def match_cover_artwork_from_bytes(jpg_bytes: bytes) -> List[Dict[str, Any]]:
    """Esegue il riconoscimento visivo multipiattaforma direttamente da byte JPEG in memoria."""
    if not jpg_bytes:
        return []
    try:
        from PIL import Image

        with Image.open(io.BytesIO(jpg_bytes)) as raw_im:
            return _match_cover_pil_image(raw_im)
    except Exception:
        return []


def merge_visual_matches(
    primary: List[Dict[str, Any]],
    secondary: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Fonde i candidati visivi di più motori applicando un bonus consenso quando coincidono."""
    by_key: Dict[Tuple[str, str], Dict[str, Any]] = {}
    seen_primary: set = set()
    seen_secondary: set = set()
    has_primary = any(
        isinstance(vm, dict) and str(vm.get("title") or "").strip() and str(vm.get("artist") or "").strip()
        for vm in (primary or [])
    )

    for is_sec, source in ((False, primary or []), (True, secondary or [])):
        for vm in source:
            if not isinstance(vm, dict):
                continue
            title = str(vm.get("title") or "").strip()
            artist = str(vm.get("artist") or "").strip()
            if not title or not artist:
                continue
            try:
                dist = float(vm.get("distance", 9.9))
            except (TypeError, ValueError):
                continue
            if dist > 0.72:
                continue
            key = (search_fold(title), search_fold(artist))
            if not key[0] or not key[1]:
                continue
            if is_sec:
                if has_primary and key not in seen_primary:
                    continue
                seen_secondary.add(key)
            else:
                seen_primary.add(key)

            year = str(vm.get("year") or "")
            mbid = str(vm.get("mbid") or "")
            prev = by_key.get(key)
            if prev is None:
                by_key[key] = {
                    "title": title,
                    "artist": artist,
                    "year": year,
                    "mbid": mbid,
                    "distance": dist,
                }
            else:
                if dist < prev["distance"]:
                    prev["title"] = title
                    prev["artist"] = artist
                    prev["distance"] = dist
                if not prev["year"] and year:
                    prev["year"] = year
                if not prev["mbid"] and mbid:
                    prev["mbid"] = mbid

    out: List[Dict[str, Any]] = []
    for key, item in by_key.items():
        dist = float(item["distance"])
        if key in seen_primary and key in seen_secondary:
            dist *= 0.85
        out.append({
            "title": item["title"],
            "artist": item["artist"],
            "year": item["year"],
            "mbid": item["mbid"],
            "distance": round(dist, 4),
        })
    out.sort(key=lambda x: float(x["distance"]))
    return out[:5]


def run_apple_vision_on_image(image_path: str, mode: str = "scan") -> Dict[str, Any]:
    """
    Esegue il riconoscimento di codici a barre, testo OCR e copertine visive su un'immagine:
    - macOS: motore nativo Apple Vision (`vision_scanner`) con fusione visiva multipiattaforma
    - Windows: `zxing-cpp` + motore nativo `Windows.Media.Ocr` (con fallback Tesseract) + matching visivo copertine
    - Linux: `zxing-cpp` + `Tesseract OCR` + matching visivo copertine
    """
    if not os.path.isfile(image_path):
        return {"barcodes": [], "lines": [], "visual_matches": []}

    if sys.platform == "darwin":
        bin_path = _vision_binary_path()
        if bin_path:
            try:
                res = subprocess.run(
                    [bin_path, mode, image_path],
                    capture_output=True,
                    text=True,
                    timeout=25,
                )
                if res.returncode == 0 and res.stdout.strip():
                    parsed = json.loads(res.stdout.strip())
                    if not parsed.get("barcodes") and mode in ("scan", "ocr"):
                        vis_primary = parsed.get("visual_matches") or []
                        ocr_lines = parsed.get("lines") or []
                        if vis_primary:
                            xp_matches = match_cover_artwork_cross_platform(image_path)
                            parsed["visual_matches"] = merge_visual_matches(vis_primary, xp_matches)
                        elif not ocr_lines:
                            xp_matches = [
                                m for m in match_cover_artwork_cross_platform(image_path)
                                if float(m.get("distance", 9.9)) <= 0.56
                            ]
                            parsed["visual_matches"] = merge_visual_matches([], xp_matches)
                        else:
                            parsed["visual_matches"] = []
                    return parsed
            except Exception:
                pass
        return {
            "barcodes": [],
            "lines": [],
            "visual_matches": match_cover_artwork_cross_platform(image_path) if mode in ("scan", "ocr") else [],
        }

    barcodes: List[str] = []
    try:
        from PIL import Image, ImageOps

        with Image.open(image_path) as im_raw:
            im_rgb = ImageOps.exif_transpose(im_raw).convert("RGB")
            for c in _decode_barcodes_cross_platform(im_rgb):
                if c not in barcodes:
                    barcodes.append(c)
            if not barcodes:
                gray = ImageOps.autocontrast(im_rgb.convert("L"), cutoff=2)
                for c in _decode_barcodes_cross_platform(gray):
                    if c not in barcodes:
                        barcodes.append(c)
            if not barcodes:
                flipped = ImageOps.mirror(im_rgb)
                for c in _decode_barcodes_cross_platform(flipped):
                    if c not in barcodes:
                        barcodes.append(c)
    except Exception:
        pass

    # Se siamo in modalità 'barcode' pura e abbiamo già trovato il codice, evitiamo l'OCR
    if mode == "barcode" and barcodes:
        return {"barcodes": barcodes, "lines": [], "visual_matches": []}

    lines: List[Dict[str, Any]] = []
    if sys.platform == "win32":
        lines = _ocr_lines_windows(image_path)
        if not lines:
            lines = _ocr_lines_tesseract(image_path)
    else:
        lines = _ocr_lines_tesseract(image_path)

    visual_matches = match_cover_artwork_cross_platform(image_path) if (not barcodes and mode in ("scan", "ocr")) else []
    return {"barcodes": barcodes, "lines": lines, "visual_matches": visual_matches}


def extract_isbn_or_ean_from_ocr_lines(lines: List[Dict[str, Any]]) -> str:
    """Cerca un codice ISBN o EAN stampato in cifre vicino al codice a barre se le barre sono danneggiate."""
    full_text = "\n".join(str(x.get("text") or "") for x in lines)
    # 1. Cerca pattern esplicito ISBN
    m_isbn = re.search(r"ISBN(?:-1[03])?[:\s]*([0-9Xx\- ]{10,17})", full_text, re.IGNORECASE)
    if m_isbn:
        digits = re.sub(r"[^0-9Xx]", "", m_isbn.group(1)).upper()
        if len(digits) in (10, 13):
            return digits
    # 2. Cerca sequenza di 12 o 13 cifre (EAN-13 / UPC-12)
    for m in re.finditer(r"\b(\d[\d \-]{10,15}\d)\b", full_text):
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) in (12, 13):
            return digits
    return ""


def parse_cover_ocr_lines(
    lines: List[Dict[str, Any]],
) -> Tuple[List[str], str, str, set, List[Tuple[str, str]]]:
    """
    Analizza le righe lette da Apple Vision sulla copertina di un libro o vinile:
    1) Individua e separa eventuali nomi di Editore (es. 'MONDADORI', 'RIZZOLI', 'EINAUDI')
       o Collana (es. 'BUR CLASSICI', 'OSCAR MONDADORI', 'PICCOLA BIBLIOTECA ADELPHI')
       così non inquinano la ricerca del Titolo/Autore ma vengono pre-impostati nei campi!
    2) Raggruppa in ordine di lettura dall'alto in basso (`-y`) le righe adiacenti con la stessa
       altezza di carattere (così un titolo su 2 righe come 'LA COSCIENZA' + 'DI ZENO' resta unito).
    3) Costruisce coppie probabili (Titolo, Autore) e permutazioni per interrogare i cataloghi
       con parametri strutturati oltre che full-text combinato.
    Restituisce: (queries, detected_publisher, detected_collection, cover_tokens_set, likely_pairs)
    """
    detected_pub = ""
    detected_coll = ""
    valid_rows: List[Tuple[float, float, str]] = []  # (height, y, text)
    deinterleaved_pairs: List[Tuple[str, str]] = []
    deinterleaved_texts: List[str] = []
    cover_tokens: set = set()

    for item in lines:
        raw = str(item.get("text") or "").strip()
        if len(raw) < 2:
            continue
        conf = float(item.get("confidence") or 0.5)
        if conf < 0.28:
            continue
        # Ignora numeri/prezzi isolati
        if re.fullmatch(r"[\d\s.,€£$L/#-]+", raw) and raw not in ("1984", "2001"):
            continue

        raw = _clean_soundtrack_credit_prefix(raw)
        f_line = fold(raw)
        # Scarta diciture editoriali di copertina ("testo latino a fronte", "introduzione di...", ecc.)
        if any(f_line.startswith(np) or np in f_line for np in OCR_NOISE_PHRASES):
            continue

        # Verifica se la riga è il nome di una Collana nota
        matched_coll = False
        for c_fold, c_pub in COLLECTION_TO_PUBLISHER_IT.items():
            if len(c_fold) >= 4 and (f_line == c_fold or f_line.startswith(c_fold + " ")):
                if not detected_coll:
                    # Trova il nome con maiuscole corrette
                    for p_list in PUBLISHER_COLLECTIONS_IT.values():
                        for c_disp in p_list:
                            if fold(c_disp) == c_fold:
                                detected_coll = c_disp
                                break
                    detected_coll = detected_coll or raw.title()
                if not detected_pub and c_pub:
                    detected_pub = c_pub
                matched_coll = True
                break
        if matched_coll:
            continue

        # Verifica se la riga è il nome di un Editore italiano noto
        if f_line in ITALIAN_PUBLISHERS_PRIORITY:
            if not detected_pub:
                detected_pub = ITALIAN_PUBLISHERS_PRIORITY[f_line]
            continue

        # Rimuovi singole parole di rumore ("ROMANZO", "SAGGI", ecc.)
        words = [w for w in raw.split() if fold(w) not in OCR_NOISE_WORDS]
        cleaned_txt = " ".join(words).strip(" ,.;:-")
        if len(cleaned_txt) < 2:
            continue

        for tok in search_fold(cleaned_txt).split():
            if len(tok) >= 2 and tok not in _TRIVIAL_STOPWORDS:
                cover_tokens.add(tok)

        height = float(item.get("height") or 0.05)
        y_pos = float(item.get("y") or 0.5)
        split_graphic = _deinterleave_mixed_case_line(cleaned_txt)
        if split_graphic:
            for idx_sg, sg_line in enumerate(split_graphic):
                valid_rows.append((height * (1.05 if idx_sg == 0 else 1.0), y_pos - idx_sg * 0.01, sg_line))
                deinterleaved_texts.append(sg_line)
            if len(split_graphic) >= 2:
                deinterleaved_pairs.append((split_graphic[1], split_graphic[0]))
                deinterleaved_pairs.append((split_graphic[0], split_graphic[1]))
        else:
            valid_rows.append((height, y_pos, cleaned_txt))

    if not valid_rows:
        return [], detected_pub, detected_coll, cover_tokens, []

    # Filtra via scritte minuscole di quarta/aletta (tieni righe >= 14% del carattere più grande,
    # così non tagliamo fuori il nome dell'autore quando il titolo ha un font enorme)
    max_h = max(r[0] for r in valid_rows)
    prominent = [r for r in valid_rows if r[0] >= max_h * 0.14]
    if not prominent:
        prominent = valid_rows

    # Ordina dall'alto verso il basso (in Apple Vision y=1.0 è in alto, y=0.0 è in basso)
    prominent.sort(key=lambda r: -r[1])

    _short_prefix_words = {
        "the", "a", "an", "il", "lo", "la", "i", "gli", "le", "un", "una",
        "of", "in", "on", "at", "by", "de", "du", "des", "el", "los", "las",
        "pink", "miles", "david", "bob", "john", "paul", "george", "elton",
        "stevie", "michael", "vladimir",
    }

    # Unisci righe consecutive che appartengono allo stesso blocco grafico (altezza simile e interlinea naturale)
    merged_raw: List[Tuple[float, float, str]] = []
    for h, y, txt in prominent:
        if merged_raw:
            prev_h, prev_y, prev_txt = merged_raw[-1]
            max_lh = max(h, prev_h)
            prev_fold = fold(prev_txt)
            is_short_prefix = prev_fold in _short_prefix_words or (len(prev_txt.split()) == 1 and len(prev_txt) <= 4)
            h_tol = 0.68 if is_short_prefix else 0.35
            y_tol = max(0.16 if is_short_prefix else 0.12, max_lh * (2.6 if is_short_prefix else 2.2))
            if abs(h - prev_h) <= max_lh * h_tol and abs(prev_y - y) <= y_tol and len(prev_txt.split()) <= 6:
                merged_raw[-1] = (max_lh, y, f"{prev_txt} {txt}")
                continue
        merged_raw.append((h, y, txt))

    merged_blocks: List[Tuple[float, str]] = [(h, txt) for h, _, txt in merged_raw]

    # Ordina i blocchi fusi per importanza visiva (altezza carattere decrescente)
    by_size = sorted(merged_blocks, key=lambda x: x[0], reverse=True)
    top_texts = [t for _, t in by_size[:5]]
    reading_texts = [t for _, t in merged_blocks[:5]]
    raw_by_size = [t for _, _, t in sorted(prominent, key=lambda r: r[0], reverse=True)[:5]]

    # Costruisci coppie probabili (Titolo, Autore) e permutazioni
    likely_pairs: List[Tuple[str, str]] = list(deinterleaved_pairs)
    if len(top_texts) >= 2:
        likely_pairs.append((top_texts[0], top_texts[1]))
        likely_pairs.append((top_texts[1], top_texts[0]))
    if len(reading_texts) >= 2:
        p1 = (reading_texts[0], reading_texts[1])
        p2 = (reading_texts[1], reading_texts[0])
        if p1 not in likely_pairs:
            likely_pairs.append(p1)
        if p2 not in likely_pairs:
            likely_pairs.append(p2)
    if len(top_texts) >= 3:
        likely_pairs.append((top_texts[0], top_texts[2]))
        likely_pairs.append((top_texts[1], top_texts[2]))

    queries: List[str] = []
    if len(top_texts) >= 2:
        queries.append(f"{top_texts[0]} {top_texts[1]}")
    if len(reading_texts) >= 2:
        queries.append(f"{reading_texts[0]} {reading_texts[1]}")
    for dt in deinterleaved_texts:
        queries.append(dt)
    if top_texts:
        queries.append(top_texts[0])
    if len(top_texts) >= 2:
        queries.append(top_texts[1])
    for rt in raw_by_size[:3]:
        queries.append(rt)
    if len(top_texts) >= 3:
        queries.append(f"{top_texts[0]} {top_texts[2]}")
        queries.append(f"{top_texts[1]} {top_texts[2]}")
        queries.append(top_texts[2])

    out: List[str] = []
    seen = set()
    for q in queries:
        qn = search_fold(q)
        sig_q = [w for w in qn.split() if len(w) >= 2 and w not in _TRIVIAL_STOPWORDS]
        if qn and qn not in seen and sig_q:
            seen.add(qn)
            out.append(q)

    # Rimuovi duplicati in likely_pairs
    uniq_pairs: List[Tuple[str, str]] = []
    seen_pairs = set()
    for t_c, a_c in likely_pairs:
        t_sig = any(len(w) >= 2 and w not in _TRIVIAL_STOPWORDS for w in search_fold(t_c).split())
        a_sig = any(len(w) >= 2 and w not in _TRIVIAL_STOPWORDS for w in search_fold(a_c).split())
        pair_key = (search_fold(t_c), search_fold(a_c))
        if pair_key not in seen_pairs and t_sig and a_sig:
            seen_pairs.add(pair_key)
            uniq_pairs.append((t_c.strip(), a_c.strip()))

    return out, detected_pub, detected_coll, cover_tokens, uniq_pairs


def build_queries_from_ocr_lines(lines: List[Dict[str, Any]]) -> List[str]:
    queries, _, _, _, _ = parse_cover_ocr_lines(lines)
    return queries
