"""Conflictanalyse: poorten, datasets/hostpaden, appnaam en afhankelijkheden t.o.v. een **gemeten** inventaris.

De inventaris komt van de Hub (read-only TrueNAS-adapter, ``app.query`` met ``active_workloads``); de appstore
heeft zelf geen TrueNAS-sleutel. Zonder inventaris is de uitkomst ``unknown`` — nooit "geen conflicten".
Veldnamen volgen TrueNAS middleware ``api/v28_0_0/app.py`` (``AppActiveWorkloads``/``UsedPorts``/``AppVolumes``).
"""

from __future__ import annotations

import posixpath
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .findings import Finding, Severity, error, warning
from .model import Manifest
from .truenas import AppCreatePayload, parse_version

#: Hostpoorten uit platformcontract §2 (voorstellen) — waarschuwing als een vreemde app ze gebruikt.
PLATFORM_PORTS: dict[int, str] = {
    30400: "ai-server-manager",
    30410: "ai-server-manager-executor",
    30420: "home-control-hub",
    30430: "appstore-en-fork",
    30440: "jelly-leon-platform",
    30450: "zoeken-en-persoonlijke-ai",
    30460: "jelly-leon",
}
MAX_INVENTORY_AGE = timedelta(minutes=15)
#: Een inventaris "uit de toekomst" (klokverschil) is niet te vertrouwen.
MAX_INVENTORY_FUTURE = timedelta(minutes=2)


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class HostPort(_Model):
    host_port: int
    host_ip: str = "0.0.0.0"  # noqa: S104 - zo rapporteert TrueNAS een binding op alle adressen


class UsedPort(_Model):
    container_port: int
    protocol: str
    host_ports: list[HostPort] = Field(default_factory=list)


class Volume(_Model):
    source: str
    destination: str
    mode: str = "rw"
    type: str = "bind"


class InstalledApp(_Model):
    name: str
    state: str | None = None
    catalog_app: str | None = None
    version: str | None = None
    platform_app_id: str | None = None  # label nl.lvos04.platform.app_id indien aanwezig
    labels: dict[str, str] = Field(default_factory=dict)  # labels uit app.config (o.a. nl.lvos04.platform.*)
    used_ports: list[UsedPort] = Field(default_factory=list)
    volumes: list[Volume] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)


class Inventory(_Model):
    observed_at: datetime
    source: Literal["truenas_readback", "hub_snapshot", "simulator"]
    truenas_version: str | None = None
    apps: list[InstalledApp] = Field(default_factory=list)


class ConflictReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok", "conflict", "unknown"]
    inventory_observed_at: datetime | None
    findings: list[Finding]


def _norm(path: str) -> str:
    return posixpath.normpath(path) if path.startswith("/") else path


def _overlap(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return a == b or a.startswith(b.rstrip("/") + "/") or b.startswith(a.rstrip("/") + "/")


def _requested_ports(manifest: Manifest, payload: AppCreatePayload | None) -> list[tuple[int, str]]:
    if payload is None:
        return []
    values: dict[str, Any] = payload.values
    out: list[tuple[int, str]] = []
    for port in values.get("ports", []) or []:
        if isinstance(port, dict) and isinstance(port.get("port_number"), int):
            out.append((port["port_number"], str(port.get("protocol", "tcp")).lower()))
    if manifest.network.host_network or values.get("host_network") is True:
        # host network: de container bindt zijn eigen poorten direct op de NAS (ports-lijst is dan leeg)
        out.extend((p.container_port, p.protocol) for p in manifest.network.ports if isinstance(p.container_port, int))
    return sorted(set(out))


def _requested_paths(payload: AppCreatePayload | None) -> list[tuple[str, bool]]:
    if payload is None:
        return []
    out: list[tuple[str, bool]] = []
    for st in payload.values.get("storage", []) or []:
        if isinstance(st, dict) and st.get("type") == "host_path":
            path = (st.get("host_path_config") or {}).get("path")
            if isinstance(path, str):
                out.append((path, bool(st.get("read_only"))))
    return out


def analyse(
    manifest: Manifest,
    payload: AppCreatePayload | None,
    inventory: Inventory | None,
    *,
    now: datetime | None = None,
) -> ConflictReport:
    findings: list[Finding] = []
    if inventory is None:
        findings.append(
            error(
                "inventory_missing",
                "inventory",
                "Geen gemeten TrueNAS-inventaris meegegeven; poort-, dataset- en afhankelijkheidsconflicten zijn onbekend.",
            )
        )
        return ConflictReport(status="unknown", inventory_observed_at=None, findings=findings)
    now = now or datetime.now(UTC)
    observed = inventory.observed_at if inventory.observed_at.tzinfo else inventory.observed_at.replace(tzinfo=UTC)
    stale = now - observed > MAX_INVENTORY_AGE
    if stale:
        findings.append(
            error(
                "inventory_stale",
                "inventory.observed_at",
                f"Inventaris is van {observed.isoformat()}; ouder dan {int(MAX_INVENTORY_AGE.total_seconds() // 60)} min. Meet opnieuw.",
            )
        )
    elif observed - now > MAX_INVENTORY_FUTURE:
        stale = True
        findings.append(
            error(
                "inventory_stale",
                "inventory.observed_at",
                f"Inventaris is van {observed.isoformat()}, dat ligt in de toekomst; controleer de klok en meet opnieuw.",
            )
        )
    name = payload.app_name if payload else manifest.app_id
    others = [a for a in inventory.apps if a.name != name]
    if len(others) != len(inventory.apps):
        findings.append(error("app_name_taken", "app_name", f"Er bestaat al een app {name!r} op de NAS."))
    # poorten
    in_use: dict[tuple[int, str], str] = {}
    for app in others:
        for up in app.used_ports:
            for hp in up.host_ports:
                in_use[(hp.host_port, up.protocol.lower())] = app.name
    for port, proto in _requested_ports(manifest, payload):
        if (port, proto) in in_use:
            findings.append(
                error(
                    "port_in_use",
                    "ports",
                    f"Hostpoort {port}/{proto} is al in gebruik door app {in_use[(port, proto)]!r}.",
                )
            )
        owner = PLATFORM_PORTS.get(port)
        if owner and owner != manifest.app_id:
            findings.append(
                warning(
                    "port_reserved_platform",
                    "ports",
                    f"Hostpoort {port} is in het platformcontract voorgesteld voor {owner!r}.",
                )
            )
    # hostpaden
    for path, ro in _requested_paths(payload):
        for app in others:
            for vol in app.volumes:
                if vol.type != "bind" or not vol.source.startswith("/"):
                    continue
                if _norm(vol.source) == _norm(path):
                    both_ro = ro and vol.mode == "ro"
                    findings.append(
                        (warning if both_ro else error)(
                            "host_path_in_use",
                            "storage",
                            f"Hostpad {path!r} wordt al gebruikt door {app.name!r}"
                            + (
                                " (beide alleen-lezen)."
                                if both_ro
                                else "; twee schrijvers op dezelfde data is verboden."
                            ),
                        )
                    )
                elif _overlap(vol.source, path):
                    findings.append(
                        (warning if ro and vol.mode == "ro" else error)(
                            "host_path_overlap",
                            "storage",
                            f"Hostpad {path!r} overlapt met {vol.source!r} van {app.name!r}.",
                        )
                    )
    # afhankelijkheden
    installed = {a.platform_app_id or a.name: a for a in others}
    for dep in manifest.dependencies:
        found = installed.get(dep.app_id)
        if found is None:
            path = f"dependencies.{dep.app_id}"
            if dep.kind == "required":
                findings.append(
                    error("dependency_missing", path, f"Vereiste app {dep.app_id!r} is niet geïnstalleerd.")
                )
            else:
                findings.append(
                    warning("dependency_optional_missing", path, f"Optionele app {dep.app_id!r} is niet geïnstalleerd.")
                )
            continue
        if dep.min_version:
            have, need = parse_version(found.version), parse_version(dep.min_version)
            if have is None or need is None:
                findings.append(
                    warning(
                        "dependency_version_unknown",
                        f"dependencies.{dep.app_id}",
                        f"Versie van {dep.app_id!r} is onbekend.",
                    )
                )
            elif have < need:
                findings.append(
                    error(
                        "dependency_version_too_low",
                        f"dependencies.{dep.app_id}",
                        f"{dep.app_id!r} heeft versie {found.version}; minimaal {dep.min_version} vereist.",
                    )
                )
    has_error = any(f.severity is Severity.ERROR for f in findings)
    status: Literal["ok", "conflict", "unknown"] = "unknown" if stale else ("conflict" if has_error else "ok")
    return ConflictReport(status=status, inventory_observed_at=observed, findings=findings)
