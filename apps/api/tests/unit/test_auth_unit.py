"""Contract 1.2 §4.3–§4.7: alleen Hub-JWT's met gepinde sleutels, vaste ``reason`` per controle en de
controlevolgorde ``state → tokensoort → handtekening/kid → claims → token_use → jti``."""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from hub_sim import HubState

from lifecycle.auth import Authenticator, verify_hub_jwt
from lifecycle.errors import ApiError
from lifecycle.hublink import HubLinkStore, Link, clear_link, now_iso
from lifecycle.keys import hub_jwk_to_pin, keyset_fingerprint_of_thumbprints

AUD = "jelly-leon-platform"


def linked(store: HubLinkStore, sim: HubState) -> str:
    service_id, token = sim.register_service(AUD)
    pins = [hub_jwk_to_pin(k) for k in sim.jwks()["keys"]]
    policy = hub_jwk_to_pin(sim.policy_jwk())
    at = now_iso()
    store.update(lambda d: d.update(
        state="gekoppeld", hub_url="http://hub.example", service_id=service_id, first_linked_at=at, linked_at=at,
        service_token=token, service_token_last4=token[-4:],
        pins={"jwt": [p.as_entry(at, "enroll") for p in pins], "policy": [policy.as_entry(at, "enroll")],
              "keyset_fingerprint": keyset_fingerprint_of_thumbprints([p.thumbprint for p in pins],
                                                                      policy.thumbprint),
              "rotation_sequence": 0}))
    return str(token)


@pytest.fixture
def env(tmp_path: Path) -> tuple[HubLinkStore, HubState, Authenticator, list[int]]:
    sim = HubState()
    store = HubLinkStore(tmp_path)
    linked(store, sim)
    polls: list[int] = []
    auth = Authenticator(store, None, on_unknown_kid=lambda: polls.append(1))
    return store, sim, auth, polls



def reason(auth: Authenticator, token: str | None, **kw: Any) -> str:
    with pytest.raises(ApiError) as exc:
        auth.authenticate(None if token is None else f"Bearer {token}", **kw)
    assert exc.value.status == 401 and exc.value.code == "unauthorized"
    assert exc.value.headers.get("WWW-Authenticate") == "Bearer"
    return str(exc.value.details["reason"])


def test_user_and_service_tokens_accepted(env: Any) -> None:
    store, sim, auth, _ = env
    p = auth.authenticate("Bearer " + sim.mint("u1", ["operator", "superuser"], AUD))
    assert p.kind == "user" and p.subject == "u1" and p.roles == frozenset({"operator"})  # onbekende rol telt niet
    s = auth.authenticate("Bearer " + sim.mint_service(AUD, store.read().service_id))
    assert s.kind == "service" and s.subject == "hub" and s.roles == frozenset({"service"})


def test_kind_role_always_wins(env: Any) -> None:
    _, sim, auth, _ = env
    assert auth.authenticate("Bearer " + sim.mint("kindje", ["admin", "kind"], AUD)).roles == frozenset({"kind"})


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"aud": "andere-dienst"}, "invalid_token"),
        ({"iss": "nep-hub"}, "invalid_token"),
        ({"ttl_s": 600}, "invalid_token"),  # exp − iat > 120 s
        ({"ttl_s": 60, "iat_offset": -100}, "token_expired"),  # exp + 30 s verstreken
        ({"ttl_s": 60, "iat_offset": 120}, "invalid_token"),  # iat/nbf in de toekomst (> 30 s)
        ({"kid": "onbekend"}, "unknown_key"),
        ({"omit": ("nbf",)}, "invalid_token"),
        ({"omit": ("exp",)}, "invalid_token"),
        ({"omit": ("sub",)}, "invalid_token"),
        ({"token_use": None}, "token_use_invalid"),
        ({"token_use": "admin"}, "token_use_invalid"),
        ({"omit": ("jti",)}, "jti_missing"),
        ({"jti": "geen-uuid"}, "invalid_token"),
        ({"typ": "hub-key-rotation+jws"}, "invalid_token"),
    ],
)
def test_user_token_rejections(env: Any, kwargs: dict[str, Any], expected: str) -> None:
    _, sim, auth, _ = env
    kwargs.setdefault("aud", AUD)
    assert reason(auth, sim.mint("u1", ["admin"], **kwargs)) == expected


def test_expiry_has_30_seconds_clock_skew(env: Any) -> None:
    _, sim, auth, _ = env
    assert auth.authenticate("Bearer " + sim.mint("u1", ["viewer"], AUD, ttl_s=60, iat_offset=-80)).roles


def test_user_token_with_service_role_and_bad_service_tokens(env: Any) -> None:
    store, sim, auth, _ = env
    assert reason(auth, sim.mint("u1", ["service"], AUD)) == "token_use_invalid"
    sid = store.read().service_id
    assert reason(auth, sim.mint_service(AUD, sid, sub="iemand")) == "token_use_invalid"
    assert reason(auth, sim.mint_service(AUD, sid, roles=["service", "admin"])) == "token_use_invalid"
    assert reason(auth, sim.mint_service(AUD, str(uuid.uuid4()))) == "token_use_invalid"  # andere registratie


def test_service_id_compared_as_uuid(env: Any) -> None:
    store, sim, auth, _ = env
    sid = str(store.read().service_id)
    assert auth.authenticate("Bearer " + sim.mint_service(AUD, sid.upper())).subject == "hub"
    assert reason(auth, sim.mint_service(AUD, "geen-uuid")) == "token_use_invalid"


def test_signature_checked_against_pinned_key_not_only_kid(env: Any) -> None:
    _, sim, auth, polls = env
    forged = sim.mint("u1", ["admin"], AUD, key=Ed25519PrivateKey.generate())  # zelfde kid, andere sleutel
    assert reason(auth, forged) == "invalid_token"
    assert polls == []


def test_unknown_kid_triggers_background_poll(env: Any) -> None:
    _, sim, auth, polls = env
    assert reason(auth, sim.mint("u1", ["admin"], AUD, kid="nieuw-1")) == "unknown_key"
    assert polls == [1]


def test_alg_none_and_hs256_and_garbage(env: Any) -> None:
    _, _, auth, _ = env
    now = int(time.time())
    claims = {"iss": "hub", "aud": AUD, "sub": "x", "iat": now, "nbf": now, "exp": now + 60, "roles": ["admin"],
              "token_use": "user", "jti": str(uuid.uuid4())}
    for token in (jwt.encode(claims, "geheim-sleutel-van-32-bytes-lang!!", algorithm="HS256"),
                  jwt.encode(claims, None, algorithm="none"), "a.b.c", "x" * 9000):
        assert reason(auth, token) == "invalid_token"
    assert reason(auth, None) == "invalid_token"
    with pytest.raises(ApiError):
        auth.authenticate("Basic abc")


def test_incoming_service_token_never_accepted(env: Any) -> None:
    store, _, auth, _ = env
    own = store.read().service_token
    assert reason(auth, own) == "service_token_not_accepted"
    assert reason(auth, "svc_" + "x" * 43) == "service_token_not_accepted"


def test_state_check_comes_first(env: Any) -> None:
    store, sim, auth, _ = env
    good = sim.mint("u1", ["admin"], AUD)
    store.update(lambda d: clear_link(d, "ingetrokken", error="ingetrokken_door_hub"))
    assert reason(auth, good) == "revoked"
    assert reason(auth, "svc_" + "x" * 43) == "revoked"  # ook vóór de tokensoort
    store.update(lambda d: d.update(state="ontkoppeld"))
    assert reason(auth, good) == "not_linked"


def test_not_linked_and_corrupt_file_fail_closed(tmp_path: Path) -> None:
    sim = HubState()
    store = HubLinkStore(tmp_path)
    auth = Authenticator(store, None)
    assert reason(auth, sim.mint("u1", ["admin"], AUD)) == "not_linked"
    linked(store, sim)
    (tmp_path / "hub-link.json").write_text("{ kapot")
    assert store.read().corrupt
    assert reason(auth, sim.mint("u1", ["admin"], AUD)) == "not_linked"


def test_verify_returns_claims_for_replay_check(env: Any) -> None:
    store, sim, _, _ = env
    jti = str(uuid.uuid4())
    link: Link = store.read()
    principal, claims = verify_hub_jwt(sim.mint("u1", ["viewer"], AUD, jti=jti), link)
    assert claims["jti"] == jti and principal.roles == frozenset({"viewer"})
