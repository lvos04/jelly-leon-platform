"""Enrollment en de dienst → Hub-aanroepen rond de koppeling (contract 1.2 §3.2–§3.8).

- Verzoek ``POST {HUB_URL}/api/v1/services/enroll``: **exact** ``app_id`` (vaste constante), precies één van
  ``enrollment_token``/``pairing_code``, ``version``, ``base_url`` en ``public_key_jwk`` ``{kty, crv, x, kid}`` met
  ``kid`` = RFC 7638-thumbprint. Geen andere velden (de Hub weigert ze met 422).
- Antwoord: alle velden van §3.3 met het juiste type; ``schema_version`` major 1; zelf berekende
  ``keyset_fingerprint`` gelijk aan het antwoord **en** aan ``HUB_KEY_FINGERPRINT``/de ingevoerde vingerafdruk.
  Afwijking → niets opslaan (``hub_antwoord_ongeldig`` of ``hub_vingerafdruk_onjuist``), definitief voor dit token.
  Oude aliassen (``token``, ``id``, ``policy_public_jwk``, ``kind``, ``secret``) bestaan niet.
- Volgorde (§3.6): ``pending`` (hash van het geheim) → POST → controles → in één atomaire stap ``gekoppeld`` →
  ``GET /api/v1/services/self`` (zet ``confirmed_at``). Herstart: zelfde env-token = toegestane herhaling.
- Fouten: 401/404/413/422 en ongeldige antwoorden zijn definitief; 429 wacht ``Retry-After``; 5xx, netwerkfouten en
  3xx (redirects worden nooit gevolgd) opnieuw met backoff + jitter (5 s → max. 15 min), stoppen 60 min na de eerste
  poging. Proxy-omgevingsvariabelen worden genegeerd (``trust_env=False``, zie ``app.default_http_client_factory``).
- Intrekking: pas als ook ``GET /api/v1/services/self`` 401 geeft (§3.8). Rotatie bij ``Hub-Token-Rotation:
  required`` via ``POST /api/v1/services/self/rotate``, bevestigd met ``services/self``.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from lifecycle import APP_ID, __version__
from lifecycle.config import Settings
from lifecycle.hublink import (
    SVC_TOKEN_RE,
    HubLinkCorrupt,
    HubLinkStore,
    Link,
    add_used_token,
    clear_link,
    now_iso,
    same_uuid,
    set_error,
)
from lifecycle.keys import (
    KeyFormatError,
    Pin,
    hub_jwk_to_pin,
    keyset_fingerprint_of_thumbprints,
    normalize_fingerprint,
    public_jwk,
)
from lifecycle.timeutil import utcnow

log = logging.getLogger("lifecycle.enrollment")

ENROLL_PATH = "/api/v1/services/enroll"
SELF_PATH = "/api/v1/services/self"
ROTATE_PATH = "/api/v1/services/self/rotate"
ROTATION_HEADER = "Hub-Token-Rotation"
REQUEST_FIELDS = frozenset({"app_id", "enrollment_token", "pairing_code", "version", "base_url", "public_key_jwk"})
ENR_TOKEN_RE = re.compile(r"^enr_[A-Za-z0-9_-]{43}$")
ENROLL_WINDOW = timedelta(minutes=60)
BACKOFF_START_S = 5.0
BACKOFF_MAX_S = 900.0
RESPONSE_STRINGS = ("schema_version", "service_id", "app_id", "service_token", "token_type", "hub_issuer",
                    "keyset_fingerprint", "jwks_url", "events_inbox_url", "key_rotation_url")


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class EnrollmentError(Exception):
    """``code`` is een vaste ``last_error``-code (§3.5); ``final`` = nooit automatisch opnieuw met dit geheim."""

    def __init__(self, code: str, *, final: bool, retry_after: float | None = None, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.final = final
        self.retry_after = retry_after
        self.detail = detail


@dataclass(frozen=True)
class EnrollResult:
    service_id: str
    service_token: str
    jwt_pins: tuple[Pin, ...]
    policy_pins: tuple[Pin, ...]
    keyset_fingerprint: str
    replayed: bool


def build_request(kind: str, secret: str, identity: Ed25519PrivateKey, settings: Settings) -> dict[str, Any]:
    if kind not in {"token", "koppelcode"}:
        raise ValueError("kind moet 'token' of 'koppelcode' zijn")
    body: dict[str, Any] = {
        "app_id": APP_ID,
        "version": __version__,
        "base_url": settings.public_url,
        "public_key_jwk": public_jwk(identity),
    }
    body["enrollment_token" if kind == "token" else "pairing_code"] = secret
    return body


def _invalid(detail: str) -> EnrollmentError:
    return EnrollmentError("hub_antwoord_ongeldig", final=True, detail=detail)


def validate_response(data: Any, *, expected_fingerprint: str | None) -> EnrollResult:
    """Alle controles van §3.3 vóór opslaan. Gooit ``EnrollmentError`` (definitief) bij elke afwijking."""
    if not isinstance(data, dict):
        raise _invalid("antwoord is geen JSON-object")
    for name in RESPONSE_STRINGS:
        if not isinstance(data.get(name), str):
            raise _invalid(f"veld {name} ontbreekt of is geen tekst")
    if not isinstance(data.get("enrollment_replayed"), bool):
        raise _invalid("veld enrollment_replayed ontbreekt of is geen boolean")
    if data["schema_version"].split(".")[0] != "1":
        raise _invalid("schema_version heeft een andere major dan 1")
    if data["app_id"] != APP_ID:
        raise _invalid("app_id in het antwoord is niet die van deze dienst")
    try:
        service_id = str(uuid.UUID(data["service_id"]))
    except ValueError as exc:
        raise _invalid("service_id is geen UUID") from exc
    if not SVC_TOKEN_RE.fullmatch(data["service_token"]):
        raise _invalid("service_token heeft niet het formaat svc_ + 43 tekens")
    if data["token_type"] != "Bearer" or data["hub_issuer"] != "hub":  # noqa: S105 - geen wachtwoord
        raise _invalid("token_type/hub_issuer onverwacht")
    jwks = data.get("jwks")
    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list) or not jwks["keys"]:
        raise _invalid("jwks heeft geen sleutels")
    try:
        jwt_pins = tuple(hub_jwk_to_pin(k) for k in jwks["keys"])
        policy_pin = hub_jwk_to_pin(data.get("policy_public_key"))
    except KeyFormatError as exc:
        raise _invalid(f"ongeldige Hub-sleutel: {exc}") from exc
    if len({p.kid for p in jwt_pins}) != len(jwt_pins):
        raise _invalid("kid komt dubbel voor in jwks")
    computed = keyset_fingerprint_of_thumbprints([p.thumbprint for p in jwt_pins], policy_pin.thumbprint)
    if normalize_fingerprint(data["keyset_fingerprint"]) != computed:
        raise EnrollmentError("hub_vingerafdruk_onjuist", final=True, detail="vingerafdruk in het antwoord klopt niet")
    if expected_fingerprint is not None and normalize_fingerprint(expected_fingerprint) != computed:
        raise EnrollmentError("hub_vingerafdruk_onjuist", final=True, detail="vingerafdruk wijkt af van de verwachte")
    return EnrollResult(service_id, data["service_token"], jwt_pins, (policy_pin,), computed,
                        data["enrollment_replayed"])


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After", "").strip()
    if raw.isdigit():
        return min(float(raw), BACKOFF_MAX_S)
    return None


def post_enroll(client: httpx.Client, hub_url: str, body: dict[str, Any], *,
                expected_fingerprint: str | None) -> EnrollResult:
    try:
        response = client.post(hub_url + ENROLL_PATH, json=body)
    except httpx.HTTPError as exc:
        raise EnrollmentError("hub_onbereikbaar", final=False, detail=type(exc).__name__) from exc
    status = response.status_code
    if status == 200:
        try:
            data = response.json()
        except ValueError as exc:
            raise _invalid("antwoord is geen JSON") from exc
        return validate_response(data, expected_fingerprint=expected_fingerprint)
    if status == 404:
        raise EnrollmentError("enroll_endpoint_ontbreekt", final=True, detail="404")
    if status == 429:
        retry = _retry_after(response)
        raise EnrollmentError("rate_limited", final=False, retry_after=retry if retry is not None else 60.0,
                              detail="429")
    if status in {401, 413, 422} or (400 <= status < 500):
        raise EnrollmentError("hub_weigerde", final=True, detail=str(status))
    # 3xx (nooit gevolgd), 5xx en andere statussen: storing, later opnieuw
    raise EnrollmentError("hub_onbereikbaar", final=False, detail=str(status))


# ---------------------------------------------------------------------------------------------- opslag (§3.6)
def _persist(store: HubLinkStore, change: Callable[[dict[str, Any]], None]) -> None:
    """Bijhouden van ``pending``/fouten; een beschadigd bestand wordt hiervoor niet vervangen (fail-closed)."""
    try:
        store.update(change)
    except HubLinkCorrupt:
        log.warning("hub-link.json is onleesbaar; voortgang van de koppelpoging niet vastgelegd")


def mark_pending(store: HubLinkStore, *, kind: str, secret_sha: str, hub_url: str) -> None:
    def change(doc: dict[str, Any]) -> None:
        pending = doc.get("pending")
        if isinstance(pending, dict) and pending.get("secret_sha256") == secret_sha and pending.get("kind") == kind:
            pending["attempts"] = int(pending.get("attempts", 0)) + 1
            pending["hub_url"] = hub_url
        else:
            doc["pending"] = {"kind": kind, "secret_sha256": secret_sha, "hub_url": hub_url,
                              "started_at": now_iso(), "attempts": 1}

    _persist(store, change)


def mark_failed(store: HubLinkStore, code: str, *, env_token_sha: str | None) -> None:
    """Definitief mislukt: ``pending`` weg (ook het ingevulde Hub-adres), ``last_error``, env-token als afgehandeld.
    ``state``, token en pins blijven zoals ze waren."""

    def change(doc: dict[str, Any]) -> None:
        doc["pending"] = None
        set_error(doc, code)
        if env_token_sha:
            add_used_token(doc, env_token_sha)

    _persist(store, change)


def mark_retrying(store: HubLinkStore, code: str) -> None:
    _persist(store, lambda doc: set_error(doc, code))


def store_success(store: HubLinkStore, result: EnrollResult, *, hub_url: str, via: str,
                  env_token_sha: str | None) -> Link:
    """In één atomaire stap ``gekoppeld`` (token, pins, ``linked_at``, eerste keer ``first_linked_at``)."""
    at = now_iso()
    source = "enroll"

    def change(doc: dict[str, Any]) -> None:
        if doc.get("pins"):
            doc["previous_pins"] = doc["pins"]
        doc.update(
            state="gekoppeld", app_id=APP_ID, hub_url=hub_url, service_id=result.service_id, via=via,
            linked_at=at, first_linked_at=doc.get("first_linked_at") or at, confirmed_at=None,
            service_token=result.service_token, service_token_last4=result.service_token[-4:],
            pins={"jwt": [p.as_entry(at, source) for p in result.jwt_pins],
                  "policy": [p.as_entry(at, source) for p in result.policy_pins],
                  "keyset_fingerprint": result.keyset_fingerprint, "rotation_sequence": 0},
            pending=None,
        )
        set_error(doc, None)
        if env_token_sha:
            add_used_token(doc, env_token_sha)

    # alleen een env-token (wie het TrueNAS-formulier kan wijzigen) mag een beschadigd bestand vervangen
    return store.update(change, replace_corrupt=via == "token")


def enroll_once(
    store: HubLinkStore,
    client: httpx.Client,
    identity: Ed25519PrivateKey,
    settings: Settings,
    *,
    kind: str,
    secret: str,
    hub_url: str,
    expected_fingerprint: str | None,
) -> EnrollResult:
    """Eén poging: ``pending`` → POST → controles → opslaan. Bij een definitieve fout is ``pending`` weer leeg."""
    secret_sha = sha256_hex(secret)
    env_sha = secret_sha if kind == "token" else None
    mark_pending(store, kind=kind, secret_sha=secret_sha, hub_url=hub_url)
    try:
        result = post_enroll(client, hub_url, build_request(kind, secret, identity, settings),
                             expected_fingerprint=expected_fingerprint)
    except EnrollmentError as exc:
        if exc.final:
            mark_failed(store, exc.code, env_token_sha=env_sha)
        log.warning("koppelen mislukt: %s (%s, definitief: %s)", exc.code, exc.detail, exc.final)
        raise
    store_success(store, result, hub_url=hub_url, via=kind, env_token_sha=env_sha)
    log.info("gekoppeld met de Hub (via %s%s)", kind, ", herhaling" if result.replayed else "")
    return result


# ---------------------------------------------------------------------------------------------- na de koppeling
@dataclass
class HubSignals:
    """Wat de Hub in antwoorden meldt; de worker handelt het af."""

    rotation_required: bool = False
    unauthorized: bool = False


def note_signals(response: httpx.Response, signals: HubSignals) -> None:
    if response.headers.get(ROTATION_HEADER, "").strip().lower() == "required":
        signals.rotation_required = True
    if response.status_code == 401:
        signals.unauthorized = True


def get_self(client: httpx.Client, hub_url: str, token: str, signals: HubSignals) -> httpx.Response | None:
    try:
        response = client.get(hub_url + SELF_PATH, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        return None
    note_signals(response, signals)
    return response


def mark_revoked(store: HubLinkStore, token: str) -> None:
    def change(doc: dict[str, Any]) -> None:
        if doc.get("state") == "gekoppeld" and doc.get("service_token") == token:
            clear_link(doc, "ingetrokken", error="ingetrokken_door_hub")

    _persist(store, change)
    log.warning("de Hub heeft de koppeling ingetrokken (services/self gaf 401); elke Hub-JWT wordt nu geweigerd")


def confirm(store: HubLinkStore, client: httpx.Client, signals: HubSignals) -> str:
    """``GET /api/v1/services/self`` met het opgeslagen token → ``confirmed_at``. 401 = ingetrokken (§3.8)."""
    link = store.read()
    token, hub_url = link.service_token, link.hub_url
    if not token or not hub_url:
        return "niet_gekoppeld"
    response = get_self(client, hub_url, token, signals)
    if response is None:
        return "onbereikbaar"
    if response.status_code == 401:
        mark_revoked(store, token)
        return "ingetrokken"
    if response.status_code != 200:
        return f"later:{response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return "later:geen_json"
    if not isinstance(body, dict) or not same_uuid(body.get("service_id"), link.service_id):
        return "later:ander_service_id"

    def change(doc: dict[str, Any]) -> None:
        if doc.get("service_token") == token and not doc.get("confirmed_at"):
            doc["confirmed_at"] = now_iso()

    _persist(store, change)
    return "bevestigd"


def check_revoked(store: HubLinkStore, client: httpx.Client, signals: HubSignals) -> bool:
    """Na een 401 op een aanroep met het service-token: alleen ``ingetrokken`` als ook ``services/self`` 401 geeft.
    Netwerkfouten, 5xx en 429 zijn nooit een intrekking."""
    link = store.read()
    if not link.service_token or not link.hub_url:
        return False
    response = get_self(client, link.hub_url, link.service_token, signals)
    if response is not None and response.status_code == 401:
        mark_revoked(store, link.service_token)
        return True
    return False


def rotate_service_token(store: HubLinkStore, client: httpx.Client, signals: HubSignals) -> str:
    """``POST /api/v1/services/self/rotate`` → nieuw token atomair opslaan → bevestigen met ``services/self``."""
    link = store.read()
    old, hub_url = link.service_token, link.hub_url
    if not old or not hub_url:
        return "niet_gekoppeld"
    try:
        response = client.post(hub_url + ROTATE_PATH, headers={"Authorization": f"Bearer {old}"})
    except httpx.HTTPError:
        return "onbereikbaar"
    if response.status_code == 401:
        return "ingetrokken" if check_revoked(store, client, signals) else "later:401"
    if response.status_code != 200:
        return f"later:{response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return "later:geen_json"
    new = body.get("service_token") if isinstance(body, dict) else None
    if not isinstance(new, str) or not SVC_TOKEN_RE.fullmatch(new) or not same_uuid(body.get("service_id"),
                                                                                    link.service_id):
        return "later:ongeldig_antwoord"

    def change(doc: dict[str, Any]) -> None:
        if doc.get("state") == "gekoppeld" and doc.get("service_token") == old:
            doc["service_token"] = new
            doc["service_token_last4"] = new[-4:]

    _persist(store, change)
    signals.rotation_required = False
    # het nieuwe token gebruiken; zo vervalt het oude 10 min later (§3.8)
    confirmation = get_self(client, hub_url, new, signals)
    if confirmation is not None and confirmation.status_code == 200:
        signals.rotation_required = confirmation.headers.get(ROTATION_HEADER, "").lower() == "required"
    log.info("service-token geroteerd")
    return "geroteerd"


# ---------------------------------------------------------------------------------------------- env-token (worker)
def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class EnvEnrollment:
    """Koppelen met ``HUB_URL`` + ``HUB_ENROLLMENT_TOKEN`` in de worker, met backoff en de 60-minutengrens."""

    def __init__(self, settings: Settings, store: HubLinkStore, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.store = store
        self.clock = clock
        self.next_at = 0.0
        self.backoff = BACKOFF_START_S
        self.failed_here: set[str] = set()  # alleen als hub-link.json onleesbaar is (dan kan de hash er niet in)

    @property
    def token_sha(self) -> str | None:
        return sha256_hex(self.settings.enrollment_token) if self.settings.enrollment_token else None

    def recover_after_restart(self) -> None:
        """§3.6 stap 5 (token): ander of geen env-token bij een lopende poging → ``koppelen_onderbroken``."""
        pending = self.store.read().pending
        if not pending or pending.get("kind") != "token" or pending.get("secret_sha256") == self.token_sha:
            return

        def change(doc: dict[str, Any]) -> None:
            current = doc.get("pending")
            if isinstance(current, dict) and current.get("kind") == "token" and \
                    current.get("secret_sha256") != self.token_sha:
                doc["pending"] = None
                set_error(doc, "koppelen_onderbroken")

        _persist(self.store, change)

    def needed(self, link: Link | None = None) -> bool:
        sha = self.token_sha
        if not self.settings.hub_url or not sha or sha in self.failed_here:
            return False
        link = link or self.store.read()
        return sha not in link.used_tokens

    def tick(self, client_factory: Callable[[], httpx.Client], identity_loader: Callable[[], Ed25519PrivateKey]) -> str:
        link = self.store.read()
        if not self.needed(link):
            return "niet_nodig"
        if self.clock() < self.next_at:
            return "wachten"
        token, hub_url, sha = self.settings.enrollment_token, self.settings.hub_url, self.token_sha
        assert token and hub_url and sha
        if not ENR_TOKEN_RE.fullmatch(token):
            mark_failed(self.store, "token_formaat_ongeldig", env_token_sha=sha)
            self._remember_failure(sha)
            return "mislukt:token_formaat_ongeldig"
        pending = link.pending
        started = _parse_iso(pending.get("started_at")) if pending and pending.get("secret_sha256") == sha else None
        if started is not None and utcnow() - started > ENROLL_WINDOW:
            mark_failed(self.store, "token_verlopen_na_60_min", env_token_sha=sha)
            self._remember_failure(sha)
            return "mislukt:token_verlopen_na_60_min"
        try:
            with client_factory() as client:
                enroll_once(self.store, client, identity_loader(), self.settings, kind="token", secret=token,
                            hub_url=hub_url, expected_fingerprint=self.settings.hub_key_fingerprint)
                confirm(self.store, client, HubSignals())
        except EnrollmentError as exc:
            if exc.final:
                self._remember_failure(sha)
                return f"mislukt:{exc.code}"
            mark_retrying(self.store, exc.code)
            base = exc.retry_after if exc.retry_after is not None else self.backoff
            delay = base if exc.retry_after is not None else min(BACKOFF_MAX_S, base * random.uniform(1.0, 1.25))  # noqa: S311
            self.backoff = min(BACKOFF_MAX_S, self.backoff * 2)
            self.next_at = self.clock() + delay
            return f"opnieuw:{exc.code}"
        self.backoff = BACKOFF_START_S
        self.next_at = 0.0
        return "gekoppeld"

    def _remember_failure(self, sha: str) -> None:
        if self.store.read().corrupt:
            self.failed_here.add(sha)


def clear_interrupted_pairing(store: HubLinkStore) -> None:
    """§3.6 stap 5 (koppelcode): na een herstart van de API is het geheim weg → ``koppelen_onderbroken``."""

    def change(doc: dict[str, Any]) -> None:
        pending = doc.get("pending")
        if isinstance(pending, dict) and pending.get("kind") == "koppelcode":
            doc["pending"] = None
            set_error(doc, "koppelen_onderbroken")

    if (store.read().pending or {}).get("kind") == "koppelcode":
        _persist(store, change)
