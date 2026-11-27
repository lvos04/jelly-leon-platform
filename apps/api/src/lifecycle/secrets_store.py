"""Geheimen bij eerste start (contract §3) en de kleine vault.

``/appdata/secrets/`` (0700) met:
- ``vault.key``         32 willekeurige bytes, AES-256-GCM-sleutel voor opgeslagen credentials;
- ``identity.ed25519``  eigen Ed25519-identiteit (PKCS#8 PEM), publieke sleutel gaat mee bij de enrollment.

Bestaande sleutels worden nooit overschreven. Initialisatie en gebruik vereisen bevestigde privérechten
op de geopende map en de beide sleutelbestanden.
De database heeft geen wachtwoord nodig: de ingebouwde PostgreSQL luistert alleen op een unix-socket in de container.
"""

from __future__ import annotations

import base64
import fcntl
import os
import secrets
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from lifecycle.geheimbestand import LEESVLAGGEN, prive_rechten

VAULT_KEY = "vault.key"
IDENTITY_KEY = "identity.ed25519"


class SecretsError(RuntimeError):
    pass


def _controleer(fd: int, modus: int, naam: str, *, aanscherpen: bool) -> None:
    if not prive_rechten(fd, modus, aanscherpen=aanscherpen):
        raise SecretsError(f"privérechten van {naam} niet bevestigd")


@contextmanager
def _geopende_sleutels(
    secrets_dir: Path, *, initialisatie: bool = False,
) -> Iterator[tuple[int, dict[str, BinaryIO]]]:
    """Houd map en bestanden open vanaf de controle tot en met gebruik of publicatie."""
    try:
        with ExitStack() as stack:
            map_fd = os.open(secrets_dir, LEESVLAGGEN | os.O_DIRECTORY)
            stack.callback(os.close, map_fd)
            fcntl.flock(map_fd, fcntl.LOCK_EX if initialisatie else fcntl.LOCK_SH)
            _controleer(map_fd, 0o700, "sleutelmap", aanscherpen=initialisatie)
            bestanden: dict[str, BinaryIO] = {}
            for naam in (VAULT_KEY, IDENTITY_KEY):
                try:
                    fd = os.open(naam, LEESVLAGGEN, dir_fd=map_fd)
                except FileNotFoundError:
                    if initialisatie:
                        continue
                    raise SecretsError(f"{naam} ontbreekt") from None
                bestanden[naam] = stack.enter_context(os.fdopen(fd, "rb"))
                _controleer(fd, 0o600, naam, aanscherpen=initialisatie)
            yield map_fd, bestanden
    except OSError as exc:
        raise SecretsError("de sleutelmap of een sleutelbestand is niet veilig toegankelijk") from exc


def ensure_secrets(secrets_dir: Path) -> list[str]:
    """Maakt ontbrekende geheimen aan. Geeft de namen terug van wat nieuw is aangemaakt (nooit de inhoud)."""
    try:
        secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except PermissionError as exc:
        raise SecretsError("kan de sleutelmap niet aanmaken: geen schrijfrechten") from exc
    except OSError as exc:
        raise SecretsError("de sleutelmap kan niet worden aangemaakt") from exc
    with _geopende_sleutels(secrets_dir, initialisatie=True) as (map_fd, bestaande), ExitStack() as stack:
        nieuw: dict[str, tuple[str, BinaryIO]] = {}
        for naam in (VAULT_KEY, IDENTITY_KEY):
            if naam in bestaande:
                continue
            tijdelijk = f".{naam}.{uuid.uuid4().hex}.tmp"
            fd = os.open(tijdelijk, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=map_fd)
            stack.callback(os.unlink, tijdelijk, dir_fd=map_fd)
            bestand: BinaryIO = stack.enter_context(os.fdopen(fd, "wb"))
            _controleer(fd, 0o600, naam, aanscherpen=True)
            nieuw[naam] = tijdelijk, bestand

        # Ook de overgeërfde rechten van het laatste nieuwe bestand zijn nu gecontroleerd.
        for naam, (_, bestand) in nieuw.items():
            if naam == VAULT_KEY:
                inhoud = secrets.token_bytes(32)
            else:
                inhoud = Ed25519PrivateKey.generate().private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                )
            bestand.write(inhoud)
            bestand.flush()
            os.fsync(bestand.fileno())
        for naam, (tijdelijk, _) in nieuw.items():
            # Een volledig geschreven sleutel publiceren zonder een bestaande naam te vervangen.
            os.link(tijdelijk, naam, src_dir_fd=map_fd, dst_dir_fd=map_fd, follow_symlinks=False)
        if nieuw:
            os.fsync(map_fd)
        return list(nieuw)


def secrets_present(secrets_dir: Path) -> bool:
    try:
        with _geopende_sleutels(secrets_dir):
            return True
    except SecretsError:
        return False


@dataclass(frozen=True)
class Vault:
    key: bytes

    @classmethod
    def load(cls, secrets_dir: Path) -> Vault:
        with _geopende_sleutels(secrets_dir) as (_, bestanden):
            key = bestanden[VAULT_KEY].read()
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
    with _geopende_sleutels(secrets_dir) as (_, bestanden):
        key = serialization.load_pem_private_key(bestanden[IDENTITY_KEY].read(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SecretsError("identity.ed25519 is geen Ed25519-sleutel")
    return key


def public_key_b64url(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
