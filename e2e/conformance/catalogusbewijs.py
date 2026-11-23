"""ALLEEN ONTWIKKELING/TEST — catalogusbewijs voor de planproef tegen de echte Hub (S-P2-420, R-MANIFEST-1).

Draait met de **venv van de Hub** (``ai-server-manager``), zodat canonieke JSON, schemapin, validatorversie en
veldschema exact die van de Hub zijn. Maakt in ``--uit``:

- ``policy/``: een kopie van het Hub-beleid met precies één witte-lijstregel voor het testmanifest (``ASM_POLICY_DIR``);
- ``appstore.json``: de drie antwoorden van een nagebootste appstore (versie met ondertekend herkomstrecord, item,
  bron) en de publieke appstoresleutel voor de inschrijving bij de Hub.

Alle sleutels zijn wegwerpsleutels van deze run; er wordt niets bewaard. Schrijft niets in de Hub-repo.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import secrets
import shutil
import uuid
from pathlib import Path

import yaml
from asm_policy.canonical import canonical_json, canonical_sha256
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from hub.catalog.asf.manifest.translate import Target, translate
from hub.catalog.asf.manifest.validator import validate_data
from hub.catalog.plannen import SCHEMA_SHA256

BRON_URL = "https://forge.test/leon/demo-catalogus"  # nagebootste Git-forge; wordt nooit benaderd


def _raw(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hub-repo", required=True)
    p.add_argument("--manifest", required=True)
    p.add_argument("--parameters", required=True, help="JSON")
    p.add_argument("--doel", required=True, help="JSON")
    p.add_argument("--app-name", required=True)
    p.add_argument("--uit", required=True)
    a = p.parse_args()
    manifest = yaml.safe_load(Path(a.manifest).read_text(encoding="utf-8"))
    rapport, model = validate_data(copy.deepcopy(manifest))
    if model is None or rapport.errors:
        raise SystemExit("testmanifest ongeldig volgens de gepinde validator van de Hub")
    vertaald = translate(model, json.loads(a.parameters), Target.model_validate(json.loads(a.doel)), a.app_name,
                         trusted_app_id=True)
    if vertaald.payload is None:
        raise SystemExit(f"vertaling geblokkeerd: {[f.code for f in vertaald.blocking()]}")
    store, uitgever = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    bron = {"source_id": str(uuid.uuid4()), "url": BRON_URL, "subpath": "apps",
            "path": f"{manifest['app_id']}.manifest.yaml", "commit": secrets.token_hex(20)}
    regel = {
        "app_id": manifest["app_id"], "version": manifest["version"],
        "catalog": {"train": "stable", "app": "ix-app", "version": json.loads(a.doel)["ix_app_version"]},
        "manifest_sha256": canonical_sha256(manifest), "image_digests": [manifest["image"]["digest"]],
        "schema_sha256": SCHEMA_SHA256, "validator_version": rapport.validator_version, "source": bron,
        "publisher_key_sha256": "sha256:" + hashlib.sha256(_raw(uitgever)).hexdigest(),
        "formuliervelden": sorted(vertaald.payload.model_dump(mode="json", exclude_none=True)["values"]),
        "kluisplekken": [],
    }
    entry_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
    record = {
        "app_id": regel["app_id"], "version": regel["version"], "entry_id": entry_id, "version_id": version_id,
        "manifest_sha256": regel["manifest_sha256"], "schema_sha256": SCHEMA_SHA256,
        "validator_version": regel["validator_version"],
        "source": {"source_id": bron["source_id"], "kind": "git", "url": BRON_URL, "ref": "main",
                   "commit": bron["commit"], "path": bron["path"], "subpath": bron["subpath"]},
        "fetched_at": "2026-11-23T10:00:00Z", "publisher_signature": "verified",
        "publisher_key_id": regel["publisher_key_sha256"][7:23],
        "publisher_key_sha256": regel["publisher_key_sha256"], "valid": True, "errors": 0, "warnings": 0,
    }
    provenance = {"record": record, "signature": base64.b64encode(store.sign(canonical_json(record))).decode(),
                  "key_id": hashlib.sha256(_raw(store)).hexdigest()[:16], "alg": "EdDSA"}
    uit = Path(a.uit)
    beleid = uit / "policy"
    shutil.copytree(Path(a.hub_repo) / "policy", beleid)
    pad = beleid / "catalogus.yaml"
    doc = yaml.safe_load(pad.read_text(encoding="utf-8"))
    doc["installaties"] = [regel]
    pad.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8")
    routes = {
        f"/catalog/v1/versions/{version_id}": {
            "id": version_id, "entry_id": entry_id, "app_id": regel["app_id"], "version": regel["version"],
            "source_id": bron["source_id"], "manifest": manifest, "provenance": provenance},
        f"/catalog/v1/entries/{regel['app_id']}": {
            "entry_id": entry_id, "app_id": regel["app_id"], "source": {"id": bron["source_id"]}},
        f"/catalog/v1/sources/{bron['source_id']}": {
            "id": bron["source_id"], "kind": "git", "enabled": True, "require_signature": True, "url": BRON_URL,
            "subpath": bron["subpath"], "pinned_commit": bron["commit"],
            "trusted_key_id": regel["publisher_key_sha256"][7:23]},
    }
    x = base64.urlsafe_b64encode(_raw(store)).rstrip(b"=").decode()
    (uit / "appstore.json").write_text(json.dumps({"routes": routes, "jwk_x": x, "version_id": version_id,
                                                   "app_id": regel["app_id"]}), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
