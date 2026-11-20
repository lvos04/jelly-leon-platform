#!/usr/bin/env bash
set -euo pipefail
venv="$RUNNER_TEMP/jlp-venv"
if ! python3.12 -m venv "$venv"; then
  apt-get update -qq
  apt-get install -y --no-install-recommends python3.12-venv
  python3.12 -m venv "$venv"
fi
echo "$venv/bin" >> "$GITHUB_PATH"
