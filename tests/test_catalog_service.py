"""
Unit test per app/archivio/catalog.py.
Copre: inizializzazione del CatalogService, caricamento dei cataloghi seed locali (libri e musica),
algoritmo di punteggio e ranking (score_suggestion), filtraggio collane/collezioni per editore
e separazione rigida tra collane editoriali e collane musicali specifiche per artista.
"""
from __future__ import annotations

import unittest

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
                    "editions:v9:CD:mbid-test-123",
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


if __name__ == "__main__":
    unittest.main()
