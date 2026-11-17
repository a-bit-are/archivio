"""
Unit test per app/archivio/config.py.
Copre: percorsi di sistema, scrittura atomica sicura, gestione delle impostazioni
(Settings) con persistenza JSON, migrazione chiavi legacy e reset configurazione.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

if "ARCHIVIO_SUPPORT_DIR" not in os.environ:
    _TEST_SUPPORT_DIR = tempfile.mkdtemp(prefix="archivio_test_cfg_")
    os.environ["ARCHIVIO_SUPPORT_DIR"] = _TEST_SUPPORT_DIR
    atexit.register(shutil.rmtree, _TEST_SUPPORT_DIR, ignore_errors=True)

from archivio.config import (
    CATEGORIES,
    DEFAULT_FILES,
    Settings,
    atomic_write_text,
    backups_dir,
    cache_dir,
    cache_path,
    config_path,
    default_archive_dir,
    reset_config_file,
    support_dir,
)


class TestConfigPaths(unittest.TestCase):
    def test_system_directories_exist(self):
        tmp_dir = tempfile.mkdtemp(prefix="test_cfg_paths_")
        try:
            with mock.patch.dict(os.environ, {"ARCHIVIO_SUPPORT_DIR": tmp_dir}):
                s_dir = support_dir()
                self.assertEqual(s_dir, tmp_dir)
                self.assertTrue(os.path.isdir(s_dir))

                c_dir = cache_dir()
                self.assertTrue(c_dir.startswith(tmp_dir))
                self.assertTrue(os.path.isdir(c_dir))

                b_dir = backups_dir()
                self.assertTrue(b_dir.startswith(s_dir))
                self.assertTrue(os.path.isdir(b_dir))

                cfg_p = config_path()
                self.assertTrue(cfg_p.startswith(s_dir))

                cache_p = cache_path()
                self.assertTrue(cache_p.startswith(c_dir))
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_default_archive_dir_respects_linux_xdg_user_dirs(self):
        tmp_home = tempfile.mkdtemp(prefix="test_linux_home_")
        try:
            cfg_dir = os.path.join(tmp_home, ".config")
            docs_it = os.path.join(tmp_home, "Documenti")
            os.makedirs(cfg_dir, exist_ok=True)
            os.makedirs(docs_it, exist_ok=True)
            with open(os.path.join(cfg_dir, "user-dirs.dirs"), "w", encoding="utf-8") as f:
                f.write('XDG_DOCUMENTS_DIR="$HOME/Documenti"\n')

            with mock.patch("archivio.config.sys.platform", "linux"), mock.patch.dict(
                os.environ,
                {"HOME": tmp_home, "XDG_CONFIG_HOME": cfg_dir},
                clear=False,
            ):
                os.environ.pop("XDG_DOCUMENTS_DIR", None)
                with mock.patch("archivio.config.os.getcwd", return_value=tmp_home):
                    self.assertEqual(default_archive_dir(), os.path.join(docs_it, "Archivio"))
                    # Non deve aver creato alcuna cartella su disco
                    self.assertFalse(os.path.exists(os.path.join(docs_it, "Archivio")))
        finally:
            shutil.rmtree(tmp_home, ignore_errors=True)


class TestAtomicWriteText(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_atomic_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_atomic_write_creates_file_and_directories(self):
        dest = os.path.join(self.test_dir, "nested", "folder", "test.txt")
        atomic_write_text(dest, "Contenuto atomico di test\n")
        self.assertTrue(os.path.isfile(dest))

        with open(dest, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "Contenuto atomico di test\n")

    def test_atomic_overwrite(self):
        dest = os.path.join(self.test_dir, "target.txt")
        atomic_write_text(dest, "Prima versione")
        atomic_write_text(dest, "Seconda versione")

        with open(dest, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertEqual(content, "Seconda versione")


class TestSettingsModel(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_settings_")
        self.old_cfg_path = config_path()
        # Mocking config path to point to a temporary file
        self.mock_cfg_file = os.path.join(self.test_dir, "config.json")
        self._orig_config_path = Settings.path

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_settings_defaults(self):
        s = Settings()
        self.assertIn(s.language, ("it", "en"))
        self.assertTrue(s.online)
        self.assertFalse(s.group_by_collection)
        self.assertEqual(s.scanner_mode, "ghpages")
        for cat in CATEGORIES:
            self.assertEqual(s.active_files[cat], DEFAULT_FILES[cat])

    def test_settings_properties_and_setters(self):
        s = Settings()
        s.folder = "/custom/path"
        self.assertEqual(s.archive_dir, "/custom/path")
        self.assertEqual(s.folder, "/custom/path")

        s.online_enabled = False
        self.assertFalse(s.online)
        self.assertFalse(s.online_enabled)

    def test_settings_save_and_load(self):
        orig_prop = Settings.path
        target = self.mock_cfg_file
        try:
            Settings.path = property(lambda _self: target)  # type: ignore

            s = Settings()
            s.archive_dir = self.test_dir
            s.language = "en"
            s.online = False
            s.group_by_collection = True
            s.scanner_mode = "local"
            s.save()

            self.assertTrue(os.path.isfile(self.mock_cfg_file))

            # Ricarichiamo le impostazioni
            loaded = Settings.load()
            self.assertEqual(loaded.archive_dir, self.test_dir)
            self.assertEqual(loaded.language, "en")
            self.assertFalse(loaded.online)
            self.assertTrue(loaded.group_by_collection)
            self.assertEqual(loaded.scanner_mode, "local")
            self.assertTrue(loaded.first_run_completed)
        finally:
            Settings.path = orig_prop  # type: ignore

    def test_settings_legacy_keys_migration(self):
        orig_prop = Settings.path
        target = self.mock_cfg_file
        try:
            Settings.path = property(lambda _self: target)  # type: ignore

            # Creiamo un file JSON con le chiavi legacy delle prime versioni
            legacy_data = {
                "ultima_cartella": self.test_dir,
                "lingua": "it",
                "ricerca_online": True,
                "online_enabled": True,
            }
            with open(self.mock_cfg_file, "w", encoding="utf-8") as f:
                json.dump(legacy_data, f)

            loaded = Settings.load()
            self.assertEqual(loaded.archive_dir, self.test_dir)
            self.assertEqual(loaded.language, "it")
            self.assertTrue(loaded.online)
        finally:
            Settings.path = orig_prop  # type: ignore

    def test_settings_invalid_values_sanitization(self):
        orig_prop = Settings.path
        target = self.mock_cfg_file
        try:
            Settings.path = property(lambda _self: target)  # type: ignore

            corrupted_data = {
                "language": "lingua_inventata",
                "scanner_mode": "modalita_inesistente",
            }
            with open(self.mock_cfg_file, "w", encoding="utf-8") as f:
                json.dump(corrupted_data, f)

            loaded = Settings.load()
            self.assertIn(loaded.language, ("it", "en"))
            self.assertEqual(loaded.scanner_mode, "ghpages")
        finally:
            Settings.path = orig_prop  # type: ignore

    def test_first_run_without_config_file(self):
        fresh_dir = os.path.join(self.test_dir, "fresh_support")
        with mock.patch.dict(os.environ, {"ARCHIVIO_SUPPORT_DIR": fresh_dir}):
            loaded = Settings.load()
            self.assertFalse(loaded.first_run_completed)
            self.assertEqual(loaded.language, "en")
            self.assertFalse(os.path.exists(config_path()))


if __name__ == "__main__":
    unittest.main()
