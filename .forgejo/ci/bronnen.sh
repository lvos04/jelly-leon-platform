#!/usr/bin/env bash
# De test vergelijkt ook met de branchkop. Daarom de hele branch ophalen, inclusief oudere pins.
set -euo pipefail
: "${BRON_TOKEN:?leestoken ontbreekt}"
branch=ccr-c133d7a0-kuz1cv
case "$1" in
  kern|conformiteit) manager=ai-server-manager ;;
  test) manager=peil-bron ;;
  rooktest) manager=asm ;;
  *) exit 2 ;;
esac
# Git vraagt de gegevens via stdin/stdout op bij de helper. De remote-URL blijft schoon.
export GIT_TERMINAL_PROMPT=0
# shellcheck disable=SC2016 # Git evalueert de helper pas bij het opvragen van de credential.
helper='!f() { if [ "$1" = get ]; then printf "username=platform-bot\npassword=%s\n" "$BRON_TOKEN"; fi; }; f'
if [[ $1 == kern || $1 == conformiteit ]]; then
  if [[ ${GITHUB_REF_TYPE:-} != tag ]]; then branch=${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:-$branch}}; fi
  git -c credential.helper= -c "credential.https://git.medialeon.nl.helper=$helper" clone --quiet --no-tags \
    --single-branch --branch "$branch" https://git.medialeon.nl/leon/ai-server-manager.git "$RUNNER_TEMP/$manager"
  exit 0
fi
status=0
for repo in ai-server-manager appstore-en-fork; do
  doel="$RUNNER_TEMP/$manager"
  if [[ $repo == appstore-en-fork ]]; then doel="$RUNNER_TEMP/appstore"; fi
  if ! git -c credential.helper= -c "credential.https://git.medialeon.nl.helper=$helper" clone --quiet --no-checkout --no-tags \
      --single-branch --branch "$branch" "https://git.medialeon.nl/leon/$repo.git" "$doel"; then
    echo "::error::Ophalen van leon/$repo mislukt (lezen van de repository vereist)."
    status=1
  fi
done
if ((status)); then exit "$status"; fi
git -C "$RUNNER_TEMP/appstore" checkout --quiet "$ASF_COMMIT"
if [[ $1 == rooktest ]]; then git -C "$RUNNER_TEMP/asm" checkout --quiet "$ASM_COMMIT"; fi
# Eén commit van een repo ophalen (alleen gelezen); een tweede pin in dezelfde map komt erbij.
haal() {
  [[ -d $1/.git ]] || git init --quiet "$1"
  git -C "$1" -c credential.helper= -c "credential.https://git.medialeon.nl.helper=$helper" \
    fetch --quiet --no-tags --depth=1 "https://git.medialeon.nl/leon/$2.git" "$3"
}
# Gepinde commit uit de herkomstkop van een manifestkopie (tools/manifest_kopie.py).
kopiepin() { sed -n '1s/^# HERKOMST .* bron: [^@]*@\([0-9a-f]\{40\}\):.*/\1/p' "install/manifests/$1"; }
if [[ $1 == test ]]; then
  haal "$RUNNER_TEMP/mso-bron" media-source-optimizer "$(cat e2e/simulators/mso-mocks.commit)"
  # Gree-fake en adapters van my_house voor de simulators van huis en energie (alleen gelezen).
  haal "$RUNNER_TEMP/my-house-bron" my_house "$(cat e2e/simulators/my-house.commit)"
  # Bron op de gepinde commit voor tests/repo/test_manifest_kopie.py.
  haal "$RUNNER_TEMP/mgu-bron" media-guard-ultimate "$(kopiepin mediaguard.manifest.yaml)"
  haal "$RUNNER_TEMP/mso-bron" media-source-optimizer "$(kopiepin media-source-optimizer.manifest.yaml)"
  haal "$RUNNER_TEMP/my-house-bron" my_house "$(kopiepin my_house.manifest.yaml)"
  haal "$RUNNER_TEMP/zoeken-bron" zoeken-en-persoonlijke-ai "$(kopiepin zoeken-en-persoonlijke-ai.manifest.yaml)"
fi
