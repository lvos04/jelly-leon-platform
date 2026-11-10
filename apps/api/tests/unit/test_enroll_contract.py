"""Contract 1.3 §3.3 (C1.3-JLP-05): het enroll-verzoek bevat exact de toegestane velden (A5) en het antwoord wordt
volledig gecontroleerd vóór opslaan; elke afwijking is definitief en slaat niets op. ``rotation_sequence`` (nieuw in
1.3) wordt overgenomen; een Hub 1.2 zonder dat veld geeft 0. HTTP-uitkomst per vaste ``last_error``-code (§3.5)."""

from __future__ import annotations

import copy
import uuid
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hub_sim import HubState

from lifecycle.config import Settings
from lifecycle.enrollment import (
    REQUEST_FIELDS,
    EnrollmentError,
    build_request,
    post_enroll,
    validate_response,
)
from lifecycle.keys import thumbprint_of_x


def good_response(sim: HubState) -> dict[str, Any]:
    return {
        "schema_version": "1.3", "service_id": str(uuid.uuid4()), "app_id": "jelly-leon-platform",
        "service_token": "svc_" + "A" * 43, "token_type": "Bearer", "hub_issuer": "hub", "jwks": sim.jwks(),
        "policy_public_key": sim.policy_jwk(), "keyset_fingerprint": sim.keyset_fingerprint(),
        "rotation_sequence": 0,
        "jwks_url": "/api/v1/auth/jwks", "events_inbox_url": "/api/v1/events/inbox",
        "key_rotation_url": "/api/v1/auth/key-rotations", "enrollment_replayed": False,
    }


def test_request_has_exactly_the_contract_fields() -> None:
    key = Ed25519PrivateKey.generate()
    for kind, field in (("token", "enrollment_token"), ("koppelcode", "pairing_code")):
        body = build_request(kind, "geheim", key, Settings(public_url="http://nas.example:30440"))
        assert set(body) == {"app_id", "version", "base_url", "public_key_jwk", field} and set(body) <= REQUEST_FIELDS
        assert body["app_id"] == "jelly-leon-platform"  # vaste constante (R1), nooit de TrueNAS-appnaam
        jwk = body["public_key_jwk"]
        assert set(jwk) == {"kty", "crv", "x", "kid"} and jwk["kid"] == thumbprint_of_x(jwk["x"])  # geen use/alg/d
    assert build_request("token", "x", key, Settings())["base_url"] is None  # verplicht veld, mag null
    with pytest.raises(ValueError):
        build_request("anders", "x", key, Settings())


def test_valid_response_is_accepted_and_pinned() -> None:
    sim = HubState()
    result = validate_response(good_response(sim), expected_fingerprint=None)
    assert result.keyset_fingerprint == sim.keyset_fingerprint()
    assert [p.kid for p in result.jwt_pins] == [sim.kid] and len(result.policy_pins) == 1
    assert result.rotation_sequence == 0
    # minor-versie hoger en onbekende extra velden: geaccepteerd (alleen major 1 telt)
    later = {**good_response(sim), "schema_version": "1.9", "nieuw_veld": 1, "rotation_sequence": 7}
    assert validate_response(later, expected_fingerprint=sim.keyset_fingerprint().lower().replace("-", " ")
                             ).rotation_sequence == 7
    # een Hub 1.2 stuurt geen rotation_sequence: dan 0 (§3.3)
    oud = {k: v for k, v in good_response(sim).items() if k != "rotation_sequence"} | {"schema_version": "1.2"}
    assert validate_response(oud, expected_fingerprint=None).rotation_sequence == 0


def _mutations(sim: HubState) -> list[tuple[str, Any]]:
    other_jwk = {**sim.policy_jwk(), "kid": sim.kid}
    return [
        ("schema_version", "2.0"), ("service_id", "geen-uuid"), ("app_id", "home-control-hub"),
        ("service_token", "svc_kort"), ("service_token", "token_zonder_prefix" + "A" * 30), ("token_type", "Basic"),
        ("hub_issuer", "nep"), ("enrollment_replayed", "nee"), ("jwks", {"keys": []}), ("jwks", None),
        ("jwks", {"keys": [{**sim.public_jwk(), "d": "privé"}]}),
        ("jwks", {"keys": [{**sim.public_jwk(), "use": "enc"}]}),
        ("jwks", {"keys": [sim.public_jwk(), other_jwk]}),  # dubbele kid
        ("policy_public_key", {k: v for k, v in sim.policy_jwk().items() if k != "alg"}), ("jwks_url", None),
        ("key_rotation_url", None), ("events_inbox_url", 5),
        ("rotation_sequence", -1), ("rotation_sequence", "3"), ("rotation_sequence", True), ("rotation_sequence", 1.5),
    ]


def test_every_invalid_field_is_refused_as_hub_antwoord_ongeldig() -> None:
    sim = HubState()
    for name, value in _mutations(sim):
        body = good_response(sim)
        if value is None:
            body.pop(name)
        else:
            body[name] = value
        with pytest.raises(EnrollmentError) as exc:
            validate_response(body, expected_fingerprint=None)
        assert exc.value.code == "hub_antwoord_ongeldig" and exc.value.final, (name, value)


def test_aliases_from_earlier_assumptions_do_not_exist() -> None:
    sim = HubState()
    body = good_response(sim)
    alias = {"id": body.pop("service_id"), "token": body.pop("service_token"),
             "policy_public_jwk": body.pop("policy_public_key"), **body}
    with pytest.raises(EnrollmentError) as exc:
        validate_response(alias, expected_fingerprint=None)
    assert exc.value.code == "hub_antwoord_ongeldig"


def test_fingerprint_must_match_response_and_expectation() -> None:
    sim = HubState()
    wrong = {**good_response(sim), "keyset_fingerprint": "0000-0000-0000-0000"}
    with pytest.raises(EnrollmentError) as exc:
        validate_response(wrong, expected_fingerprint=None)
    assert exc.value.code == "hub_vingerafdruk_onjuist" and exc.value.final
    with pytest.raises(EnrollmentError) as exc:
        validate_response(good_response(sim), expected_fingerprint="MEGG-8GFG-2E4Y-K2RQ")  # andere Hub
    assert exc.value.code == "hub_vingerafdruk_onjuist"


def _client(status: int, body: Any = None, headers: dict[str, str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body, headers=headers or {})

    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.mark.parametrize(
    ("status", "headers", "code", "final", "retry"),
    [
        (401, None, "hub_weigerde", True, None),
        (404, None, "enroll_endpoint_ontbreekt", True, None),
        (413, None, "hub_incompatibel", True, None),
        (422, None, "hub_incompatibel", True, None),
        (400, None, "hub_incompatibel", True, None),
        (429, {"Retry-After": "120"}, "rate_limited", False, 120.0),
        (500, None, "hub_onbereikbaar", False, None),
        (503, None, "hub_onbereikbaar", False, None),
        (503, {"Retry-After": "30"}, "hub_onbereikbaar", False, 30.0),  # §5: nooit eerder dan Retry-After
        (503, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, "hub_onbereikbaar", False, None),  # datumvorm
        (500, {"Retry-After": "30"}, "hub_onbereikbaar", False, None),  # alleen 429/503 dragen Retry-After
        (429, {"Retry-After": "1800"}, "rate_limited", False, 1800.0),  # boven de backoffgrens: toch volgen
        (307, {"Location": "http://elders/"}, "hub_onbereikbaar", False, None),  # redirect = storing, niet volgen
    ],
)
def test_http_status_mapping(status: int, headers: dict[str, str] | None, code: str, final: bool,
                             retry: float | None) -> None:
    with _client(status, {"code": "x"}, headers) as client, pytest.raises(EnrollmentError) as exc:
        post_enroll(client, "http://hub.example", {}, expected_fingerprint=None)
    assert (exc.value.code, exc.value.final, exc.value.retry_after) == (code, final, retry)


def test_network_error_is_retryable() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("weg", request=request)

    with httpx.Client(transport=httpx.MockTransport(boom)) as client, pytest.raises(EnrollmentError) as exc:
        post_enroll(client, "http://hub.example", {}, expected_fingerprint=None)
    assert exc.value.code == "hub_onbereikbaar" and not exc.value.final


def test_forbidden_address_after_dns_is_hub_adres_ongeldig_and_not_final(monkeypatch: pytest.MonkeyPatch) -> None:
    """§3.2: een naam die naar loopback wijst (DNS) wordt per verbinding geweigerd; het token blijft bruikbaar."""
    import socket

    from lifecycle.nettransport import guarded_transport

    def resolve(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with httpx.Client(transport=guarded_transport(allow_loopback=False)) as client, \
            pytest.raises(EnrollmentError) as exc:
        post_enroll(client, "http://hub.voorbeeld.invalid:30400", {}, expected_fingerprint=None)
    assert exc.value.code == "hub_adres_ongeldig" and not exc.value.final


def test_response_validation_does_not_mutate_input() -> None:
    sim = HubState()
    body = good_response(sim)
    before = copy.deepcopy(body)
    validate_response(body, expected_fingerprint=None)
    assert body == before
