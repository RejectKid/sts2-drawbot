#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    ./install.sh
fi

nohup .venv/bin/python src/sts2_gui.py >/dev/null 2>&1 &
printf '%s\n' "STS2 Drawbot GUI launched."
