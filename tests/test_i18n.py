"""
Unit test per app/archivio/i18n.py.
Copre: parità e completezza di tutte le chiavi di traduzione tra italiano e inglese,
corrispondenza dei segnaposto di formattazione ({cat}, {fn}, {files}, ecc.),
funzione tr() con interpolazione e gestione di fallback su chiavi non censite.
"""
from __future__ import annotations

import re
import unittest

from archivio.i18n import TEXTS, tr

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


class TestI18nCompletenessAndParity(unittest.TestCase):
    def test_keys_parity_between_italian_and_english(self):
        it_keys = set(TEXTS["it"].keys())
        en_keys = set(TEXTS["en"].keys())

        missing_in_en = it_keys - en_keys
        missing_in_it = en_keys - it_keys

        self.assertEqual(
            missing_in_en,
            set(),
            f"Chiavi presenti in italiano ma mancanti in inglese: {missing_in_en}",
        )
        self.assertEqual(
            missing_in_it,
            set(),
            f"Chiavi presenti in inglese ma mancanti in italiano: {missing_in_it}",
        )

    def test_formatting_placeholders_match(self):
        it_dict = TEXTS["it"]
        en_dict = TEXTS["en"]

        for key in it_dict:
            it_val = it_dict[key]
            en_val = en_dict.get(key, "")

            it_placeholders = set(_PLACEHOLDER_RE.findall(it_val))
            en_placeholders = set(_PLACEHOLDER_RE.findall(en_val))

            self.assertEqual(
                it_placeholders,
                en_placeholders,
                f"I segnaposto di formattazione non corrispondono per la chiave '{key}': "
                f"IT={it_placeholders} vs EN={en_placeholders}",
            )


class TestI18nFunction(unittest.TestCase):
    def test_tr_basic_lookup(self):
        self.assertEqual(tr("it", "app_title"), "Archivio")
        self.assertEqual(tr("en", "app_title"), "Archivio")
        self.assertEqual(tr("it", "cat_Libri"), "Libri")
        self.assertEqual(tr("en", "cat_Libri"), "Books")

    def test_tr_formatting_interpolation(self):
        msg_it = tr("it", "dlg_save_as_prompt", cat="CD")
        self.assertIn("CD", msg_it)

        msg_en = tr("en", "dlg_save_as_prompt", cat="CD")
        self.assertIn("CD", msg_en)

        msg_del = tr("it", "msg_confirm_delete", n=5)
        self.assertIn("5", msg_del)

    def test_tr_fallback_for_unknown_key(self):
        # Una chiave inesistente non deve sollevare eccezioni, ma restituire il nome della chiave
        self.assertEqual(tr("it", "chiave_inesistente_12345"), "chiave_inesistente_12345")
        self.assertEqual(tr("en", "non_existing_key_xyz"), "non_existing_key_xyz")


if __name__ == "__main__":
    unittest.main()
