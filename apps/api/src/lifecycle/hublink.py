"""Koppelstatus met de Hub: ``/appdata/secrets/hub-link.json`` (contract 1.2 §3.5).

Eén bestand (0600, eigenaar 568, in ``/appdata/secrets`` 0700), **niet** in de database: een database-herstel mag
geen oude koppeling, oude pins of een ingetrokken token terugbrengen, en DB-exports bevatten zo geen credentials.

- Schrijven: nieuw bestand in dezelfde map (``O_CREAT|O_EXCL``, 0600) → ``fsync`` → ``rename`` → ``fsync`` van de map,
  onder een exclusieve vergrendeling op ``hub-link.lock``; lezen-wijzigen-schrijven altijd binnen die vergrendeling
  (API en worker schrijven allebei).
- Lezen: elk proces leest het bestand opnieuw zodra inode, mtime of grootte verandert, zodat een ontkoppeling,
  intrekking of rotatie door het ene proces direct voor het andere geldt.
- Een onleesbaar of ongeldig bestand is **fail-closed**: de dienst vertrouwt dan geen enkele Hub-JWT, toont geen
  koppelformulier en alleen een nieuw ``HUB_ENROLLMENT_TOKEN`` (via de Hub of het TrueNAS-formulier) kan het
  vervangen; het oude bestand blijft dan als ``hub-link.json.beschadigd-<tijd>`` staan.
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import os
import re
import secrets
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from lifecycle import APP_ID
from lifecycle.keys import KeyFormatError, Pin, pin_from_entry
from lifecycle.timeutil import iso_z, utcnow

FILE_NAME = "hub-link.json"
LOCK_NAME = "hub-link.lock"
FILE_SCHEMA_VERSION = "1.2"
STATES = ("nooit_gekoppeld", "gekoppeld", "ontkoppeld", "ingetrokken")
MAX_USED_TOKENS = 20
SVC_TOKEN_RE = re.compile(r"^svc_[A-Za-z0-9_-]{43}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

STATE_LABELS_NL = {
    "nooit_gekoppeld": "Nog nooit gekoppeld met de Hub",
    "gekoppeld": "Gekoppeld met de Hub",
    "ontkoppeld": "Ontkoppeld door de Hub",
    "ingetrokken": "Ingetrokken door de Hub",
}

# Vaste codes (contract §3.5 "o.a."; de laatste drie zijn eigen aanvullingen, ADR-0011) met een vaste NL-melding.
ERROR_MESSAGES_NL = {
    "hub_onbereikbaar": "De Hub is niet bereikbaar; de dienst probeert het later opnieuw.",
    "hub_weigerde": "De Hub weigerde de koppeling: het token of de koppelcode is ongeldig, verlopen of al gebruikt. "
    "Maak in de Hub een nieuw token of een nieuwe koppelcode.",
    "hub_antwoord_ongeldig": "De Hub gaf een ongeldig antwoord; er is niets opgeslagen. Controleer of de Hub "
    "contract 1.2 ondersteunt.",
    "hub_vingerafdruk_onjuist": "De vingerafdruk van de Hub klopt niet; er is niets opgeslagen. Controleer het adres "
    "van de Hub en vergelijk de vingerafdruk met die in de Hub.",
    "enroll_endpoint_ontbreekt": "Deze Hub-versie kent aanmelden niet. Werk de Hub bij en maak een nieuw token.",
    "rate_limited": "De Hub vraagt even te wachten (te veel pogingen); de dienst probeert het daarna opnieuw.",
    "koppelen_onderbroken": "Koppelen is onderbroken door een herstart. Voer de koppelcode opnieuw in of maak in de "
    "Hub een nieuw token.",
    "token_verlopen_na_60_min": "Koppelen is niet gelukt binnen 60 minuten. Maak in de Hub een nieuw token.",
    "ingetrokken_door_hub": "De Hub heeft deze koppeling ingetrokken. Opnieuw koppelen gaat via de Hub.",
    "token_formaat_ongeldig": "HUB_ENROLLMENT_TOKEN heeft niet het juiste formaat (enr_ + 43 tekens). Maak in de Hub "
    "een nieuw token.",
    "sleutelrotatie_ongeldig": "Een sleutelrotatie van de Hub was ongeldig en is niet toegepast. Opnieuw koppelen gaat "
    "via de Hub.",
    "overzetten_onvolledig": "De koppeling uit de vorige versie kon niet volledig worden overgezet (sleutels "
    "ontbraken). Opnieuw koppelen gaat via de Hub.",
}


class HubLinkCorrupt(RuntimeError):
    """``hub-link.json`` is onleesbaar of ongeldig (fail-closed)."""


def empty_doc() -> dict[str, Any]:
    return {
        "schema_version": FILE_SCHEMA_VERSION,
        "state": "nooit_gekoppeld",
        "app_id": APP_ID,
        "hub_url": None,
        "service_id": None,
        "via": None,
        "first_linked_at": None,
        "linked_at": None,
        "confirmed_at": None,
        "service_token": None,
        "service_token_last4": None,
        "pins": None,
        "previous_pins": None,
        "pending": None,
        "used_enrollment_token_sha256": [],
        "last_error": None,
    }


def _str_or_none(doc: dict[str, Any], key: str) -> None:
    if doc.get(key) is not None and not isinstance(doc[key], str):
        raise HubLinkCorrupt(f"{key} ongeldig")


def _check_pins(pins: Any) -> None:
    if not isinstance(pins, dict):
        raise HubLinkCorrupt("pins ongeldig")
    for purpose in ("jwt", "policy"):
        entries = pins.get(purpose)
        if not isinstance(entries, list) or not entries:
            raise HubLinkCorrupt(f"pins.{purpose} ontbreekt")
        kids = set()
        for entry in entries:
            try:
                kids.add(pin_from_entry(entry).kid)
            except KeyFormatError as exc:
                raise HubLinkCorrupt(f"pins.{purpose}: {exc}") from exc
        if len(kids) != len(entries):
            raise HubLinkCorrupt(f"pins.{purpose}: dubbele kid")
    if not isinstance(pins.get("keyset_fingerprint"), str):
        raise HubLinkCorrupt("pins.keyset_fingerprint ontbreekt")
    seq = pins.get("rotation_sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise HubLinkCorrupt("pins.rotation_sequence ongeldig")


def validate_doc(raw: Any) -> dict[str, Any]:
    """Structuurcontrole; ontbrekende optionele velden krijgen hun lege waarde. Gooit ``HubLinkCorrupt``."""
    if not isinstance(raw, dict):
        raise HubLinkCorrupt("geen JSON-object")
    doc = {**empty_doc(), **raw}
    if doc["state"] not in STATES:
        raise HubLinkCorrupt("onbekende state")
    for key in ("hub_url", "service_id", "via", "first_linked_at", "linked_at", "confirmed_at", "service_token",
                "service_token_last4"):
        _str_or_none(doc, key)
    used = doc["used_enrollment_token_sha256"]
    if not isinstance(used, list) or not all(isinstance(h, str) and HEX64_RE.fullmatch(h) for h in used):
        raise HubLinkCorrupt("used_enrollment_token_sha256 ongeldig")
    pending = doc["pending"]
    if pending is not None and (
        not isinstance(pending, dict) or pending.get("kind") not in {"token", "koppelcode"}
        or not HEX64_RE.fullmatch(str(pending.get("secret_sha256", "")))
        or not isinstance(pending.get("hub_url"), str) or not isinstance(pending.get("started_at"), str)
        or not isinstance(pending.get("attempts"), int)
    ):
        raise HubLinkCorrupt("pending ongeldig")
    err = doc["last_error"]
    if err is not None and (not isinstance(err, dict) or not isinstance(err.get("code"), str)
                            or not isinstance(err.get("at"), str)):
        raise HubLinkCorrupt("last_error ongeldig")
    if doc["state"] == "gekoppeld":
        if not SVC_TOKEN_RE.fullmatch(str(doc["service_token"] or "")):
            raise HubLinkCorrupt("service_token ontbreekt of is ongeldig")
        if not doc["service_id"] or not doc["hub_url"]:
            raise HubLinkCorrupt("service_id of hub_url ontbreekt")
        _check_pins(doc["pins"])
    elif doc["service_token"] is not None or doc["pins"] is not None:
        raise HubLinkCorrupt("token of pins buiten de toestand 'gekoppeld'")
    return doc


def same_uuid(a: Any, b: Any) -> bool:
    """Vergelijk twee UUID's ongeacht schrijfwijze (hoofdletters, streepjes); niet-UUID's zijn nooit gelijk."""
    try:
        return a is not None and b is not None and uuid.UUID(str(a)) == uuid.UUID(str(b))
    except ValueError:
        return False


def error_view(err: dict[str, Any] | None) -> dict[str, str] | None:
    if not err:
        return None
    code = str(err.get("code"))
    return {"code": code, "message_nl": ERROR_MESSAGES_NL.get(code, "Onbekende fout."), "at": str(err.get("at"))}


@dataclass(frozen=True)
class Link:
    """Momentopname van ``hub-link.json`` (alleen lezen)."""

    doc: dict[str, Any] = field(default_factory=empty_doc)
    corrupt: bool = False

    @property
    def state(self) -> str:
        return str(self.doc["state"])

    @property
    def linked(self) -> bool:
        return not self.corrupt and self.state == "gekoppeld"

    @property
    def ever_linked(self) -> bool:
        """Bepaalt §3.7: een beschadigd bestand telt als 'ooit gekoppeld' (nooit het koppelformulier tonen)."""
        return self.corrupt or bool(self.doc.get("first_linked_at"))

    @property
    def service_token(self) -> str | None:
        return str(self.doc["service_token"]) if self.linked else None

    @property
    def service_id(self) -> str | None:
        value = self.doc.get("service_id")
        return str(value) if value else None

    @property
    def hub_url(self) -> str | None:
        value = self.doc.get("hub_url")
        return str(value) if value else None

    @property
    def pending(self) -> dict[str, Any] | None:
        value = self.doc.get("pending")
        return dict(value) if isinstance(value, dict) else None

    @property
    def used_tokens(self) -> list[str]:
        return list(self.doc.get("used_enrollment_token_sha256") or [])

    @property
    def pins(self) -> dict[str, Any] | None:
        value = self.doc.get("pins") if self.linked else None
        return value if isinstance(value, dict) else None

    def pin_map(self, purpose: str) -> dict[str, Pin]:
        pins = self.pins
        if not pins:
            return {}
        return {pin.kid: pin for pin in (pin_from_entry(e) for e in pins.get(purpose, []))}

    @property
    def rotation_sequence(self) -> int:
        pins = self.pins
        return int(pins["rotation_sequence"]) if pins else 0

    @property
    def keyset_fingerprint(self) -> str | None:
        pins = self.pins
        return str(pins["keyset_fingerprint"]) if pins else None

    def public(self) -> dict[str, Any]:
        """Witte lijst van §3.5 (statuspagina, connector). Nooit ``service_token`` of ``pending.secret_sha256``."""
        if self.corrupt:
            # weigert alles zoals 'ontkoppeld'; herstel = opnieuw koppelen via de Hub (nieuw env-token)
            return {"state": "ontkoppeld", "state_label_nl": "Koppelbestand onleesbaar; opnieuw koppelen via de Hub",
                    "koppelen_bezig": False,
                    "hub_url": None, "service_id": None, "via": None, "linked_at": None, "first_linked_at": None,
                    "keyset_fingerprint": None, "service_token_last4": None, "last_error": None, "beschadigd": True}
        d = self.doc
        return {
            "state": self.state,
            "state_label_nl": STATE_LABELS_NL[self.state],
            "koppelen_bezig": d.get("pending") is not None,
            "hub_url": d.get("hub_url"),
            "service_id": d.get("service_id"),
            "via": d.get("via"),
            "linked_at": d.get("linked_at"),
            "first_linked_at": d.get("first_linked_at"),
            "keyset_fingerprint": self.keyset_fingerprint,
            "service_token_last4": d.get("service_token_last4") if self.linked else None,
            "last_error": error_view(d.get("last_error")),
            "beschadigd": False,
        }

    def connector_view(self) -> dict[str, Any]:
        view = self.public()
        view["pinned_kids"] = {"jwt": sorted(self.pin_map("jwt")), "policy": sorted(self.pin_map("policy"))}
        view["rotation_sequence"] = self.rotation_sequence
        return view


class HubLinkStore:
    """Leest en schrijft ``hub-link.json``; veilig voor meerdere processen (vergrendeling + atomair vervangen)."""

    def __init__(self, secrets_dir: Path) -> None:
        self.dir = secrets_dir
        self.path = secrets_dir / FILE_NAME
        self.lock_path = secrets_dir / LOCK_NAME
        self._mutex = threading.Lock()
        self._cache: tuple[tuple[int, int, int], Link] | None = None

    def read(self) -> Link:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return Link()
        except OSError:
            return Link(corrupt=True)
        key = (st.st_ino, st.st_mtime_ns, st.st_size)
        with self._mutex:
            if self._cache is not None and self._cache[0] == key:
                return self._cache[1]
        link = self._load()
        with self._mutex:
            self._cache = (key, link)
        return link

    def _load(self) -> Link:
        try:
            return Link(validate_doc(json.loads(self.path.read_bytes())))
        except FileNotFoundError:
            return Link()
        except (OSError, ValueError, HubLinkCorrupt):
            return Link(corrupt=True)

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def update(self, change: Callable[[dict[str, Any]], None], *, replace_corrupt: bool = False) -> Link:
        """Lezen-wijzigen-schrijven onder de vergrendeling. Een beschadigd bestand wordt alleen vervangen als
        ``replace_corrupt`` (geslaagde env-enrollment); anders ``HubLinkCorrupt``."""
        with self._locked():
            current = self._load()
            if current.corrupt:
                if not replace_corrupt:
                    raise HubLinkCorrupt("hub-link.json is onleesbaar; alleen een nieuwe koppeling vervangt het")
                stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
                with contextlib.suppress(FileNotFoundError):
                    os.replace(self.path, self.dir / f"{FILE_NAME}.beschadigd-{stamp}")
                doc = empty_doc()
            else:
                doc = copy.deepcopy(current.doc)
            change(doc)
            doc = validate_doc(doc)  # nooit een ongeldig bestand schrijven
            self._write(doc)
        return self.read()

    def _write(self, doc: dict[str, Any]) -> None:
        data = (json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n").encode("utf-8")
        tmp = self.dir / f".{FILE_NAME}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            try:
                view = memoryview(data)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
        dir_fd = os.open(self.dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)


# ---------------------------------------------------------------------------------------------- wijzigingen (puur)
def now_iso(now: datetime | None = None) -> str:
    return iso_z(now or utcnow()) or ""


def set_error(doc: dict[str, Any], code: str | None, now: datetime | None = None) -> None:
    doc["last_error"] = None if code is None else {"code": code, "at": now_iso(now)}


def add_used_token(doc: dict[str, Any], sha256_hex: str) -> None:
    used = [h for h in doc.get("used_enrollment_token_sha256") or [] if h != sha256_hex]
    used.append(sha256_hex)
    doc["used_enrollment_token_sha256"] = used[-MAX_USED_TOKENS:]


def clear_link(doc: dict[str, Any], new_state: str, *, error: str | None = None, now: datetime | None = None) -> None:
    """Naar ``ontkoppeld``/``ingetrokken``: token gewist, pins naar ``previous_pins`` (alleen audit)."""
    if doc.get("pins"):
        doc["previous_pins"] = doc["pins"]
    doc["state"] = new_state
    doc["service_token"] = None
    doc["service_token_last4"] = None
    doc["pins"] = None
    doc["confirmed_at"] = None
    set_error(doc, error, now)
