"""Instellingen uit omgevingsvariabelen.

De standaard TrueNAS Custom App heeft géén verplichte variabelen: alles heeft een veilige standaardwaarde.
``HUB_URL`` en ``HUB_ENROLLMENT_TOKEN`` geeft alleen de Hub mee wanneer hij deze app via de TrueNAS-API installeert
(contract §3). Beide zijn niet-geheim: het token is eenmalig en vervalt na 60 minuten of na eerste gebruik.
Geheimen staan nooit in de omgeving; die maakt de app zelf aan in ``/appdata/secrets`` (zie ``secrets_store``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PORT = 30440
DEFAULT_DATA_DIR = "/appdata"
DEFAULT_DB_URL = "postgresql+psycopg://lifecycle@/lifecycle?host=/tmp/lifecycle-pg"


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


def normalize_hub_url(raw: str | None) -> str | None:
    """Alleen ``http(s)://host[:poort][/pad]`` zonder gebruikersnaam/wachtwoord, query of fragment."""
    if raw is None:
        return None
    value = raw.strip().rstrip("/")
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ConfigError("HUB_URL moet beginnen met http:// of https:// en een host bevatten")
    if parts.username or parts.password:
        raise ConfigError("HUB_URL mag geen gebruikersnaam of wachtwoord bevatten")
    if parts.query or parts.fragment:
        raise ConfigError("HUB_URL mag geen query (?…) of fragment (#…) bevatten")
    return value


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(DEFAULT_DATA_DIR)
    database_url: str = DEFAULT_DB_URL
    host: str = "0.0.0.0"  # noqa: S104 - de container luistert op alle interfaces; TrueNAS publiceert de poort
    port: int = DEFAULT_PORT
    hub_url: str | None = None
    enrollment_token: str | None = field(default=None, repr=False)
    public_url: str | None = None
    jwt_issuer: str = "hub"
    jwks_ttl_seconds: int = 300
    jwks_min_refresh_seconds: int = 30
    jwks_max_stale_seconds: int = 900
    http_connect_timeout: float = 5.0
    http_read_timeout: float = 15.0
    worker_interval_seconds: int = 5
    idempotency_ttl_hours: int = 24
    alembic_dir: Path | None = None

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def jwks_url(self) -> str | None:
        return f"{self.hub_url}/api/v1/auth/jwks" if self.hub_url else None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    alembic_dir = _opt(env, "LIFECYCLE_ALEMBIC_DIR")
    public_url = _opt(env, "LIFECYCLE_PUBLIC_URL")
    return Settings(
        data_dir=Path(env.get("LIFECYCLE_DATA_DIR", DEFAULT_DATA_DIR)),
        database_url=env.get("LIFECYCLE_DATABASE_URL", DEFAULT_DB_URL),
        host=env.get("LIFECYCLE_HOST", "0.0.0.0"),  # noqa: S104
        port=_int(env, "LIFECYCLE_PORT", DEFAULT_PORT, 1, 65535),
        hub_url=normalize_hub_url(_opt(env, "HUB_URL")),
        enrollment_token=_opt(env, "HUB_ENROLLMENT_TOKEN"),
        public_url=normalize_hub_url(public_url) if public_url else None,
        jwks_ttl_seconds=_int(env, "LIFECYCLE_JWKS_TTL_SECONDS", 300, 10, 3600),
        jwks_min_refresh_seconds=_int(env, "LIFECYCLE_JWKS_MIN_REFRESH_SECONDS", 30, 0, 600),
        jwks_max_stale_seconds=_int(env, "LIFECYCLE_JWKS_MAX_STALE_SECONDS", 900, 10, 86400),
        worker_interval_seconds=_int(env, "LIFECYCLE_WORKER_INTERVAL_SECONDS", 5, 1, 300),
        idempotency_ttl_hours=_int(env, "LIFECYCLE_IDEMPOTENCY_TTL_HOURS", 24, 1, 720),
        alembic_dir=Path(alembic_dir) if alembic_dir else None,
    )
