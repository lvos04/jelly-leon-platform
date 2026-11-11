"""Alleen-lezen aanroepen naar de Hub voor de planner (contract 1.3 §4.4, C1.3-JLP-14).

- ``inventaris.lezen``: ``GET /api/v1/apps`` (cursor over één momentopname; bij 409 ``snapshot_changed`` opnieuw vanaf
  de eerste pagina, hooguit ``MAX_HERSTART`` keer) en per app ``GET /api/v1/apps/{id}`` (poorten, opslag, catalogus).
- ``catalogus.lezen``: ``{train, app, version}`` → ``{beschikbaar: ja|nee|onbekend, gemeten_op}``. Het contract noemt
  geen pad en de Hub heeft de route nog niet; het pad hieronder is een **voorlopige** keuze op één plek (ADR-0022).

Eerlijk bij elke storing: zonder scope (403 ``scope_ontbreekt``) of zonder route (404/501) → ``capability_unavailable``;
onbereikbaar, 5xx, 429/503 of een onleesbaar antwoord → ``unknown``. Nooit een verzonnen inventaris of ``ja``. De dienst
leest hier nooit geheimen en schrijft niets; hij heeft geen TrueNAS-sleutel.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from appstore.manifest.conflicts import HostPort, InstalledApp, Inventory, UsedPort, Volume
from lifecycle.enrollment import retry_after_seconds

APPS_PATH = "/api/v1/apps"
# VOORLOPIG (contract 1.3 §4.4 noemt geen pad): één plek om het echte pad in te vullen zodra de Hub de route heeft.
CATALOG_AVAILABILITY_PATH = "/api/v1/catalog/availability"
PAGE_SIZE = 100
MAX_HERSTART = 3
MAX_APPS = 2000
# Versheid (contract 1.3 §6.2): een meting ouder dan ``max_age`` is ``unknown`` met ``meting_verouderd`` en telt nooit
# als actueel. Het contract noemt voor ``inventaris.lezen`` en ``catalogus.lezen`` geen eigen ``max_age``; hier geldt
# de standaard van 900 s (een afnemer mag strenger zijn dan de bron, nooit soepeler). Een meting "uit de toekomst"
# (klokverschil) is evenmin te vertrouwen; de marge is die van de conflictanalyse van de manifeststandaard.
MAX_METING_LEEFTIJD = timedelta(seconds=900)
MAX_METING_TOEKOMST = timedelta(minutes=2)
VEROUDERD = "meting_verouderd"


@dataclass(frozen=True)
class Inventaris:
    status: str  # ok | unknown | capability_unavailable
    reden: str | None = None
    inventory: Inventory | None = None
    records: dict[str, dict[str, Any]] = field(default_factory=dict)  # detail van de Hub per TrueNAS-appnaam
    gemeten_op: str | None = None
    snapshot_id: str | None = None
    retry_after_s: float | None = None

    def view(self) -> dict[str, Any]:
        return {"status": self.status, "reden": self.reden, "bron": "hub_snapshot" if self.inventory else None,
                "gemeten_op": self.gemeten_op, "snapshot_id": self.snapshot_id,
                "aantal_apps": len(self.inventory.apps) if self.inventory else None,
                "retry_after_s": self.retry_after_s}


@dataclass(frozen=True)
class Beschikbaarheid:
    beschikbaar: str  # ja | nee | onbekend
    status: str = "ok"  # ok | unknown | capability_unavailable
    reden: str | None = None
    gemeten_op: str | None = None
    train: str | None = None
    app: str | None = None
    version: str | None = None

    def view(self) -> dict[str, Any]:
        return {"train": self.train, "app": self.app, "version": self.version, "beschikbaar": self.beschikbaar,
                "status": self.status, "reden": self.reden, "gemeten_op": self.gemeten_op}

    @property
    def available_versions(self) -> list[str] | None:
        """Vorm voor de vertaling: ``[versie]`` = gemeten aanwezig, ``[]`` = gemeten afwezig, ``None`` = onbekend."""
        if self.beschikbaar == "ja" and self.version:
            return [self.version]
        return [] if self.beschikbaar == "nee" else None


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _te_oud(gemeten: datetime, now: datetime) -> bool:
    gemeten = gemeten if gemeten.tzinfo else gemeten.replace(tzinfo=UTC)
    return now - gemeten > MAX_METING_LEEFTIJD or gemeten - now > MAX_METING_TOEKOMST


def verse_inventaris(inventaris: Inventaris, now: datetime) -> Inventaris:
    """De inventaris zoals de planner hem mag gebruiken: ouder dan ``MAX_METING_LEEFTIJD`` (of uit de toekomst) →
    ``unknown`` met ``meting_verouderd`` en **zonder** apps, met het tijdstip van de laatste meting. Zo bouwt geen
    enkel plan (ook geen update of terugzetplan, die geen conflictanalyse doen) op een oude meting."""
    if inventaris.inventory is None or not _te_oud(inventaris.inventory.observed_at, now):
        return inventaris
    return Inventaris("unknown", VEROUDERD, gemeten_op=inventaris.gemeten_op, snapshot_id=inventaris.snapshot_id)


def verse_meting(meting: Beschikbaarheid, now: datetime) -> Beschikbaarheid:
    """De catalogusmeting zoals de planner haar mag gebruiken: ``ja``/``nee`` zonder leesbaar tijdstip of ouder dan
    ``MAX_METING_LEEFTIJD`` (of uit de toekomst) → ``onbekend`` (``unknown``); het tijdstip blijft zichtbaar."""
    if meting.beschikbaar not in ("ja", "nee"):
        return meting
    gemeten = _parse_ts(meting.gemeten_op)
    if gemeten is None:
        return replace(meting, beschikbaar="onbekend", status="unknown", reden="meting_zonder_tijdstip")
    if _te_oud(gemeten, now):
        return replace(meting, beschikbaar="onbekend", status="unknown", reden=VEROUDERD)
    return meting


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 65536 else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def installed_app(record: dict[str, Any]) -> InstalledApp:
    """Eén app uit het detail van de Hub in de vorm van de conflictanalyse (alleen gemeten velden)."""
    catalogus = _dict(record.get("catalogus"))
    image = _dict(record.get("image"))
    ports: dict[tuple[int, str], list[HostPort]] = {}
    for p in record.get("poorten") or []:
        if not isinstance(p, dict):
            continue
        host, proto = _int(p.get("host_poort")), str(p.get("protocol") or "tcp").lower()
        if host is None:
            continue
        container = _int(p.get("container_poort")) or host
        ports.setdefault((container, proto), []).append(
            HostPort(host_port=host, host_ip=str(p.get("host_ip") or "0.0.0.0")))  # noqa: S104 - zo meldt TrueNAS het
    volumes = [
        Volume(source=str(m["bron"]), destination=str(m.get("doel") or ""),
               type="bind" if m.get("soort") == "hostpad" or str(m.get("type")) == "bind" else str(m.get("type") or ""))
        for m in record.get("opslag") or [] if isinstance(m, dict) and m.get("bron")
    ]
    repo, tag = image.get("repository"), image.get("tag")
    version = record.get("versie") or catalogus.get("app_versie")
    return InstalledApp(
        name=str(record.get("naam") or record.get("runtime_id") or ""),
        state=str(record["runtime_staat"]) if record.get("runtime_staat") else None,
        catalog_app=str(catalogus["app"]) if catalogus.get("app") else None,
        version=str(version) if version else None,
        platform_app_id=str(record["app_id"]) if record.get("app_id") else None,
        used_ports=[UsedPort(container_port=c, protocol=proto, host_ports=hosts)
                    for (c, proto), hosts in ports.items()],
        volumes=volumes,
        images=[f"{repo}:{tag}"] if repo and tag else [],
    )


class HubLezer:
    """Leest inventaris en catalogusbeschikbaarheid met het service-token (nooit een gebruikerstoken)."""

    def __init__(self, client: httpx.Client, hub_url: str, service_token: str) -> None:
        self.client = client
        self.hub_url = hub_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {service_token}", "Accept": "application/json"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response | None:
        try:
            return self.client.get(self.hub_url + path, params=params, headers=self.headers)
        except httpx.HTTPError:
            return None

    @staticmethod
    def _storing(response: httpx.Response | None) -> tuple[str, str, float | None]:
        if response is None:
            return "unknown", "hub_onbereikbaar", None
        status = response.status_code
        code = ""
        try:
            body = response.json()
            code = str(body.get("code") or "") if isinstance(body, dict) else ""
        except ValueError:
            pass
        if status == 403 and code == "scope_ontbreekt":
            return "capability_unavailable", "scope_ontbreekt", None
        if status in (404, 405, 501):
            return "capability_unavailable", "route_ontbreekt", None
        if status in (429, 503):
            return "unknown", "hub_vraagt_te_wachten", retry_after_seconds(response)
        if status == 401:
            return "unknown", "service_token_geweigerd", None
        return "unknown", f"hub_antwoord_{status}", None

    def inventaris(self) -> Inventaris:
        for _ in range(MAX_HERSTART):
            result = self._inventaris_eenmaal()
            if result is not None:
                return result
        return Inventaris("unknown", "meting_wisselt")

    def _inventaris_eenmaal(self) -> Inventaris | None:
        """Eén doorloop over één momentopname; ``None`` = de meting wisselde (409), opnieuw beginnen."""
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        snapshot: str | None = None
        gemeten: str | None = None
        while True:
            params: dict[str, Any] = {"limit": PAGE_SIZE}
            if cursor:
                params["cursor"] = cursor
            response = self._get(APPS_PATH, params)
            if response is not None and response.status_code == 409:
                return None
            if response is None or response.status_code != 200:
                status, reden, retry = self._storing(response)
                return Inventaris(status, reden, retry_after_s=retry)
            try:
                page = response.json()
                state = page["inventaris"]
                page_items = page["items"]
                if not isinstance(state, dict) or not isinstance(page_items, list):
                    raise TypeError("vorm")
            except (ValueError, KeyError, TypeError):
                return Inventaris("unknown", "antwoord_ongeldig")
            if snapshot is None:
                snapshot = str(page.get("snapshot_id") or "")
                gemeten = page.get("meting_observed_at") or state.get("observed_at")
                if state.get("status") != "ok" or state.get("verouderd"):
                    reden = str(state.get("reason_code") or ("verouderd" if state.get("verouderd") else "onbekend"))
                    return Inventaris("unknown", f"inventaris_{reden}", gemeten_op=gemeten, snapshot_id=snapshot)
            elif str(page.get("snapshot_id") or "") != snapshot:
                return None
            items.extend(i for i in page_items if isinstance(i, dict))
            cursor = page.get("next_cursor")
            if not cursor:
                break
            if len(items) > MAX_APPS:
                return Inventaris("unknown", "inventaris_te_groot")
        observed = _parse_ts(gemeten)
        if observed is None:
            return Inventaris("unknown", "meting_ontbreekt", snapshot_id=snapshot)
        records: dict[str, dict[str, Any]] = {}
        for item in items:
            response = self._get(f"{APPS_PATH}/{item.get('id')}")
            if response is None or response.status_code != 200:
                status, reden, retry = self._storing(response)
                return Inventaris(status, reden, gemeten_op=gemeten, snapshot_id=snapshot, retry_after_s=retry)
            try:
                record = response.json()
            except ValueError:
                return Inventaris("unknown", "antwoord_ongeldig", gemeten_op=gemeten, snapshot_id=snapshot)
            if not isinstance(record, dict) or not (record.get("naam") or record.get("runtime_id")):
                return Inventaris("unknown", "antwoord_ongeldig", gemeten_op=gemeten, snapshot_id=snapshot)
            records[str(record.get("naam") or record.get("runtime_id"))] = record
        try:
            inventory = Inventory(observed_at=observed, source="hub_snapshot",
                                  apps=[installed_app(r) for r in records.values()])
        except ValueError:
            return Inventaris("unknown", "antwoord_ongeldig", gemeten_op=gemeten, snapshot_id=snapshot)
        return Inventaris("ok", None, inventory, records, gemeten, snapshot)

    def beschikbaarheid(self, train: str, app: str, version: str) -> Beschikbaarheid:
        base = {"train": train, "app": app, "version": version}
        response = self._get(CATALOG_AVAILABILITY_PATH, base)
        if response is None or response.status_code != 200:
            status, reden, _retry = self._storing(response)
            return Beschikbaarheid("onbekend", status, reden, **base)
        try:
            body = response.json()
            waarde = body["beschikbaar"]
        except (ValueError, KeyError, TypeError):
            return Beschikbaarheid("onbekend", "unknown", "antwoord_ongeldig", **base)
        if waarde not in ("ja", "nee", "onbekend"):
            return Beschikbaarheid("onbekend", "unknown", "antwoord_ongeldig", **base)
        gemeten = body.get("gemeten_op") if isinstance(body.get("gemeten_op"), str) else None
        if waarde != "onbekend" and _parse_ts(gemeten) is None:
            return Beschikbaarheid("onbekend", "unknown", "meting_zonder_tijdstip", **base)
        return Beschikbaarheid(str(waarde), "ok", "niet_gemeten" if waarde == "onbekend" else None, gemeten, **base)
