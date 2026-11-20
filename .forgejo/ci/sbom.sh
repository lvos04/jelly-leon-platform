#!/usr/bin/env bash
set -euo pipefail
# Syft leest alleen een kopie van de geteste image. De scanner heeft geen netwerk of registercredential nodig.
syft=docker.io/anchore/syft:v1.57.2@sha256:e97e20b6e6d1699c60296b66ab337941724f9bd06ac279d893eda89601fc4b2a
werkmap=$(mktemp -d "$RUNNER_TEMP/sbom.XXXXXXXX")
archief="$werkmap/image.tar"
scanner=$(docker create --network none -e SYFT_CHECK_FOR_APP_UPDATE=false "$syft" scan docker-archive:/image.tar -o spdx-json)
trap 'docker rm -fv "$scanner" >/dev/null; rm -rf "$werkmap"' EXIT
docker image save "$LIFECYCLE_IMAGE" -o "$archief"
# De scanner leest het archief als zijn eigen gebruiker. De tijdelijke bovenliggende map is 0700.
chmod 644 "$archief"
docker cp "$archief" "$scanner:/image.tar"
docker start --attach "$scanner" > release/sbom.spdx.json
[[ $(docker wait "$scanner") == 0 ]]
jq -e '.spdxVersion and (.packages | length > 0)' release/sbom.spdx.json >/dev/null
