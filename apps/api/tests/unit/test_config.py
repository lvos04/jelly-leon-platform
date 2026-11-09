from __future__ import annotations

import pytest

from lifecycle.config import DEFAULT_PORT, ConfigError, load_settings, normalize_hub_url


def test_defaults_need_no_env() -> None:
    s = load_settings({})
    assert s.port == DEFAULT_PORT == 30440
    assert str(s.data_dir) == "/appdata"
    assert s.hub_url is None and s.enrollment_token is None
    assert "host=/tmp/lifecycle-pg" in s.database_url


def test_hub_url_validation() -> None:
    assert normalize_hub_url(" http://nas.local:30400/ ") == "http://nas.local:30400"
    assert normalize_hub_url("") is None
    for bad in ["ftp://x", "http://user:pw@x", "http://x/?a=1", "x:30400", "http://"]:
        with pytest.raises(ConfigError):
            normalize_hub_url(bad)


def test_enrollment_token_not_in_repr() -> None:
    s = load_settings({"HUB_URL": "http://hub:30400", "HUB_ENROLLMENT_TOKEN": "geheim-token-123456"})
    assert "geheim-token" not in repr(s)
    assert s.jwks_url == "http://hub:30400/api/v1/auth/jwks"


@pytest.mark.parametrize("value", ["abc", "0", "70000"])
def test_port_validation(value: str) -> None:
    with pytest.raises(ConfigError):
        load_settings({"LIFECYCLE_PORT": value})
