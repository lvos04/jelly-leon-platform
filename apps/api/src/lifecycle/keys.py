"""Sleutels, thumbprints en de sleutelset-vingerafdruk (contract 1.3 §3.3, §3.4, §4.5).

- **Thumbprint** = RFC 7638: ``base64url(SHA-256('{"crv":"Ed25519","kty":"OKP","x":"<x>"}'))``.
- **Sleutelset-vingerafdruk** (voor mensen): SHA-256 over
  ``"jl-hub-keyset-v1\\njwt:" + join(",", thumbprints van alle jwt-sleutels) + "\\npolicy:" + join(",", thumbprints
  van alle policy-sleutels)``, **beide lijsten gesorteerd op bytewaarde** (1.3: tijdens een policy-rotatie telt elke
  gepinde policy-sleutel mee; met één policy-sleutel gelijk aan 1.2); de eerste 80 bit als Crockford-base32, 16 tekens
  in groepen van 4 (``XXXX-XXXX-XXXX-XXXX``).
- Invoer van mensen (vingerafdruk, koppelcode) wordt genormaliseerd: hoofdletters, alleen letters/cijfers,
  ``O→0``, ``I/L→1``.

Een eigen implementatie (niet gedeeld met de Hub of de Hub-simulator); de testvectoren van het contract bewaken dat
alle implementaties hetzelfde rekenen (``tests/unit/test_keys.py``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
KEYSET_PREFIX = "jl-hub-keyset-v1"
_GROUP = "[0-9A-HJKMNP-TV-Z]{4}"
FINGERPRINT_RE = re.compile(rf"^{_GROUP}-{_GROUP}-{_GROUP}-{_GROUP}$")
KID_MAX = 128


class KeyFormatError(ValueError):
    """Ongeldige sleutel of ongeldig sleutelformaat (de melding bevat nooit geheim materiaal)."""


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", value):
        raise KeyFormatError("geen base64url")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError) as exc:
        raise KeyFormatError("geen base64url") from exc


def canonical_x(x: Any) -> bytes:
    """De 32 bytes van een Ed25519-``x``; alleen de canonieke base64url-vorm (43 tekens, geen opvulling) is geldig."""
    if not isinstance(x, str) or len(x) != 43:
        raise KeyFormatError("x moet 43 tekens base64url zijn")
    raw = b64url_decode(x)
    if len(raw) != 32 or b64url(raw) != x:
        raise KeyFormatError("x moet 32 bytes in canonieke base64url zijn")
    return raw


def thumbprint_of_x(x: str) -> str:
    canonical_x(x)
    canonical = json.dumps({"crv": "Ed25519", "kty": "OKP", "x": x}, separators=(",", ":"), sort_keys=True)
    return b64url(hashlib.sha256(canonical.encode("utf-8")).digest())


def jwk_thumbprint(jwk: Mapping[str, Any]) -> str:
    """RFC 7638-thumbprint van een **publieke** Ed25519-JWK."""
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or "d" in jwk:
        raise KeyFormatError("alleen een publieke Ed25519-JWK (kty=OKP, crv=Ed25519, zonder 'd')")
    return thumbprint_of_x(str(jwk.get("x")))


def crockford(data: bytes) -> str:
    bits = "".join(f"{byte:08b}" for byte in data)
    bits += "0" * (-len(bits) % 5)
    return "".join(CROCKFORD[int(bits[i : i + 5], 2)] for i in range(0, len(bits), 5))


def keyset_fingerprint_of_thumbprints(jwt_thumbprints: Iterable[str], policy_thumbprints: Iterable[str]) -> str:
    """§4.5: beide lijsten gesorteerd op bytewaarde; minstens één sleutel per doel."""
    jwt_tps = sorted(jwt_thumbprints, key=lambda t: t.encode("utf-8"))
    policy_tps = sorted(policy_thumbprints, key=lambda t: t.encode("utf-8"))
    if not jwt_tps or not policy_tps:
        raise KeyFormatError("minstens één JWT-sleutel en één policy-sleutel nodig")
    text = f"{KEYSET_PREFIX}\njwt:{','.join(jwt_tps)}\npolicy:{','.join(policy_tps)}"
    code = crockford(hashlib.sha256(text.encode("utf-8")).digest()[:10])
    return "-".join(code[i : i + 4] for i in range(0, 16, 4))


def keyset_fingerprint(jwt_keys: Iterable[Mapping[str, Any]], policy_keys: Iterable[Mapping[str, Any]]) -> str:
    return keyset_fingerprint_of_thumbprints((jwk_thumbprint(k) for k in jwt_keys),
                                             (jwk_thumbprint(k) for k in policy_keys))


def normalize_human(value: str) -> str:
    """Hoofdletterongevoelig, streepjes/spaties weg, ``O→0`` en ``I/L→1`` (contract §3.3, §4.5)."""
    cleaned = "".join(ch for ch in value.upper() if ch.isascii() and ch.isalnum())
    return cleaned.replace("O", "0").replace("I", "1").replace("L", "1")


def normalize_fingerprint(value: str | None) -> str | None:
    """Genormaliseerde vingerafdruk in de vorm ``XXXX-XXXX-XXXX-XXXX``, of ``None`` als het geen geldige is."""
    if value is None:
        return None
    flat = normalize_human(value)
    if len(flat) != 16 or any(ch not in CROCKFORD for ch in flat):
        return None
    return "-".join(flat[i : i + 4] for i in range(0, 16, 4))


def normalize_pairing_code(value: str | None) -> str | None:
    """Koppelcode: ≤ 32 tekens invoer, na normalisatie precies 12 tekens uit het Crockford-alfabet (anders ``None``)."""
    if value is None or len(value) > 32:
        return None
    flat = normalize_human(value)
    if len(flat) != 12 or any(ch not in CROCKFORD for ch in flat):
        return None
    return flat


def public_jwk(identity: Ed25519PrivateKey) -> dict[str, str]:
    """Eigen publieke sleutel voor het enroll-verzoek: precies ``kty``, ``crv``, ``x`` en ``kid`` = thumbprint."""
    x = b64url(identity.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": thumbprint_of_x(x)}


@dataclass(frozen=True)
class Pin:
    """Een gepinde Hub-sleutel zoals in ``hub-link.json`` (``pins.jwt`` / ``pins.policy``)."""

    kid: str
    x: str
    thumbprint: str

    def public_key(self) -> Ed25519PublicKey:
        return Ed25519PublicKey.from_public_bytes(canonical_x(self.x))

    def as_entry(self, pinned_at: str, source: str) -> dict[str, Any]:
        return {"kid": self.kid, "jwk": {"kty": "OKP", "crv": "Ed25519", "x": self.x}, "thumbprint": self.thumbprint,
                "pinned_at": pinned_at, "source": source}


def hub_jwk_to_pin(jwk: Any) -> Pin:
    """Een Hub-sleutel uit het enroll-antwoord of een rotatie: geldig Ed25519, zonder ``d``, met ``use:"sig"``,
    ``alg:"EdDSA"`` en een ``kid`` (contract §3.3). Gooit ``KeyFormatError`` bij elke afwijking."""
    if not isinstance(jwk, dict):
        raise KeyFormatError("JWK is geen object")
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise KeyFormatError("JWK is geen OKP/Ed25519")
    if "d" in jwk:
        raise KeyFormatError("JWK bevat privé-materiaal")
    if jwk.get("use") != "sig" or jwk.get("alg") != "EdDSA":
        raise KeyFormatError("JWK mist use=sig/alg=EdDSA")
    kid = jwk.get("kid")
    if not isinstance(kid, str) or not kid or len(kid) > KID_MAX:
        raise KeyFormatError("JWK mist een geldige kid")
    x = jwk.get("x")
    return Pin(kid=kid, x=str(x), thumbprint=thumbprint_of_x(str(x)))


def pin_from_entry(entry: Any) -> Pin:
    """Pin uit ``hub-link.json`` teruglezen; de thumbprint wordt opnieuw berekend en moet kloppen."""
    if not isinstance(entry, dict) or not isinstance(entry.get("jwk"), dict):
        raise KeyFormatError("ongeldige pin")
    jwk = entry["jwk"]
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or "d" in jwk:
        raise KeyFormatError("ongeldige pin")
    kid, x = entry.get("kid"), jwk.get("x")
    if not isinstance(kid, str) or not kid or not isinstance(x, str):
        raise KeyFormatError("ongeldige pin")
    tp = thumbprint_of_x(x)
    if entry.get("thumbprint") != tp:
        raise KeyFormatError("thumbprint van de pin klopt niet")
    return Pin(kid=kid, x=x, thumbprint=tp)
