from __future__ import annotations

import pytest

from lifecycle.config import (
    DEFAULT_PORT,
    ConfigError,
    host_header_allowed,
    load_settings,
    normalize_base_url,
    normalize_hub_url,
)


def test_defaults_need_no_env() -> None:
    s = load_settings({})
    assert s.port == DEFAULT_PORT == 30440
    assert str(s.data_dir) == "/appdata"
    assert s.hub_url is None and s.enrollment_token is None and s.hub_key_fingerprint is None
    assert not s.allow_loopback_hub
    assert "host=/tmp/lifecycle-pg" in s.database_url


def test_empty_values_fall_back_to_defaults_and_data_dir_must_be_absolute() -> None:
    """Review: een leeg veld (bijv. uit .env.example of het formulier) gaf een lege database-URL en de werkmap als
    datamap; nu de standaard, en een relatief pad wordt geweigerd."""
    s = load_settings({"LIFECYCLE_DATA_DIR": "", "LIFECYCLE_DATABASE_URL": " ", "LIFECYCLE_HOST": ""})
    assert str(s.data_dir) == "/appdata" and "host=/tmp/lifecycle-pg" in s.database_url and s.host == "0.0.0.0"  # noqa: S104
    for bad in ("appdata", "./appdata", "."):
        with pytest.raises(ConfigError):
            load_settings({"LIFECYCLE_DATA_DIR": bad})


def test_api_never_trusts_forwarded_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    """§3.7: de rate-limit telt per bron-IP van de TCP-verbinding; uvicorn mag X-Forwarded-For nooit gebruiken (ook
    niet via FORWARDED_ALLOW_IPS), en de opdrachtregel heeft daar geen optie meer voor."""
    import uvicorn

    from lifecycle import cli

    seen: dict[str, object] = {}
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr("lifecycle.app.create_app", lambda settings: object())
    assert cli.cmd_api() == 0
    assert seen["proxy_headers"] is False and "forwarded_allow_ips" not in seen
    with pytest.raises(SystemExit):
        cli.main(["api", "--forwarded-allow-ips", "*"])


def test_hub_url_rules_section_3_2() -> None:
    assert normalize_hub_url(" http://nas.local:30400/ ") == "http://nas.local:30400"
    assert normalize_hub_url("http://nas.local.:30400") == "http://nas.local:30400"  # één punt aan het eind telt niet
    assert normalize_hub_url("HTTPS://NAS.example:30400") == "https://nas.example:30400"
    assert normalize_hub_url("http://[fd00::5]:30400") == "http://[fd00::5]:30400"
    assert normalize_hub_url("") is None
    for bad in ["ftp://x", "http://user:pw@x", "http://x/?a=1", "http://x#y", "x:30400", "http://", "http://x/pad",
                "http://127.0.0.1:30400", "http://localhost:30400", "http://[::1]:30400", "http://0.0.0.0:30400",
                "http://[::]:30400", "http://169.254.1.2:30400", "http://[fe80::1]:30400",
                "http://[::ffff:127.0.0.1]:30400", "http://x:99999", "http://bad_host:30400",
                # 1.3 §3.2: alleen canonieke IPv4 (a.b.c.d); andere numerieke vormen lezen resolvers als 127.0.0.1
                "http://127.1:30400", "http://2130706433:30400", "http://0x7f000001:30400",
                "http://010.0.0.1:30400", "http://0:30400", "http://1.2.3.4.5:30400", "http://nas.local..:30400",
                "http://[fe80::1%25eth0]:30400"]:
        with pytest.raises(ConfigError):
            normalize_hub_url(bad)


def test_loopback_only_with_explicit_dev_flag() -> None:
    assert normalize_hub_url("http://127.0.0.1:30499", allow_loopback=True) == "http://127.0.0.1:30499"
    s = load_settings({"HUB_URL": "http://127.0.0.1:30499", "LIFECYCLE_DEV_ALLOW_LOOPBACK_HUB": "1"})
    assert s.hub_url == "http://127.0.0.1:30499" and s.allow_loopback_hub and s.hub_url_error is None
    # 1.3 §3.5: een ongeldige HUB_URL laat de dienst niet crashen; hij meldt hub_adres_ongeldig en koppelt niet
    s = load_settings({"HUB_URL": "http://127.0.0.1:30499"})
    assert s.hub_url is None and s.hub_url_error == "hub_adres_ongeldig" and any("loopback" in w for w in s.warnings)
    s = load_settings({"HUB_URL": "http://169.254.0.1:30400", "LIFECYCLE_DEV_ALLOW_LOOPBACK_HUB": "1"})
    assert s.hub_url is None and s.hub_url_error == "hub_adres_ongeldig"  # link-local blijft ook met de vlag verboden


def test_key_fingerprint_is_normalized_and_validated() -> None:
    s = load_settings({"HUB_KEY_FINGERPRINT": "megg 8gfg 2e4y k2rq"})
    assert s.hub_key_fingerprint == "MEGG-8GFG-2E4Y-K2RQ" and s.hub_key_fingerprint_error is None
    s = load_settings({"HUB_KEY_FINGERPRINT": "MEGG-8GFG"})
    assert s.hub_key_fingerprint is None and s.hub_key_fingerprint_error == "vingerafdruk_ongeldig"


def test_public_url_may_have_a_path_and_invalid_becomes_null() -> None:
    assert normalize_base_url("http://nas.example:30440/") == "http://nas.example:30440"
    assert normalize_base_url("https://proxy.example/jlp") == "https://proxy.example/jlp"
    for bad in ("ftp://x", "http://u:p@x", "http://x?a", "http://x y", "http://127.0.0.1:30440", "http://127.1",
                "http://169.254.1.1/jlp", "http://[::]:30440"):
        with pytest.raises(ConfigError):
            normalize_base_url(bad)
    # §3.3: base_url alleen sturen als hij geldig is, anders null — nooit een reden om niet te starten of te koppelen
    s = load_settings({"LIFECYCLE_PUBLIC_URL": "http://127.0.0.1:30440"})
    assert s.public_url is None and any("base_url wordt null" in w for w in s.warnings)


@pytest.mark.parametrize("host", ["192.168.1.10:30440", "192.168.1.10", "[fd00::5]:30440", "truenas", "truenas:30440",
                                  "nas.local", "NAS.LOCAL.:30440", "nas.home.arpa", "nas.lan:1", "nas.internal",
                                  "127.0.0.1:30440"])
def test_host_header_allowed_section_3_7(host: str) -> None:
    assert host_header_allowed(host)


@pytest.mark.parametrize("host", ["nas.example.com", "evil.test:30440", "jlp.voorbeeld.nl", "127.1", "2130706433",
                                  "0x7f000001", "010.0.0.1", "0", "[fe80::1%eth0]:1", "", None, "a b", "nas.local:abc",
                                  "[::1", "nas.local..", "1.2.3.4.5", "nas.local:30440:1", "x" * 301])
def test_host_header_refused_dns_rebinding(host: str | None) -> None:
    """§3.7 punt 4: alleen IP-adres (canoniek), naam zonder punt of .local/.lan/.internal/.home.arpa."""
    assert not host_header_allowed(host)


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
