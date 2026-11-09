from __future__ import annotations

import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lifecycle.config import Settings
from lifecycle.enrollment import REQUEST_FIELDS, EnrollmentError, build_request, jwk_thumbprint, parse_response
from lifecycle.errors import ApiError
from lifecycle.idempotency import parse_key, request_hash
from lifecycle.outbox import backoff_seconds
from lifecycle.pagination import MAX_LIMIT, clamp_limit, decode_cursor, encode_cursor


def test_enroll_request_shape_and_secret_placement() -> None:
    key = Ed25519PrivateKey.generate()
    body = build_request("token", "eenmalig-token", key, Settings(public_url="http://nas:30440"))
    assert body["app_id"] == "jelly-leon-platform" and body["enrollment_token"] == "eenmalig-token"
    assert "pairing_code" not in body
    assert body["public_key_jwk"]["kty"] == "OKP" and body["public_key_jwk"]["crv"] == "Ed25519"
    body2 = build_request("koppelcode", "ABC-123", key, Settings())
    assert body2["pairing_code"] == "ABC-123" and "enrollment_token" not in body2
    with pytest.raises(ValueError):
        build_request("anders", "x", key, Settings())


def test_enroll_request_has_only_fields_the_hub_accepts() -> None:
    """Review P0-afronding: de Hub (``EnrollIn``, extra="forbid") en contract 1.2 §3.3 weigeren onbekende velden met
    422. Eerder stuurde de client ook ``kind``, ``capabilities`` en ``schema_version`` mee (en ``use``/``alg`` in de
    JWK): tegen de echte Hub was elk eenmalig token dan verbrand."""
    key = Ed25519PrivateKey.generate()
    for kind, field in (("token", "enrollment_token"), ("koppelcode", "pairing_code")):
        body = build_request(kind, "geheim-123456", key, Settings())
        assert set(body) == {"app_id", "version", "base_url", "public_key_jwk", field}
        assert set(body) <= REQUEST_FIELDS
        jwk = body["public_key_jwk"]
        assert set(jwk) == {"kty", "crv", "x", "kid"} and "d" not in jwk
        assert jwk["kid"] == jwk_thumbprint(jwk["x"])
    # RFC 8037 bijlage A.3: thumbprint van de voorbeeldsleutel
    rfc_x, rfc_thumbprint = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo", "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"
    assert jwk_thumbprint(rfc_x) == rfc_thumbprint


def test_parse_response_tolerant_but_strict_on_token() -> None:
    r = parse_response({"id": 7, "token": "t" * 20, "jwks": {"keys": []}, "policy_public_key": {"x": "abc"}})
    assert r.service_id == "7" and r.jwks is None and r.policy_public_key == "abc"
    for bad in ([], {"service_id": "x"}, {"service_id": "x", "service_token": "kort"}, {"service_token": "t" * 20}):
        with pytest.raises(EnrollmentError):
            parse_response(bad)


def test_idempotency_key_and_hash() -> None:
    with pytest.raises(ApiError) as exc:
        parse_key(None)
    assert exc.value.code == "idempotency_key_required"
    with pytest.raises(ApiError):
        parse_key("geen-uuid")
    k = uuid.uuid4()
    assert parse_key(str(k)) == k
    assert request_hash("post", "/x", {"a": 1, "b": 2}) == request_hash("POST", "/x", {"b": 2, "a": 1})
    assert request_hash("POST", "/x", {"a": 1}) != request_hash("POST", "/x", {"a": 2})


def test_pagination() -> None:
    assert clamp_limit(None) == 50 and clamp_limit(MAX_LIMIT) == 200
    for bad in (0, 201):
        with pytest.raises(ApiError):
            clamp_limit(bad)
    assert decode_cursor(encode_cursor({"after": 5})) == {"after": 5}
    with pytest.raises(ApiError):
        decode_cursor("!!!")


def test_backoff_grows_and_caps() -> None:
    assert 1 <= backoff_seconds(0) <= 1.25
    assert backoff_seconds(5) >= 32
    assert backoff_seconds(30) <= 3600 * 1.25
