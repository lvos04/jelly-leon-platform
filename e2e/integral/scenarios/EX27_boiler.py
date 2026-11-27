"""Boilerbeleid vanuit het scherm, de Hub, een ondertekende assistentactor en de huis-API."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from e2e.integral.project import MAP
from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.huis import Huis, toestand

SCENARIO = {
    "eigenaar": "my_house",
    "verwachting": "De boiler is alleen leesbaar. Een gewoon stopcontact mag een voorstel krijgen.",
    "groepen": ("huis",), "diensten": ("my-house", "huis-browser"),
}


def b64(waarde: bytes) -> str:
    return base64.urlsafe_b64encode(waarde).rstrip(b"=").decode("ascii")


def publieke_sleutel(privaat: Ed25519PrivateKey, naam: str) -> dict[str, str]:
    return {"kty": "OKP", "crv": "Ed25519", "kid": naam, "alg": "EdDSA", "use": "sig",
            "x": b64(privaat.public_key().public_bytes_raw())}


def koppel_proefondertekenaar(huis: Huis) -> Ed25519PrivateKey:
    sleutel = Ed25519PrivateKey.generate()
    bron = {"jwks": {"keys": [publieke_sleutel(Ed25519PrivateKey.generate(), "jwt")]},
            "policy_public_key": publieke_sleutel(sleutel, "beleid")}
    pad = "/api/v1/system/hub-keys"
    body = {"mode": "paste", "source": json.dumps(bron)}
    headers = {"If-Match": huis.api.get(pad).raise_for_status().headers["etag"]}
    kandidaat = huis.api.post(pad + "/verify", json=body, headers=headers).raise_for_status().json()
    huis.api.put(pad, json={**body, "fingerprint": kandidaat["fingerprint"]},
                 headers={**headers, "Idempotency-Key": str(uuid.uuid4())}).raise_for_status()
    # Het contract gebruikt hele UTC-seconden. De uitgiftetijd moet ná het koppelmoment liggen.
    gekoppeld = datetime.now(UTC)
    wacht("eerste geldige uitgifteseconde", lambda: datetime.now(UTC).replace(microsecond=0) > gekoppeld, 5)
    return sleutel


def assistentvoorstel(client: httpx.Client, sleutel: Ed25519PrivateKey, actie: str,
                    doel: dict[str, str]) -> httpx.Response:
    nu = datetime.now(UTC).replace(microsecond=0)
    body: dict[str, Any] = {
        "proposal": {"idempotency_key": str(uuid.uuid4()), "action_id": actie,
                     "target": doel, "parameters": {"on": False}, "expected_version": "eigenaar",
                     "actor": {"type": "ai", "id": "huisproef"}, "risk_claim": "R1"},
        "approval": None, "policy_decision": {"risk_class": "R1", "rule_ids": ["huisproef"],
                                             "policy_hash": "sha256:" + hashlib.sha256(b"huisproef").hexdigest()},
        "nonce": str(uuid.uuid4()), "issued_at": nu.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": (nu + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    body["signature"] = b64(sleutel.sign(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()))
    return client.post("/connector/v1/propose", json=body, headers={"Idempotency-Key": str(uuid.uuid4())})


def koppel_hubviewer(huis: Huis) -> httpx.Client:
    # Dit adres komt uitsluitend in de synthetische koppelcode. Verkeer loopt via het eigen projectnet.
    huis.schrijf("PUT", "/system/settings", {"lan_url": "http://huisproef.local"})
    code = huis.schrijf("POST", "/pairing/codes", {"role": "viewer", "purpose": "hub", "label": "Proef-Hub"})
    viewer = huis.kern.client("my-house", 8080)
    assert viewer.get("/connector/v1/discover").status_code == 401
    antwoord = viewer.post("/api/v1/pairing/redeem", json={"code": code["code"], "device_name": "Proef-Hub"})
    assert antwoord.status_code == 200
    koppeling = antwoord.json()
    if (koppeling["client"]["role"], koppeling["client"]["purpose"]) != ("viewer", "hub"):
        raise AssertionError("De koppeling gaf niet de reguliere Hub-leesrol")
    viewer.headers["Authorization"] = "Bearer " + koppeling["token"]
    return viewer


def meet_hubviewer(huis: Huis, viewer: httpx.Client, sleutel: Ed25519PrivateKey,
                   doel: dict[str, str], moment: str) -> None:
    voor = huis.wachtrij()
    lees = viewer.get("/connector/v1/discover")
    assert lees.status_code == 200 and lees.json()["actions"]
    antwoord = assistentvoorstel(viewer, sleutel, "socket.relay.propose", doel)
    assert antwoord.status_code == 403 and antwoord.json().get("code") == "forbidden"
    verschil = huis.wachtrij() - voor
    assert verschil == 0
    huis.meet(moment, {"ontdekking_http": lees.status_code, "voorstel_http": antwoord.status_code,
                       "nieuwe_opdrachten": verschil})


def test_boiler_vier_ingangen(kern: Kern) -> None:
    huis = Huis(kern)
    sim = huis.start_sim("homewizard", 30221, "--apparaat", "socket")
    apparaat = huis.koppel("homewizard", "my-house:30221").raise_for_status().json()["id"]
    huis.bevestig(apparaat)
    sleutel = koppel_proefondertekenaar(huis)
    stand = toestand(sim)["power_on"]
    wachtrij = huis.wachtrij()
    stekker = huis.get(f"/devices/{apparaat}")
    assert (stekker["provider"], stekker["kind"], stekker["compatibility"]) == ("homewizard", "energy_socket", "tested")
    assert stekker["protected"] is False and not stekker["roles"]
    actie, doel = "socket.relay.propose", {"device_id": apparaat}
    viewer = koppel_hubviewer(huis)
    meet_hubviewer(huis, viewer, sleutel, doel, "hubviewer_voor_binding")
    gewoon = assistentvoorstel(huis.api, sleutel, actie, doel)
    gewoon_antwoord = gewoon.json()
    huis.meet("gewoon_stopcontact", {
        "http": gewoon.status_code, "voorstel": gewoon_antwoord.get("status") == "proposed",
        "alleen_concept": gewoon_antwoord.get("concept_only") is True,
        "juiste_doel_en_stand": (gewoon_antwoord.get("blast_radius") == [doel]
                                 and gewoon_antwoord.get("diff", {}).get("proposed") == {"on": False}),
        "nieuwe_opdrachten": huis.wachtrij() - wachtrij,
    })
    huis.schrijf("POST", f"/devices/{apparaat}/bind", {"role": "boiler"})
    boiler = huis.get(f"/devices/{apparaat}")
    assert boiler["protected"] is True and "boiler" in boiler["roles"]

    meet_hubviewer(huis, viewer, sleutel, doel, "hubviewer_na_binding")

    browser = (MAP.parent / "fixtures/huis/boiler-ui.cjs").read_text()
    ui = json.loads(huis.project.compose("exec", "-T", "huis-browser", "node", "-e", browser,
                    invoer=json.dumps({"apparaat": apparaat, "cookies": dict(huis.api.cookies)}), tijd=75))
    assert ui == {"beleidsslot_zichtbaar": True, "schakelbedieningen": 0}
    huis.meet("browser", ui)

    hub = kern.hub.post("/api/v1/actions", headers={"Idempotency-Key": str(uuid.uuid4())}, json={
        "action_id": "boiler.set", "target": {"host_id": "nas-primary"}, "parameters": {"on": False},
        "reason": "Lokale boilerproef",
    })
    assert hub.status_code == 403 and "boiler_verboden" in [b["code"] for b in hub.json()["details"]["blokkades"]]
    huis.meet("hub", hub.status_code)
    ai = assistentvoorstel(huis.api, sleutel, actie, doel)
    assert ai.status_code == 409 and ai.json()["code"] == "policy_denied"
    huis.meet("ondertekende_assistentactor", ai.status_code)
    licht = assistentvoorstel(huis.api, sleutel, "light.group.set", {"group_id": apparaat})
    assert licht.status_code == 409 and licht.json()["code"] == "policy_denied"
    huis.meet("ondertekende_lichtgroepactie_op_boiler", licht.status_code)
    direct = huis.api.post(f"/api/v1/lights/groups/{apparaat}", json={"on": False})
    assert direct.status_code == 409 and direct.json()["detail"]["code"] == "policy_denied"
    huis.meet("rechtstreeks_http", direct.status_code)

    def leesvermogen() -> bool:
        live = huis.get(f"/homes/{huis.woning}/energy/live")
        vermogen = toestand(sim)["data"]["active_power_w"]
        return any(p["role"] == "boiler" and p["watts"] == vermogen for p in live["parts"])

    wacht("actueel boilervermogen uit de simulator", leesvermogen)
    einde = toestand(sim)
    meter = {"vermogen_w": einde["data"]["active_power_w"], "relais_ongewijzigd": einde["power_on"] == stand,
             "schakelverzoeken": len(einde["schakelverzoeken"]), "nieuwe_opdrachten": huis.wachtrij() - wachtrij}
    huis.meet("meter", meter)
    assert meter["relais_ongewijzigd"] and meter["schakelverzoeken"] == meter["nieuwe_opdrachten"] == 0
    assert huis.metingen["gewoon_stopcontact"] == {
        "http": 200, "voorstel": True, "alleen_concept": True, "juiste_doel_en_stand": True, "nieuwe_opdrachten": 0,
    }, "Gewoon stopcontact kreeg geen zuiver conceptvoorstel"
