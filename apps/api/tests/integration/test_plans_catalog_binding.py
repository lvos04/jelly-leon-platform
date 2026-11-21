"""Vertrouwde plannerinvoer (contract 1.3 §8.2, M3): ``catalog_binding`` alleen van de Hub als dienst, met de claim
``catalog_request_sha256`` over ``[POST, pad, Idempotency-Key, body]``. Vormen zoals de Hub ze verwacht
(ai-server-manager ``hub/catalog/plannen.py``). Sim-getest; alle waarden zijn testwaarden.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from typing import Any

import planhulp as h
import pytest
from sqlalchemy import create_engine, text

from appstore.manifest import validate_text

PREVIEW = "/lifecycle/v1/plans/install/preview"
AUD = "jelly-leon-platform"


def _claim(key: str, body: dict[str, Any], path: str = PREVIEW) -> str:
    # Onafhankelijk van de code nagerekend volgens de contracttekst: canonieke JSON, sleutels gesorteerd, geen spaties.
    tekst = json.dumps(["POST", path, key, body], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(tekst.encode("utf-8")).hexdigest()


def binding(**record: Any) -> dict[str, Any]:
    report, _m, _r = validate_text(h.manifest("gotify"))
    rec = {"app_id": "gotify", "version": report.version, "manifest_sha256": report.manifest_sha256,
           "entry_id": str(uuid.uuid4()), "version_id": str(uuid.uuid4()), "valid": True, **record}
    return {
        "appstore_service_id": str(uuid.uuid4()),
        "source_approval_revision": "sha256:" + "ab" * 32,
        "provenance": {"record": rec, "signature": "dGVzdA==", "key_id": "0123456789abcdef", "alg": "EdDSA"},
        "release": {"catalog": {"train": "stable", "app": "ix-app", "version": "1.4.8"},
                    "image_digests": [h.GOTIFY_DIGEST]},
        "instance": {"host_id": "nas-primary", "instance_id": str(uuid.uuid4()), "registry_revision": "inventaris:1",
                     "app_name": "gotify", "expected_version": "absent"},
    }


def hub_body(b: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"manifest_yaml": h.manifest("gotify"), "parameters": h.GOTIFY, "app_name": "gotify",
            "doel": h.DOEL_JSON, "catalog_binding": binding() if b is None else b}


def hub_kop(hub: Any, key: str, claim: str | None) -> dict[str, str]:
    extra = {} if claim is None else {"catalog_request_sha256": claim}
    return {"Authorization": "Bearer " + hub.state.sim.mint_service(AUD, extra=extra), "Idempotency-Key": key}


def stuur(client: Any, hub: Any, body: dict[str, Any], *, claim_body: dict[str, Any] | None = None,
          claim: str | None = "", key: str | None = None) -> Any:
    key = key or str(uuid.uuid4())
    if claim == "":
        claim = _claim(key, body if claim_body is None else claim_body)
    return client.post(PREVIEW, json=body, headers=hub_kop(hub, key, claim))


def plannen(pg_url: str) -> int:
    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            return int(conn.execute(text("SELECT count(*) FROM lifecycle_plans")).scalar_one())
    finally:
        engine.dispose()


def meetbaar(hub: Any) -> None:
    p2 = hub.state.sim.p2
    p2.zet_apps()
    p2.zet_beschikbaar("stable", "ix-app", "1.4.8", "ja")


def test_hub_binding_wordt_onveranderd_bewaard_en_teruggegeven(client: Any, auth: Any, hub: Any,
                                                               clean_db: str) -> None:
    meetbaar(hub)
    body = hub_body()
    key = str(uuid.uuid4())
    r = stuur(client, hub, body, key=key)
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["catalog_binding"] == body["catalog_binding"] and "catalog_binding" not in view["plan"]
    assert view["status"] == "klaar_voor_voorstel" and view["vrijgegeven_stappen"] == ["installeren"]
    # De stap zoals de Hub haar herkent (plannen.py::_actiestap/_stap_past).
    acties = [s for s in view["plan"]["stappen"] if (s.get("voorstel") or {}).get("action_id")]
    assert len(acties) == 1 and acties[0]["id"] == "installeren"
    voorstel = acties[0]["voorstel"]
    assert voorstel["action_id"] == "app.install" and voorstel["target"] == {"app_id": "gotify"}
    assert voorstel["expected_version"] == "absent" and isinstance(voorstel["parameters"], dict)

    gelezen = client.get(f"/lifecycle/v1/plans/{view['id']}",
                         headers={"Authorization": "Bearer " + hub.state.sim.mint_service(AUD)})
    assert gelezen.status_code == 200 and gelezen.json()["catalog_binding"] == body["catalog_binding"]
    assert gelezen.json()["etag"] == view["etag"]

    # Herhaling met dezelfde sleutel en een nieuw JWT (nieuwe jti): hetzelfde plan, geen tweede rij.
    opnieuw = stuur(client, hub, body, key=key)
    assert opnieuw.status_code == 201 and opnieuw.json()["id"] == view["id"]
    assert opnieuw.json()["catalog_binding"] == body["catalog_binding"] and plannen(clean_db) == 1

    # De Hub meldt het stapresultaat met zijn dienst-JWT (ADR-0022 besluit 7: WRITE_ROLES bevat de dienst).
    melden = client.post(f"/lifecycle/v1/plans/{view['id']}/steps/installeren/result",
                         headers={"Authorization": "Bearer " + hub.state.sim.mint_service(AUD),
                                  "Idempotency-Key": str(uuid.uuid4())},
                         json={"status": "voltooid", "execution_id": str(uuid.uuid4()),
                               "readback": {"staat": "RUNNING", "digests": [h.GOTIFY_DIGEST]}})
    assert melden.status_code == 200, melden.text
    assert melden.json()["catalog_binding"] == body["catalog_binding"]  # na melden ongewijzigd
    assert melden.json()["etag"] != view["etag"]


def test_gebruiker_zonder_binding_ziet_null(client: Any, auth: Any, hub: Any) -> None:
    meetbaar(hub)
    body = {k: v for k, v in hub_body().items() if k != "catalog_binding"}
    r = client.post(PREVIEW, json=body, headers=auth("operator", idem=True))
    assert r.status_code == 201 and r.json()["catalog_binding"] is None


@pytest.mark.parametrize("geval", ["operator", "geen_claim", "andere_body", "andere_sleutel", "ander_pad",
                                   "ander_doel"])
def test_binding_zonder_geldige_claim_wordt_geweigerd(client: Any, auth: Any, hub: Any, clean_db: str,
                                                      geval: str) -> None:
    meetbaar(hub)
    body = hub_body()
    key = str(uuid.uuid4())
    if geval == "operator":
        r = client.post(PREVIEW, json=body, headers=auth("operator", idem=True))
        reden = "alleen_hub_dienst"
    elif geval == "geen_claim":
        r = stuur(client, hub, body, claim=None)
        reden = "claim_ontbreekt"
    elif geval == "andere_body":
        anders = copy.deepcopy(body)
        anders["parameters"]["host_port"] = 30299
        r = stuur(client, hub, body, claim_body=anders)
        reden = "claim_past_niet"
    elif geval == "andere_sleutel":
        r = stuur(client, hub, body, claim=_claim(str(uuid.uuid4()), body), key=key)
        reden = "claim_past_niet"
    elif geval == "ander_pad":
        r = stuur(client, hub, body, claim=_claim(key, body, "/lifecycle/v1/plans/update/preview"), key=key)
        reden = "claim_past_niet"
    else:
        body["catalog_binding"]["instance"]["app_name"] = "andere-naam"
        r = stuur(client, hub, body)
        reden = "doel_past_niet"
    assert r.status_code == 403, r.text
    assert (r.json()["code"], r.json()["details"]["reason"]) == ("forbidden", reden)
    assert plannen(clean_db) == 0


def test_claim_zonder_binding_is_onverwacht(client: Any, hub: Any, clean_db: str) -> None:
    meetbaar(hub)
    body = {k: v for k, v in hub_body().items() if k != "catalog_binding"}
    r = stuur(client, hub, body)
    assert r.status_code == 403 and r.json()["details"]["reason"] == "claim_zonder_binding"
    upd = client.post("/lifecycle/v1/plans/update/preview", json=body,
                      headers=hub_kop(hub, str(uuid.uuid4()), "sha256:" + "0" * 64))
    assert upd.status_code == 403 and upd.json()["details"]["reason"] == "claim_onverwacht"
    assert plannen(clean_db) == 0


@pytest.mark.parametrize("veld", ["app_id", "version", "manifest_sha256"])
def test_record_dat_niet_bij_het_manifest_past_wordt_geweigerd(client: Any, hub: Any, clean_db: str,
                                                               veld: str) -> None:
    meetbaar(hub)
    waarde = {"app_id": "andere-app", "version": "9.9.9", "manifest_sha256": "sha256:" + "cd" * 32}[veld]
    r = stuur(client, hub, hub_body(binding(**{veld: waarde})))
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "catalogusbinding_past_niet" and r.json()["details"]["velden"] == [veld]
    assert plannen(clean_db) == 0


@pytest.mark.parametrize("wijzig", ["extra_veld", "vault_refs", "zonder_instance_id", "expected_version",
                                    "lege_digests", "zonder_record"])
def test_onvolledige_of_onverwachte_binding_geeft_422(client: Any, hub: Any, clean_db: str, wijzig: str) -> None:
    b = binding()
    if wijzig == "extra_veld":
        b["trusted"] = True
    elif wijzig == "vault_refs":
        b["vault_refs"] = []  # de planner maakt de kluismarkeringen zelf; de aanvrager levert ze niet
    elif wijzig == "zonder_instance_id":
        del b["instance"]["instance_id"]
    elif wijzig == "expected_version":
        b["instance"]["expected_version"] = '"etag-1"'
    elif wijzig == "lege_digests":
        b["release"]["image_digests"] = []
    else:
        b["provenance"] = {"signature": "dGVzdA=="}
    r = stuur(client, hub, hub_body(b))
    assert r.status_code == 422, r.text
    assert plannen(clean_db) == 0


def test_updatepreview_kent_geen_binding(client: Any, hub: Any) -> None:
    body = hub_body()
    key = str(uuid.uuid4())
    r = client.post("/lifecycle/v1/plans/update/preview", json=body, headers=hub_kop(hub, key, None))
    assert r.status_code == 422


def test_misvormde_claim_is_een_ongeldig_token(client: Any, hub: Any) -> None:
    r = stuur(client, hub, hub_body(), claim="sha256:NIET-HEX")
    assert r.status_code == 401 and r.json()["details"]["reason"] == "invalid_token"
