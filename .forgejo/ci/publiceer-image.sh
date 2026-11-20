#!/usr/bin/env bash
set -euo pipefail
: "${REGISTRY_USER:?registergebruiker ontbreekt}" "${REGISTRY_TOKEN:?registertoken ontbreekt}"
ref="$REGISTRY_IMAGE:$VERSIE"
export DOCKER_CONFIG
DOCKER_CONFIG=$(mktemp -d "$RUNNER_TEMP/register.XXXXXXXX")
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
export PATH="$DOCKER_CONFIG:$PATH"
# Docker krijgt de credential via een helper. config.json bevat uitsluitend de naam van die helper.
echo '{"credHelpers":{"git.medialeon.nl":"jlp-forgejo"}}' > "$DOCKER_CONFIG/config.json"
cat > "$DOCKER_CONFIG/docker-credential-jlp-forgejo" <<'HELPER'
#!/bin/sh
read -r server
[ "$1" = get ] && [ "$server" = git.medialeon.nl ] || exit 1
exec jq -cn '{Username:env.REGISTRY_USER, Secret:env.REGISTRY_TOKEN}'
HELPER
chmod 700 "$DOCKER_CONFIG/docker-credential-jlp-forgejo"
if melding=$(docker buildx imagetools inspect "$ref" 2>&1); then
  echo "::error::$ref bestaat al. Verhoog VERSION."; exit 1
fi
case "$melding" in
  *"$ref: not found"|*"manifest unknown"*|*"name unknown"*) ;;
  *) echo '::error::Bestaan van de versie kon niet worden vastgesteld.'; exit 1 ;;
esac
docker tag "$LIFECYCLE_IMAGE" "$ref"
trap 'docker image rm "$ref" >/dev/null; rm -rf "$DOCKER_CONFIG"' EXIT
docker push "$ref"
digest=$(docker buildx imagetools inspect --format '{{json .Manifest}}' "$ref" | jq -er .digest)
[[ $digest =~ ^sha256:[0-9a-f]{64}$ ]]
jq -n --arg image "$REGISTRY_IMAGE" --arg tag "$VERSIE" --arg digest "$digest" \
  --arg sha "$GITHUB_SHA" --arg tijd "$(docker image inspect -f '{{.Created}}' "$LIFECYCLE_IMAGE")" \
  '{schema_version:"1", app_id:"jelly-leon-platform", version:$tag, images:{($image):{tag:$tag,digest:$digest}},
    source_commit:$sha, built_at:$tijd}' > release/digests.json
