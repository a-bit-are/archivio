"""
Unit test approfonditi per app/archivio/textutil.py.
Copre: normalizzazione diacritici, chiavi di ricerca, ordinamento per cognome
(italiano, internazionale, aristocratico, band, mononimi), titoli con articoli
e numeri romani, matching di coerenza autore/chiave e collane.
"""
from __future__ import annotations

import unittest

from archivio.textutil import (
    _parse_vol_number,
    collection_sort_parts,
    fold,
    search_fold,
    sort_key_matches_author,
    surname_key,
    title_sort_key,
    tokens,
    year_of,
)


class TestTextUtilFold(unittest.TestCase):
    def test_fold_accents_and_casing(self):
        self.assertEqual(fold("È un'emozione"), "e un'emozione")
        self.assertEqual(fold("Caffè, città, più, perché"), "caffe, citta, piu, perche")
        self.assertEqual(fold("Crêuza de mä"), "creuza de ma")
        self.assertEqual(fold("García Márquez"), "garcia marquez")
        self.assertEqual(fold("Mötley Crüe"), "motley crue")
        self.assertEqual(fold("Björk"), "bjork")
        self.assertEqual(fold("Święto"), "swieto")

    def test_fold_typographic_quotes(self):
        self.assertEqual(fold("L’infinito"), "l'infinito")
        self.assertEqual(fold("‘La voce del silenzio’"), "'la voce del silenzio'")
        self.assertEqual(fold("`Backtick`"), "'backtick'")

    def test_fold_empty_and_spaces(self):
        self.assertEqual(fold(""), "")
        self.assertEqual(fold("   "), "")
        self.assertEqual(fold("   Test   "), "test")

    def test_search_fold_punctuation_and_spaces(self):
        self.assertEqual(search_fold("L'arte della guerra!"), "l arte della guerra")
        self.assertEqual(search_fold("Rock & Roll (1975) - Remastered"), "rock roll 1975 remastered")
        self.assertEqual(search_fold("Mario, Rossi: Un libro;"), "mario rossi un libro")
        self.assertEqual(search_fold("    Spazi     multipli   "), "spazi multipli")

    def test_tokens(self):
        self.assertEqual(tokens("Il nome della rosa"), ["il", "nome", "della", "rosa"])
        self.assertEqual(tokens("Pink Floyd - The Dark Side of the Moon"), [
            "pink", "floyd", "the", "dark", "side", "of", "the", "moon"
        ])
        self.assertEqual(tokens(""), [])


class TestTitleSortKey(unittest.TestCase):
    def test_strip_italian_articles(self):
        self.assertEqual(title_sort_key("Il nome della rosa"), "nome della rosa")
        self.assertEqual(title_sort_key("La solitudine dei numeri primi"), "solitudine dei numeri primi")
        self.assertEqual(title_sort_key("Lo strano caso del dottor Jekyll"), "strano caso del dottor jekyll")
        self.assertEqual(title_sort_key("I promessi sposi"), "promessi sposi")
        self.assertEqual(title_sort_key("Gli indifferenti"), "indifferenti")
        self.assertEqual(title_sort_key("Le città invisibili"), "citta invisibili")
        self.assertEqual(title_sort_key("Un cappello pieno di ciliege"), "cappello pieno di ciliege")
        self.assertEqual(title_sort_key("Uno studio in rosso"), "studio in rosso")
        self.assertEqual(title_sort_key("Una stanza tutta per sé"), "stanza tutta per se")

    def test_strip_elided_articles(self):
        self.assertEqual(title_sort_key("L'ombra del vento"), "ombra del vento")
        self.assertEqual(title_sort_key("L’insostenibile leggerezza dell'essere"), "insostenibile leggerezza dell essere")
        self.assertEqual(title_sort_key("Un'estate con Montaigne"), "estate con montaigne")

    def test_strip_international_articles(self):
        self.assertEqual(title_sort_key("The Catcher in the Rye"), "catcher in the rye")
        self.assertEqual(title_sort_key("A Moveable Feast"), "moveable feast")
        self.assertEqual(title_sort_key("An American Tragedy"), "american tragedy")
        self.assertEqual(title_sort_key("El laberinto de la soledad"), "laberinto de la soledad")
        self.assertEqual(title_sort_key("Der Steppenwolf"), "steppenwolf")
        self.assertEqual(title_sort_key("Die Verwandlung"), "verwandlung")

    def test_punctuation_stripping_and_volume_normalization(self):
        self.assertEqual(title_sort_key("«I promessi sposi»"), "promessi sposi")
        self.assertEqual(title_sort_key("\"The Great Gatsby\""), "great gatsby")
        # Volume numbers formatted with 6 digits for natural numerical sorting
        self.assertEqual(title_sort_key("Storia d'Italia Vol. 2"), "storia d italia vol 000002")
        self.assertEqual(title_sort_key("Enciclopedia Parte IV"), "enciclopedia parte 000004")
        self.assertEqual(title_sort_key("Trattato Tomo I"), "trattato tomo 000001")


class TestSurnameKeyBooks(unittest.TestCase):
    def test_simple_names(self):
        self.assertEqual(surname_key("Umberto Eco"), "Eco, Umberto")
        self.assertEqual(surname_key("Italo Calvino"), "Calvino, Italo")
        self.assertEqual(surname_key("Stephen King"), "King, Stephen")
        self.assertEqual(surname_key("Jane Austen"), "Austen, Jane")

    def test_multi_word_given_names(self):
        self.assertEqual(surname_key("Pier Paolo Pasolini"), "Pasolini, Pier Paolo")
        self.assertEqual(surname_key("Carlo Emilio Gadda"), "Gadda, Carlo Emilio")
        self.assertEqual(surname_key("Arthur Conan Doyle"), "Conan Doyle, Arthur")

    def test_particle_and_compound_prefixes(self):
        self.assertEqual(surname_key("Antonio De Sanctis"), "De Sanctis, Antonio")
        self.assertEqual(surname_key("Ludwig van Beethoven"), "van Beethoven, Ludwig")
        self.assertEqual(surname_key("Simone de Beauvoir"), "de Beauvoir, Simone")
        self.assertEqual(surname_key("Giuseppe Tomasi di Lampedusa"), "Tomasi di Lampedusa, Giuseppe")
        self.assertEqual(surname_key("Gabriele D'Annunzio"), "D'Annunzio, Gabriele")
        self.assertEqual(surname_key("Jean de La Fontaine"), "de La Fontaine, Jean")
        self.assertEqual(surname_key("Vincent van Gogh"), "van Gogh, Vincent")

    def test_already_inverted_names(self):
        self.assertEqual(surname_key("Eco, Umberto"), "Eco, Umberto")
        self.assertEqual(surname_key("Calvino, Italo"), "Calvino, Italo")
        self.assertEqual(surname_key("De Sanctis, Antonio"), "De Sanctis, Antonio")

    def test_hispanic_double_surnames(self):
        self.assertEqual(surname_key("Gabriel García Márquez"), "García Márquez, Gabriel")
        self.assertEqual(surname_key("Mario Vargas Llosa"), "Vargas Llosa, Mario")
        self.assertEqual(surname_key("Jorge Luis Borges"), "Borges, Jorge Luis")

    def test_honorifics_and_suffixes(self):
        self.assertEqual(surname_key("Padre Pio"), "Pio, Padre")
        self.assertEqual(surname_key("Don Lorenzo Milani"), "Milani, Lorenzo (Don)")
        self.assertEqual(surname_key("Martin Luther King Jr."), "King Jr., Martin Luther")

    def test_mononyms_and_single_words(self):
        self.assertEqual(surname_key("Dante"), "Dante")
        self.assertEqual(surname_key("Omero"), "Omero")
        self.assertEqual(surname_key("Platone"), "Platone")
        self.assertEqual(surname_key(""), "")


class TestSurnameKeyMusic(unittest.TestCase):
    def test_bands_with_articles(self):
        self.assertEqual(surname_key("The Beatles", is_music=True), "Beatles, The")
        self.assertEqual(surname_key("The Rolling Stones", is_music=True), "Rolling Stones, The")
        self.assertEqual(surname_key("The Cure", is_music=True), "Cure, The")
        self.assertEqual(surname_key("I Nomadi", is_music=True), "Nomadi, I")
        self.assertEqual(surname_key("Il Teatro degli Orrori", is_music=True), "Teatro degli Orrori, Il")

    def test_known_bands_without_articles(self):
        self.assertEqual(surname_key("Pink Floyd", is_music=True), "Pink Floyd")
        self.assertEqual(surname_key("Led Zeppelin", is_music=True), "Led Zeppelin")
        self.assertEqual(surname_key("Dire Straits", is_music=True), "Dire Straits")
        self.assertEqual(surname_key("Deep Purple", is_music=True), "Deep Purple")
        self.assertEqual(surname_key("Elio e le Storie Tese", is_music=True), "Elio e le Storie Tese")
        self.assertEqual(surname_key("Premiata Forneria Marconi", is_music=True), "Premiata Forneria Marconi")
        self.assertEqual(surname_key("Daft Punk", is_music=True), "Daft Punk")

    def test_music_solo_artists(self):
        self.assertEqual(surname_key("Fabrizio De André", is_music=True), "De André, Fabrizio")
        self.assertEqual(surname_key("Lucio Battisti", is_music=True), "Battisti, Lucio")
        self.assertEqual(surname_key("David Bowie", is_music=True), "Bowie, David")
        self.assertEqual(surname_key("Miles Davis", is_music=True), "Davis, Miles")

    def test_music_mononyms(self):
        self.assertEqual(surname_key("Sting", is_music=True), "Sting")
        self.assertEqual(surname_key("Madonna", is_music=True), "Madonna")
        self.assertEqual(surname_key("Prince", is_music=True), "Prince")

    def test_music_collaborations_and_features(self):
        self.assertEqual(surname_key("Simon & Garfunkel", is_music=True), "Simon & Garfunkel")
        self.assertEqual(surname_key("Miles Davis feat. John Coltrane", is_music=True), "Miles Davis feat. John Coltrane")
        self.assertEqual(surname_key("Queen + Paul Rodgers", is_music=True), "Queen + Paul Rodgers")


class TestSortKeyMatchesAuthor(unittest.TestCase):
    def test_matches_valid(self):
        self.assertTrue(sort_key_matches_author("Cremonini, Cesare", "Cesare Cremonini"))
        self.assertTrue(sort_key_matches_author("Smile, The", "The Smile"))
        self.assertTrue(sort_key_matches_author("De André, Fabrizio", "Fabrizio De André"))
        self.assertTrue(sort_key_matches_author("van Beethoven, Ludwig", "Ludwig van Beethoven"))
        self.assertTrue(sort_key_matches_author("Bach, Johann Sebastian", "J.S. Bach"))

    def test_rejects_mismatch(self):
        self.assertFalse(sort_key_matches_author("Mozart, Wolfgang Amadeus", "Cesare Cremonini"))
        self.assertFalse(sort_key_matches_author("Beethoven, Ludwig van", "Pink Floyd"))
        self.assertFalse(sort_key_matches_author("Morricone, Ennio", "The Beatles"))

    def test_empty_or_neutral(self):
        self.assertTrue(sort_key_matches_author("", "Cesare Cremonini"))
        self.assertTrue(sort_key_matches_author("Cremonini, Cesare", ""))


class TestCollectionSortPartsAndYear(unittest.TestCase):
    def test_roman_numerals_parser(self):
        self.assertEqual(_parse_vol_number("I"), 1)
        self.assertEqual(_parse_vol_number("II"), 2)
        self.assertEqual(_parse_vol_number("IV"), 4)
        self.assertEqual(_parse_vol_number("X"), 10)
        self.assertEqual(_parse_vol_number("XXIV"), 24)
        self.assertEqual(_parse_vol_number("42"), 42)
        self.assertIsNone(_parse_vol_number("non_numero"))
        self.assertIsNone(_parse_vol_number(""))

    def test_collection_sort_parts(self):
        base, num, disp = collection_sort_parts("I Meridiani")
        self.assertEqual(base, "i meridiani")
        self.assertEqual(num, 999999)
        self.assertEqual(disp, "I Meridiani")

        base, num, disp = collection_sort_parts("I Meridiani Vol. II")
        self.assertEqual(base, "i meridiani")
        self.assertEqual(num, 2)
        self.assertEqual(disp, "I Meridiani")

        base, num, disp = collection_sort_parts("Grandi Classici #15")
        self.assertEqual(base, "grandi classici")
        self.assertEqual(num, 15)
        self.assertEqual(disp, "Grandi Classici")

        base, num, disp = collection_sort_parts("Piccola Biblioteca Adelphi #32")
        self.assertEqual(base, "piccola biblioteca adelphi")
        self.assertEqual(num, 32)
        self.assertEqual(disp, "Piccola Biblioteca Adelphi")

        base, num, disp = collection_sort_parts("")
        self.assertEqual(base, "")
        self.assertEqual(num, 999999)
        self.assertEqual(disp, "")

    def test_year_of(self):
        self.assertEqual(year_of("1984"), "1984")
        self.assertEqual(year_of("Anno 2001"), "2001")
        self.assertEqual(year_of("(c1995)"), "1995")
        self.assertEqual(year_of("2024-05-12"), "2024")
        self.assertEqual(year_of(1973), "1973")
        self.assertEqual(year_of("senza anno"), "")
        self.assertEqual(year_of(None), "")


if __name__ == "__main__":
    unittest.main()
