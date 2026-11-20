#!/usr/bin/env bash
set -euo pipefail
# De bestaande pytest-modules slaan zonder daemon de imageproeven over.
docker version --format '{{.Server.Version}}'
python -m pip install --disable-pip-version-check pip==26.3
if [[ $1 == rooktest ]]; then
  pip install -e 'apps/api[test]' -e 'e2e/simulators/truenas[test]'
  bash .forgejo/ci/validator.sh /opt/appstore /opt/validator
  python tools/rooktest.py --hub-image "$HUB_IMAGE" --executor-image "$EXECUTOR_IMAGE" \
    --lifecycle-image "$LIFECYCLE_IMAGE" --validator /opt/validator/bin/jl-manifest \
    --hub-commit "$ASM_COMMIT" --eigen-commit "$GITHUB_SHA" --validator-commit "$ASF_COMMIT" --rapport /rapporten/rooktest.json
  python tools/upstream_rooktest.py --rapport /rapporten/upstream-rooktest.json
  python tools/nodered_rooktest.py --rapport /rapporten/nodered-rooktest.json
  exit 0
fi

pip install -e 'apps/api[test]'
if [[ $1 == image ]]; then
  loopback=$(getent ahostsv4 localhost | awk 'NR == 1 {print $1}')
  mkdir -p "$TMPDIR/appdata"
  dienst=$(docker run -d -v "$TMPDIR/appdata:/appdata" -p "$loopback::30440" "$LIFECYCLE_IMAGE")
  trap 'docker rm -fv "$dienst" >/dev/null' EXIT
  curl -fsS --retry 90 --retry-delay 1 --retry-all-errors "http://$(docker port "$dienst" 30440/tcp)/readyz"
  docker exec "$dienst" python /usr/local/lib/lifecycle-allinone/healthcheck.py
  docker exec "$dienst" python /usr/local/lib/lifecycle-allinone/healthcheck.py --controle
  for _ in $(seq 1 60); do
    [[ $(docker inspect -f '{{.State.Health.Status}}' "$dienst") == healthy ]] && break
    sleep 2
  done
  [[ $(docker inspect -f '{{.State.Health.Status}}' "$dienst") == healthy ]]
  docker rm -fv "$dienst" >/dev/null
  trap - EXIT

  sim=$(docker run -d -p "$loopback::8080" "$SIM_IMAGE")
  trap 'docker rm -fv "$sim" >/dev/null' EXIT
  curl -fsS --retry 30 --retry-delay 1 --retry-all-errors "http://$(docker port "$sim" 8080/tcp)/api/versions"
  docker rm -fv "$sim" >/dev/null
  trap - EXIT
fi
python -m pytest -q tests/e2e
