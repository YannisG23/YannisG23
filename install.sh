#!/usr/bin/env bash
# Installation de J.A.R.V.I.S. (macOS / Linux) : ./install.sh
set -euo pipefail
cd "$(dirname "$0")"
echo "=== Installation de J.A.R.V.I.S. ==="

PY=""
for candidate in python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] <= (3, 12)))' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 à 3.12 introuvable."
  echo "macOS : brew install python@3.12   |   Ubuntu : sudo apt install python3.12 python3.12-venv"
  exit 1
fi

if [ "$(uname)" = "Linux" ] && ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
  echo "Il manque PortAudio pour le micro : sudo apt install libportaudio2"
fi

[ -d .venv ] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[all]"

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Ouvre le fichier .env et remplis ta clé API Anthropic, ton prénom et ta ville."
  echo "Ensuite relance : ./jarvis.sh --doctor"
  exit 0
fi

python -m jarvis --doctor || true
echo
echo "Installation terminée. Lance Jarvis avec : ./jarvis.sh"
