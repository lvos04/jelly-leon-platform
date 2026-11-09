from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from lifecycle.redact import last4, redact, redact_text
from lifecycle.secrets_store import (
    IDENTITY_KEY,
    VAULT_KEY,
    SecretsError,
    Vault,
    ensure_secrets,
    load_identity,
    secrets_present,
)


def test_first_start_creates_secrets_once(tmp_path: Path) -> None:
    d = tmp_path / "secrets"
    assert ensure_secrets(d) == [VAULT_KEY, IDENTITY_KEY]
    before = (d / VAULT_KEY).read_bytes(), (d / IDENTITY_KEY).read_bytes()
    assert ensure_secrets(d) == []  # nooit overschrijven
    assert ((d / VAULT_KEY).read_bytes(), (d / IDENTITY_KEY).read_bytes()) == before
    assert stat.S_IMODE(d.stat().st_mode) == 0o700
    for name in (VAULT_KEY, IDENTITY_KEY):
        assert stat.S_IMODE((d / name).stat().st_mode) == 0o600
    assert secrets_present(d)


def test_too_wide_permissions_are_tightened(tmp_path: Path) -> None:
    d = tmp_path / "secrets"
    ensure_secrets(d)
    os.chmod(d / VAULT_KEY, 0o644)
    ensure_secrets(d)
    assert stat.S_IMODE((d / VAULT_KEY).stat().st_mode) == 0o600


def test_vault_roundtrip_and_aad(tmp_path: Path) -> None:
    ensure_secrets(tmp_path)
    vault = Vault.load(tmp_path)
    blob = vault.encrypt("svc_token_value", "hub_link.service_token")
    assert b"svc_token_value" not in blob
    assert vault.decrypt(blob, "hub_link.service_token") == "svc_token_value"
    with pytest.raises(Exception):  # noqa: B017 - verkeerde AAD moet falen (InvalidTag)
        vault.decrypt(blob, "andere.context")


def test_vault_rejects_bad_key(tmp_path: Path) -> None:
    (tmp_path / VAULT_KEY).write_bytes(b"kort")
    with pytest.raises(SecretsError):
        Vault.load(tmp_path)
    with pytest.raises(SecretsError):
        load_identity(tmp_path)


def test_redaction() -> None:
    assert redact({"password": "x", "service_token_last4": "abcd", "nested": [{"api_key": "k"}]}) == {
        "password": "***",
        "service_token_last4": "abcd",
        "nested": [{"api_key": "***"}],
    }
    assert "Bearer ***" in redact_text("Authorization: Bearer eyJabc.def.ghi")
    text = redact_text("trace 123e4567-e89b-12d3-a456-426614174000 digest " + "b" * 64)
    assert "123e4567-e89b-12d3-a456-426614174000" in text and "b" * 64 in text
    assert last4("abcdefgh1234") == "1234" and last4("kort") is None
