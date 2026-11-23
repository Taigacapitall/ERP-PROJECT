#!/usr/bin/env bash
# Copie les livrables dans le dépôt Vorpal SANS écraser de fichier existant.
# Usage : scripts/install_vorpal_compta.sh /chemin/vers/depot_vorpal   (ajouter --dry-run pour simuler)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:?chemin du dépôt Vorpal requis}"
DRY="${2:-}"
[ -d "$DEST/vorpal/core" ] || { echo "ERREUR : $DEST ne ressemble pas au dépôt Vorpal (vorpal/core absent)"; exit 1; }
cd "$SRC"
while IFS= read -r -d '' f; do
  case "$f" in *__pycache__*|*.pyc|*/scripts/install_vorpal_compta.sh) continue;; esac
  rel="${f#./}"
  case "$rel" in vorpal/core/*) echo "REFUSÉ (Core gelé) : $rel"; exit 1;; esac
  if [ -e "$DEST/$rel" ]; then echo "EXISTE, conservé : $rel"; continue; fi
  echo "COPIE : $rel"
  if [ "$DRY" != "--dry-run" ]; then mkdir -p "$(dirname "$DEST/$rel")"; cp "$f" "$DEST/$rel"; fi
done < <(find vorpal odoo_addons tests docs -type f -print0)
echo "Terminé. Aucun commit/push effectué. HANDOFF.md non modifié : ajoutez docs/HANDOFF_SECTION_COMPTA.md à la main."
