#!/usr/bin/env bash
set -euo pipefail
api="https://git.medialeon.nl/api/v1/repos/$GITHUB_REPOSITORY"
tag="v$VERSIE"
aanroep() {
  curl --silent --show-error --header @<(printf 'Authorization: token %s\n' "$API_TOKEN") "$@"
}
# Een vrijgave hoort alleen bij een al bestaande tag op de geteste commit.
aanroep --fail "$api/tags/$tag" | jq -e --arg sha "$GITHUB_SHA" '.commit.sha == $sha' >/dev/null
bestand="$RUNNER_TEMP/release.json"
status=$(aanroep -o "$bestand" -w '%{http_code}' "$api/releases/tags/$tag")
case "$status" in
  200) ;;
  404)
    digest=$(jq -er --arg image "$REGISTRY_IMAGE" '.images[$image].digest' release/digests.json)
    jq -n --arg tag "$tag" --arg naam "jelly-leon-platform $VERSIE" --arg ref "$REGISTRY_IMAGE:$VERSIE@$digest" \
      '{tag_name:$tag, name:$naam, body:("Geteste CI-image: `"+$ref+"`."), draft:true}' |
      aanroep --fail -H 'Content-Type: application/json' --data-binary @- "$api/releases" > "$bestand" ;;
  *) echo "::error::Ophalen van de release gaf HTTP $status."; exit 1 ;;
esac
id=$(jq -er .id "$bestand")
for naam in digests.json sbom.spdx.json; do
  asset=$(jq -r --arg naam "$naam" '.assets[]? | select(.name == $naam) | .browser_download_url' "$bestand")
  if [[ -n $asset ]]; then
    [[ $asset == "https://git.medialeon.nl/$GITHUB_REPOSITORY/"* ]]
    aanroep --fail --location --proto-redir "=https" "$asset" | cmp "release/$naam" -
  else
    aanroep --fail -F "attachment=@release/$naam" "$api/releases/$id/assets?name=$naam" >/dev/null
  fi
done
if jq -e .draft "$bestand" >/dev/null; then
  aanroep --fail -X PATCH -H 'Content-Type: application/json' -d '{"draft":false}' "$api/releases/$id" >/dev/null
fi
