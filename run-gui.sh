#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    ./install.sh
fi

.venv/bin/python src/sts2_gui.py
