"""Ondertekende herkomst (provenance) van manifesten — B08-001 "signed manifest provenance".

Twee lagen:

1. **Uitgever-handtekening (optioneel per bron, verplicht als de bron dat eist):** Ed25519 over de canonieke JSON
   van het manifest (sleutels gesorteerd, UTF-8, geen spaties — zelfde canonicalisatie als contract §8), los bestand
   ``<manifest>.sig`` met base64. De bron legt de vertrouwde publieke sleutel vast.
2. **Catalogus-provenance (altijd):** de appstore ondertekent per opgeslagen manifestversie een record met hash,
   bron (soort, URL, ref/commit, pad), ophaaltijd, schema-/validatorversie, validatie-uitkomst en de status van de
   uitgever-handtekening, met zijn eigen Ed25519-identiteit (``/appdata/secrets/identity.ed25519``).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from pydantic import BaseModel, ConfigDict

from .canonical import canonical_json


def raw_public(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def key_id(key: Ed25519PublicKey) -> str:
    """Zelfde sleutel-ID als de Hub (eerste 16 hex van sha256 over de ruwe publieke sleutel)."""
    return hashlib.sha256(raw_public(key)).hexdigest()[:16]


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def public_jwk(key: Ed25519PublicKey) -> dict[str, str]:
    return {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": b64url(raw_public(key)),
        "kid": key_id(key),
        "use": "sig",
        "alg": "EdDSA",
    }


def load_public_key(text: str) -> Ed25519PublicKey:
    """Publieke sleutel uit PEM (SubjectPublicKeyInfo) of base64/base64url van de 32 ruwe bytes."""
    text = text.strip()
    if text.startswith("-----BEGIN"):
        key = serialization.load_pem_public_key(text.encode())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("geen Ed25519-sleutel")
        return key
    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.replace("+", "-").replace("/", "_"))
    except (binascii.Error, ValueError) as exc:
        raise ValueError("sleutel is geen geldige base64") from exc
    if len(raw) != 32:
        raise ValueError("Ed25519-sleutel moet 32 bytes zijn")
    return Ed25519PublicKey.from_public_bytes(raw)


def sign_data(private_key: Ed25519PrivateKey, data: Any) -> str:
    return base64.b64encode(private_key.sign(canonical_json(data))).decode()


def verify_data(public_key: Ed25519PublicKey, data: Any, signature_b64: str) -> bool:
    try:
        signature = base64.b64decode(signature_b64.strip(), validate=True)
        public_key.verify(signature, canonical_json(data))
    except (InvalidSignature, binascii.Error, ValueError):
        return False
    return True


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    kind: Literal["bundled", "git", "http", "upload"]
    url: str | None
    ref: str | None
    commit: str | None
    path: str | None


class ProvenanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    app_id: str | None
    version: str | None
    manifest_sha256: str
    schema_sha256: str
    validator_version: str
    source: SourceRef
    fetched_at: datetime
    publisher_signature: Literal["verified", "not_required", "absent"]
    publisher_key_id: str | None
    valid: bool
    errors: int
    warnings: int


class SignedProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    record: ProvenanceRecord
    signature: str
    key_id: str
    alg: Literal["EdDSA"] = "EdDSA"


def sign_provenance(identity: Ed25519PrivateKey, record: ProvenanceRecord) -> SignedProvenance:
    return SignedProvenance(
        record=record,
        signature=sign_data(identity, record.model_dump(mode="json")),
        key_id=key_id(identity.public_key()),
    )


def verify_provenance(public_key: Ed25519PublicKey, signed: SignedProvenance) -> bool:
    return signed.key_id == key_id(public_key) and verify_data(
        public_key, signed.record.model_dump(mode="json"), signed.signature
    )
