"""Ondertekende sleutelrotatie van de Hub (contract 1.2 §4.5).

``GET {hub_url}/api/v1/auth/key-rotations?after=<rotation_sequence>`` (zonder authenticatie: de handtekening is het
bewijs) → ``{"items":[{"sequence":n,"statement":"<JWS compact>"}]}``. Per statement:

- JWS-header ``{"alg":"EdDSA","kid":"<gepinde kid>","typ":"hub-key-rotation+jws"}``; payload
  ``{"type":"hub_key_rotation","iss":"hub","sequence":n,"issued_at":"…Z","purpose":"jwt|policy","add":[JWK],
  "retire":["kid"]}``;
- precies één doel per statement, ondertekend met een **op dat moment gepinde** sleutel van dat doel;
- ``sequence`` strikt oplopend zonder gaten (over beide doelen samen), in volgorde toepassen;
- nooit de laatste sleutel van een doel intrekken;
- een ongeldig of ontbrekend statement stopt het toepassen (incident, geen verdere statements).

Na toepassen: ``rotation_sequence`` en ``keyset_fingerprint`` in ``hub-link.json``. De Hub heeft rotatie nog niet
gebouwd en geeft ``{"items":[]}``; een Hub zonder dit endpoint (404) betekent: geen rotaties.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from lifecycle.hublink import HubLinkCorrupt, HubLinkStore, now_iso, set_error
from lifecycle.keys import (
    KeyFormatError,
    Pin,
    b64url_decode,
    hub_jwk_to_pin,
    keyset_fingerprint_of_thumbprints,
    pin_from_entry,
)

log = logging.getLogger("lifecycle.keyrotation")

KEY_ROTATIONS_PATH = "/api/v1/auth/key-rotations"
STATEMENT_TYP = "hub-key-rotation+jws"
UNKNOWN_KID_MIN_INTERVAL_S = 30.0
MAX_ITEMS = 100


class RotationInvalid(Exception):
    pass


@dataclass(frozen=True)
class Change:
    sequence: int
    purpose: str
    add: tuple[Pin, ...]
    retire: tuple[str, ...]


def verify_statement(item: Any, pins: dict[str, list[Pin]], expected_sequence: int) -> Change:
    """Controleert één statement tegen de **huidige** pins. Gooit ``RotationInvalid``."""
    if not isinstance(item, dict) or item.get("sequence") != expected_sequence:
        raise RotationInvalid(f"sequence {expected_sequence} ontbreekt of klopt niet (gat of herhaling)")
    statement = item.get("statement")
    if not isinstance(statement, str) or statement.count(".") != 2 or len(statement) > 65536:
        raise RotationInvalid("statement is geen JWS compact")
    try:
        header = json.loads(b64url_decode(statement.split(".")[0]))
        raw_payload = json.loads(b64url_decode(statement.split(".")[1]))
    except (KeyFormatError, ValueError) as exc:
        raise RotationInvalid("statement onleesbaar") from exc
    if not isinstance(header, dict) or header.get("alg") != "EdDSA" or header.get("typ") != STATEMENT_TYP:
        raise RotationInvalid("header onjuist (alg/typ)")
    purpose = raw_payload.get("purpose") if isinstance(raw_payload, dict) else None
    if purpose not in {"jwt", "policy"}:
        raise RotationInvalid("purpose moet jwt of policy zijn")
    signer = next((p for p in pins[purpose] if p.kid == header.get("kid")), None)
    if signer is None:
        raise RotationInvalid("ondertekend met een sleutel die voor dit doel niet gepind is")
    try:
        verified = jwt.api_jws.PyJWS().decode_complete(statement, signer.public_key(), algorithms=["EdDSA"])
        payload = json.loads(verified["payload"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise RotationInvalid("handtekening ongeldig") from exc
    if (payload.get("type") != "hub_key_rotation" or payload.get("iss") != "hub"
            or payload.get("sequence") != expected_sequence or payload.get("purpose") != purpose
            or not isinstance(payload.get("issued_at"), str)):
        raise RotationInvalid("payload onjuist")
    add_raw, retire_raw = payload.get("add", []), payload.get("retire", [])
    if not isinstance(add_raw, list) or not isinstance(retire_raw, list) or not all(isinstance(k, str)
                                                                                     for k in retire_raw):
        raise RotationInvalid("add/retire ongeldig")
    if not add_raw and not retire_raw:
        raise RotationInvalid("statement wijzigt niets")
    try:
        add = tuple(hub_jwk_to_pin(k) for k in add_raw)
    except KeyFormatError as exc:
        raise RotationInvalid(f"nieuwe sleutel ongeldig: {exc}") from exc
    current = {p.kid for p in pins[purpose]}
    if any(k not in current for k in retire_raw):
        raise RotationInvalid("retire noemt een sleutel die niet gepind is")
    after = (current - set(retire_raw)) | {p.kid for p in add}
    if len({p.kid for p in add}) != len(add) or any(p.kid in current - set(retire_raw) for p in add):
        raise RotationInvalid("kid van een nieuwe sleutel bestaat al")
    if not after:
        raise RotationInvalid("de laatste sleutel van een doel mag nooit worden ingetrokken")
    return Change(expected_sequence, purpose, add, tuple(retire_raw))


def apply_items(doc: dict[str, Any], items: list[Any]) -> tuple[int, str | None]:
    """Past statements in volgorde toe op ``doc`` (in-place). Geeft (aantal toegepast, fouttekst of None)."""
    pins_doc = doc.get("pins")
    if doc.get("state") != "gekoppeld" or not isinstance(pins_doc, dict):
        return 0, None
    pins = {purpose: [pin_from_entry(e) for e in pins_doc[purpose]] for purpose in ("jwt", "policy")}
    entries = {purpose: list(pins_doc[purpose]) for purpose in ("jwt", "policy")}
    seq = int(pins_doc["rotation_sequence"])
    applied = 0
    error: str | None = None
    at = now_iso()
    for item in sorted(items, key=lambda i: i.get("sequence", 0) if isinstance(i, dict) else 0)[:MAX_ITEMS]:
        if isinstance(item, dict) and isinstance(item.get("sequence"), int) and item["sequence"] <= seq:
            continue  # al toegepast
        try:
            change = verify_statement(item, pins, seq + 1)
        except RotationInvalid as exc:
            error = str(exc)
            break
        keep = [e for e in entries[change.purpose] if e["kid"] not in change.retire]
        entries[change.purpose] = keep + [p.as_entry(at, f"rotatie:{change.sequence}") for p in change.add]
        pins[change.purpose] = [pin_from_entry(e) for e in entries[change.purpose]]
        seq = change.sequence
        applied += 1
    if applied:
        pins_doc["jwt"], pins_doc["policy"] = entries["jwt"], entries["policy"]
        pins_doc["rotation_sequence"] = seq
        pins_doc["keyset_fingerprint"] = keyset_fingerprint_of_thumbprints(
            [p.thumbprint for p in pins["jwt"]], _policy_thumbprint(pins["policy"]))
    if error:
        set_error(doc, "sleutelrotatie_ongeldig")
    return applied, error


def _policy_thumbprint(policy: list[Pin]) -> str:
    """§4.5 kent één policy-sleutel in de vingerafdruk. Staan er tijdelijk twee (rotatie met ``add`` zonder
    ``retire``), dan telt de nieuwste (laatst toegevoegde); contract-issue, ADR-0011."""
    return policy[-1].thumbprint


def poll(store: HubLinkStore, client: httpx.Client) -> str:
    link = store.read()
    if not link.linked or not link.hub_url:
        return "niet_gekoppeld"
    try:
        response = client.get(link.hub_url + KEY_ROTATIONS_PATH, params={"after": link.rotation_sequence})
    except httpx.HTTPError:
        return "onbereikbaar"
    if response.status_code == 404:
        return "niet_beschikbaar"  # oudere Hub zonder rotatie: dan is rotatie = opnieuw koppelen
    if response.status_code != 200:
        return f"later:{response.status_code}"
    try:
        body = response.json()
    except ValueError:
        return "later:geen_json"
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return "later:ongeldig_antwoord"
    if not items:
        return "geen"
    result: dict[str, Any] = {}

    def change(doc: dict[str, Any]) -> None:
        result["applied"], result["error"] = apply_items(doc, items)

    try:
        store.update(change)
    except HubLinkCorrupt:
        return "beschadigd"
    if result.get("error"):
        log.error("sleutelrotatie van de Hub geweigerd: %s (geen verdere statements toegepast)", result["error"])
        return f"geweigerd:{result.get('applied', 0)}"
    if not result.get("applied"):
        return "geen"  # intussen al toegepast (bijv. door de achtergrondpoll van het andere proces)
    log.info("sleutelrotatie toegepast (%s statement(s))", result["applied"])
    return f"toegepast:{result['applied']}"


class UnknownKidPoller:
    """API-proces: bij een onbekende ``kid`` één extra poll op de achtergrond, hooguit elke 30 s."""

    def __init__(self, store: HubLinkStore, client_factory: Callable[[], httpx.Client],
                 clock: Callable[[], float] = time.monotonic, run_inline: bool = False) -> None:
        self.store = store
        self.client_factory = client_factory
        self.clock = clock
        self.run_inline = run_inline
        self._lock = threading.Lock()
        self._last: float | None = None
        self.triggered = 0
        self.last_result: str | None = None

    def trigger(self) -> None:
        with self._lock:
            now = self.clock()
            if self._last is not None and now - self._last < UNKNOWN_KID_MIN_INTERVAL_S:
                return
            self._last = now
            self.triggered += 1
        if self.run_inline:
            self._run()
        else:
            threading.Thread(target=self._run, name="key-rotation-poll", daemon=True).start()

    def _run(self) -> None:
        try:
            with self.client_factory() as client:
                self.last_result = poll(self.store, client)
        except Exception as exc:  # achtergrondtaak: nooit het API-proces laten vallen
            self.last_result = f"fout:{type(exc).__name__}"
            log.warning("achtergrondpoll sleutelrotatie mislukt (%s)", type(exc).__name__)
