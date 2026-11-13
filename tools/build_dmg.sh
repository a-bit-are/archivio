#!/usr/bin/env bash
# ==============================================================================
# Archivio
# Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3
#
# Genera l'immagine disco d'installazione macOS "Archivio-macOS.dmg"
# con collegamento rapido ad /Applications, layout grafico nativo (icone
# posizionate correttamente per il drag-and-drop) e icona personalizzata.
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_BUNDLE="$ROOT_DIR/dist/Archivio.app"
DMG_ROOT="$ROOT_DIR/Archivio-macOS.dmg"
ICON_PATH="$ROOT_DIR/assets/archivio.icns"
VOL_NAME="Archivio"

# Se il bundle .app non esiste ancora, lo compila automaticamente
if [[ ! -d "$APP_BUNDLE" ]]; then
    echo "Bundle .app non trovato: avvio compilazione con tools/build_macos_app.sh..."
    bash "$ROOT_DIR/tools/build_macos_app.sh"
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/archivio_dmg_build.XXXXXX")"
DMG_RW="$TEMP_DIR/temp_rw.dmg"

cleanup() {
    # Smonta eventuali volumi residui prima di rimuovere la cartella temporanea
    if [[ -d "/Volumes/$VOL_NAME" ]]; then
        hdiutil detach "/Volumes/$VOL_NAME" -force >/dev/null 2>&1 || true
    fi
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

echo "== 1. Creazione immagine disco temporanea scrivibile (180M HFS+) =="
hdiutil create \
    -size 180m \
    -fs HFS+ \
    -volname "$VOL_NAME" \
    -ov \
    "$DMG_RW" >/dev/null

echo "== 2. Montaggio del volume per configurazione layout =="
# Se esiste già un volume con lo stesso nome, smontalo prima
if [[ -d "/Volumes/$VOL_NAME" ]]; then
    hdiutil detach "/Volumes/$VOL_NAME" -force >/dev/null 2>&1 || true
fi

DEV_LINE=$(hdiutil attach -readwrite -noverify -noautoopen "$DMG_RW" | grep "/Volumes/$VOL_NAME" || true)
if [[ -z "$DEV_LINE" ]]; then
    DEV_LINE=$(hdiutil attach -readwrite -noverify -noautoopen "$DMG_RW" | tail -n 1)
fi
DEV=$(echo "$DEV_LINE" | awk '{print $1}')
MOUNT_POINT="/Volumes/$VOL_NAME"

echo "Volume montato su $MOUNT_POINT ($DEV)"

echo "== 3. Copia dell'applicazione e creazione collegamento /Applications =="
cp -R "$APP_BUNDLE" "$MOUNT_POINT/Archivio.app"
ln -s /Applications "$MOUNT_POINT/Applications"

# Imposta l'icona del volume se presente
if [[ -f "$ICON_PATH" ]]; then
    cp "$ICON_PATH" "$MOUNT_POINT/.VolumeIcon.icns"
    if command -v SetFile >/dev/null 2>&1; then
        SetFile -a C "$MOUNT_POINT" || true
    fi
fi

echo "== 4. Posizionamento delle icone nella cartella del volume (AppleScript) =="
if osascript -e 'tell application "Finder" to get name' >/dev/null 2>&1; then
    osascript << APPLESCRIPT || true
tell application "Finder"
    tell disk "$VOL_NAME"
        open
        set w to container window
        set current view of w to icon view
        set toolbar visible of w to false
        set statusbar visible of w to false
        set the bounds of w to {300, 180, 840, 540}
        set viewOptions to the icon view options of w
        set icon size of viewOptions to 120
        set text size of viewOptions to 12
        set arrangement of viewOptions to not arranged
        set position of item "Archivio.app" to {140, 170}
        set position of item "Applications" to {400, 170}
        close
        open
        delay 1
    end tell
end tell
APPLESCRIPT
    echo "Posizioni icone impostate: Archivio.app {140, 170}, Applicazioni {400, 170}"
else
    echo "Finder non disponibile per AppleScript: procedo con layout standard."
fi

sync
echo "== 5. Smontaggio volume e conversione compressa finale =="
hdiutil detach "$DEV" >/dev/null

mkdir -p "$ROOT_DIR/dist"
rm -f "$DMG_ROOT" "$ROOT_DIR/dist/Archivio-macOS.dmg"

hdiutil convert "$DMG_RW" \
    -format UDZO \
    -imagekey zlib-level=9 \
    -o "$DMG_ROOT" >/dev/null

# Mantiene una copia anche in dist/ e preserva dist/Archivio.app per chi compila da sorgenti
cp "$DMG_ROOT" "$ROOT_DIR/dist/Archivio-macOS.dmg"

echo "== DMG generato con successo: =="
echo "  ✓ $DMG_ROOT (radice del repository per GitHub)"
echo "  ✓ $ROOT_DIR/dist/Archivio-macOS.dmg (cartella di compilazione locale dist/)"
