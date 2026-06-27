#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

printf '\nInstalled. Try:\n'
printf '  ./.venv/bin/python src/sts2_gui.py\n'
