"""Instellingen uit omgevingsvariabelen.

De standaard TrueNAS Custom App heeft géén verplichte variabelen: alles heeft een veilige standaardwaarde.
``HUB_URL``, ``HUB_ENROLLMENT_TOKEN`` en ``HUB_KEY_FINGERPRINT`` (contract 1.2 §3.2) geeft de Hub mee wanneer hij deze
app via de TrueNAS-API installeert, of vult de beheerder zelf in het formulier in. Het token is eenmalig (60 min);
de vingerafdruk is niet geheim. ``HUB_URL`` dient alleen de enrollment; JWT's worden alleen geaccepteerd met de bij een
**geslaagde** koppeling gepinde sleutels (ADR-0009, ADR-0011). Geheimen staan nooit in de omgeving; die maakt de app
zelf aan in ``/appdata/secrets`` (zie ``secrets_store``).
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from lifecycle.keys import normalize_fingerprint

DEFAULT_PORT = 30440
DEFAULT_DATA_DIR = "/appdata"
DEFAULT_DB_URL = "postgresql+psycopg://lifecycle@/lifecycle?host=/tmp/lifecycle-pg"
DEV_LOOPBACK_FLAG = "LIFECYCLE_DEV_ALLOW_LOOPBACK_HUB"
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


class ConfigError(ValueError):
    """Ongeldige configuratie; de melding is Nederlands en bevat nooit een geheim."""


def _int(env: Mapping[str, str], name: str, default: int, lo: int, hi: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} moet een geheel getal zijn (gekregen: {raw!r})") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{name} moet tussen {lo} en {hi} liggen (gekregen: {value})")
    return value


def _opt(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "").strip()
    return value or None


def _flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in {"1", "true", "ja", "yes"}


def _host_problem(host: str, allow_loopback: bool) -> str | None:
    """Loopback, link-local en ``0.0.0.0``/``::`` zijn nooit een Hub-adres (loopback alleen met de ontwikkelvlag)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        lowered = host.lower().rstrip(".")
        if not _HOST_RE.fullmatch(lowered):
            return "ongeldige hostnaam"
        if lowered == "localhost" or lowered.endswith(".localhost"):
            return None if allow_loopback else "loopback (localhost) is geen Hub-adres"
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_unspecified:
        return "0.0.0.0/:: is geen Hub-adres"
    if ip.is_link_local:
        return "link-local (169.254.0.0/16, fe80::/10) is geen Hub-adres"
    if ip.is_multicast:
        return "multicast is geen Hub-adres"
    if ip.is_loopback and not allow_loopback:
        return "loopback (127.0.0.0/8, ::1) is geen Hub-adres"
    return None


def normalize_hub_url(raw: str | None, *, allow_loopback: bool = False, name: str = "HUB_URL") -> str | None:
    """``http(s)://<host>[:<poort>]`` zonder pad, query, fragment of inloggegevens (contract 1.2 §3.2).

    Een afsluitende ``/`` mag. Geldt voor ``HUB_URL`` én voor het Hub-adres op de statuspagina.
    """
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > 512:
        raise ConfigError(f"{name} is te lang (max. 512 tekens)")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ConfigError(f"{name} heeft een ongeldige poort") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConfigError(f"{name} moet beginnen met http:// of https:// en een host bevatten")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ConfigError(f"{name} mag geen gebruikersnaam of wachtwoord bevatten")
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise ConfigError(f"{name} mag geen query (?…) of fragment (#…) bevatten")
    if parts.path not in {"", "/"}:
        raise ConfigError(f"{name} mag geen pad bevatten (alleen http(s)://host[:poort])")
    problem = _host_problem(parts.hostname, allow_loopback)
    if problem:
        raise ConfigError(f"{name}: {problem}")
    host = parts.hostname.lower()
    if ":" in host:  # IPv6-letterlijk adres
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "")


def normalize_base_url(raw: str | None) -> str | None:
    """``LIFECYCLE_PUBLIC_URL`` → ``base_url`` van het enroll-verzoek (alleen informatief; pad toegestaan)."""
    if raw is None or not raw.strip():
        return None
    value = raw.strip().rstrip("/")
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or len(value) > 512 or re.search(r"\s", value)):
        raise ConfigError("LIFECYCLE_PUBLIC_URL moet http(s)://host[:poort][/pad] zijn (max. 512 tekens)")
    return value


def parse_fingerprint(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    value = normalize_fingerprint(raw)
    if value is None:
        raise ConfigError("HUB_KEY_FINGERPRINT moet de vorm XXXX-XXXX-XXXX-XXXX hebben (16 tekens, Crockford-base32)")
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(DEFAULT_DATA_DIR)
    database_url: str = DEFAULT_DB_URL
    host: str = "0.0.0.0"  # noqa: S104 - de container luistert op alle interfaces; TrueNAS publiceert de poort
    port: int = DEFAULT_PORT
    hub_url: str | None = None
    enrollment_token: str | None = field(default=None, repr=False)
    hub_key_fingerprint: str | None = None
    allow_loopback_hub: bool = False
    public_url: str | None = None
    jwt_issuer: str = "hub"
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 15.0
    worker_interval_seconds: int = 5
    key_rotation_poll_seconds: int = 900
    idempotency_ttl_hours: int = 24
    alembic_dir: Path | None = None

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"


def _data_dir(env: Mapping[str, str]) -> Path:
    """Leeg = standaard ``/appdata``; alleen een absoluut pad (een leeg of relatief pad zou naar de werkmap wijzen)."""
    raw = _opt(env, "LIFECYCLE_DATA_DIR") or DEFAULT_DATA_DIR
    if not raw.startswith("/"):
        raise ConfigError(f"LIFECYCLE_DATA_DIR moet een absoluut pad zijn (gekregen: {raw!r})")
    return Path(raw)


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    alembic_dir = _opt(env, "LIFECYCLE_ALEMBIC_DIR")
    allow_loopback = _flag(env, DEV_LOOPBACK_FLAG)
    return Settings(
        data_dir=_data_dir(env),
        database_url=_opt(env, "LIFECYCLE_DATABASE_URL") or DEFAULT_DB_URL,
        host=_opt(env, "LIFECYCLE_HOST") or "0.0.0.0",  # noqa: S104
        port=_int(env, "LIFECYCLE_PORT", DEFAULT_PORT, 1, 65535),
        hub_url=normalize_hub_url(_opt(env, "HUB_URL"), allow_loopback=allow_loopback),
        enrollment_token=_opt(env, "HUB_ENROLLMENT_TOKEN"),
        hub_key_fingerprint=parse_fingerprint(_opt(env, "HUB_KEY_FINGERPRINT")),
        allow_loopback_hub=allow_loopback,
        public_url=normalize_base_url(_opt(env, "LIFECYCLE_PUBLIC_URL")),
        worker_interval_seconds=_int(env, "LIFECYCLE_WORKER_INTERVAL_SECONDS", 5, 1, 300),
        key_rotation_poll_seconds=_int(env, "LIFECYCLE_KEY_ROTATION_POLL_SECONDS", 900, 30, 900),
        idempotency_ttl_hours=_int(env, "LIFECYCLE_IDEMPOTENCY_TTL_HOURS", 24, 1, 720),
        alembic_dir=Path(alembic_dir) if alembic_dir else None,
    )
