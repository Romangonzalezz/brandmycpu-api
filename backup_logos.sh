#!/usr/bin/env bash
# Baja a backend/media/logos/ todos los logos que hoy viven en el disco
# efimero del server. Correr ANTES de montar el volumen: el volumen se monta
# vacio y tapa lo que habia.
#
#   ./backup_logos.sh https://tu-api.up.railway.app
set -euo pipefail

API="${1:?Falta la URL de la API. Uso: ./backup_logos.sh https://tu-api...}"
DEST="$(dirname "$0")/media/logos"
mkdir -p "$DEST"

urls=$(curl -fsS "$API/api/spots/" | grep -o '"logo_url":"[^"]*"' | cut -d'"' -f4)
[ -n "$urls" ] || { echo "La API no devolvio ningun logo_url."; exit 1; }

for u in $urls; do
  name="${u##*/}"
  if curl -fsSL "${u/http:/https:}" -o "$DEST/$name"; then
    echo "ok   $name  ($(wc -c <"$DEST/$name") bytes)"
  else
    echo "FALLO $name  <- $u"
  fi
done
echo "--- en $DEST:"; ls -la "$DEST"
