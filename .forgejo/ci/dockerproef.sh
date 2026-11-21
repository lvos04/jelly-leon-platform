#!/usr/bin/env bash
# De bestaande proeven starten zelf containers en simulators. Loopback, bridge en tijdelijke
# bind mounts moeten dus vanuit het harnas en de geïsoleerde bouwdaemon hetzelfde betekenen.
set -euo pipefail
modus="$1"
proefbestand="$RUNNER_TEMP/harnas.proef"
if [[ $modus == ruim ]]; then
  # Afsluitende stap: ook na afbreken het harnas en alle containers die de proef startte weg.
  if [[ -s $proefbestand ]]; then
    docker ps -aq --filter "label=nl.medialeon.ci.proef=$(cat "$proefbestand")" | xargs -r docker rm -fv >/dev/null
    docker volume rm "$(cat "$proefbestand")" >/dev/null 2>&1 || true
  fi
  exit 0
fi
docker buildx build --load --iidfile "$RUNNER_TEMP/harnas.id" .forgejo/ci/harnas
volume=$(docker volume create)
echo "$volume" > "$proefbestand"
harnas=
# shellcheck disable=SC2329 # Wordt vanuit de EXIT-trap aangeroepen.
opruimen() {
  if [[ -n $harnas ]]; then docker rm -fv "$harnas" >/dev/null; fi
  docker volume rm "$volume" >/dev/null
}
trap opruimen EXIT
# Alleen het eigen tijdelijke volume krijgt in het harnas hetzelfde pad als bij de daemon.
testmap=$(docker volume inspect --format '{{.Mountpoint}}' "$volume")
harnas=$(docker create --network host --mount "type=volume,source=$volume,target=$testmap" \
  --label "nl.medialeon.ci.proef=$volume" -e "CI_PROEF=$volume" \
  --mount type=bind,source=/var/run/docker.sock,target=/var/run/docker.sock \
  -e TMPDIR="$testmap" -e LIFECYCLE_IMAGE -e SIM_IMAGE -e HUB_IMAGE -e EXECUTOR_IMAGE \
  -e GITHUB_SHA -e ASF_COMMIT -e ASM_COMMIT "$(cat "$RUNNER_TEMP/harnas.id")" \
  bash .forgejo/ci/proeven.sh "$modus")
git archive HEAD | docker cp - "$harnas:/werk"
if [[ $modus == rooktest ]]; then
  git -C "$RUNNER_TEMP/appstore" archive --prefix=appstore/ "$ASF_COMMIT" apps/api | docker cp - "$harnas:/opt"
fi
status=0
docker start --attach "$harnas" || status=$?
if [[ $modus == rooktest ]]; then
  mkdir -p "$RUNNER_TEMP/rookrapporten"
  docker cp "$harnas:/rapporten/." "$RUNNER_TEMP/rookrapporten/"
fi
if ((status)); then exit "$status"; fi
exit "$(docker wait "$harnas")"
