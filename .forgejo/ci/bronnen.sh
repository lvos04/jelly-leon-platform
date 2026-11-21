#!/usr/bin/env bash
# De test vergelijkt ook met de branchkop. Daarom de hele branch ophalen, inclusief oudere pins.
set -euo pipefail
: "${BRON_TOKEN:?leestoken ontbreekt}"
branch=ccr-c133d7a0-kuz1cv
case "$1" in
  test) manager=peil-bron ;;
  rooktest) manager=asm ;;
  *) exit 2 ;;
esac
# Git vraagt de gegevens via stdin/stdout op bij de helper. De remote-URL blijft schoon.
export GIT_TERMINAL_PROMPT=0
# shellcheck disable=SC2016 # Git evalueert de helper pas bij het opvragen van de credential.
helper='!f() { if [ "$1" = get ]; then printf "username=platform-bot\npassword=%s\n" "$BRON_TOKEN"; fi; }; f'
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
if [[ $1 == test ]]; then
  doel="$RUNNER_TEMP/mso-bron"
  pin=$(cat e2e/simulators/mso-mocks.commit)
  git init --quiet "$doel"
  git -C "$doel" -c credential.helper= -c "credential.https://git.medialeon.nl.helper=$helper" \
    fetch --quiet --no-tags --depth=1 https://git.medialeon.nl/leon/media-source-optimizer.git "$pin"
  # Gree-fake en adapters van my_house voor de simulators van huis en energie (alleen gelezen).
  doel="$RUNNER_TEMP/my-house-bron"
  pin=$(cat e2e/simulators/my-house.commit)
  git init --quiet "$doel"
  git -C "$doel" -c credential.helper= -c "credential.https://git.medialeon.nl.helper=$helper" \
    fetch --quiet --no-tags --depth=1 https://git.medialeon.nl/leon/my_house.git "$pin"
fi
