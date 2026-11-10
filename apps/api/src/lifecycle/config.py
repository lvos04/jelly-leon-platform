"""Instellingen uit omgevingsvariabelen en de adresregels van het platform (contract 1.3 §3.2, §3.3, §3.7).

De standaard TrueNAS Custom App heeft géén verplichte variabelen: alles heeft een veilige standaardwaarde.
``HUB_URL``, ``HUB_ENROLLMENT_TOKEN`` en ``HUB_KEY_FINGERPRINT`` (contract 1.3 §3.2) geeft de Hub mee wanneer hij deze
app via de TrueNAS-API installeert, of vult de beheerder zelf in het formulier in. Het token is eenmalig (60 min);
de vingerafdruk is niet geheim. ``HUB_URL`` dient alleen de enrollment; JWT's worden alleen geaccepteerd met de bij een
**geslaagde** koppeling gepinde sleutels (ADR-0009, ADR-0011). Geheimen staan nooit in de omgeving; die maakt de app
zelf aan in ``/appdata/secrets`` (zie ``secrets_store``).

Contract 1.3 (ADR-0019): een ongeldige ``HUB_URL`` of ``HUB_KEY_FINGERPRINT`` laat de dienst **niet** crashen: hij
start, spreekt de Hub niet aan en meldt ``last_error`` ``hub_adres_ongeldig``/``vingerafdruk_ongeldig`` (de beheerder
kan het formulier verbeteren; het token blijft bruikbaar). Een ongeldige ``LIFECYCLE_PUBLIC_URL`` wordt ``base_url:
null`` met een waarschuwing in de log (§3.3: nooit een reden om niet te koppelen).

**Adresregels (vorm, §3.2):** een IPv4-adres alleen als ``a.b.c.d`` (vier decimale delen 0–255 zonder voorloopnul); een
host van alleen cijfers, punten en hex-delen die daar niet aan voldoet (``127.1``, ``2130706433``, ``0x7f000001``,
``010.0.0.1``, ``0``) is ongeldig; IPv6 alleen tussen ``[]``; één punt aan het eind van een naam telt niet mee. Bij een
hostnaam gelden de verboden bereiken ook voor het opgeloste adres, bij elke verbinding (``lifecycle.nettransport``).
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
_OCTET = r"(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
CANONICAL_IPV4 = re.compile(rf"^{_OCTET}(\.{_OCTET}){{3}}$")
# Iets dat op een getal lijkt (decimaal, octaal met voorloopnul, hex) maar geen canonieke a.b.c.d is: browsers en
# resolvers lezen ``127.1``, ``2130706433`` of ``0x7f000001`` als 127.0.0.1; daarom nooit als naam accepteren.
_NUMERIEK = re.compile(r"^(0x[0-9a-f]*|[0-9]+)(\.(0x[0-9a-f]*|[0-9]*))*$", re.IGNORECASE)
# §3.7 punt 4: lokale namen waaronder een Host-header op de statuspagina mag (DNS-rebinding gebruikt publieke namen).
LOKALE_DOMEINEN = (".local", ".lan", ".internal", ".home.arpa")


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


def strip_trailing_dot(name: str) -> str:
    """§3.2: één punt aan het eind van een naam telt niet mee (``localhost.`` = ``localhost``); twee punten niet."""
    return name[:-1] if name.endswith(".") and not name.endswith("..") else name


def ip_problem(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, allow_loopback: bool) -> str | None:
    """Verboden bereiken voor een Hub-adres (ook na DNS-resolutie): ``0.0.0.0``/``::``, link-local, multicast en
    loopback (loopback alleen met de ontwikkelvlag)."""
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


def _host_problem(host: str, allow_loopback: bool) -> str | None:
    """Loopback, link-local en ``0.0.0.0``/``::`` zijn nooit een Hub-adres (loopback alleen met de ontwikkelvlag);
    IPv4 alleen in de canonieke vorm (§3.2)."""
    if ":" in host:  # IPv6 (urlsplit haalt de [] weg; zonder [] is de URL al ongeldig)
        try:
            return ip_problem(ipaddress.IPv6Address(host), allow_loopback)
        except ValueError:
            return "ongeldig IPv6-adres"
    lowered = strip_trailing_dot(host.lower())
    if CANONICAL_IPV4.fullmatch(lowered):
        return ip_problem(ipaddress.IPv4Address(lowered), allow_loopback)
    if _NUMERIEK.fullmatch(lowered):
        return "IPv4-adres alleen als a.b.c.d (vier delen 0–255, zonder voorloopnul)"
    if not _HOST_RE.fullmatch(lowered) or len(lowered) > 253:
        return "ongeldige hostnaam"
    if lowered == "localhost" or lowered.endswith(".localhost"):
        return None if allow_loopback else "loopback (localhost) is geen Hub-adres"
    return None


def normalize_hub_url(raw: str | None, *, allow_loopback: bool = False, name: str = "HUB_URL") -> str | None:
    """``http(s)://<host>[:<poort>]`` zonder pad, query, fragment of inloggegevens (contract 1.3 §3.2).

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
    if "%" in parts.netloc:
        raise ConfigError(f"{name}: een IPv6-zone (%…) is geen Hub-adres")
    problem = _host_problem(parts.hostname, allow_loopback)
    if problem:
        raise ConfigError(f"{name}: {problem}")
    host = strip_trailing_dot(parts.hostname.lower())
    if ":" in host:  # IPv6-letterlijk adres
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "")


def normalize_base_url(raw: str | None) -> str | None:
    """``LIFECYCLE_PUBLIC_URL`` → ``base_url`` van het enroll-verzoek (alleen informatief; pad toegestaan).

    §3.3: alleen ``http(s)://host[:poort][/pad]``, ≤ 512 tekens en de adresregels van §3.2 (geen loopback, link-local,
    ``0.0.0.0``/``::``; IPv4 canoniek). Gooit ``ConfigError``; ``load_settings`` maakt daar ``None`` + waarschuwing van.
    """
    if raw is None or not raw.strip():
        return None
    value = raw.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise ConfigError("LIFECYCLE_PUBLIC_URL heeft een ongeldige poort") from exc
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
            or "@" in parts.netloc or parts.query or parts.fragment or "?" in value or "#" in value
            or len(value) > 512 or re.search(r"\s", value) or "%" in parts.netloc):
        raise ConfigError("LIFECYCLE_PUBLIC_URL moet http(s)://host[:poort][/pad] zijn (max. 512 tekens)")
    problem = _host_problem(parts.hostname, False)
    if problem:
        raise ConfigError(f"LIFECYCLE_PUBLIC_URL: {problem}")
    host = strip_trailing_dot(parts.hostname.lower())
    if ":" in host:
        host = f"[{host}]"
    return f"{parts.scheme}://{host}" + (f":{port}" if port is not None else "") + parts.path


def host_header_allowed(host_header: str | None) -> bool:
    """§3.7 punt 4 (DNS-rebinding): een POST op de statuspagina alleen als de ``Host``-header (zonder poort) een
    IP-adres in de vorm van §3.2 is (IPv6 tussen ``[]``), een naam zonder punt, of een naam onder ``.local``, ``.lan``,
    ``.internal`` of ``.home.arpa``."""
    if not host_header or len(host_header) > 300 or any(ch.isspace() for ch in host_header):
        return False
    value = host_header.strip()
    if value.startswith("["):
        end = value.find("]")
        if end < 0 or (value[end + 1:] and not re.fullmatch(r":[0-9]{1,5}", value[end + 1:])):
            return False
        try:
            ipaddress.IPv6Address(value[1:end])  # zonder zone (%…): IPv6Address weigert die niet altijd
        except ValueError:
            return False
        return "%" not in value
    host, _, port = value.partition(":")
    if port and not re.fullmatch(r"[0-9]{1,5}", port):
        return False
    name = strip_trailing_dot(host.lower())
    if CANONICAL_IPV4.fullmatch(name):
        return True
    if not name or _NUMERIEK.fullmatch(name) or not _HOST_RE.fullmatch(name) or len(name) > 253:
        return False
    return "." not in name or name.endswith(LOKALE_DOMEINEN)


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
    # contract 1.3 §3.5: vaste last_error-code als HUB_URL/HUB_KEY_FINGERPRINT ongeldig is (de dienst start wel)
    hub_url_error: str | None = None
    hub_key_fingerprint_error: str | None = None
    warnings: tuple[str, ...] = ()

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
    warnings: list[str] = []
    hub_url: str | None = None
    hub_url_error: str | None = None
    try:
        hub_url = normalize_hub_url(_opt(env, "HUB_URL"), allow_loopback=allow_loopback)
    except ConfigError as exc:  # §3.5: niet crashen; de Hub niet aanspreken, last_error hub_adres_ongeldig
        hub_url_error = "hub_adres_ongeldig"
        warnings.append(f"{exc}; de dienst koppelt niet tot HUB_URL klopt (last_error hub_adres_ongeldig)")
    fingerprint: str | None = None
    fingerprint_error: str | None = None
    try:
        fingerprint = parse_fingerprint(_opt(env, "HUB_KEY_FINGERPRINT"))
    except ConfigError as exc:
        fingerprint_error = "vingerafdruk_ongeldig"
        warnings.append(f"{exc}; de dienst koppelt niet tot de vingerafdruk klopt (last_error vingerafdruk_ongeldig)")
    public_url: str | None = None
    try:
        public_url = normalize_base_url(_opt(env, "LIFECYCLE_PUBLIC_URL"))
    except ConfigError as exc:  # §3.3: base_url alleen sturen als hij geldig is, anders null
        warnings.append(f"{exc}; base_url wordt null")
    return Settings(
        data_dir=_data_dir(env),
        database_url=_opt(env, "LIFECYCLE_DATABASE_URL") or DEFAULT_DB_URL,
        host=_opt(env, "LIFECYCLE_HOST") or "0.0.0.0",  # noqa: S104
        port=_int(env, "LIFECYCLE_PORT", DEFAULT_PORT, 1, 65535),
        hub_url=hub_url,
        enrollment_token=_opt(env, "HUB_ENROLLMENT_TOKEN"),
        hub_key_fingerprint=fingerprint,
        allow_loopback_hub=allow_loopback,
        public_url=public_url,
        worker_interval_seconds=_int(env, "LIFECYCLE_WORKER_INTERVAL_SECONDS", 5, 1, 300),
        key_rotation_poll_seconds=_int(env, "LIFECYCLE_KEY_ROTATION_POLL_SECONDS", 900, 30, 900),
        idempotency_ttl_hours=_int(env, "LIFECYCLE_IDEMPOTENCY_TTL_HOURS", 24, 1, 720),
        alembic_dir=Path(alembic_dir) if alembic_dir else None,
        hub_url_error=hub_url_error,
        hub_key_fingerprint_error=fingerprint_error,
        warnings=tuple(warnings),
    )
