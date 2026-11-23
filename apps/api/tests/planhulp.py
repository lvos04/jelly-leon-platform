"""Gedeelde testgegevens voor de planner (B08-003/B08-004). Alle namen, paden en versies zijn **testwaarden**
(``pool-x`` bestaat nergens); de NAS-gegevens D01–D18 zijn onbekend."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hub_p2 import app_record

from appstore.manifest import validate_text
from appstore.manifest.conflicts import Inventory
from lifecycle.hubread import Beschikbaarheid, Inventaris, installed_app
from lifecycle.planner import Doel

ROOT = Path(__file__).resolve().parents[3]
NU = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
DOEL = Doel("25.04.2.4", "1.4.8")
DOEL_JSON = {"truenas_version": "25.04.2.4", "ix_app_version": "1.4.8"}
GOTIFY_REPO = "ghcr.io/gotify/server"
GOTIFY_DIGEST = "sha256:44fc5bbd1c0618878e33073d38c2c13126b48f245c3fe12236b1da48eb203b86"
OUDE_DIGEST = "sha256:" + "a1" * 32
GOTIFY = {"host_port": 30215, "data_path": "/mnt/pool-x/apps/gotify", "timezone": "Europe/Amsterdam",
          "rpo": "PT24H", "rto": "PT4H"}
SCHADUW = {"host_port": 30216}
INSTALL_PREVIEW = "/lifecycle/v1/plans/install/preview"
AUD = "jelly-leon-platform"


def manifest(naam: str) -> str:
    return (ROOT / "install" / "manifests" / f"{naam}.manifest.yaml").read_text(encoding="utf-8")


def gotify_alleen_image_veilig() -> str:
    """TESTVARIANT van het Gotify-manifest zonder migraties en met ``tag_only_rollback_safe: true``: de enige soort app
    waarvoor het terugzetplan alleen de image terug mag zetten (ADR-0025). Het echte Gotify-manifest zegt het
    omgekeerde; deze variant bestaat alleen om de regels voor de digest bij terugzetten te kunnen toetsen."""
    tekst = manifest("gotify")
    oud = "upgrade:\n  migrations: true\n  tag_only_rollback_safe: false\n"
    assert tekst.count(oud) == 1
    return tekst.replace(oud, "upgrade:\n  migrations: false\n  tag_only_rollback_safe: true\n")


def gotify_record(*, tag: str = "3.0.0", digest: str | None = OUDE_DIGEST, host_port: int = 30215,
                  dataset: str | None = "pool-x/apps/gotify", naam: str = "gotify") -> dict[str, Any]:
    return app_record(naam, repository=GOTIFY_REPO, tag=tag, digest=digest, app_id="gotify",
                      poorten=[(8080, host_port, "tcp")], opslag=[("/mnt/pool-x/apps/gotify", "/app/data", dataset)])


def inventaris(*records: dict[str, Any]) -> Inventaris:
    """Een verse, gemeten inventaris in de vorm die ``HubLezer.inventaris`` oplevert."""
    per_naam = {str(r["naam"]): r for r in records}
    return Inventaris("ok", None, Inventory(observed_at=NU, source="hub_snapshot",
                                            apps=[installed_app(r) for r in records]),
                      per_naam, "2026-11-11T12:00:00Z", "meting-1")


def meting(waarde: str, status: str = "ok", reden: str | None = None) -> Any:
    def lees(train: str, app: str, version: str) -> Beschikbaarheid:
        return Beschikbaarheid(waarde, status, reden, "2026-11-11T11:59:00Z" if waarde != "onbekend" else None,
                               train, app, version)

    return lees


def codes(document: dict[str, Any]) -> set[str]:
    return {b["code"] for b in document["blokkades"]}


def stap(document: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(s for s in document["stappen"] if s["id"] == step_id)


def status(voortgang: list[dict[str, Any]]) -> dict[str, str]:
    return {s["id"]: s["status"] for s in voortgang}


# ------------------------------------------------------------------ gebonden installatiepreview (§8.2), zoals de Hub
def claim_van(key: str, body: dict[str, Any], path: str = INSTALL_PREVIEW) -> str:
    # Onafhankelijk van de code nagerekend volgens de contracttekst: canonieke JSON, sleutels gesorteerd, geen spaties.
    tekst = json.dumps(["POST", path, key, body], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(tekst.encode("utf-8")).hexdigest()


def binding(**record: Any) -> dict[str, Any]:
    report, _m, _r = validate_text(manifest("gotify"))
    rec = {"app_id": "gotify", "version": report.version, "manifest_sha256": report.manifest_sha256,
           "entry_id": str(uuid.uuid4()), "version_id": str(uuid.uuid4()), "valid": True, **record}
    return {
        "appstore_service_id": str(uuid.uuid4()),
        "source_approval_revision": "sha256:" + "ab" * 32,
        "provenance": {"record": rec, "signature": "dGVzdA==", "key_id": "0123456789abcdef", "alg": "EdDSA"},
        "release": {"catalog": {"train": "stable", "app": "ix-app", "version": "1.4.8"},
                    "image_digests": [GOTIFY_DIGEST]},
        "instance": {"host_id": "nas-primary", "instance_id": str(uuid.uuid4()), "registry_revision": "inventaris:1",
                     "app_name": "gotify", "expected_version": "absent"},
    }


def hub_body(b: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"manifest_yaml": manifest("gotify"), "parameters": GOTIFY, "app_name": "gotify",
            "doel": DOEL_JSON, "catalog_binding": binding() if b is None else b}


def hub_kop(hub: Any, key: str, claim: str | None) -> dict[str, str]:
    extra = {} if claim is None else {"catalog_request_sha256": claim}
    return {"Authorization": "Bearer " + hub.state.sim.mint_service(AUD, extra=extra), "Idempotency-Key": key}


def stuur(client: Any, hub: Any, body: dict[str, Any], *, claim_body: dict[str, Any] | None = None,
          claim: str | None = "", key: str | None = None) -> Any:
    key = key or str(uuid.uuid4())
    if claim == "":
        claim = claim_van(key, body if claim_body is None else claim_body)
    return client.post(INSTALL_PREVIEW, json=body, headers=hub_kop(hub, key, claim))
