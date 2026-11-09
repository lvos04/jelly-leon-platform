"""A5 / contract 1.2 §4.5: thumbprints en sleutelset-vingerafdruk tegen de testvectoren van het contract, plus de
normalisatie van menselijke invoer (vingerafdruk, koppelcode)."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lifecycle.keys import (
    KeyFormatError,
    canonical_x,
    hub_jwk_to_pin,
    jwk_thumbprint,
    keyset_fingerprint,
    normalize_fingerprint,
    normalize_pairing_code,
    pin_from_entry,
    public_jwk,
    thumbprint_of_x,
)

JWT_X = "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo"  # RFC 8037 A.1
POLICY_X = "A6EHv_POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg"
SECOND_X = "Kay64UG8yvCyLhqU000LxzYeUm0L_hLIl5S8kyKWbdc"


def jwk(x: str) -> dict[str, str]:
    return {"kty": "OKP", "crv": "Ed25519", "x": x}


def test_thumbprints_match_contract_vectors() -> None:
    assert thumbprint_of_x(JWT_X) == "kPrK_qmxVWaYVA9wwBF6Iuo3vVzz7TxHCTwXBygrS4k"
    assert thumbprint_of_x(POLICY_X) == "1IG2tMH7J2wbJZnOf8LJzQitKf7LMvoAElsuDMVM54Y"
    assert thumbprint_of_x(SECOND_X) == "AkXIZFzonb59ZmGwyKgi3H3BwMi6amevqdKQiLZhdtc"
    # andere velden in de JWK (use/alg/kid) tellen niet mee in de RFC 7638-thumbprint
    assert jwk_thumbprint({**jwk(JWT_X), "use": "sig", "alg": "EdDSA", "kid": "x"}) == thumbprint_of_x(JWT_X)


def test_keyset_fingerprint_matches_contract_vectors_in_any_order() -> None:
    assert keyset_fingerprint([jwk(JWT_X)], jwk(POLICY_X)) == "MEGG-8GFG-2E4Y-K2RQ"
    assert keyset_fingerprint([jwk(JWT_X), jwk(SECOND_X)], jwk(POLICY_X)) == "CYRC-JS8C-14AN-JR17"
    assert keyset_fingerprint([jwk(SECOND_X), jwk(JWT_X)], jwk(POLICY_X)) == "CYRC-JS8C-14AN-JR17"
    # de rol telt: dezelfde sleutels met verwisselde doelen geven een andere vingerafdruk
    assert keyset_fingerprint([jwk(POLICY_X)], jwk(JWT_X)) != "MEGG-8GFG-2E4Y-K2RQ"
    with pytest.raises(KeyFormatError):
        keyset_fingerprint([], jwk(POLICY_X))


@pytest.mark.parametrize("raw", ["MEGG-8GFG-2E4Y-K2RQ", "megg8gfg2e4yk2rq", " MEGG 8GFG 2E4Y K2RQ ",
                                 "megg-8gfg-2e4y-k2rq"])
def test_fingerprint_normalization(raw: str) -> None:
    assert normalize_fingerprint(raw) == "MEGG-8GFG-2E4Y-K2RQ"


def test_fingerprint_normalization_maps_confusables_and_rejects_bad_input() -> None:
    assert normalize_fingerprint("0OIL-1111-2222-3333") == "0011-1111-2222-3333"  # O→0, I/L→1
    for bad in ("MEGG-8GFG-2E4Y", "MEGG-8GFG-2E4Y-K2RQ-X", "UUUU-UUUU-UUUU-UUUU", "", "ÄÄÄÄ-8GFG-2E4Y-K2RQ"):
        assert normalize_fingerprint(bad) is None, bad


def test_pairing_code_normalization() -> None:
    assert normalize_pairing_code("abcd-efgh-jkmn") == "ABCDEFGHJKMN"
    assert normalize_pairing_code("K0PP-ELI1-2345") == "K0PPE1112345"
    for bad in ("ABCD-EFGH-JKM", "ABCD-EFGH-JKMNP", "ABCU-EFGH-JKMN", "x" * 33, ""):
        assert normalize_pairing_code(bad) is None, bad


def test_non_canonical_or_wrong_length_x_rejected() -> None:
    canonical_x(JWT_X)
    for bad in (JWT_X + "=", JWT_X[:-1], JWT_X[:-1] + "p", "A" * 42 + "!", 7):  # 'p' laat padding-bits staan
        with pytest.raises(KeyFormatError):
            canonical_x(bad)


def test_own_public_jwk_has_exactly_kty_crv_x_kid_with_thumbprint_kid() -> None:
    key = public_jwk(Ed25519PrivateKey.generate())
    assert set(key) == {"kty", "crv", "x", "kid"}
    assert key["kid"] == thumbprint_of_x(key["x"])


def test_hub_jwk_requirements() -> None:
    good = {**jwk(JWT_X), "kid": "k1", "use": "sig", "alg": "EdDSA"}
    pin = hub_jwk_to_pin(good)
    assert pin.kid == "k1" and pin.thumbprint == thumbprint_of_x(JWT_X)
    for bad in ({**good, "d": "geheim"}, {**good, "use": "enc"}, {k: v for k, v in good.items() if k != "alg"},
                {**good, "kid": ""}, {**good, "crv": "X25519"}, {**good, "x": JWT_X[:-1]}, "geen-object"):
        with pytest.raises(KeyFormatError):
            hub_jwk_to_pin(bad)
    entry = pin.as_entry("2026-11-09T00:00:00Z", "enroll")
    assert pin_from_entry(entry) == pin
    with pytest.raises(KeyFormatError):
        pin_from_entry({**entry, "thumbprint": "vervalst"})
