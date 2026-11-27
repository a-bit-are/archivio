#!/usr/bin/env python3
"""
Mega-suite di test su 1.000+ copertine reali, simulazioni webcam 16:9,
rifiuto falsi positivi fuori indice e regressioni della sessione live.
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(ROOT, "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from archivio.catalog import (  # noqa: E402
    CatalogService,
    PersistentCache,
    Suggestion,
    _clean_album_title_suffixes,
)
from archivio.remote_scanner import (  # noqa: E402
    RemoteScannerService,
    parse_cover_ocr_lines,
)
from archivio.textutil import search_fold, surname_key  # noqa: E402

SEED_COVERS_PATH = os.path.join(APP_DIR, "archivio", "data", "seed_covers.json")
VISION_BIN_PATH = os.path.join(APP_DIR, "archivio", "data", "vision_scanner")
CACHE_IMG_DIR = os.path.join(tempfile.gettempdir(), "archivio_cover_artwork_cache")


def _safe_cache_suffix(title: str, artist: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", f"{artist}_{title}".lower()).strip("_")[:90] + ".jpg"


def _build_cache_path_map() -> dict[str, str]:
    by_suffix: dict[str, str] = {}
    if os.path.isdir(CACHE_IMG_DIR):
        for fname in sorted(os.listdir(CACHE_IMG_DIR)):
            if fname.lower().endswith((".jpg", ".jpeg", ".png")):
                fpath = os.path.join(CACHE_IMG_DIR, fname)
                if os.path.getsize(fpath) > 1000:
                    suffix = re.sub(r"^\d+_", "", fname)
                    by_suffix.setdefault(suffix, fpath)
    return by_suffix


def _titles_equivalent(expected_t: str, expected_a: str, got_t: str, got_a: str) -> bool:
    et = search_fold(_clean_album_title_suffixes(expected_t))
    gt = search_fold(_clean_album_title_suffixes(got_t))
    ea = search_fold(expected_a)
    ga = search_fold(got_a)
    a_ok = bool(ea and ga and (ea == ga or ea in ga or ga in ea))
    if not a_ok:
        ea_toks = {t for t in ea.split() if len(t) >= 3}
        ga_toks = {t for t in ga.split() if len(t) >= 3}
        a_ok = bool(ea_toks and ga_toks and (ea_toks <= ga_toks or ga_toks <= ea_toks))
    if not a_ok:
        return False
    if et == gt or (et and gt and (et in gt or gt in et)):
        return True
    et_toks = [t for t in et.split() if len(t) >= 2]
    gt_toks = set(gt.split())
    return bool(et_toks and all(t in gt_toks for t in et_toks))


def _run_batch_scan(paths: list[str], workers: int = 4) -> dict[str, dict]:
    """Esegue `vision_scanner batch_scan` in parallelo su blocchi di immagini."""
    chunk_size = max(1, (len(paths) + workers - 1) // workers)
    chunks = [paths[i:i + chunk_size] for i in range(0, len(paths), chunk_size)]

    def _run_chunk(chunk: list[str]) -> list[tuple[str, dict]]:
        payload = "\n".join(chunk) + "\n__QUIT__\n"
        proc = subprocess.run(
            [VISION_BIN_PATH, "batch_scan"],
            input=payload,
            capture_output=True,
            text=True,
            timeout=900,
            check=True,
        )
        raw_lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if len(raw_lines) != len(chunk):
            raise AssertionError(
                f"Output batch_scan inatteso: attese {len(chunk)} righe, ottenute {len(raw_lines)}"
            )
        return [(p, json.loads(ln)) for p, ln in zip(chunk, raw_lines)]

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as pool:
        for pairs in pool.map(_run_chunk, chunks):
            for p, d in pairs:
                out[p] = d
    return out


class _OfflineMockCatalog(CatalogService):
    """
    CatalogService isolato dalla rete per test deterministici ad alto volume:
    combina il `SeedIndex` reale (`seed_music.json`) con l'archivio album indicizzati.
    """

    def __init__(self, cache: PersistentCache, extra_catalog: list[tuple[str, str, str]]) -> None:
        super().__init__(cache=cache, discogs_token="")
        self._extra_records: list[tuple[str, str, str, str, str]] = []
        for t, a, y in extra_catalog:
            self._extra_records.append((t, a, y, search_fold(t), search_fold(a)))
        # Aggiunge anche distrattori osservati in sessione reale
        for t, a, y in (
            ("Sacred Love", "Sting", "2003"),
            ("Gli amici di Roland", "Angelo Di Mario", "1975"),
            ("La boum", "Vladimir Cosma", "1980"),
            ("Coming Out", "The Manhattan Transfer", "1976"),
            ("The Manhattan Transfer", "The Manhattan Transfer", "1975"),
            ("Paris (Live)", "Supertramp", "1980"),
            ("Fuse", "Everything but the Girl", "2023"),
        ):
            self._extra_records.append((t, a, y, search_fold(t), search_fold(a)))

    def search_online(
        self,
        category: str,
        query: str,
        lang: str = "it",
        field_mode: str = "title",
        limit: int = 25,
        is_cancelled=None,
        context_author: str = "",
        context_title: str = "",
        on_chunk=None,
    ) -> list[Suggestion]:
        qf = search_fold(query)
        caf = search_fold(context_author)
        q_toks = [w for w in qf.split() if len(w) >= 2]
        a_toks = [w for w in caf.split() if len(w) >= 2]
        if not q_toks and not a_toks:
            return []

        scored: list[Suggestion] = []
        for t, a, y, tf, af in self._extra_records:
            if a_toks and not any(tok in af for tok in a_toks):
                continue
            combined = f"{tf} {af}"
            if qf == tf and (not caf or caf in af or af in caf):
                sc = 11000.0
            elif qf == combined.strip():
                sc = 10800.0
            elif q_toks and all(tok in combined for tok in q_toks):
                sc = 9200.0 - abs(len(tf) - len(qf))
            else:
                hits = sum(1 for tok in q_toks if tok in combined)
                if hits < max(1, (len(q_toks) + 1) // 2):
                    continue
                sc = 5000.0 + hits * 600.0
            scored.append(
                Suggestion(
                    title=t,
                    author=a,
                    year=y,
                    publisher="",
                    author_sort=surname_key(a, category),
                    work_id="",
                    category=category,
                    source="online",
                    popularity=5000,
                    publishers=[],
                    years=[y] if y else [],
                    from_isbn=False,
                    score=sc,
                )
            )
        scored.sort(key=lambda s: -s.score)
        return scored[:limit]


@unittest.skipUnless(sys.platform == "darwin" and os.path.isfile(VISION_BIN_PATH), "Richiede macOS e vision_scanner compilato")
class TestMega1000Covers(unittest.TestCase):
    """Suite di validazione su 1.000+ copertine reali, webcam 16:9, falsi positivi e casi live."""

    _tmp_dir_obj: tempfile.TemporaryDirectory | None = None
    _seed_entries: list[tuple[str, str, str, str]] = []
    _batch_results: dict[str, dict] = {}
    _service: RemoteScannerService | None = None
    _catalog: _OfflineMockCatalog | None = None

    @classmethod
    def setUpClass(cls) -> None:
        with open(SEED_COVERS_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f)
        items = payload.get("items") or []
        if len(items) < 1000:
            raise AssertionError(
                f"seed_covers.json contiene solo {len(items)} copertine (< 1000 richieste)"
            )

        suffix_map = _build_cache_path_map()
        entries: list[tuple[str, str, str, str]] = []
        for row in items:
            title, artist, year = str(row[0]), str(row[1]), str(row[2] or "")
            img_path = suffix_map.get(_safe_cache_suffix(title, artist))
            if img_path and os.path.isfile(img_path):
                entries.append((title, artist, year, img_path))

        if len(entries) < 1000:
            raise unittest.SkipTest(
                f"Trovate solo {len(entries)} immagini JPEG in cache (< 1000 richieste per il mega-test)"
            )

        cls._tmp_dir_obj = tempfile.TemporaryDirectory(prefix="archivio_mega1000_")
        cache = PersistentCache(os.path.join(cls._tmp_dir_obj.name, "test_cache.sqlite3"))
        cls._catalog = _OfflineMockCatalog(
            cache=cache,
            extra_catalog=[(t, a, y) for t, a, y, _ in entries],
        )
        cls._service = RemoteScannerService(
            cls._catalog,
            lambda: ("Vinili", "it", {"Libri": [], "CD": [], "Vinili": []}),
        )
        cls._seed_entries = entries[:1000]
        cls._batch_results = _run_batch_scan([e[3] for e in cls._seed_entries], workers=4)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls._tmp_dir_obj is not None:
            cls._tmp_dir_obj.cleanup()

    def test_part_a_1000_real_covers_end_to_end(self) -> None:
        """Parte A: riconoscimento end-to-end su 1.000 copertine reali (>= 98%)."""
        assert self._service is not None
        ok_count = 0
        failures: list[str] = []

        for title, artist, _yr, img_path in self._seed_entries:
            vision = self._batch_results[img_path]
            res = self._service.process_remote_action(
                {
                    "action": "scan_ocr_lines",
                    "category": "Vinili",
                    "barcodes": vision.get("barcodes") or [],
                    "lines": vision.get("lines") or [],
                    "visual_matches": vision.get("visual_matches") or [],
                    "auto_add": False,
                }
            )
            it = res.get("item") or {}
            if res.get("status") == "ok" and _titles_equivalent(
                title, artist, str(it.get("title") or ""), str(it.get("author") or "")
            ):
                ok_count += 1
            elif len(failures) < 15:
                failures.append(
                    f"{title} — {artist} -> status={res.get('status')} got=({it.get('title')} — {it.get('author')})"
                )

        acc = (ok_count / len(self._seed_entries)) * 100.0
        self.assertGreaterEqual(
            acc,
            98.0,
            f"Precisione mega-test 1000 copertine insufficiente: {ok_count}/{len(self._seed_entries)} ({acc:.2f}%). Esempi falliti: {failures}",
        )

    def test_part_b1_100_webcam_16_9_frames(self) -> None:
        """Parte B1: 100 copertine simulate in frame webcam 16:9 con sfondo, rotazione, shift e luminosità."""
        assert self._service is not None and self._tmp_dir_obj is not None
        rng = random.Random(20261115)
        sample_100 = self._seed_entries[::10][:100]
        frame_dir = os.path.join(self._tmp_dir_obj.name, "frames_16_9")
        os.makedirs(frame_dir, exist_ok=True)

        frame_paths: list[tuple[str, str, str]] = []
        for idx, (title, artist, _yr, img_path) in enumerate(sample_100):
            bg = Image.new("RGB", (1280, 720), (rng.randint(150, 195), rng.randint(120, 165), rng.randint(90, 130)))
            draw = ImageDraw.Draw(bg)
            for stripe_y in range(0, 720, 48):
                draw.line([(0, stripe_y), (1280, stripe_y + 18)], fill=(125, 95, 70), width=4)

            with Image.open(img_path) as cov_raw:
                cov = ImageOps.exif_transpose(cov_raw).convert("RGB")
            side = int(720 * rng.uniform(0.70, 0.86))
            cov = cov.resize((side, side), Image.Resampling.LANCZOS)
            cov = ImageEnhance.Brightness(cov).enhance(rng.uniform(0.88, 1.12))
            angle = rng.uniform(-3.5, 3.5)
            cov_rot = cov.convert("RGBA").rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)

            max_dx = int(1280 * 0.10)
            cx = 640 + rng.randint(-max_dx, max_dx)
            cy = 360 + rng.randint(-28, 28)
            x0 = max(0, cx - cov_rot.width // 2)
            y0 = max(0, cy - cov_rot.height // 2)
            bg.paste(cov_rot, (x0, y0), cov_rot)

            out_path = os.path.join(frame_dir, f"cam_{idx:03d}.jpg")
            bg.save(out_path, "JPEG", quality=88)
            frame_paths.append((title, artist, out_path))

        scanned = _run_batch_scan([p[2] for p in frame_paths], workers=4)
        ok_count = 0
        failures: list[str] = []
        for title, artist, fpath in frame_paths:
            vis = scanned[fpath]
            res = self._service.process_remote_action(
                {
                    "action": "scan_ocr_lines",
                    "category": "Vinili",
                    "barcodes": vis.get("barcodes") or [],
                    "lines": vis.get("lines") or [],
                    "visual_matches": vis.get("visual_matches") or [],
                    "auto_add": False,
                }
            )
            it = res.get("item") or {}
            if res.get("status") == "ok" and _titles_equivalent(
                title, artist, str(it.get("title") or ""), str(it.get("author") or "")
            ):
                ok_count += 1
            elif len(failures) < 10:
                failures.append(f"{title} — {artist} -> {res.get('status')} {it.get('title')} — {it.get('author')}")

        acc = (ok_count / len(frame_paths)) * 100.0
        self.assertGreaterEqual(
            acc,
            95.0,
            f"Precisione frame webcam 16:9 troppo bassa: {ok_count}/{len(frame_paths)} ({acc:.2f}%). Esempi: {failures}",
        )

    def test_part_b2_100_out_of_index_false_positive_rejection(self) -> None:
        """Parte B2: 100 copertine fuori indice con falso positivo visivo avversario -> vince l'OCR."""
        assert self._service is not None
        candidates_with_ocr: list[tuple[str, str, list[dict]]] = []
        _stop = {"the", "and", "for", "del", "dei", "con", "per", "una"}
        for title, artist, _yr, img_path in self._seed_entries:
            lines = self._batch_results[img_path].get("lines") or []
            _qs, _dp, _dc, toks, _lp = parse_cover_ocr_lines(lines)
            sig_t_toks = {w for w in search_fold(title).split() if len(w) >= 3 and w not in _stop}
            if len(toks) >= 2 and len(sig_t_toks & toks) >= 1:
                candidates_with_ocr.append((title, artist, lines))
            if len(candidates_with_ocr) >= 100:
                break

        self.assertGreaterEqual(len(candidates_with_ocr), 100)

        distractors = [
            ("Sacred Love", "Sting", "2003"),
            ("Fragile", "Yes", "1971"),
            ("Appetite for Destruction", "Guns N' Roses", "1987"),
            ("Ligabue", "Luciano Ligabue", "1990"),
        ]
        ok_count = 0
        failures: list[str] = []
        for idx, (title, artist, lines) in enumerate(candidates_with_ocr[:100]):
            d_t, d_a, d_y = distractors[idx % len(distractors)]
            if search_fold(d_t) == search_fold(title):
                d_t, d_a, d_y = distractors[(idx + 1) % len(distractors)]
            fake_vm = [
                {
                    "title": d_t,
                    "artist": d_a,
                    "year": d_y,
                    "mbid": "",
                    "distance": 0.49 + (idx % 12) * 0.01,
                }
            ]
            res = self._service.process_remote_action(
                {
                    "action": "scan_ocr_lines",
                    "category": "Vinili",
                    "barcodes": [],
                    "lines": lines,
                    "visual_matches": fake_vm,
                    "auto_add": False,
                }
            )
            it = res.get("item") or {}
            if res.get("status") == "ok" and _titles_equivalent(
                title, artist, str(it.get("title") or ""), str(it.get("author") or "")
            ):
                ok_count += 1
            elif len(failures) < 10:
                failures.append(f"{title} — {artist} -> {it.get('title')} — {it.get('author')}")

        acc = (ok_count / 100.0) * 100.0
        self.assertGreaterEqual(
            acc,
            95.0,
            f"Rifiuto falsi positivi fuori indice insufficiente: {ok_count}/100 ({acc:.2f}%). Esempi: {failures}",
        )

    def test_part_c_live_session_regressions(self) -> None:
        """Parte C: regressioni puntuali su tutti i bug emersi durante la sessione live."""
        assert self._service is not None and self._catalog is not None

        # 1) Pulizia suffissi commerciali [Import] / (Limited Edition - Green Coloured Vinyl)
        self.assertEqual(_clean_album_title_suffixes("Paris (Live) [Import]"), "Paris (Live)")
        self.assertEqual(
            _clean_album_title_suffixes("Fuse (Limited Edition - Green Coloured Vinyl)"),
            "Fuse",
        )

        # 2) Barcode 0082839670210 -> Amazon restituisce Paris (Live) [Import] senza etichetta/anno: arricchimento A&M / 1980
        cat_real = CatalogService(cache=self._catalog.cache, discogs_token="")
        mock_amazon_sug = Suggestion(
            title=_clean_album_title_suffixes("Paris (Live) [Import]"),
            author="Supertramp",
            year="",
            publisher="",
            author_sort="Supertramp",
            work_id="",
            category="Vinili",
            source="online",
            popularity=4200,
            publishers=[],
            years=[],
            from_isbn=True,
            score=9820.0,
        )
        with (
            mock.patch.object(cat_real, "_search_itunes_music", return_value=[]),
            mock.patch.object(cat_real, "_search_musicbrainz", return_value=[]),
            mock.patch.object(cat_real, "_search_ibs_isbn", return_value=[]),
            mock.patch.object(cat_real, "_search_amazon_isbn", return_value=[cat_real._apply_curated_profile(mock_amazon_sug)]),
        ):
            sugs = cat_real._search_music_parallel("Vinili", "0082839670210", "0082839670210", "title", 10, None)
        self.assertTrue(sugs)
        self.assertEqual(sugs[0].title, "Paris (Live)")
        self.assertEqual(sugs[0].publisher, "A&M")
        self.assertEqual(sugs[0].year, "1980")

        # 3) Barcode 0602448946560 (Fuse) + Buon Natale, Mondo!: rigetto rilasci non corrispondenti (Baktabak 1978, Gli amici di Roland)
        def fake_mb_get(url: str, **kwargs):
            if "musicbrainz.org" in url:
                if "Buon" in url or "buon" in url or "Di" in url:
                    return {
                        "releases": [
                            {
                                "title": "Gli amici di Roland",
                                "date": "1975",
                                "artist-credit": [{"name": "Roland", "artist": {"sort-name": "amici di Roland, Gli"}}],
                                "label-info": [{"label": {"name": "Fontana"}}],
                                "media": [{"format": '12" Vinyl'}],
                            }
                        ]
                    }
                return {
                    "releases": [
                        {
                            "title": "Fuse",
                            "date": "2023",
                            "artist-credit": [{"name": "Everything but the Girl", "artist": {"sort-name": "Everything but the Girl"}}],
                            "label-info": [{"label": {"name": "Virgin"}}],
                            "media": [{"format": '12" Vinyl'}],
                        },
                        {
                            "title": "Interview Picture Disc",
                            "date": "1978",
                            "artist-credit": [{"name": "Other Band", "artist": {"sort-name": "Other Band"}}],
                            "label-info": [{"label": {"name": "Baktabak"}}],
                            "media": [{"format": '12" Vinyl'}],
                        },
                    ]
                }
            return None

        with mock.patch.object(cat_real, "_http_get_json", side_effect=fake_mb_get):
            ed_bn = cat_real.fetch_editions("Vinili", "", "Buon Natale, Mondo!", "Angelo Di Mario")
            self.assertNotIn("Fontana", ed_bn.publishers)
            self.assertNotIn("1975", ed_bn.years)
            self.assertNotEqual(ed_bn.author_sort, "amici di Roland, Gli")

            ed_fuse = cat_real.fetch_editions(
                "Vinili", "", "Fuse (Limited Edition - Green Coloured Vinyl)", "Everything but the Girl"
            )
            self.assertEqual(ed_fuse.default_publisher, "Virgin")
            self.assertEqual(ed_fuse.default_year, "2023")
            self.assertNotIn("Baktabak", ed_fuse.publishers)
            self.assertNotIn("1978", ed_fuse.years)

        # 4) Casi OCR live: Manhattan Transfer, Coming Out (interleaved), La boum (soundtrack credit prefix)
        live_cases = [
            (
                [
                    {"text": "The", "height": 0.04, "y": 0.84, "confidence": 0.9},
                    {"text": "Manhattan", "height": 0.12, "y": 0.72, "confidence": 0.9},
                    {"text": "transfer", "height": 0.11, "y": 0.60, "confidence": 0.9},
                ],
                "The Manhattan Transfer",
                "The Manhattan Transfer",
            ),
            (
                [{"text": "The COMINE Manhattan OUT Transfer", "height": 0.09, "y": 0.72, "confidence": 0.9}],
                "Coming Out",
                "The Manhattan Transfer",
            ),
            (
                [
                    {"text": "BANDE ORIGINALE DU FILM", "height": 0.06, "y": 0.86, "confidence": 0.9},
                    {"text": "inclus", "height": 0.03, "y": 0.78, "confidence": 0.9},
                    {"text": "REALITY", "height": 0.05, "y": 0.73, "confidence": 0.9},
                    {"text": "Musique de Vladimir COSMA", "height": 0.04, "y": 0.14, "confidence": 0.9},
                ],
                "La boum",
                "Vladimir Cosma",
            ),
        ]
        # Aggiunge Reality al mock solo come track hint tramite titolo reale di Vladimir Cosma
        self._catalog._extra_records.append(("La boum", "Vladimir Cosma", "1980", "la boum", "vladimir cosma"))
        for lines_case, exp_t, exp_a in live_cases:
            res = self._service.process_remote_action(
                {
                    "action": "scan_ocr_lines",
                    "category": "Vinili",
                    "barcodes": [],
                    "lines": lines_case,
                    "visual_matches": [{"title": "Sacred Love", "artist": "Sting", "year": "2003", "distance": 0.55}],
                    "auto_add": False,
                }
            )
            it = res.get("item") or {}
            self.assertEqual(res.get("status"), "ok")
            self.assertTrue(
                _titles_equivalent(exp_t, exp_a, str(it.get("title") or ""), str(it.get("author") or "")),
                f"Atteso {exp_t} — {exp_a}, ottenuto {it.get('title')} — {it.get('author')}",
            )

        # 5) Test sulle foto reali catturate durante la sessione se presenti in /tmp
        temp_dir = tempfile.gettempdir()
        for fname, exp_t, exp_a in (
            ("test_live_tmt.jpg", "The Manhattan Transfer", "The Manhattan Transfer"),
            ("test_live_coming_out.jpg", "Coming Out", "The Manhattan Transfer"),
            ("test_live_la_boum.jpg", "La boum", "Vladimir Cosma"),
        ):
            fpath = os.path.join(temp_dir, fname)
            if os.path.isfile(fpath):
                vis = _run_batch_scan([fpath], workers=1)[fpath]
                res = self._service.process_remote_action(
                    {
                        "action": "scan_ocr_lines",
                        "category": "Vinili",
                        "barcodes": vis.get("barcodes") or [],
                        "lines": vis.get("lines") or [],
                        "visual_matches": vis.get("visual_matches") or [],
                        "auto_add": False,
                    }
                )
                it = res.get("item") or {}
                self.assertEqual(res.get("status"), "ok", f"{fname}: {res}")
                self.assertTrue(
                    _titles_equivalent(exp_t, exp_a, str(it.get("title") or ""), str(it.get("author") or "")),
                    f"{fname}: atteso {exp_t} — {exp_a}, ottenuto {it.get('title')} — {it.get('author')}",
                )

        # 6) Preferenza anno originale su Vinili (vs remaster/ristampa) e conservazione anno su barcode
        year_cases = [
            ("A Day at the Races", "Queen", "2008", ["1976", "2008", "2011"], False, "1976"),
            ("Appetite for Destruction", "Guns N' Roses", "2016", ["1987", "2016", "2022"], False, "1987"),
            ("Amnesiac", "Radiohead", "2016", ["2001", "2016"], False, "2001"),
            ("Atom Heart Mother", "Pink Floyd", "2011", ["1970", "2011"], False, "1970"),
            ("Fuse", "Everything but the Girl", "2023", ["1999", "2023"], True, "2023"),
        ]
        for t, a, y, yrs, from_bc, exp_y in year_cases:
            sug = Suggestion(
                title=t,
                author=a,
                year=y,
                publisher="Virgin" if from_bc else "EMI",
                author_sort=surname_key(a, "Vinili"),
                work_id="",
                category="Vinili",
                source="online",
                popularity=9000,
                publishers=[],
                years=yrs,
                from_isbn=from_bc,
                score=9500.0,
            )
            it_obj = self._service._suggestion_to_item(sug, "Vinili", "0602448946560" if from_bc else "", "", "it")
            self.assertEqual(it_obj.year, exp_y, f"{t}: atteso anno {exp_y}, ottenuto {it_obj.year}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
