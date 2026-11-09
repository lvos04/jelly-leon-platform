from __future__ import annotations

import time
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hub_sim import HubState

from lifecycle.auth import (
    Authenticator,
    AuthError,
    JwksCache,
    parse_jwks,
    sha256_hex,
    verify_hub_jwt,
)
from lifecycle.errors import ApiError


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_cache(hub: HubState, fail: dict[str, bool], clock: Clock, **kw: Any) -> tuple[JwksCache, list[str]]:
    calls: list[str] = []

    def fetch(url: str) -> Any:
        calls.append(url)
        if fail.get("down"):
            raise ConnectionError("weg")
        return hub.jwks()

    cache = JwksCache(lambda: "http://hub/api/v1/auth/jwks", fetch, clock=clock, **kw)
    return cache, calls


def test_valid_token_roles_filtered() -> None:
    hub = HubState()
    cache, _ = make_cache(hub, {}, Clock())
    p = verify_hub_jwt(hub.mint("u1", ["operator", "superuser"], "jelly-leon-platform"), cache)
    assert p.kind == "user" and p.subject == "u1" and p.roles == frozenset({"operator"})


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"aud": "andere-dienst"}, "jwt_audience"),
        ({"iss": "nep-hub"}, "jwt_issuer"),
        ({"ttl_s": 600}, "jwt_lifetime"),
        ({"ttl_s": -60}, "jwt_expired"),
        ({"kid": "onbekend"}, "jwt_kid_unknown"),
    ],
)
def test_rejections(kwargs: dict[str, Any], reason: str) -> None:
    hub = HubState()
    cache, _ = make_cache(hub, {}, Clock())
    kwargs.setdefault("aud", "jelly-leon-platform")
    with pytest.raises(AuthError) as exc:
        verify_hub_jwt(hub.mint("u1", ["admin"], **kwargs), cache)
    assert exc.value.reason == reason


def test_forged_signature_rejected() -> None:
    hub = HubState()
    cache, _ = make_cache(hub, {}, Clock())
    forged = hub.mint("u1", ["admin"], "jelly-leon-platform", key=Ed25519PrivateKey.generate())
    with pytest.raises(AuthError) as exc:
        verify_hub_jwt(forged, cache)
    assert exc.value.reason == "jwt_invalid"


def test_alg_none_and_hs256_rejected() -> None:
    import jwt

    hub = HubState()
    cache, _ = make_cache(hub, {}, Clock())
    now = int(time.time())
    claims = {"iss": "hub", "aud": "jelly-leon-platform", "sub": "x", "iat": now, "exp": now + 60, "roles": ["admin"]}
    for token in (jwt.encode(claims, "geheim-sleutel-van-32-bytes-lang!!", algorithm="HS256"),
                  jwt.encode(claims, None, algorithm="none")):
        with pytest.raises(AuthError) as exc:
            verify_hub_jwt(token, cache)
        assert exc.value.reason in {"jwt_alg_rejected", "jwt_malformed"}


def test_cache_ttl_refresh_and_fail_closed() -> None:
    hub = HubState()
    clock = Clock()
    fail: dict[str, bool] = {}
    cache, calls = make_cache(hub, fail, clock, ttl_s=300, min_refresh_s=30, max_stale_s=900)
    tok = hub.mint("u1", ["viewer"], "jelly-leon-platform")
    verify_hub_jwt(tok, cache)
    verify_hub_jwt(tok, cache)
    assert len(calls) == 1  # gecachet
    fail["down"] = True
    clock.t += 400  # TTL verlopen, Hub weg: binnen max_stale blijft de cache bruikbaar
    verify_hub_jwt(tok, cache)
    assert len(calls) == 2
    clock.t += 10  # binnen min_refresh: geen nieuwe poging
    verify_hub_jwt(tok, cache)
    assert len(calls) == 2
    clock.t += 600  # voorbij max_stale → fail-closed
    with pytest.raises(AuthError) as exc:
        verify_hub_jwt(tok, cache)
    assert exc.value.reason == "jwks_unavailable"


def test_no_hub_url_fails_closed() -> None:
    cache = JwksCache(lambda: None, lambda url: {})
    with pytest.raises(AuthError) as exc:
        cache.key_for("k")
    assert exc.value.reason == "jwks_unavailable"


def test_parse_jwks_ignores_non_ed25519() -> None:
    keys = parse_jwks({"keys": [{"kty": "RSA", "kid": "r", "n": "x", "e": "AQAB"}, HubState().public_jwk()]})
    assert list(keys) == ["hub-sim-1"]
    with pytest.raises(ValueError):
        parse_jwks({"nope": 1})


def test_authenticator_service_token_constant_time_and_not_linked() -> None:
    cache = JwksCache(lambda: None, lambda url: {})
    stored: dict[str, str | None] = {"hash": None}
    a = Authenticator(cache, lambda: stored["hash"])
    with pytest.raises(ApiError) as exc:
        a.authenticate("Bearer svc_abc")
    assert exc.value.details["reason"] == "not_linked"
    stored["hash"] = sha256_hex("svc_goed_token_123456")
    assert a.authenticate("Bearer svc_goed_token_123456").roles == frozenset({"service"})
    with pytest.raises(ApiError) as exc:
        a.authenticate("Bearer svc_fout_token_123456")
    assert exc.value.status == 401
    for header in (None, "", "Basic abc", "Bearer "):
        with pytest.raises(ApiError):
            a.authenticate(header)
