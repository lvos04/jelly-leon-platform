"""Toestand van de gesimuleerde NAS: versieprofiel, pools, datasets, snapshots, apps, catalogus, schijven, alerts,
API-keys, jobs en ingestelde storingen. Alles in het geheugen; ``reset()`` zet de begintoestand terug.

Alle namen zijn zichtbaar nep (pool ``simpool``, schijven ``SIM-…``) zodat niemand ze voor echte NAS-gegevens houdt.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

# Versieprofielen. Bron API-versielijsten: truenas/middleware src/middlewared/middlewared/api/ op de tags
# TS-25.04.2.6 en TS-25.10.8 (map v25_04_0 … v25_10_5). De laatste API-versie is ook /api/current.
PROFILES: dict[str, dict[str, Any]] = {
    "25.04.2.6": {"family": "25.04", "api_versions": ["v25.04.0", "v25.04.1", "v25.04.2"]},
    "25.10.8": {
        "family": "25.10",
        "api_versions": [
            "v25.04.0", "v25.04.1", "v25.04.2", "v25.10.0", "v25.10.1", "v25.10.2", "v25.10.3", "v25.10.4",
            "v25.10.5",
        ],
    },
}
DEFAULT_VERSION = "25.10.8"

# Rollen (vereenvoudigd uit middlewared/role.py): READONLY_ADMIN omvat alle *_READ-rollen.
READ_ROLES = {"APPS_READ", "CATALOG_READ", "POOL_READ", "DATASET_READ", "SNAPSHOT_READ", "DISK_READ",
              "REPORTING_READ", "ALERT_LIST_READ", "CLOUD_SYNC_READ", "REPLICATION_TASK_READ", "SYSTEM_GENERAL_READ",
              "SNAPSHOT_TASK_READ"}
ROLE_INCLUDES: dict[str, set[str]] = {
    "READONLY_ADMIN": READ_ROLES,
    "APPS_WRITE": {"APPS_READ", "CATALOG_READ", "CATALOG_WRITE"},
    "SNAPSHOT_WRITE": {"SNAPSHOT_READ"},
    "DATASET_WRITE": {"DATASET_READ"},
    "POOL_WRITE": {"POOL_READ"},
    "FULL_ADMIN": READ_ROLES | {"APPS_WRITE", "CATALOG_WRITE", "SNAPSHOT_WRITE", "DATASET_WRITE", "POOL_WRITE"},
}


def expand_roles(roles: set[str]) -> set[str]:
    out = set(roles)
    for role in roles:
        out |= ROLE_INCLUDES.get(role, set())
    return out


def ms_now() -> int:
    return int(time.time() * 1000)


def date(ms: int | None = None) -> dict[str, int]:
    """TrueNAS codeert datetimes in JSON als ``{"$date": <epoch-ms>}`` (aanname; zie README)."""
    return {"$date": ms if ms is not None else ms_now()}


@dataclass
class ApiKey:
    id: int
    key: str
    username: str
    roles: set[str]
    revoked: bool = False

    @property
    def full(self) -> str:
        return f"{self.id}-{self.key}"


# Nep-catalogus. Versies en app_version overgenomen uit truenas/apps (commit f142a16, 2026-11-06). Zoals in die
# commit houdt elke trein per app **alleen de nieuwste versie** (contract 1.2 §2.2): een vastgezette oudere versie
# kan dus uit de catalogus verdwijnen (``app.create`` → "Version … not found"). Al geïnstalleerde versies blijven
# lokaal bekend (``SimState.installed_versions``), zodat ``app.rollback`` naar een eerder geïnstalleerde versie werkt.
# Testbediening ``POST /_sim/catalog/publish`` zet een nieuwe versie in de trein en laat de vorige verdwijnen.
CATALOG: dict[str, dict[str, dict[str, Any]]] = {
    "stable": {
        "ix-app": {"versions": {"1.4.8": "1.4.8"}, "title": "iX App", "categories": ["custom"],
                   "min_scale_version": "24.10.2.2"},
    },
    "community": {
        "authentik": {"versions": {"1.3.63": "2026.8.5"}, "title": "Authentik", "categories": ["security"]},
        "immich": {"versions": {"1.14.62": "v3.3.6"}, "title": "Immich", "categories": ["media"]},
        "uptime-kuma": {"versions": {"1.2.25": "2.5.9"}, "title": "Uptime Kuma", "categories": ["monitoring"]},
        "gotify": {"versions": {"1.1.17": "3.1.1"}, "title": "Gotify", "categories": ["notifications"]},
        "node-red": {"versions": {"1.4.35": "5.0.9-24"}, "title": "Node-RED", "categories": ["productivity"]},
        "adguard-home": {"versions": {"1.3.21": "v0.107.80"}, "title": "AdGuard Home",
                         "categories": ["networking"]},
        "beszel-hub": {"versions": {"1.1.22": "0.21.1"}, "title": "Beszel Hub", "categories": ["monitoring"]},
    },
}


def vkey(version: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in version.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def latest(versions: dict[str, str]) -> str:
    return max(versions, key=vkey)


@dataclass
class SimState:
    version: str = DEFAULT_VERSION
    hostname: str = "truenas-sim"
    boot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    boot_ms: int = field(default_factory=ms_now)
    api_keys: dict[int, ApiKey] = field(default_factory=dict)
    pools: list[dict[str, Any]] = field(default_factory=list)
    datasets: dict[str, dict[str, Any]] = field(default_factory=dict)
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    apps: dict[str, dict[str, Any]] = field(default_factory=dict)
    preferred_trains: list[str] = field(default_factory=lambda: ["stable"])
    disks: list[dict[str, Any]] = field(default_factory=list)
    smart_results: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    gpus: dict[str, dict[str, Any]] = field(default_factory=dict)
    snapshot_tasks: list[dict[str, Any]] = field(default_factory=list)
    cloudsync_tasks: list[dict[str, Any]] = field(default_factory=list)
    replication_tasks: list[dict[str, Any]] = field(default_factory=list)
    extra_alerts: list[dict[str, Any]] = field(default_factory=list)
    jobs: dict[int, dict[str, Any]] = field(default_factory=dict)
    next_job_id: int = 1
    faults: dict[str, Any] = field(default_factory=dict)
    violations: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    catalog: dict[str, dict[str, dict[str, Any]]] = field(default_factory=lambda: copy.deepcopy(CATALOG))
    # (trein, catalogusapp, versie) → app_version van ooit lokaal geïnstalleerde versies (blijven na catalogwissel)
    installed_versions: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def app_version_of(self, train: str, catalog_app: str, version: str) -> str | None:
        versions = self.catalog.get(train, {}).get(catalog_app, {}).get("versions", {})
        return versions.get(version) or self.installed_versions.get((train, catalog_app, version))

    def publish(self, train: str, catalog_app: str, version: str, app_version: str) -> None:
        """Nieuwe versie in de trein; de vorige verdwijnt uit de catalogus (lokaal geïnstalleerde blijven bekend)."""
        meta = self.catalog[train][catalog_app]
        meta["versions"] = {version: app_version}
        for app in self.apps.values():  # geïnstalleerde apps zien de nieuwe versie (zoals na catalog.sync)
            if app["metadata"]["name"] == catalog_app and app["metadata"]["train"] == train:
                app["latest_version"] = version
                app["upgrade_available"] = catalog_app != "ix-app" and vkey(version) > vkey(app["version"])

    @property
    def profile(self) -> dict[str, Any]:
        return PROFILES[self.version]

    @property
    def family(self) -> str:
        return str(self.profile["family"])

    def violation(self, code: str, detail: str) -> None:
        self.violations.append({"code": code, "detail": detail, "at_ms": ms_now()})

    # ------------------------------------------------------------------ opbouw
    @classmethod
    def initial(cls, version: str = DEFAULT_VERSION) -> SimState:
        if version not in PROFILES:
            raise ValueError(f"onbekende simulatorversie {version!r}; kies uit {sorted(PROFILES)}")
        s = cls(version=version)
        s.api_keys = {
            1: ApiKey(1, "simreadonlykey0000000000000000000000000000000000000000000000000", "hub-readonly",
                      {"READONLY_ADMIN"}),
            2: ApiKey(2, "simwritekey00000000000000000000000000000000000000000000000000000", "executor",
                      {"READONLY_ADMIN", "APPS_WRITE", "SNAPSHOT_WRITE", "DATASET_WRITE", "POOL_WRITE"}),
        }
        s.pools = [
            s._pool(1, "simpool", 8_000_000_000_000, 2_000_000_000_000),
            s._pool(2, "simbackup", 4_000_000_000_000, 1_000_000_000_000),
        ]
        for name in ("simpool", "simpool/apps", "simpool/media", "simpool/ix-apps", "simbackup",
                     "simbackup/snapshots"):
            s.add_dataset(name)
        s.disks = [
            {"identifier": f"{{serial}}SIM-DISK-{i}", "name": f"sd{chr(97 + i)}", "serial": f"SIM-DISK-{i}",
             "model": "SIM DISK 8TB", "size": 8_000_000_000_000, "type": "HDD", "rotationrate": 7200,
             "pool": "simpool" if i < 2 else "simbackup", "togglesmart": True, "description": "simulator"}
            for i in range(3)
        ]
        s.smart_results = {d["name"]: [{"num": 1, "description": "Short offline", "status": "SUCCESS",
                                        "status_verbose": "Completed without error", "remaining": 0,
                                        "lifetime": 12000, "lba_of_first_error": None}] for d in s.disks}
        s.gpus = {
            "0000:03:00.0": {"vendor": "INTEL", "description": "SIM Intel GPU A (discreet)", "pci_slot": "0000:03:00.0",
                             "vendor_specific_config": {}, "error": None,
                             "gpu_details": {"addr": {"pci_slot": "0000:03:00.0"}, "devices": ["/dev/dri/renderD129"],
                                             "available_to_host": True}},
            "0000:00:02.0": {"vendor": "INTEL", "description": "SIM Intel GPU B (geïntegreerd)",
                             "pci_slot": "0000:00:02.0", "vendor_specific_config": {}, "error": None,
                             "gpu_details": {"addr": {"pci_slot": "0000:00:02.0"}, "devices": ["/dev/dri/renderD128"],
                                             "available_to_host": True}},
        }
        s.snapshot_tasks = [{"id": 1, "dataset": "simpool/apps", "recursive": True, "lifetime_value": 2,
                             "lifetime_unit": "WEEK", "enabled": True, "naming_schema": "auto-%Y-%m-%d_%H-%M",
                             "schedule": {"minute": "0", "hour": "*", "dom": "*", "month": "*", "dow": "*"},
                             "state": {"state": "FINISHED"}}]
        s.cloudsync_tasks = []
        s.replication_tasks = []
        return s

    @staticmethod
    def _pool(pid: int, name: str, size: int, allocated: int) -> dict[str, Any]:
        return {"id": pid, "name": name, "guid": str(10_000_000 + pid), "path": f"/mnt/{name}", "status": "ONLINE",
                "healthy": True, "warning": False, "status_code": "OK", "status_detail": None, "size": size,
                "allocated": allocated, "free": size - allocated, "is_decrypted": True,
                "scan": {"function": "SCRUB", "state": "FINISHED", "errors": 0, "end_time": date()},
                "topology": {"data": [{"type": "MIRROR", "status": "ONLINE", "children": []}], "log": [],
                             "cache": [], "spare": [], "special": [], "dedup": []}}

    def add_dataset(self, name: str, *, origin: str | None = None) -> dict[str, Any]:
        pool = name.split("/")[0]
        ds = {"id": name, "name": name, "pool": pool, "type": "FILESYSTEM", "mountpoint": f"/mnt/{name}",
              "encrypted": False, "children": [], "origin": {"value": origin or "", "rawvalue": origin or ""},
              "used": {"parsed": 1_000_000, "rawvalue": "1000000", "value": "976K"},
              "available": {"parsed": 1_000_000_000_000, "rawvalue": "1000000000000", "value": "931G"},
              "acltype": {"value": "POSIX"}, "user_properties": {}}
        self.datasets[name] = ds
        return ds

    def pool_of(self, name: str) -> dict[str, Any] | None:
        return next((p for p in self.pools if p["name"] == name.split("/")[0]), None)

    def alerts(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for pool in self.pools:
            if pool["status"] != "ONLINE":
                out.append({"uuid": f"pool-{pool['name']}", "klass": "VolumeStatus", "level": "CRITICAL",
                            "formatted": f"Pool {pool['name']} state is {pool['status']}.", "dismissed": False,
                            "datetime": date(), "source": "", "args": {"volume": pool["name"],
                                                                         "state": pool["status"]}})
        for disk, results in self.smart_results.items():
            if results and results[0]["status"] != "SUCCESS":
                out.append({"uuid": f"smart-{disk}", "klass": "SMART", "level": "CRITICAL",
                            "formatted": f"Device: /dev/{disk}, self-test failed.", "dismissed": False,
                            "datetime": date(), "source": "", "args": {"device": disk}})
        return out + copy.deepcopy(self.extra_alerts)

    def used_ports(self) -> list[int]:
        ports: set[int] = set()
        for app in self.apps.values():
            for port in app["active_workloads"]["used_ports"]:
                for hp in port["host_ports"]:
                    ports.add(int(hp["host_port"]))
        return sorted(ports)
