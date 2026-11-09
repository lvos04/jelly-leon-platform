from __future__ import annotations

import pytest

from lifecycle.config import DEFAULT_PORT, ConfigError, load_settings, normalize_base_url, normalize_hub_url


def test_defaults_need_no_env() -> None:
    s = load_settings({})
    assert s.port == DEFAULT_PORT == 30440
    assert str(s.data_dir) == "/appdata"
    assert s.hub_url is None and s.enrollment_token is None and s.hub_key_fingerprint is None
    assert not s.allow_loopback_hub
    assert "host=/tmp/lifecycle-pg" in s.database_url


def test_hub_url_rules_section_3_2() -> None:
    assert normalize_hub_url(" http://nas.local:30400/ ") == "http://nas.local:30400"
    assert normalize_hub_url("HTTPS://NAS.example:30400") == "https://nas.example:30400"
    assert normalize_hub_url("http://[fd00::5]:30400") == "http://[fd00::5]:30400"
    assert normalize_hub_url("") is None
    for bad in ["ftp://x", "http://user:pw@x", "http://x/?a=1", "http://x#y", "x:30400", "http://", "http://x/pad",
                "http://127.0.0.1:30400", "http://localhost:30400", "http://[::1]:30400", "http://0.0.0.0:30400",
                "http://[::]:30400", "http://169.254.1.2:30400", "http://[fe80::1]:30400",
                "http://[::ffff:127.0.0.1]:30400", "http://x:99999", "http://bad_host:30400"]:
        with pytest.raises(ConfigError):
            normalize_hub_url(bad)


def test_loopback_only_with_explicit_dev_flag() -> None:
    assert normalize_hub_url("http://127.0.0.1:30499", allow_loopback=True) == "http://127.0.0.1:30499"
    s = load_settings({"HUB_URL": "http://127.0.0.1:30499", "LIFECYCLE_DEV_ALLOW_LOOPBACK_HUB": "1"})
    assert s.hub_url == "http://127.0.0.1:30499" and s.allow_loopback_hub
    with pytest.raises(ConfigError):
        load_settings({"HUB_URL": "http://127.0.0.1:30499"})
    with pytest.raises(ConfigError):  # link-local blijft ook met de vlag verboden
        load_settings({"HUB_URL": "http://169.254.0.1:30400", "LIFECYCLE_DEV_ALLOW_LOOPBACK_HUB": "1"})


def test_key_fingerprint_is_normalized_and_validated() -> None:
    s = load_settings({"HUB_KEY_FINGERPRINT": "megg 8gfg 2e4y k2rq"})
    assert s.hub_key_fingerprint == "MEGG-8GFG-2E4Y-K2RQ"
    with pytest.raises(ConfigError):
        load_settings({"HUB_KEY_FINGERPRINT": "MEGG-8GFG"})


def test_public_url_may_have_a_path() -> None:
    assert normalize_base_url("http://nas.example:30440/") == "http://nas.example:30440"
    assert normalize_base_url("https://proxy.example/jlp") == "https://proxy.example/jlp"
    for bad in ("ftp://x", "http://u:p@x", "http://x?a", "http://x y"):
        with pytest.raises(ConfigError):
            normalize_base_url(bad)


def test_enrollment_token_not_in_repr() -> None:
    s = load_settings({"HUB_URL": "http://hub.example:30400", "HUB_ENROLLMENT_TOKEN": "enr_" + "g" * 43})
    assert "enr_" not in repr(s)
    assert s.hub_url == "http://hub.example:30400"  # alleen voor de enrollment; JWT's pas na geslaagde koppeling


@pytest.mark.parametrize(("name", "value"), [("LIFECYCLE_PORT", "abc"), ("LIFECYCLE_PORT", "0"),
                                             ("LIFECYCLE_PORT", "70000"),
                                             ("LIFECYCLE_KEY_ROTATION_POLL_SECONDS", "3600")])
def test_int_validation(name: str, value: str) -> None:
    with pytest.raises(ConfigError):
        load_settings({name: value})
