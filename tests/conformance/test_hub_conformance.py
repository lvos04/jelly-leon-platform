"""JP10 / A5: gedraagt de Hub zich volgens contract 1.3 §3.3–§3.8 en §4.5, gezien vanuit deze dienst?

Een Hub die nog 1.2 volgt (``schema_version: "1.2"``, zonder ``rotation_sequence``) blijft geldig voor de dienst (§3.3:
elke major 1); een Hub die "1.3" meldt, moet ``rotation_sequence`` meesturen (de echte Hub stapt over in C1.3-ASM-02).

Dezelfde tests draaien tegen de Hub-simulator (standaard) en tegen een echte Hub (zie ``conftest.py``). De volgorde
telt: eerst de schemafouten (die verbruiken het token niet), daarna de volledige stroom met het ene token.
Gebruikt de eigen clientcode van de dienst (``lifecycle.enrollment``) om het antwoord te controleren.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lifecycle.enrollment import validate_response
from lifecycle.keys import public_jwk, thumbprint_of_x

APP = "jelly-leon-platform"
Target = Any  # conftest.Target (niet importeren: meerdere conftest-modules in één sessie)
IDENTITY = Ed25519PrivateKey.generate()
STATE: dict[str, Any] = {}


def client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False, trust_env=False)


def body(target: Target, **override: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"app_id": APP, "enrollment_token": target.token, "version": "0.1.0", "base_url": None,
                            "public_key_jwk": public_jwk(IDENTITY)}
    data.update(override)
    return {k: v for k, v in data.items() if v is not ...}


def enroll(target: Target, payload: dict[str, Any]) -> httpx.Response:
    with client() as c:
        return c.post(target.base_url + "/api/v1/services/enroll", json=payload)


def self_info(target: Target, token: str) -> httpx.Response:
    with client() as c:
        return c.get(target.base_url + "/api/v1/services/self", headers={"Authorization": f"Bearer {token}"})


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "token"},  # onbekend veld
        {"capabilities": ["read"]},
        {"pairing_code": "ABCD-EFGH-JKMN"},  # beide geheimen
        {"enrollment_token": "enr_te_kort"},
        {"public_key_jwk": {**public_jwk(IDENTITY), "use": "sig"}},
        {"public_key_jwk": {**public_jwk(IDENTITY), "alg": "EdDSA"}},
        {"public_key_jwk": {**public_jwk(IDENTITY), "kid": "geen-thumbprint"}},
        {"version": ...},  # verplicht veld ontbreekt
    ],
)
def test_schema_errors_are_422_and_do_not_consume_the_token(target: Target, change: dict[str, Any]) -> None:
    r = enroll(target, body(target, **change))
    assert r.status_code == 422, (target.kind, change, r.status_code, r.text)
    assert r.json().get("code") == "validation_error"


def test_unknown_token_is_one_401_code(target: Target) -> None:
    r = enroll(target, body(target, enrollment_token="enr_" + uuid.uuid4().hex + "A" * 11))
    assert r.status_code == 401 and r.json().get("code") == "invalid_enrollment", (target.kind, r.text)


def test_enroll_response_passes_the_service_checks(target: Target) -> None:
    r = enroll(target, body(target))
    assert r.status_code == 200, (target.kind, r.text)
    assert "no-store" in r.headers.get("Cache-Control", "")
    data = r.json()
    result = validate_response(data, expected_fingerprint=target.fingerprint)  # dezelfde controles als bij koppelen
    assert data["enrollment_replayed"] is False
    assert data["schema_version"] in {"1.2", "1.3"}, data["schema_version"]
    if data["schema_version"] == "1.3" or target.kind == "simulator":  # 1.3: rotation_sequence verplicht (§3.3)
        assert isinstance(data.get("rotation_sequence"), int) and data["rotation_sequence"] >= 0
        assert result.rotation_sequence == data["rotation_sequence"]
    STATE.update(first=result, data=data)


def test_replay_before_first_use_gives_new_token(target: Target) -> None:
    first = STATE["first"]
    r = enroll(target, body(target))
    assert r.status_code == 200, (target.kind, r.text)
    replay = validate_response(r.json(), expected_fingerprint=target.fingerprint)
    assert r.json()["enrollment_replayed"] is True and replay.service_id == first.service_id
    assert replay.service_token != first.service_token
    assert self_info(target, first.service_token).status_code == 401  # het ongebruikte token is ingetrokken
    STATE["token"] = replay.service_token


def test_services_self_confirms_and_closes_the_replay(target: Target) -> None:
    r = self_info(target, STATE["token"])
    assert r.status_code == 200, (target.kind, r.text)
    info = r.json()
    assert info["service_id"] == STATE["first"].service_id and info["app_id"] == APP
    assert info["keyset_fingerprint"] == STATE["first"].keyset_fingerprint
    again = enroll(target, body(target))
    assert again.status_code == 401 and again.json().get("code") == "invalid_enrollment"
    other_key = body(target, public_key_jwk=public_jwk(Ed25519PrivateKey.generate()))
    assert enroll(target, other_key).status_code == 401  # andere identiteitssleutel: nooit een herhaling


def test_jwks_and_key_rotations_endpoints(target: Target) -> None:
    with client() as c:
        jwks = c.get(target.base_url + "/api/v1/auth/jwks")
        rotations = c.get(target.base_url + "/api/v1/auth/key-rotations", params={"after": 0})
    assert jwks.status_code == 200
    assert {k["kid"] for k in jwks.json()["keys"]} >= {p.kid for p in STATE["first"].jwt_pins}
    assert rotations.status_code == 200, (target.kind, rotations.text)  # zonder authenticatie (§4.5)
    assert isinstance(rotations.json().get("items"), list)


def test_self_rotate_keeps_old_token_until_grace(target: Target) -> None:
    old = STATE["token"]
    with client() as c:
        r = c.post(target.base_url + "/api/v1/services/self/rotate", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 200, (target.kind, r.text)
    new = r.json()["service_token"]
    assert new.startswith("svc_") and r.json()["service_id"] == STATE["first"].service_id
    assert "oude_tokens_geldig_tot" in r.json()
    assert self_info(target, new).status_code == 200
    assert self_info(target, old).status_code == 200  # nog geldig tot 10 min na het eerste gebruik van het nieuwe


def test_pinned_kid_matches_a_thumbprint_or_key_id(target: Target) -> None:
    """Informatief: de dienst pint ``kid`` + ``x`` + thumbprint; de ``kid`` zelf mag een eigen ID van de Hub zijn."""
    for pin in STATE["first"].jwt_pins:
        assert pin.thumbprint == thumbprint_of_x(pin.x) and pin.kid
