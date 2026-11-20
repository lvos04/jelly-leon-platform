#!/usr/bin/env bash
set -euo pipefail
actionlint .github/workflows/{ci,release}.yml
for workflow in .forgejo/workflows/*.yml; do
  # actionlint kent de absolute Forgejo-action-URL niet. Controleer dezelfde action en pin als owner/repo@sha.
  sed 's#uses: https://code.forgejo.org/#uses: #' "$workflow" | actionlint -stdin-filename "$workflow" -
done
shellcheck .forgejo/ci/*.sh
