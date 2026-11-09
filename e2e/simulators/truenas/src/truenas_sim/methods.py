"""Gesimuleerde TrueNAS-methoden. Gedrag volgt de broncode van truenas/middleware (TS-25.04.2.6 / TS-25.10.8) waar
dat voor het platform telt; alles is sterk vereenvoudigd. Bewuste afwijkingen staan in README.md (o.a.: de simulator
WEIGERT ``custom_compose_config``/``custom_app=true`` en registreert overtredingen zoals ``latest``-tags)."""

from __future__ import annotations

import copy
import re
import uuid
from typing import TYPE_CHECKING, Any

from truenas_sim.rpc import (
    EACCES,
    EBUSY,
    EEXIST,
    EINVAL,
    ENOENT,
    EPERM,
    RpcError,
    call_error,
    filter_list,
    register,
    validation_error,
)
from truenas_sim.state import SimState, date, latest, vkey

if TYPE_CHECKING:
    from truenas_sim.server import Ctx, JobHandle

APP_NAME = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
IX_APP = "ix-app"
APP_READ = ("APPS_READ",)
APP_WRITE = ("APPS_WRITE",)


def _arg(params: tuple[Any, ...], index: int, default: Any = None) -> Any:
    return params[index] if len(params) > index else default


# ====================================================================== auth / core / system
@register("auth.login_with_api_key", no_auth=True)
async def auth_login_with_api_key(ctx: Ctx, api_key: str = "") -> bool:
    """Authenticeer de sessie met een API-key (``<id>-<key>``); geeft true/false."""
    return ctx.login_with_key(str(api_key))


@register("auth.login_ex", no_auth=True)
async def auth_login_ex(ctx: Ctx, data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Uitgebreide login; de simulator ondersteunt alleen mechanism API_KEY_PLAIN."""
    data = data or {}
    if data.get("mechanism") != "API_KEY_PLAIN":
        raise validation_error([("auth_login_ex.mechanism", "Alleen API_KEY_PLAIN wordt gesimuleerd")])
    ok = ctx.login_with_key(str(data.get("api_key", "")), username=data.get("username"))
    return {"response_type": "SUCCESS" if ok else "AUTH_ERR"}


@register("auth.me")
async def auth_me(ctx: Ctx) -> dict[str, Any]:
    """Wie ben ik (vereenvoudigd)."""
    return {"pw_name": ctx.session.username, "privilege": {"roles": {"$set": sorted(ctx.session.roles)}},
            "account_attributes": ["API_KEY"]}


@register("auth.logout")
async def auth_logout(ctx: Ctx) -> bool:
    """Sessie afmelden."""
    ctx.session.logout()
    return True


@register("core.ping", no_auth=True)
async def core_ping(ctx: Ctx) -> str:
    """Levensteken."""
    return "pong"


@register("core.get_methods")
async def core_get_methods(ctx: Ctx, service: str | None = None, target: str = "WS") -> dict[str, Any]:
    """Publieke methoden van déze API-versie (vereenvoudigde metadata)."""
    out: dict[str, Any] = {}
    for name, method in sorted(ctx.methods.items()):
        if service and not name.startswith(service + "."):
            continue
        out[name] = {"description": method.description, "job": method.job, "roles": sorted(method.roles),
                     "filterable": method.filterable, "no_auth_required": method.no_auth, "accepts": [],
                     "returns": [], "removed_in": None}
    return out


def _job_view(job: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(job)


@register("core.get_jobs", filterable=True)
async def core_get_jobs(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """Jobs van de eigen gebruiker (FULL_ADMIN: alle) — na een gesimuleerde herstart is de lijst leeg."""
    visible = [_job_view(j) for j in ctx.state.jobs.values() if ctx.job_access_error(j) is None]
    return filter_list(visible, filters, options)


def _owned_job(ctx: Ctx, job_id: int) -> None:
    job = ctx.state.jobs.get(int(job_id))
    if job is not None and (error := ctx.job_access_error(job)) is not None:
        raise call_error(EPERM, error)


@register("core.job_wait")
async def core_job_wait(ctx: Ctx, job_id: int) -> Any:
    """Wacht tot een eigen job klaar is en geef het resultaat (fout als de job faalde)."""
    _owned_job(ctx, job_id)
    return await ctx.server.wait_job(int(job_id))


@register("core.job_abort")
async def core_job_abort(ctx: Ctx, job_id: int) -> None:
    """Eigen job afbreken (alleen als hij nog niet klaar is); een read-only key kan geen schrijfjob stoppen."""
    _owned_job(ctx, job_id)
    ctx.server.abort_job(int(job_id))


@register("core.subscribe")
async def core_subscribe(ctx: Ctx, event: str) -> str:
    """Abonneer op een collectie (core.get_jobs, app.query); notificatie ``collection_update``."""
    sub_id = str(uuid.uuid4())
    ctx.session.subscriptions[sub_id] = str(event)
    return sub_id


@register("core.unsubscribe")
async def core_unsubscribe(ctx: Ctx, sub_id: str) -> None:
    """Abonnement opzeggen."""
    ctx.session.subscriptions.pop(str(sub_id), None)


@register("system.info", roles=("SYSTEM_GENERAL_READ",))
async def system_info(ctx: Ctx) -> dict[str, Any]:
    """Basisinformatie van het (gesimuleerde) systeem."""
    s = ctx.state
    return {"version": s.version, "buildtime": date(s.boot_ms), "hostname": s.hostname, "physmem": 64 * 2**30,
            "model": "SIM CPU (simulator)", "cores": 8, "physical_cores": 8, "loadavg": [0.1, 0.1, 0.1],
            "uptime": "0:10:00", "uptime_seconds": 600.0, "system_serial": "SIM-SERIAL",
            "system_product": "TRUENAS-SIMULATOR", "boottime": date(s.boot_ms), "datetime": date(),
            "timezone": "Etc/UTC", "ecc_memory": False}


@register("system.version", roles=("SYSTEM_GENERAL_READ",))
async def system_version(ctx: Ctx) -> str:
    """Volledige versienaam (aanname formaat ``TrueNAS-<versie>``)."""
    return f"TrueNAS-{ctx.state.version}"


@register("system.version_short", roles=("SYSTEM_GENERAL_READ",))
async def system_version_short(ctx: Ctx) -> str:
    """Korte versie, bijv. 25.10.8."""
    return ctx.state.version


@register("system.boot_id", no_auth=True)
async def system_boot_id(ctx: Ctx) -> str:
    """Wisselt bij elke (gesimuleerde) herstart."""
    return ctx.state.boot_id


@register("system.ready", no_auth=True)
async def system_ready(ctx: Ctx) -> bool:
    """Systeem klaar."""
    return True


# ====================================================================== opslag
@register("pool.query", roles=("POOL_READ",), filterable=True)
async def pool_query(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """Pools met status/gezondheid (via storingsinjectie instelbaar)."""
    return filter_list(copy.deepcopy(ctx.state.pools), filters, options)


@register("pool.dataset.query", roles=("DATASET_READ",), filterable=True)
async def pool_dataset_query(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """Datasets (platte lijst; ``children`` blijft leeg in de simulator)."""
    return filter_list(copy.deepcopy(list(ctx.state.datasets.values())), filters, options)


@register("pool.dataset.create", roles=("DATASET_WRITE",))
async def pool_dataset_create(ctx: Ctx, data: dict[str, Any]) -> dict[str, Any]:
    """Dataset aanmaken (vereenvoudigd; ouder moet bestaan)."""
    name = str((data or {}).get("name", ""))
    parent = name.rsplit("/", 1)[0] if "/" in name else ""
    if not parent or parent not in ctx.state.datasets:
        raise validation_error([("pool_dataset_create.name", "Parent dataset does not exist")])
    if name in ctx.state.datasets:
        raise validation_error([("pool_dataset_create.name", "Dataset already exists")])
    return copy.deepcopy(ctx.state.add_dataset(name))


def _snap_entry(ctx: Ctx, dataset: str, name: str) -> dict[str, Any]:
    sid = f"{dataset}@{name}"
    now = date()
    return {"id": sid, "name": sid, "dataset": dataset, "snapshot_name": name, "pool": dataset.split("/")[0],
            "type": "SNAPSHOT", "properties": {"creation": {"value": str(now["$date"]), "rawvalue": str(now["$date"]),
                                                            "parsed": now, "source": "NONE"}},
            "holds": {}, "createtxg": str(len(ctx.state.snapshots) + 1)}


async def _snapshot_query(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    return filter_list(copy.deepcopy(list(ctx.state.snapshots.values())), filters, options)


async def _snapshot_create(ctx: Ctx, data: dict[str, Any]) -> dict[str, Any]:
    data = data or {}
    dataset, name = str(data.get("dataset", "")), str(data.get("name", ""))
    if dataset not in ctx.state.datasets:
        raise validation_error([("snapshot_create.dataset", "Dataset not found")])
    if not name or "@" in name:
        raise validation_error([("snapshot_create.name", "Invalid snapshot name")])
    targets = [d for d in ctx.state.datasets if d == dataset or (data.get("recursive") and d.startswith(dataset + "/"))]
    if f"{dataset}@{name}" in ctx.state.snapshots:
        raise call_error(EEXIST, f"Snapshot {dataset}@{name} already exists")
    for ds in targets:
        ctx.state.snapshots[f"{ds}@{name}"] = _snap_entry(ctx, ds, name)
    return copy.deepcopy(ctx.state.snapshots[f"{dataset}@{name}"])


async def _snapshot_clone(ctx: Ctx, data: dict[str, Any]) -> bool:
    data = data or {}
    snap, dst = str(data.get("snapshot", "")), str(data.get("dataset_dst", ""))
    if snap not in ctx.state.snapshots:
        raise call_error(ENOENT, f"Snapshot {snap} not found")
    if dst in ctx.state.datasets:
        raise validation_error([("snapshot_clone.dataset_dst", "Dataset already exists")])
    if dst.rsplit("/", 1)[0] not in ctx.state.datasets:
        raise validation_error([("snapshot_clone.dataset_dst", "Parent dataset does not exist")])
    ctx.state.add_dataset(dst, origin=snap)
    return True


async def _snapshot_rollback(ctx: Ctx, snap_id: str, options: dict[str, Any] | None = None) -> None:
    if str(snap_id) not in ctx.state.snapshots:
        raise call_error(ENOENT, f"Snapshot {snap_id} not found")
    ctx.state.violation("snapshot_rollback", f"snapshot {snap_id} teruggezet (destructief; alleen via eigenaar)")


# 25.04: zfs.snapshot.* publiek; 25.10: pool.snapshot.* publiek en zfs.snapshot.* privé (hier: afwezig).
for _prefix, _fam in (("zfs.snapshot", ("25.04",)), ("pool.snapshot", ("25.10",))):
    register(f"{_prefix}.query", roles=("SNAPSHOT_READ",), families=_fam, filterable=True,
             description="Snapshots")(_snapshot_query)
    register(f"{_prefix}.create", roles=("SNAPSHOT_WRITE",), families=_fam, description="Snapshot maken")(
        _snapshot_create)
    register(f"{_prefix}.clone", roles=("SNAPSHOT_WRITE",), families=_fam, description="Snapshot klonen")(
        _snapshot_clone)
    register(f"{_prefix}.rollback", roles=("SNAPSHOT_WRITE",), families=_fam,
             description="Snapshot terugzetten (destructief)")(_snapshot_rollback)


@register("pool.snapshottask.query", roles=("SNAPSHOT_TASK_READ",), filterable=True)
async def pool_snapshottask_query(ctx: Ctx, filters: list[Any] | None = None,
                                  options: dict[str, Any] | None = None) -> Any:
    """Periodieke snapshottaken."""
    return filter_list(copy.deepcopy(ctx.state.snapshot_tasks), filters, options)


@register("cloudsync.query", roles=("CLOUD_SYNC_READ",), filterable=True)
async def cloudsync_query(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """Cloud-synctaken (standaard leeg: de echte OneDrive-taak is onbekend, besluit D04)."""
    return filter_list(copy.deepcopy(ctx.state.cloudsync_tasks), filters, options)


@register("replication.query", roles=("REPLICATION_TASK_READ",), filterable=True)
async def replication_query(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """Replicatietaken."""
    return filter_list(copy.deepcopy(ctx.state.replication_tasks), filters, options)


@register("alert.list", roles=("ALERT_LIST_READ",))
async def alert_list(ctx: Ctx) -> list[dict[str, Any]]:
    """Actieve alerts (o.a. afgeleid van gedegradeerde pools en falende SMART-tests)."""
    return ctx.state.alerts()


@register("disk.query", roles=("DISK_READ",), filterable=True)
async def disk_query(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """Schijven."""
    return filter_list(copy.deepcopy(ctx.state.disks), filters, options)


# 25.04: smart.test.* publiek (MW-25.04 plugins/smart.py). 25.10: plugin verwijderd (MW-25.10.8 heeft geen
# plugins/smart.py, alleen privé disk.smart_test) en api.truenas.com/v25.10 documenteert geen smart.*-methode →
# hier afwezig (-32601); afnemers melden capability_unavailable. SMART-alerts blijven via alert.list (beide versies).
@register("smart.test.results", roles=("REPORTING_READ",), families=("25.04",), filterable=True)
async def smart_test_results(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """SMART-testresultaten per schijf (alleen 25.04; schema vereenvoudigd, zie README)."""
    rows = [{"disk": disk, "tests": copy.deepcopy(tests)} for disk, tests in ctx.state.smart_results.items()]
    return filter_list(rows, filters, options)


@register("disk.temperatures", roles=("REPORTING_READ",))
async def disk_temperatures(ctx: Ctx, names: list[str] | None = None, include_thresholds: bool = False) -> Any:
    """Temperatuur per schijf (vast 35 °C in de simulator)."""
    return {d["name"]: 35 for d in ctx.state.disks if not names or d["name"] in names}


# ====================================================================== catalogus
def _catalog_item(catalog: dict[str, Any], train: str, name: str) -> dict[str, Any]:
    meta = catalog[train][name]
    versions = meta["versions"]
    top = latest(versions)
    return {"name": name, "title": meta["title"], "train": train, "categories": meta["categories"],
            "latest_version": top, "latest_app_version": versions[top],
            "latest_human_version": f"{versions[top]}_{top}",
            "last_update": "2026-11-06 00:00:00", "recommended": False, "healthy": True, "supported": True,
            "description": f"{meta['title']} (sim-catalogus)", "home": "", "icon_url": "", "maintainers": [],
            "screenshots": [], "sources": [], "tags": [], "popularity_rank": None}


@register("app.available", roles=("CATALOG_READ",), filterable=True)
async def app_available(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """Beschikbare catalogusapps in de voorkeurstreinen."""
    installed = {(a["metadata"]["name"], a["metadata"]["train"]) for a in ctx.state.apps.values()}
    rows = [
        {"catalog": "TRUENAS", "installed": (name, train) in installed, **_catalog_item(ctx.state.catalog, train, name)}
        for train in ctx.state.preferred_trains if train in ctx.state.catalog
        for name in ctx.state.catalog[train] if name != IX_APP
    ]
    return filter_list(rows, filters, options)


@register("catalog.get_app_details", roles=("CATALOG_READ",))
async def catalog_get_app_details(ctx: Ctx, app_name: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Details en versies van één catalogusapp (``{"train": …}``)."""
    train = str((options or {}).get("train", "stable"))
    catalog = ctx.state.catalog
    if train not in catalog:
        raise call_error(ENOENT, f"Unable to locate {train!r} train")
    if app_name not in catalog[train]:
        raise call_error(ENOENT, f"Unable to locate {app_name!r} app in {train!r} train")
    item = _catalog_item(catalog, train, app_name)
    item["versions"] = {
        v: {"version": v, "human_version": f"{av}_{v}", "healthy": True, "supported": True,
            "app_metadata": {"name": app_name, "train": train, "version": v, "app_version": av}}
        for v, av in catalog[train][app_name]["versions"].items()
    }
    return item


@register("catalog.config", roles=("CATALOG_READ",))
async def catalog_config(ctx: Ctx) -> dict[str, Any]:
    """Catalogusinstellingen."""
    return {"id": "TRUENAS", "label": "TRUENAS", "preferred_trains": list(ctx.state.preferred_trains),
            "location": "/mnt/.ix-apps/truenas_catalog"}


@register("catalog.trains", roles=("CATALOG_READ",))
async def catalog_trains(ctx: Ctx) -> list[str]:
    """Beschikbare treinen."""
    return sorted(ctx.state.catalog)


@register("catalog.update", roles=("CATALOG_WRITE",))
async def catalog_update(ctx: Ctx, data: dict[str, Any]) -> dict[str, Any]:
    """Voorkeurstreinen instellen (bijv. community toevoegen)."""
    trains = list((data or {}).get("preferred_trains", []))
    if not trains or any(t not in ctx.state.catalog for t in trains):
        raise validation_error([("catalog_update.preferred_trains", "Onbekende of lege treinenlijst")])
    ctx.state.preferred_trains = trains
    return await catalog_config(ctx)


async def _sync_runner(ctx: Ctx, job: JobHandle) -> None:
    await job.progress(100, "Synced catalog")


@register("catalog.sync", roles=("CATALOG_WRITE",), job=True)
def catalog_sync(ctx: Ctx) -> Any:
    """Catalogus synchroniseren (job)."""
    return lambda job: _sync_runner(ctx, job)


# ====================================================================== apps
def _ports_from_values(values: dict[str, Any]) -> list[dict[str, Any]]:
    if values.get("host_network"):
        return []
    out = []
    for port in values.get("ports", []) or []:
        if port.get("bind_mode", "published") != "published":
            continue
        out.append({"container_port": int(port["container_port"]), "protocol": port.get("protocol", "tcp"),
                    "host_ports": [{"host_port": int(port["port_number"]), "host_ip": ip}
                                   for ip in (port.get("host_ips") or ["0.0.0.0"])]})  # noqa: S104
    return out


def _volumes(app_name: str, values: dict[str, Any]) -> list[dict[str, Any]]:
    vols = []
    for st in values.get("storage", []) or []:
        if st.get("type") == "host_path":
            src = (st.get("host_path_config") or {}).get("path", "")
        elif st.get("type") == "ix_volume":
            name = (st.get("ix_volume_config") or {}).get("dataset_name", "storage_entry")
            src = f"/mnt/.ix-apps/app_mounts/{app_name}/{name}"
        else:
            src = str(st.get("type"))
        vols.append({"source": src, "destination": st.get("mount_path", ""), "mode": "ro" if st.get("read_only")
                     else "rw", "type": "bind"})
    return vols


def _image_of(catalog_app: str, values: dict[str, Any], app_version: str) -> str:
    if catalog_app == IX_APP:
        image = values.get("image") or {}
        return f"{image.get('repository', '')}:{image.get('tag') or 'latest'}"
    return f"sim/{catalog_app}:{app_version}"


def _app_entry(sim: SimState, name: str, train: str, catalog_app: str, version: str, values: dict[str, Any],
               state: str = "RUNNING") -> dict[str, Any]:
    app_version = sim.app_version_of(train, catalog_app, version)
    if app_version is None:
        raise call_error(ENOENT, f"Version {version} not found in {catalog_app} app")
    sim.installed_versions[(train, catalog_app, version)] = app_version
    image = _image_of(catalog_app, values, app_version)
    ports = _ports_from_values(values)
    top = latest(sim.catalog[train][catalog_app]["versions"])
    return {
        "name": name, "id": name, "state": state, "upgrade_available": vkey(top) > vkey(version) and
        catalog_app != IX_APP, "latest_version": top, "image_updates_available": False, "custom_app": False,
        "migrated": False, "human_version": f"{app_version}_{version}", "version": version,
        "metadata": {"name": catalog_app, "train": train, "version": version, "app_version": app_version,
                     "title": sim.catalog[train][catalog_app]["title"]},
        "active_workloads": {"containers": 1 if state == "RUNNING" else 0, "used_ports": ports,
                             "used_host_ips": [], "container_details": [
                                 {"id": uuid.uuid4().hex[:12], "service_name": name, "image": image,
                                  "port_config": ports, "state": "running" if state == "RUNNING" else "exited",
                                  "volume_mounts": _volumes(name, values)}],
                             "volumes": _volumes(name, values), "images": [image], "networks": []},
        "notes": values.get("notes"), "portals": {}, "version_details": None, "config": copy.deepcopy(values),
        "_history": [version],
    }


def _public(app: dict[str, Any], with_config: bool) -> dict[str, Any]:
    out = {k: copy.deepcopy(v) for k, v in app.items() if not k.startswith("_")}
    if not with_config:
        out["config"] = None
    return out


def _reject_compose(ctx: Ctx, data: dict[str, Any], where: str) -> None:
    if data.get("custom_app") or data.get("custom_compose_config") or data.get("custom_compose_config_string"):
        ctx.state.violation("custom_compose_config", f"{where}: compose/YAML-installatie geprobeerd")
        raise call_error(EINVAL, "SIM-BELEID: custom_app/custom_compose_config (Install via YAML) is in dit platform "
                                 "verboden; gebruik de catalogusapp 'ix-app' met values.")


def _validate_values(ctx: Ctx, app_name: str, catalog_app: str, values: dict[str, Any], prefix: str) -> None:
    errors: list[tuple[str, str]] = []
    if catalog_app == IX_APP:
        image = values.get("image") or {}
        if not image.get("repository"):
            errors.append((f"{prefix}.image.repository", "Field is required"))
        tag = str(image.get("tag") or "")
        if not tag or tag == "latest":
            ctx.state.violation("image_tag_latest", f"{app_name}: tag {tag or '(leeg → latest)'}")
        if values.get("privileged"):
            ctx.state.violation("privileged", f"{app_name}: privileged=true")
        if values.get("host_network"):
            ctx.state.violation("host_network", f"{app_name}: host_network=true")
    used = {p: n for n, a in ctx.state.apps.items() if n != app_name
            for port in a["active_workloads"]["used_ports"] for p in [h["host_port"] for h in port["host_ports"]]}
    for i, port in enumerate(_ports_from_values(values)):
        for hp in port["host_ports"]:
            if hp["host_port"] in used:
                errors.append((f"{prefix}.ports.{i}.port_number",
                               f"The port is being used by following services: {used[hp['host_port']]!r}"))
    for i, st in enumerate(values.get("storage", []) or []):
        if st.get("type") == "host_path":
            path = str((st.get("host_path_config") or {}).get("path", ""))
            ds = path.removeprefix("/mnt/")
            if not path.startswith("/mnt/") or ds not in ctx.state.datasets:
                errors.append((f"{prefix}.storage.{i}.host_path_config.path",
                               "This path does not exist or is not a dataset under /mnt"))
    if errors:
        raise validation_error(errors)


def _pre_create(ctx: Ctx, data: dict[str, Any] | None = None) -> None:
    data = data or {}
    _reject_compose(ctx, data, "app.create")
    name = str(data.get("app_name", ""))
    if not APP_NAME.match(name) or len(name) > 40:
        raise validation_error([("app_create.app_name", "String should match pattern '^[a-z]([-a-z0-9]*[a-z0-9])?$'")])
    if not data.get("catalog_app"):
        raise validation_error([("app_create.catalog_app", "This field is required")])


async def _create_runner(ctx: Ctx, job: JobHandle, data: dict[str, Any]) -> dict[str, Any]:
    name, catalog_app = data["app_name"], data["catalog_app"]
    train, version = data.get("train", "stable"), str(data.get("version", "latest"))
    if name in ctx.state.apps:
        raise call_error(EEXIST, f"Application with name {name} already exists")
    if train not in ctx.state.catalog or catalog_app not in ctx.state.catalog[train]:
        raise call_error(ENOENT, f"Unable to locate {catalog_app!r} app in {train!r} train")
    versions = ctx.state.catalog[train][catalog_app]["versions"]
    if version == "latest":
        ctx.state.violation("version_latest", f"{name}: catalogusversie 'latest'")
        version = latest(versions)
    if version not in versions:
        raise call_error(ENOENT, f"Version {version} not found in {catalog_app} app")
    values = copy.deepcopy(data.get("values") or {})
    _validate_values(ctx, name, catalog_app, values, "app_create.values")
    await job.progress(25, "Initial Validation completed")
    app = _app_entry(ctx.state, name, train, catalog_app, version, values, state="DEPLOYING")
    created_datasets: list[str] = []
    for st in values.get("storage", []) or []:
        if st.get("type") == "ix_volume":
            ds = f"simpool/ix-apps/app_mounts/{name}/{(st.get('ix_volume_config') or {}).get('dataset_name')}"
            for candidate in (ds.rsplit("/", 1)[0], ds):
                if candidate not in ctx.state.datasets:
                    ctx.state.add_dataset(candidate)
                    created_datasets.append(candidate)
    ctx.state.apps[name] = app
    await ctx.server.emit("app.query", "added", name, _public(app, False))
    try:
        await job.progress(60, "App installation in progress, pulling images", checkpoint=True)
    except RpcError:
        # zoals middlewared crud.py create_internal/remove_failed_resources: mislukte installatie wordt opgeruimd
        # (app weg, tijdens deze installatie aangemaakte ixVolume-datasets weg). Een herstart (Reboot) valt hier
        # bewust buiten: dan blijft de app half toegepast staan (CRASHED).
        await job.progress(80, f"Failure occurred while installing {name!r}, cleaning up")
        ctx.state.apps.pop(name, None)
        for ds in reversed(created_datasets):
            ctx.state.datasets.pop(ds, None)
        await ctx.server.emit("app.query", "removed", name, None)
        raise
    app["state"] = "RUNNING"
    await ctx.server.emit("app.query", "changed", name, _public(app, False))
    await job.progress(100, f"{name!r} installed successfully")
    return _public(app, False)


@register("app.create", roles=APP_WRITE, job=True, pre=_pre_create)
def app_create(ctx: Ctx, data: dict[str, Any]) -> Any:
    """App installeren: catalogusapp (``ix-app`` = standaard Custom App) met ``values`` — job."""
    return lambda job: _create_runner(ctx, job, data)


def _get_app(ctx: Ctx, name: str) -> dict[str, Any]:
    app = ctx.state.apps.get(str(name))
    if app is None:
        raise call_error(ENOENT, f"{name!r} app not found")
    return app


@register("app.query", roles=APP_READ, filterable=True)
async def app_query(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """Geïnstalleerde apps; ``options.extra.retrieve_config`` geeft de waarden mee."""
    with_config = bool(((options or {}).get("extra") or {}).get("retrieve_config"))
    rows = [_public(a, with_config) for a in ctx.state.apps.values()]
    opts = {k: v for k, v in (options or {}).items() if k != "extra"}
    return filter_list(rows, filters, opts)


@register("app.config", roles=APP_READ)
async def app_config(ctx: Ctx, app_name: str) -> dict[str, Any]:
    """Huidige waarden van een app."""
    config: dict[str, Any] = copy.deepcopy(_get_app(ctx, app_name)["config"])
    return config


@register("app.used_ports", roles=APP_READ)
async def app_used_ports(ctx: Ctx) -> list[int]:
    """Door apps gebruikte hostpoorten."""
    return ctx.state.used_ports()


@register("app.gpu_choices", roles=APP_READ)
async def app_gpu_choices(ctx: Ctx) -> dict[str, Any]:
    """GPU-keuzes (simulator: twee zichtbaar nep-Intel-GPU's, instelbaar via /_sim/fixtures)."""
    return copy.deepcopy({k: v for k, v in ctx.state.gpus.items() if not v.get("error")})


@register("app.rollback_versions", roles=APP_READ)
async def app_rollback_versions(ctx: Ctx, app_name: str) -> list[str]:
    """Versies waarheen terugzetten kan (alleen eerder geïnstalleerde catalogusversies)."""
    app = _get_app(ctx, app_name)
    return sorted((v for v in app["_history"] if vkey(v) < vkey(app["version"])), key=vkey)


@register("app.upgrade_summary", roles=APP_READ)
async def app_upgrade_summary(ctx: Ctx, app_name: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Samenvatting van een upgrade naar een catalogusversie."""
    app = _get_app(ctx, app_name)
    meta = app["metadata"]
    versions = ctx.state.catalog[meta["train"]][meta["name"]]["versions"]
    if not app["upgrade_available"]:
        raise call_error(ENOENT, f"No upgrade available for {app_name!r}")
    target = str((options or {}).get("app_version", "latest"))
    target = latest(versions) if target == "latest" else target
    return {"latest_version": latest(versions), "latest_human_version": versions[latest(versions)],
            "upgrade_version": target, "upgrade_human_version": versions.get(target, ""),
            "available_versions_for_upgrade": [{"version": v, "human_version": versions[v]} for v in
                                               sorted(versions, key=vkey) if vkey(v) > vkey(app["version"])],
            "changelog": None}


def _pre_update(ctx: Ctx, app_name: str = "", data: dict[str, Any] | None = None) -> None:
    _reject_compose(ctx, data or {}, "app.update")


async def _update_runner(ctx: Ctx, job: JobHandle, app_name: str, data: dict[str, Any]) -> dict[str, Any]:
    app = _get_app(ctx, app_name)
    meta = app["metadata"]
    # Echt gedrag (crud.py update_internal): config.update(values) — alleen een ondiepe merge op het hoogste niveau.
    new_values = copy.deepcopy(app["config"])
    new_values.update(copy.deepcopy((data or {}).get("values") or {}))
    _validate_values(ctx, app_name, meta["name"], new_values, "app_update.values")
    await job.progress(25, "Initial Validation completed")
    before = copy.deepcopy(app)
    app["config"] = new_values
    await job.progress(60, "Configuration updated", checkpoint=True)
    fresh = _app_entry(ctx.state, app_name, meta["train"], meta["name"], app["version"], new_values,
                       state="RUNNING" if before["state"] != "STOPPED" else "STOPPED")
    fresh["_history"] = before["_history"]
    ctx.state.apps[app_name] = fresh
    await ctx.server.emit("app.query", "changed", app_name, _public(fresh, False))
    await job.progress(100, f"Update completed for {app_name!r}")
    return _public(fresh, False)


@register("app.update", roles=APP_WRITE, job=True, pre=_pre_update)
def app_update(ctx: Ctx, app_name: str, data: dict[str, Any] | None = None) -> Any:
    """App-waarden bijwerken (bij ix-app o.a. nieuwe image-tag) — job."""
    return lambda job: _update_runner(ctx, job, app_name, data or {})


async def _delete_runner(ctx: Ctx, job: JobHandle, app_name: str, options: dict[str, Any]) -> bool:
    app = _get_app(ctx, app_name)
    await job.progress(20, f"Deleting {app_name!r} app", checkpoint=True)
    if options.get("remove_ix_volumes", False):
        ctx.state.violation("remove_ix_volumes", f"{app_name}: remove_ix_volumes=true (data gewist)")
        prefix = f"simpool/ix-apps/app_mounts/{app_name}"
        for ds in [d for d in ctx.state.datasets if d == prefix or d.startswith(prefix + "/")]:
            del ctx.state.datasets[ds]
    del ctx.state.apps[app_name]
    _ = app
    await ctx.server.emit("app.query", "removed", app_name, None)
    await job.progress(100, f"Deleted {app_name!r} app")
    return True


@register("app.delete", roles=APP_WRITE, job=True)
def app_delete(ctx: Ctx, app_name: str, options: dict[str, Any] | None = None) -> Any:
    """App verwijderen; ``remove_ix_volumes`` standaard false (data blijft) — job."""
    opts = {"remove_images": True, "remove_ix_volumes": False, "force_remove_ix_volumes": False,
            "force_remove_custom_app": False, **(options or {})}
    return lambda job: _delete_runner(ctx, job, app_name, opts)


async def _state_runner(ctx: Ctx, job: JobHandle, app_name: str, target: str) -> Any:
    app = _get_app(ctx, app_name)
    await job.progress(50, f"{target.lower()} {app_name!r}", checkpoint=True)
    app["state"] = target
    app["active_workloads"]["containers"] = 1 if target == "RUNNING" else 0
    for c in app["active_workloads"]["container_details"]:
        c["state"] = "running" if target == "RUNNING" else "exited"
    await ctx.server.emit("app.query", "changed", app_name, _public(app, False))
    await job.progress(100, "done")
    return _public(app, False) if target == "RUNNING" else None


@register("app.start", roles=APP_WRITE, job=True)
def app_start(ctx: Ctx, app_name: str) -> Any:
    """App starten — job."""
    return lambda job: _state_runner(ctx, job, app_name, "RUNNING")


@register("app.stop", roles=APP_WRITE, job=True)
def app_stop(ctx: Ctx, app_name: str) -> Any:
    """App stoppen — job."""
    return lambda job: _state_runner(ctx, job, app_name, "STOPPED")


@register("app.redeploy", roles=APP_WRITE, job=True)
def app_redeploy(ctx: Ctx, app_name: str) -> Any:
    """App opnieuw uitrollen — job."""
    return lambda job: _state_runner(ctx, job, app_name, "RUNNING")


async def _rollback_runner(ctx: Ctx, job: JobHandle, app_name: str, options: dict[str, Any]) -> dict[str, Any]:
    app = _get_app(ctx, app_name)
    target = str(options.get("app_version", ""))
    if target == app["version"]:
        raise validation_error([("options.app_version", "Cannot rollback to same version")])
    if target not in [v for v in app["_history"] if vkey(v) < vkey(app["version"])]:
        raise validation_error([("options.app_version", "Specified version is not available for rollback")])
    if app["state"] == "STOPPED":
        raise validation_error([("app_name", "App must not be in stopped state to rollback")])
    await job.progress(20, f"Completed validation for {app_name!r} app rollback to {target!r} version",
                       checkpoint=True)
    meta = app["metadata"]
    fresh = _app_entry(ctx.state, app_name, meta["train"], meta["name"], target, app["config"])
    fresh["_history"] = [v for v in app["_history"] if vkey(v) <= vkey(target)]
    ctx.state.apps[app_name] = fresh
    await ctx.server.emit("app.query", "changed", app_name, _public(fresh, False))
    await job.progress(100, f"Rollback completed for {app_name!r} app to {target!r} version")
    return _public(fresh, False)


@register("app.rollback", roles=APP_WRITE, job=True)
def app_rollback(ctx: Ctx, app_name: str, options: dict[str, Any] | None = None) -> Any:
    """Terug naar een eerder geïnstalleerde catalogusversie (géén image-tag-rollback bij ix-app) — job."""
    if not options or "app_version" not in options:
        raise validation_error([("options.app_version", "Field required")])
    return lambda job: _rollback_runner(ctx, job, app_name, options)


async def _upgrade_runner(ctx: Ctx, job: JobHandle, app_name: str, options: dict[str, Any]) -> dict[str, Any]:
    app = _get_app(ctx, app_name)
    if app["state"] == "STOPPED":
        raise call_error(EINVAL, "In order to upgrade an app, it must not be in stopped state")
    meta = app["metadata"]
    if meta["name"] == IX_APP:
        # echt gedrag: bij ix-app alleen images opnieuw ophalen en herstarten; geen versiewissel
        await job.progress(10, "Pulling app images", checkpoint=True)
        app["image_updates_available"] = False
        await job.progress(100, "App successfully upgraded and redeployed")
        return _public(app, False)
    if not app["upgrade_available"]:
        raise call_error(ENOENT, f"No upgrade available for {app_name!r}")
    versions = ctx.state.catalog[meta["train"]][meta["name"]]["versions"]
    target = str(options.get("app_version", "latest"))
    if target == "latest":
        ctx.state.violation("version_latest", f"{app_name}: upgrade naar 'latest'")
        target = latest(versions)
    if target not in versions or vkey(target) <= vkey(app["version"]):
        raise call_error(ENOENT, f"Unable to locate {target!r} version for {meta['name']!r} app")
    await job.progress(40, f"Configuration updated for {app_name!r}, upgrading app", checkpoint=True)
    values = copy.deepcopy(app["config"])
    values.update(copy.deepcopy(options.get("values") or {}))
    fresh = _app_entry(ctx.state, app_name, meta["train"], meta["name"], target, values)
    fresh["_history"] = [*app["_history"], target]
    ctx.state.apps[app_name] = fresh
    await ctx.server.emit("app.query", "changed", app_name, _public(fresh, False))
    await job.progress(100, "Upgraded app successfully")
    return _public(fresh, False)


@register("app.upgrade", roles=APP_WRITE, job=True)
def app_upgrade(ctx: Ctx, app_name: str, options: dict[str, Any] | None = None) -> Any:
    """Catalogusapp naar een (vaste) versie upgraden — job."""
    return lambda job: _upgrade_runner(ctx, job, app_name, options or {})


@register("app.image.query", roles=APP_READ, filterable=True)
async def app_image_query(ctx: Ctx, filters: list[Any] | None = None, options: dict[str, Any] | None = None) -> Any:
    """Images op de host met (gesimuleerde) repo_digests."""
    rows = []
    for app in ctx.state.apps.values():
        for image in app["active_workloads"]["images"]:
            name_tag, _, pinned = image.partition("@")
            repo = name_tag.rsplit(":", 1)[0] if ":" in name_tag.rsplit("/", 1)[-1] else name_tag
            # zoals Docker: een tag met @sha256:… trekt precies die digest; anders een (gesimuleerde) digest van de tag
            digest = pinned or "sha256:" + uuid.uuid5(uuid.NAMESPACE_URL, image).hex * 2
            if ctx.state.faults.get("digest_mismatch"):
                digest = "sha256:" + uuid.uuid5(uuid.NAMESPACE_URL, "anders:" + image).hex * 2
            rows.append({"id": digest, "repo_tags": [name_tag], "repo_digests": [f"{repo}@{digest}"],
                         "size": 100_000_000, "dangling": False, "update_available": False, "created": "",
                         "author": "", "comment": ""})
    return filter_list(rows, filters, options)


__all__ = ["EACCES", "EBUSY"]
