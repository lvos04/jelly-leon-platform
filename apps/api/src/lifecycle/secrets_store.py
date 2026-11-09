"""Geheimen bij eerste start (contract §3) en de kleine vault.

``/appdata/secrets/`` (0700) met:
- ``vault.key``         32 willekeurige bytes, AES-256-GCM-sleutel voor opgeslagen credentials;
- ``identity.ed25519``  eigen Ed25519-identiteit (PKCS#8 PEM), publieke sleutel gaat mee bij de enrollment.

Bestaande bestanden worden **nooit** overschreven; alleen te ruime rechten worden teruggezet naar 0600.
De database heeft geen wachtwoord nodig: de ingebouwde PostgreSQL luistert alleen op een unix-socket in de container.
"""

from __future__ import annotations

import base64
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VAULT_KEY = "vault.key"
IDENTITY_KEY = "identity.ed25519"


class SecretsError(RuntimeError):
    pass


def _write_new(path: Path, data: bytes) -> bool:
    """Schrijft alleen als het bestand nog niet bestaat (O_EXCL). True = nieuw aangemaakt."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    return True


def _tighten(path: Path, mode: int) -> None:
    current = path.stat().st_mode & 0o777
    if current & ~mode:
        os.chmod(path, mode)


def ensure_secrets(secrets_dir: Path) -> list[str]:
    """Maakt ontbrekende geheimen aan. Geeft de namen terug van wat nieuw is aangemaakt (nooit de inhoud)."""
    try:
        secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except PermissionError as exc:
        raise SecretsError(f"kan {secrets_dir} niet aanmaken: geen schrijfrechten") from exc
    _tighten(secrets_dir, 0o700)
    created: list[str] = []
    if _write_new(secrets_dir / VAULT_KEY, secrets.token_bytes(32)):
        created.append(VAULT_KEY)
    pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    if _write_new(secrets_dir / IDENTITY_KEY, pem):
        created.append(IDENTITY_KEY)
    for name in (VAULT_KEY, IDENTITY_KEY):
        _tighten(secrets_dir / name, 0o600)
    return created


def secrets_present(secrets_dir: Path) -> bool:
    return all((secrets_dir / name).is_file() for name in (VAULT_KEY, IDENTITY_KEY))


@dataclass(frozen=True)
class Vault:
    key: bytes

    @classmethod
    def load(cls, secrets_dir: Path) -> Vault:
        try:
            key = (secrets_dir / VAULT_KEY).read_bytes()
        except FileNotFoundError as exc:
            raise SecretsError("vault.key ontbreekt; start de app opnieuw zodat hij wordt aangemaakt") from exc
        if len(key) != 32:
            raise SecretsError("vault.key heeft een ongeldige lengte (verwacht 32 bytes)")
        return cls(key)

    def encrypt(self, plaintext: str, aad: str) -> bytes:
        nonce = secrets.token_bytes(12)
        return b"v1" + nonce + AESGCM(self.key).encrypt(nonce, plaintext.encode(), aad.encode())

    def decrypt(self, blob: bytes, aad: str) -> str:
        if not blob.startswith(b"v1") or len(blob) < 2 + 12 + 16:
            raise SecretsError("onbekend vault-formaat")
        nonce, ciphertext = blob[2:14], blob[14:]
        return AESGCM(self.key).decrypt(nonce, ciphertext, aad.encode()).decode()


def load_identity(secrets_dir: Path) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key((secrets_dir / IDENTITY_KEY).read_bytes(), password=None)
    except FileNotFoundError as exc:
        raise SecretsError("identity.ed25519 ontbreekt") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise SecretsError("identity.ed25519 is geen Ed25519-sleutel")
    return key


def public_key_b64url(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
