"""
Unit test per app/archivio/catalog.py.
Copre: inizializzazione del CatalogService, caricamento dei cataloghi seed locali (libri e musica),
algoritmo di punteggio e ranking (score_suggestion), filtraggio collane/collezioni per editore
e separazione rigida tra collane editoriali e collane musicali specifiche per artista.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import unittest

if "ARCHIVIO_SUPPORT_DIR" not in os.environ:
    _TEST_SUPPORT_DIR = tempfile.mkdtemp(prefix="archivio_test_cfg_")
    os.environ["ARCHIVIO_SUPPORT_DIR"] = _TEST_SUPPORT_DIR
    atexit.register(shutil.rmtree, _TEST_SUPPORT_DIR, ignore_errors=True)

from archivio.catalog import (
    CatalogService,
    Suggestion,
    score_suggestion,
)
from archivio.textutil import search_fold, tokens


class TestCatalogScoring(unittest.TestCase):
    def test_exact_title_match_has_highest_score(self):
        q = "il nome della rosa"
        q_tokens = tokens(q)
        score_exact = score_suggestion(
            query_norm=search_fold(q),
            query_tokens=q_tokens,
            title_norm=search_fold("Il nome della rosa"),
            author_norm=search_fold("Umberto Eco"),
            popularity=100,
            field_mode="title",
        )
        score_partial = score_suggestion(
            query_norm=search_fold(q),
            query_tokens=q_tokens,
            title_norm=search_fold("La rosa purpurea del Cairo"),
            author_norm=search_fold("Woody Allen"),
            popularity=50,
            field_mode="title",
        )
        self.assertGreater(score_exact, score_partial)
        self.assertGreater(score_exact, 80.0)

    def test_combined_title_author_query(self):
        q = "umberto eco il nome della rosa"
        q_tokens = tokens(q)
        score = score_suggestion(
            query_norm=search_fold(q),
            query_tokens=q_tokens,
            title_norm=search_fold("Il nome della rosa"),
            author_norm=search_fold("Umberto Eco"),
            popularity=100,
            field_mode="both",
        )
        self.assertGreater(score, 70.0)

    def test_music_artist_scoring(self):
        q = "pink floyd the wall"
        q_tokens = tokens(q)
        score_pink = score_suggestion(
            query_norm=search_fold(q),
            query_tokens=q_tokens,
            title_norm=search_fold("The Wall"),
            author_norm=search_fold("Pink Floyd"),
            popularity=100,
            field_mode="both",
        )
        score_other = score_suggestion(
            query_norm=search_fold(q),
            query_tokens=q_tokens,
            title_norm=search_fold("The Wall"),
            author_norm=search_fold("Kansas"),
            popularity=20,
            field_mode="both",
        )
        self.assertGreater(score_pink, score_other)


class TestCatalogServiceSeedAndCollections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = CatalogService()

    @classmethod
    def tearDownClass(cls):
        cls.service.close()

    def test_seed_catalogs_are_loaded(self):
        # Il servizio deve avere caricato in memoria i cataloghi locali seed di libri e musica
        self.service.seed.ensure_loaded()
        self.assertTrue(hasattr(self.service.seed, "books"))
        self.assertTrue(hasattr(self.service.seed, "music"))
        self.assertGreater(len(self.service.seed.books), 100)
        self.assertGreater(len(self.service.seed.music), 100)

    def test_book_collections_for_publisher(self):
        # Per gli editori di libri vengono restituite le collane note dell'editore
        mondadori_colls = self.service.collections_for_publisher("Mondadori", "Libri")
        self.assertTrue(any("Oscar" in c for c in mondadori_colls))

        adelphi_colls = self.service.collections_for_publisher("Adelphi", "Libri")
        self.assertTrue(any("Adelphi" in c or "Piccola" in c for c in adelphi_colls))

        einaudi_colls = self.service.collections_for_publisher("Einaudi", "Libri")
        self.assertTrue(any("Coralli" in c or "Stile libero" in c for c in einaudi_colls))

    def test_music_collections_never_return_generic_collections(self):
        # Per la musica (CD o Vinili), non devono MAI comparire collane generiche indiscriminate
        colls = self.service.collections_for_publisher("Virgin", "CD", author="Cesare Cremonini")
        self.assertNotIn("Original Album Classics", colls)
        self.assertNotIn("SHM-CD Japanese Edition", colls)
        self.assertNotIn("DeAgostini", colls)

        colls_xl = self.service.collections_for_publisher("XL Recordings", "CD", author="The Smile")
        self.assertNotIn("Original Album Classics", colls_xl)
        self.assertNotIn("DeAgostini", colls_xl)

    def test_local_search_finds_seed_items(self):
        # Ricerca locale immediata di un grande classico nei semi offline
        self.service.seed.ensure_loaded()
        results = self.service.search_local("Libri", "1984", archive_items=[], limit=5)
        self.assertGreaterEqual(len(results), 1)
        titles = [r.title for r in results]
        self.assertTrue(any("1984" in t for t in titles))

        music_results = self.service.search_local("CD", "Dark Side", archive_items=[], limit=5)
        self.assertGreaterEqual(len(music_results), 1)
        self.assertTrue(any("Dark Side" in r.title for r in music_results))

    def test_persistent_cache_search_cached_items_matches_v11_keys(self):
        import os
        import tempfile
        from archivio.catalog import PersistentCache

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "cache.sqlite")
            cache = PersistentCache(db_file=db_path)
            try:
                cache.set(
                    "search:v11:CD:vasco rossi",
                    {"items": [{"title": "Platinum Collection", "author": "Vasco Rossi", "publisher": "EMI", "year": "2006"}]},
                )
                found = cache.search_cached_items("CD", ["vasco", "rossi"])
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0]["title"], "Platinum Collection")
            finally:
                cache.close()

    def test_persistent_cache_clear_removes_wal_and_resets_data(self):
        import os
        import tempfile
        from archivio.catalog import CatalogService, PersistentCache

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "cache.sqlite")
            cache = PersistentCache(db_file=db_path)
            svc = CatalogService(cache=cache)
            try:
                cache.set(
                    "search:v11:Libri:it:title::test chiaro",
                    {"items": [{"title": "Opera Test", "author": "Autore Test", "publisher": "Einaudi", "year": "2020"}]},
                )
                self.assertIsNotNone(cache.get("search:v11:Libri:it:title::test chiaro"))
                cache.set(
                    "editions:v10:CD:mbid-test-123",
                    {"publishers": ["EMI"], "years": ["2011"], "default_publisher": "EMI", "default_year": "2011"},
                )
                ed_map = svc._cached_editions_map("CD", ["mbid-test-123"])
                self.assertIn("mbid-test-123", ed_map)
                self.assertEqual(ed_map["mbid-test-123"]["default_publisher"], "EMI")

                cache.clear()
                self.assertIsNone(cache.get("search:v11:Libri:it:title::test chiaro"))
                self.assertEqual(svc._cached_editions_map("CD", ["mbid-test-123"]), {})
                cache.set("after:clear", {"ok": True})
                self.assertEqual(cache.get("after:clear"), {"ok": True})
            finally:
                svc.close()

    def test_single_char_noise_not_matched_in_score_suggestion(self):
        from archivio.catalog import score_suggestion

        self.assertLess(
            score_suggestion(
                "il dono",
                ["il", "dono"],
                "o",
                "fcukers",
                29593,
                field_mode="title",
                allow_fuzzy=True,
                is_catalog=True,
            ),
            0.0,
        )
        self.assertLess(
            score_suggestion(
                "il dono",
                ["il", "dono"],
                "l",
                "steve hillage",
                12000,
                field_mode="title",
                allow_fuzzy=True,
                is_catalog=False,
            ),
            0.0,
        )

    def test_local_search_love_you_live_and_italian_classics(self):
        self.service.seed.ensure_loaded()
        res_ctx = self.service.search_local(
            "Vinili", "love you live", archive_items=[], context_author="the Rolling Stones", limit=8
        )
        self.assertTrue(
            any(r.title == "Love You Live" and r.author == "The Rolling Stones" for r in res_ctx)
        )

        res_combined = self.service.search_local(
            "Vinili", "love you live rolling stones", archive_items=[], limit=8
        )
        self.assertTrue(
            any(r.title == "Love You Live" and r.author == "The Rolling Stones" for r in res_combined)
        )

        res_morandi = self.service.search_local("Vinili", "immagine italiana", archive_items=[], limit=8)
        self.assertTrue(
            any("Immagine italiana" in r.title and "Morandi" in r.author for r in res_morandi)
        )

        res_stadio = self.service.search_local("Vinili", "la faccia delle donne", archive_items=[], limit=8)
        self.assertTrue(
            any("La faccia delle donne" in r.title and "Stadio" in r.author for r in res_stadio)
        )

        res_dono = self.service.search_local("Vinili", "Il dono", archive_items=[], limit=10)
        self.assertFalse(any(r.author == "Fcukers" for r in res_dono))

    def test_search_local_contextual_no_leak_and_reads_cached_items(self):
        import os
        import tempfile
        from archivio.catalog import CatalogService, PersistentCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PersistentCache(db_file=os.path.join(tmpdir, "cache.sqlite"))
            svc = CatalogService(cache=cache)
            try:
                svc.seed.ensure_loaded()
                # Cache salvata in precedenza sotto chiave non contestuale viene trovata in modalità contestuale
                cache.set(
                    "search:v11:Vinili:it:title::some girls",
                    {
                        "items": [
                            {
                                "title": "Some Girls (Deluxe)",
                                "author": "The Rolling Stones",
                                "year": "1978",
                                "publisher": "Rolling Stones Records",
                                "popularity": 15000,
                            }
                        ]
                    },
                )
                cached_ctx = svc.search_local(
                    "Vinili", "some girls", archive_items=[], context_author="the Rolling Stones", limit=8
                )
                self.assertTrue(any("Some Girls" in r.title for r in cached_ctx))

                # Un titolo inesistente per un artista non deve far trapelare altri artisti ("The Neighbourhood")
                empty_ctx = svc.search_local(
                    "Vinili", "titolo totalmente inesistente xyz", archive_items=[], context_author="the Rolling Stones", limit=8
                )
                self.assertEqual(empty_ctx, [])
            finally:
                svc.close()

    def test_search_online_discards_and_refreshes_stale_single_char_cache(self):
        import os
        import tempfile
        from unittest.mock import patch
        from archivio.catalog import CatalogService, PersistentCache, Suggestion

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PersistentCache(db_file=os.path.join(tmpdir, "cache.sqlite"))
            svc = CatalogService(cache=cache)
            try:
                cache.set(
                    "search:v11:Vinili:it:title::il dono",
                    {
                        "items": [
                            {
                                "title": "L",
                                "author": "Steve Hillage",
                                "year": "1976",
                                "publisher": "Virgin",
                                "popularity": 9000,
                                "score": 1461.0,
                            }
                        ]
                    },
                )
                fresh = [
                    Suggestion(
                        title="Il dono",
                        author="Renato Zero",
                        year="2005",
                        publisher="Tattica",
                        category="Vinili",
                        source="online",
                        popularity=8200,
                        score=12000.0,
                    )
                ]
                with patch.object(svc, "_search_music_parallel", return_value=fresh) as mock_mp:
                    got = svc.search_online("Vinili", "Il dono", lang="it", field_mode="title", limit=10)
                    mock_mp.assert_called_once()
                    self.assertEqual(len(got), 1)
                    self.assertEqual(got[0].author, "Renato Zero")
            finally:
                svc.close()

    def test_search_musicbrainz_preserves_folded_tokens_and_cancel_exits_fast(self):
        import os
        import tempfile
        import time
        from unittest.mock import patch
        from archivio.catalog import CatalogService, PersistentCache

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = PersistentCache(db_file=os.path.join(tmpdir, "cache.sqlite"))
            svc = CatalogService(cache=cache)
            try:
                svc._last_mb_call = time.time()
                t0 = time.monotonic()
                res_cancel = svc._http_get_json(
                    "https://musicbrainz.org/ws/2/release-group/?query=test&fmt=json",
                    is_musicbrainz=True,
                    is_cancelled=lambda: True,
                )
                self.assertIsNone(res_cancel)
                self.assertLess(time.monotonic() - t0, 0.25)

                mb_payload = {
                    "release-groups": [
                        {
                            "id": "09291c11-4b3d-31a5-bcbf-9b03ce365174",
                            "title": "Love You Live",
                            "first-release-date": "1977-09-20",
                            "count": 12,
                            "artist-credit": [{"name": "The Rolling Stones", "artist": {"sort-name": "Rolling Stones, The"}}],
                        }
                    ]
                }
                with patch.object(svc, "_http_get_json", return_value=mb_payload), patch.object(
                    svc, "_http_post_json", return_value=[]
                ):
                    hits = svc._search_musicbrainz(
                        category="Vinili",
                        query="Love You Live",
                        q_norm="love you live",
                        field_mode="title",
                        limit=10,
                        is_cancelled=None,
                        context_author="the Rolling Stones",
                        ctx_author_norm="the rolling stones",
                    )
                    self.assertEqual(len(hits), 1)
                    self.assertEqual(hits[0].title, "Love You Live")
                    self.assertEqual(hits[0].author, "The Rolling Stones")
            finally:
                svc.close()


if __name__ == "__main__":
    unittest.main()
