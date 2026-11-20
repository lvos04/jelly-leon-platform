#!/usr/bin/env bash
set -euo pipefail
bron="$1/apps/api"
venv="$2"
python -m venv "$venv"
"$venv/bin/pip" install --disable-pip-version-check -q --require-hashes -r "$bron/requirements.lock"
"$venv/bin/pip" install --disable-pip-version-check -q --no-deps "$bron"
