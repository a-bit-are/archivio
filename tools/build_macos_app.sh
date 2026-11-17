#!/usr/bin/env bash
# ==============================================================================
# Archivio
# Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3
#
# Costruisce il bundle nativo macOS "dist/Archivio.app" tramite PyInstaller
# applicando l'icona in "assets/archivio.icns" e rimuovendo i file temporanei di build.
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
ICON_PATH="$ROOT_DIR/assets/archivio.icns"

if [[ ! -x "$PYTHON_BIN" ]]; then
    echo "Errore: ambiente .venv non trovato in $ROOT_DIR/.venv"
    echo "Esegui prima: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pyinstaller"
    exit 1
fi

ICON_ARGS=()
if [[ -f "$ICON_PATH" ]]; then
    ICON_ARGS=(--icon "$ICON_PATH" --add-data "$ICON_PATH:assets")
fi

# Sincronizza la web-app dello scanner remoto e compila l'helper nativo Apple Vision
cp -f "$ROOT_DIR/docs/scanner.html" "$ROOT_DIR/app/archivio/data/scanner.html"
if command -v swiftc >/dev/null 2>&1 && [[ -f "$ROOT_DIR/tools/vision_scanner.swift" ]]; then
    swiftc -O "$ROOT_DIR/tools/vision_scanner.swift" -o "$ROOT_DIR/app/archivio/data/vision_scanner"
    chmod +x "$ROOT_DIR/app/archivio/data/vision_scanner"
fi

echo "== Costruzione bundle macOS 'Archivio.app' =="
"$PYTHON_BIN" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "Archivio" \
    --osx-bundle-identifier "com.antoniocafaro.archivio" \
    --workpath "$ROOT_DIR/build/pyinstaller" \
    --specpath "$ROOT_DIR/build" \
    --distpath "$ROOT_DIR/dist" \
    --paths "$ROOT_DIR/app" \
    --add-data "$ROOT_DIR/app/archivio/data:archivio/data" \
    --add-binary "$ROOT_DIR/app/archivio/data/vision_scanner:archivio/data" \
    "${ICON_ARGS[@]}" \
    "$ROOT_DIR/app/libreria.py"

# Aggiorna Info.plist con versione 1.0.0 e NSCameraUsageDescription per la fotocamera del Mac
"$PYTHON_BIN" - <<'PY'
import os, plistlib
plist_path = os.path.abspath("dist/Archivio.app/Contents/Info.plist")
if os.path.isfile(plist_path):
    with open(plist_path, "rb") as f:
        pl = plistlib.load(f)
    pl["CFBundleShortVersionString"] = "1.0.0"
    pl["CFBundleVersion"] = "1.0.0"
    pl["NSCameraUsageDescription"] = (
        "Archivio utilizza la fotocamera del computer per scansionare i codici a barre ISBN/EAN "
        "e le copertine di libri, CD e vinili."
    )
    pl["NSHighResolutionCapable"] = True
    with open(plist_path, "wb") as f:
        plistlib.dump(pl, f)
PY

codesign --force --deep --sign - "$ROOT_DIR/dist/Archivio.app"

# Pulizia artefatti intermedi per mantenere la cartella di progetto sempre ordinata
rm -rf "$ROOT_DIR/build"
rm -rf "$ROOT_DIR/dist/Archivio"
rm -f "$ROOT_DIR/Archivio.spec"

echo "== Completato! Bundle nativo disponibile in: $ROOT_DIR/dist/Archivio.app =="
