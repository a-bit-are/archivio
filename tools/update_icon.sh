#!/usr/bin/env bash
# ==============================================================================
# Archivio
# Copyright (C) 2026 Antonio Cafaro — Licenza GNU GPLv3
#
# Converte un'immagine PNG/JPEG (1024x1024 consigliato) o copia un file .icns
# in "assets/archivio.icns" usando gli strumenti nativi macOS (sips + iconutil).
# Uso: bash tools/update_icon.sh /percorso/alla/nuova_icona.png
# ==============================================================================
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Uso: bash tools/update_icon.sh <file_icona.png | file_icona.jpg | file_icona.icns>"
    exit 1
fi

SRC_IMG="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_ICNS="$ROOT_DIR/assets/archivio.icns"

if [[ ! -f "$SRC_IMG" ]]; then
    echo "Errore: file '$SRC_IMG' non trovato."
    exit 1
fi

mkdir -p "$ROOT_DIR/assets"

if [[ "$SRC_IMG" == *.icns ]]; then
    cp "$SRC_IMG" "$DEST_ICNS"
    echo "Icona .icns copiata in: $DEST_ICNS"
    exit 0
fi

TMP_ICONSET="$(mktemp -d "${TMPDIR:-/tmp}/archivio_icon.XXXXXX")/archivio.iconset"
mkdir -p "$TMP_ICONSET"
cleanup() {
    rm -rf "$(dirname "$TMP_ICONSET")"
}
trap cleanup EXIT

for sz in 16 32 128 256 512; do
    sips -s format png -z "$sz" "$sz" "$SRC_IMG" --out "$TMP_ICONSET/icon_${sz}x${sz}.png" >/dev/null
    sz2=$((sz * 2))
    sips -s format png -z "$sz2" "$sz2" "$SRC_IMG" --out "$TMP_ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done

iconutil -c icns "$TMP_ICONSET" -o "$DEST_ICNS"
echo "Nuova icona macOS generata con successo in: $DEST_ICNS"
