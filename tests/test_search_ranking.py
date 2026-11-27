#!/usr/bin/env python3
"""
Test di regressione per la rilevanza e il ranking delle ricerche nel catalogo.
Verifica che le query testuali specifiche non vengano scavalcate da bonus di popolarità
di libri celebri che condividono solo articoli/stopword ("il", "re", "dell").
"""
import os
import tempfile
import unittest

from archivio.catalog import CatalogService, PersistentCache
from archivio.store import Item


class TestSearchRanking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.cat = CatalogService(cache=PersistentCache(db_file=os.path.join(cls._tmp.name, "test_cache.sqlite")))
        cls.cat.seed.ensure_loaded()

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()
        cls._tmp.cleanup()

    def test_multi_word_query_ranks_exact_book_first(self):
        items = [
            Item(title="Il re Mida dell'estate", author="Edoardo Vianello", publisher="RCA", year="1963", category="Libri"),
            Item(title="Re Lear", author="William Shakespeare", publisher="Mondadori", year="1606", category="Libri"),
            Item(title="La sacra Bibbia", author="Autori Vari", publisher="CEI", year="2008", category="Libri"),
        ]
        hits = self.cat.search_local("Libri", "edoardo vianello il re mida", items, limit=10)
        self.assertTrue(len(hits) > 0, "Dovrebbe trovare almeno un risultato")
        top = hits[0]
        self.assertIn("mida", top.title.lower())
        for h in hits:
            self.assertNotIn("sacra bibbia", h.title.lower())
            self.assertNotEqual(h.title.lower(), "re lear")

    def test_subtitle_phrase_ranks_exact_book_first(self):
        items = [
            Item(title="Il re Mida dell'estate", author="Edoardo Vianello", publisher="RCA", year="1963", category="Libri"),
            Item(title="Il richiamo della foresta", author="Jack London", publisher="Mondadori", year="1903", category="Libri"),
        ]
        hits = self.cat.search_local("Libri", "il re mida dell'estate", items, limit=10)
        self.assertTrue(len(hits) > 0, "Dovrebbe trovare almeno un risultato")
        top = hits[0]
        self.assertIn("mida", top.title.lower())
        for h in hits:
            self.assertNotIn("sacra bibbia", h.title.lower())
            self.assertNotIn("richiamo della foresta", h.title.lower())

    def test_phrase_query_does_not_leak_unrelated_popular_books(self):
        items = [
            Item(title="Il secondo senso", author="Autore Test", publisher="Mondadori", year="1990", category="Libri"),
            Item(title="Il grande Gatsby", author="F. Scott Fitzgerald", publisher="Mondadori", year="1925", category="Libri"),
            Item(title="Il mastino dei Baskerville", author="Arthur Conan Doyle", publisher="Mondadori", year="1902", category="Libri"),
        ]
        hits = self.cat.search_local("Libri", "il secondo senso", items, limit=10)
        self.assertTrue(len(hits) > 0, "Dovrebbe trovare risultati")
        top = hits[0]
        self.assertIn("secondo senso", top.title.lower())
        for h in hits:
            self.assertNotIn("sacra bibbia", h.title.lower())
            self.assertNotIn("mastino dei baskerville", h.title.lower())
            self.assertNotIn("grande gatsby", h.title.lower())

    def test_typo_tolerance_intact(self):
        # Refuso 'umberto ecco' -> Eco
        hits_eco = self.cat.search_local("Libri", "umberto ecco", [], limit=5)
        self.assertTrue(any("eco" in h.author.lower() for h in hits_eco))

        # Refuso 'punk floyd' -> Pink Floyd
        hits_pf = self.cat.search_local("CD", "punk floyd", [], limit=5)
        self.assertTrue(any("pink floyd" in h.author.lower() for h in hits_pf))

    def test_cached_search_with_approximate_title_matches_subtitle_diluted_book(self):
        cache_key = "search:v11:Libri:it:title::guccini aime"
        self.cat.cache.set(
            cache_key,
            {
                "items": [
                    {
                        "title": "Tra i castagni dell'Appennino",
                        "author": "Francesco Guccini, Marco Aime",
                        "publisher": "Rizzoli",
                        "year": "2020",
                        "category": "Libri",
                        "source": "online",
                        "from_isbn": False,
                        "score": 85.0,
                    }
                ]
            },
        )
        hits = self.cat.search_local("Libri", "tra i castagni dell'appennino", [], limit=5)
        self.assertTrue(len(hits) > 0, "Dovrebbe trovare il libro precedentemente cercato per parole chiave")
        top = hits[0]
        self.assertIn("castagni", top.title.lower())
        self.assertIn("appennino", top.title.lower())
        self.assertIn("aime", top.author.lower())


    def test_music_typo_and_multiword_scoring(self):
        """Refuso 'platinun collection vasco rossi' deve abbinare 'The Platinum Collection' di Vasco Rossi con punteggio elevato."""
        from archivio.catalog import score_suggestion, search_fold
        q = "platinun collection vasco rossi"
        q_norm = search_fold(q)
        q_toks = q_norm.split()
        t_norm = search_fold("The Platinum Collection")
        a_norm = search_fold("Vasco Rossi")
        sc = score_suggestion(q_norm, q_toks, t_norm, a_norm, popularity=4000, field_mode="title", allow_fuzzy=True)
        self.assertGreater(sc, 5000.0)

        # Senza refuso il punteggio deve essere ancora più alto
        q_clean = "platinum collection vasco rossi"
        sc_clean = score_suggestion(search_fold(q_clean), q_clean.split(), t_norm, a_norm, popularity=4000, field_mode="title", allow_fuzzy=True)
        self.assertGreater(sc_clean, 12000.0)

    def test_interrupted_title_with_publisher_and_extra_word(self):
        # Titolo interrotto con sigla editore finale (sia con Autore fissato sia senza)
        hits_ctx_cde = self.cat.search_local(
            "Libri", "Ossi di sep cde", [], lang="it", field_mode="title", context_author="Eugenio Montale", limit=5
        )
        self.assertTrue(hits_ctx_cde)
        self.assertIn("ossi di seppia", hits_ctx_cde[0].title.lower())

        hits_no_ctx_cde = self.cat.search_local("Libri", "Ossi di sep cde", [], lang="it", field_mode="title", limit=5)
        self.assertTrue(hits_no_ctx_cde)
        self.assertIn("ossi di seppia", hits_no_ctx_cde[0].title.lower())

        # Titolo interrotto con parola extra arbitraria non riconosciuta
        hits_ctx_extra = self.cat.search_local(
            "Libri", "Ossi di sep pippo", [], lang="it", field_mode="title", context_author="Eugenio Montale", limit=5
        )
        self.assertTrue(hits_ctx_extra)
        self.assertIn("ossi di seppia", hits_ctx_extra[0].title.lower())

        hits_no_ctx_extra = self.cat.search_local("Libri", "Ossi di sep pippo", [], lang="it", field_mode="title", limit=5)
        self.assertTrue(hits_no_ctx_extra)
        self.assertIn("ossi di seppia", hits_no_ctx_extra[0].title.lower())

        hits_bompiani = self.cat.search_local(
            "Libri", "Il nome della ros bompiani", [], lang="it", field_mode="title", limit=5
        )
        self.assertTrue(hits_bompiani)
        self.assertIn("nome della rosa", hits_bompiani[0].title.lower())

        # Anti-leak: query totalmente estranea con contesto autore deve restare vuota
        empty_ctx = self.cat.search_local(
            "Libri", "titolo totalmente inesistente xyz", [], lang="it", field_mode="title", context_author="Eugenio Montale", limit=5
        )
        self.assertEqual(empty_ctx, [])

    def test_cached_items_relaxed_fallback(self):
        self.cat.cache.set(
            "search:v11:Libri:it:title::ossi di seppia",
            {
                "items": [
                    {
                        "title": "Ossi di seppia",
                        "author": "Eugenio Montale",
                        "publisher": "Mondadori",
                        "year": "2024",
                        "popularity": 4000,
                    }
                ]
            },
        )
        found = self.cat.cache.search_cached_items("Libri", ["ossi", "di", "sep", "cde"])
        self.assertTrue(any(it.get("title") == "Ossi di seppia" for it in found))
        hits = self.cat.search_local("Libri", "Ossi di sep cde", [], lang="it", field_mode="title", limit=5)
        self.assertTrue(hits)
        self.assertIn("ossi di seppia", hits[0].title.lower())
        self.assertTrue(any(h.title == "Ossi di seppia" and h.source == "online" for h in hits))


if __name__ == "__main__":
    unittest.main()
