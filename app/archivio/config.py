"""Percorsi di sistema multi-piattaforma e impostazioni persistenti dell'applicazione."""
from __future__ import annotations

import json
import locale
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from typing import Dict

CATEGORIES = ("Libri", "CD", "Vinili")

DEFAULT_FILES: Dict[str, str] = {
    "Libri": "libri.csv",
    "CD": "cd.csv",
    "Vinili": "vinili.csv",
}

CATEGORY_PREFIXES: Dict[str, str] = {
    "Libri": "libri_",
    "CD": "cd_",
    "Vinili": "vinili_",
}


def support_dir() -> str:
    """Cartella dati utente: ~/Library/Application Support/Archivio su macOS, %APPDATA%/Archivio su Windows, ~/.config/archivio su Linux."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        base = os.path.join(appdata, "Archivio")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/Archivio")
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
        base = os.path.join(xdg, "archivio")
    os.makedirs(base, exist_ok=True)
    return base


def config_path() -> str:
    return os.path.join(support_dir(), "config.json")


def reset_config_file() -> None:
    """Elimina il file di configurazione attivo per ripristinare il primo avvio."""
    p = config_path()
    try:
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


def cache_dir() -> str:
    """Cartella cache: ~/Library/Caches/Archivio su macOS, %LOCALAPPDATA%/Archivio/Cache su Windows, ~/.cache/archivio su Linux."""
    if sys.platform == "win32":
        localapp = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(localapp, "Archivio", "Cache")
    elif sys.platform == "darwin":
        path = os.path.expanduser("~/Library/Caches/Archivio")
    else:
        xdg_cache = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        path = os.path.join(xdg_cache, "archivio")
    os.makedirs(path, exist_ok=True)
    return path


def cache_path() -> str:
    return os.path.join(cache_dir(), "catalog_cache.sqlite3")


def backups_dir() -> str:
    path = os.path.join(support_dir(), "Backups")
    os.makedirs(path, exist_ok=True)
    return path


def resource_path(*parts: str) -> str:
    """Percorso di una risorsa, valido sia da sorgente sia nel bundle .app (PyInstaller)."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.join(base, *parts)
    if os.path.exists(candidate):
        return candidate
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), *parts)


def default_archive_dir() -> str:
    """Restituisce il percorso predefinito suggerito (senza creare cartelle su disco prima della conferma utente)."""
    cwd = os.getcwd()
    if any(os.path.isfile(os.path.join(cwd, fn)) for fn in DEFAULT_FILES.values()):
        return cwd
    return os.path.expanduser("~/Documents/Archivio")


def _detect_system_language() -> str:
    for env_key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(env_key, "").lower()
        if val.startswith("it"):
            return "it"
        if val.startswith("en"):
            return "en"
    try:
        import plistlib
        gp = os.path.expanduser("~/Library/Preferences/.GlobalPreferences.plist")
        if os.path.isfile(gp):
            with open(gp, "rb") as f:
                langs = plistlib.load(f).get("AppleLanguages") or []
            if langs and str(langs[0]).lower().startswith("it"):
                return "it"
    except Exception:
        pass
    try:
        loc, _ = locale.getlocale()
        if loc and loc.lower().startswith("it"):
            return "it"
    except Exception:
        pass
    return "en"


def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """Scrittura atomica: il file originale non resta mai a metà in caso di errore o crash."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".part", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@dataclass
class Settings:
    archive_dir: str = ""
    language: str = "en"
    online: bool = True
    discogs_token: str = ""
    scanner_mode: str = "ghpages"
    scanner_gh_pages_url: str = "https://a-bit-are.github.io/archivio/scanner.html"
    group_by_collection: bool = False
    first_run_completed: bool = False
    last_category: str = "Libri"
    active_files: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FILES))
    camera_id: str = ""
    camera_auto_capture: bool = True
    camera_burst_mode: bool = False
    geometry: str = ""
    splitter: str = ""
    sort: dict = field(default_factory=dict)

    @property
    def path(self) -> str:
        return config_path()

    @property
    def folder(self) -> str:
        return self.archive_dir or default_archive_dir()

    @folder.setter
    def folder(self, val: str) -> None:
        self.archive_dir = val

    @property
    def online_enabled(self) -> bool:
        return self.online

    @online_enabled.setter
    def online_enabled(self, val: bool) -> None:
        self.online = bool(val)

    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        has_file = os.path.isfile(s.path)
        try:
            with open(s.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
            has_file = False

        saved_dir = (
            data.get("archive_dir")
            or data.get("folder")
            or data.get("ultima_cartella")
            or ""
        ).strip()
        s.archive_dir = saved_dir or default_archive_dir()
        s.first_run_completed = bool(
            data.get("first_run_completed", bool(has_file and saved_dir))
        )
        lang = data.get("language") or data.get("lingua") or _detect_system_language()
        s.language = lang if lang in ("it", "en") else _detect_system_language()
        s.online = bool(data.get("online", data.get("online_enabled", data.get("ricerca_online", True))))
        s.discogs_token = str(data.get("discogs_token") or os.environ.get("DISCOGS_TOKEN") or "").strip()
        s.scanner_gh_pages_url = str(
            data.get("scanner_gh_pages_url") or "https://a-bit-are.github.io/archivio/scanner.html"
        ).strip()
        raw_sm = str(data.get("scanner_mode") or "").strip()
        if raw_sm in ("ghpages", "local"):
            s.scanner_mode = raw_sm
        else:
            s.scanner_mode = "ghpages"
        s.group_by_collection = bool(data.get("group_by_collection", False))
        s.last_category = data.get("last_category", "Libri")
        af = data.get("active_files") or {}
        for c in CATEGORIES:
            s.active_files[c] = os.path.basename(af.get(c) or DEFAULT_FILES[c])
        s.camera_id = str(data.get("camera_id") or "").strip()
        s.camera_auto_capture = bool(data.get("camera_auto_capture", True))
        s.camera_burst_mode = bool(data.get("camera_burst_mode", False))
        s.geometry = data.get("geometry", "")
        s.splitter = data.get("splitter", "")
        s.sort = data.get("sort", {}) or {}
        return s

    def save(self) -> None:
        if self.archive_dir:
            self.first_run_completed = True
        payload = asdict(self)
        # Mantiene anche le chiavi legacy per retrocompatibilità
        payload["ultima_cartella"] = self.archive_dir
        payload["lingua"] = self.language
        try:
            atomic_write_text(self.path, json.dumps(payload, indent=2, ensure_ascii=False))
        except OSError:
            pass
