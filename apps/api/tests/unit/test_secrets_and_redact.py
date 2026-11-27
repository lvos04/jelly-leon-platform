from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

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
    ensure_secrets(tmp_path)
    (tmp_path / VAULT_KEY).write_bytes(b"kort")
    with pytest.raises(SecretsError, match="ongeldige lengte"):
        Vault.load(tmp_path)
    (tmp_path / IDENTITY_KEY).unlink()
    with pytest.raises(SecretsError, match=r"identity\.ed25519 ontbreekt"):
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


@pytest.mark.parametrize("chmod", ["werkt", "geweigerd", "genegeerd"])
def test_first_start_on_an_acl_dataset_never_crashes(tmp_path: Path, acl_dataset: Any, chmod: str) -> None:
    """TrueNAS-preset Apps: bij aclmode=restricted weigert chmod; de eerste start (en elke volgende) gaat toch door."""
    acl_dataset(chmod, True)
    secrets_dir = tmp_path / "secrets"
    assert sorted(ensure_secrets(secrets_dir)) == [IDENTITY_KEY, VAULT_KEY]
    assert ensure_secrets(secrets_dir) == []
    modus = stat.S_IMODE((secrets_dir / VAULT_KEY).stat().st_mode)
    assert modus == (0o600 if chmod == "werkt" else 0o770)


@pytest.mark.parametrize("chmod", ["geweigerd", "genegeerd"])
@pytest.mark.parametrize("onveilig", ["map", VAULT_KEY, IDENTITY_KEY])
def test_onbevestigde_rechten_stoppen_initialisatie_en_gebruik(
    tmp_path: Path, acl_dataset: Any, chmod: str, onveilig: str,
) -> None:
    map_ = tmp_path / "sleutels"
    ensure_secrets(map_)
    voor = {naam: (map_ / naam).read_bytes() for naam in (VAULT_KEY, IDENTITY_KEY)}
    versleuteld = Vault.load(map_).encrypt("proefinhoud", "proef")
    if onveilig == "map":
        map_.chmod(0o755)
    else:
        (map_ / onveilig).chmod(0o644)
    acl_dataset(chmod, False)
    with pytest.raises(SecretsError):
        ensure_secrets(map_)
    for laad in (Vault.load, load_identity):
        with pytest.raises(SecretsError):
            laad(map_)
    assert not secrets_present(map_)
    assert voor == {naam: (map_ / naam).read_bytes() for naam in voor}
    acl_dataset("werkt", False)
    assert ensure_secrets(map_) == []
    assert Vault.load(map_).decrypt(versleuteld, "proef") == "proefinhoud"
    assert voor == {naam: (map_ / naam).read_bytes() for naam in voor}


@pytest.mark.parametrize("bestaand", [VAULT_KEY, IDENTITY_KEY])
def test_weigering_laat_de_andere_sleutel_ontbreken(tmp_path: Path, acl_dataset: Any, bestaand: str) -> None:
    pad = tmp_path / bestaand
    pad.touch(mode=0o644)
    pad.chmod(0o644)
    acl_dataset("geweigerd", False)
    with pytest.raises(SecretsError):
        ensure_secrets(tmp_path)
    assert list(tmp_path.iterdir()) == [pad] and pad.read_bytes() == b""


def test_geen_sleutelmateriaal_bij_onbevestigde_overerving(
    tmp_path: Path, acl_dataset: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lifecycle import secrets_store

    acl_dataset("genegeerd", True, toegang="groep", alleen_bestanden=True)
    monkeypatch.setattr(secrets_store.secrets, "token_bytes", lambda n: pytest.fail("sleutelmateriaal aangemaakt"))
    with pytest.raises(SecretsError):
        ensure_secrets(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("onderdeel", ["map", VAULT_KEY, IDENTITY_KEY])
def test_sleutelinitialisatie_en_lezers_weigeren_een_symbolische_link(tmp_path: Path, onderdeel: str) -> None:
    map_ = tmp_path / "sleutels"
    ensure_secrets(map_)
    voor = {naam: (map_ / naam).read_bytes() for naam in (VAULT_KEY, IDENTITY_KEY)}
    pad = map_ if onderdeel == "map" else map_ / onderdeel
    origineel = tmp_path / "origineel"
    pad.rename(origineel)
    pad.symlink_to(origineel)
    with pytest.raises(SecretsError):
        ensure_secrets(map_)
    assert not secrets_present(map_)
    for laad in (Vault.load, load_identity):
        with pytest.raises(SecretsError):
            laad(map_)
    assert pad.is_symlink() and voor == {naam: (map_ / naam).read_bytes() for naam in voor}
