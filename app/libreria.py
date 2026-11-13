#!/usr/bin/env python3
"""
Entrypoint principale di Archivio (macOS, Windows, Linux).

Avvia l'applicazione nativa Qt6 / PySide6-Essentials (licenza LGPLv3).
Se invocato da un interprete Python privo di PySide6, seleziona automaticamente
l'ambiente virtuale locale del progetto (`.venv/bin/python` o `.venv/Scripts/python.exe`).
"""
from __future__ import annotations

import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(APP_DIR)
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)


def _ensure_pyside6_runtime() -> None:
    try:
        import PySide6  # noqa: F401
        return
    except ImportError:
        pass

    venv_python = (
        os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
        if sys.platform == "win32"
        else os.path.join(PROJECT_ROOT, ".venv", "bin", "python")
    )
    if os.path.isfile(venv_python) and os.path.realpath(sys.executable) != os.path.realpath(venv_python):
        os.execv(venv_python, [venv_python, os.path.abspath(__file__), *sys.argv[1:]])


_ensure_pyside6_runtime()

from archivio.textutil import surname_key, title_sort_key  # noqa: E402,F401
from archivio.ui import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
