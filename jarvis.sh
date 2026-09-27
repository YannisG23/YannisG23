#!/usr/bin/env bash
# Lance J.A.R.V.I.S. : ./jarvis.sh  (options : --text, --doctor, --memory...)
cd "$(dirname "$0")"
if [ ! -f .venv/bin/activate ]; then
  echo "Lance d'abord ./install.sh"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate
exec python -m jarvis "$@"
