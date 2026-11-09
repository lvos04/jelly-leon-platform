"""WebSocket-server (JSON-RPC 2.0) op ``/api/current`` en ``/api/<api-versie>``, plus de test-beheer-API ``/_sim``.

Protocol (zie README): verzoek ``{"jsonrpc":"2.0","id":…,"method":…,"params":[…]}``; antwoord met ``result`` of
``error``; notificaties ``collection_update`` (``msg`` added/changed/removed) voor abonnementen via
``core.subscribe``. Job-methoden geven direct een job-ID; voortgang via ``core.get_jobs``/events, resultaat via
``core.job_wait``.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from truenas_sim import methods as _methods  # noqa: F401 - registreert alle methoden
from truenas_sim.rpc import (
    CALL_ERROR,
    EINVAL,
    ENOENT,
    ENOTAUTHENTICATED,
    INTERNAL_ERROR,
    INVALID_JSON,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    TOO_MANY_CALLS,
    Method,
    RpcError,
    call_error,
    methods_for,
    validation_error,
)
from truenas_sim.state import PROFILES, SimState, date, expand_roles, ms_now

MAX_CONCURRENT_CALLS = 20
SECRET_ARG_KEYS = {"values", "custom_compose_config", "custom_compose_config_string", "api_key", "password"}


class Reboot(Exception):
    """Gesimuleerde herstart tijdens een job."""


@dataclass(eq=False)
class Session:
    ws: WebSocket
    api_version: str
    family: str
    authenticated: bool = False
    username: str | None = None
    roles: set[str] = field(default_factory=set)
    key_id: int | None = None
    subscriptions: dict[str, str] = field(default_factory=dict)
    inflight: int = 0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def logout(self) -> None:
        self.authenticated, self.username, self.roles, self.key_id = False, None, set(), None

    async def send(self, payload: dict[str, Any]) -> None:
        async with self.send_lock:
            with contextlib.suppress(Exception):
                await self.ws.send_text(json.dumps(payload))


@dataclass
class Ctx:
    server: SimServer
    session: Session
    methods: dict[str, Method]

    @property
    def state(self) -> SimState:
        return self.server.state

    def job_access_error(self, job: dict[str, Any]) -> str | None:
        """Zoals middlewared ``Job.credential_access_error`` (TS-25.10.8 ``job.py``, ``utils/privilege.py``): een sessie
        zonder FULL_ADMIN ziet en beheert alleen haar eigen jobs. (De echte ``read_roles`` per job zijn niet
        nagebootst: de simulator is hier dus even streng of strenger.)"""
        return job_access_error(self.session, job)

    def login_with_key(self, raw: str, username: str | None = None) -> bool:
        try:
            key_id = int(raw.split("-", 1)[0])
        except ValueError:
            return False
        key = self.state.api_keys.get(key_id)
        if key is None or key.revoked or key.full != raw or (username and username != key.username):
            return False
        if self.state.faults.get("insecure_transport"):
            # echt gedrag (auth.py, NEP): API-key over onversleutelde verbinding → key wordt ingetrokken
            key.revoked = True
            self.state.violation("api_key_insecure_transport", f"key {key_id} gebruikt over onversleutelde verbinding")
            return False
        self.session.authenticated, self.session.username = True, key.username
        self.session.roles, self.session.key_id = set(key.roles), key.id
        return True


def job_access_error(session: Session, job: dict[str, Any]) -> str | None:
    if "FULL_ADMIN" in expand_roles(session.roles):
        return None
    owner = ((job.get("credentials") or {}).get("data") or {}).get("username")
    if owner is None:
        return "Only users with full administrative privileges can access internally ran jobs"
    if owner == session.username:
        return None
    return "Job is not owned by current session"


class JobHandle:
    def __init__(self, server: SimServer, job: dict[str, Any]) -> None:
        self.server, self.job = server, job

    async def progress(self, percent: int, description: str, checkpoint: bool = False) -> None:
        state = self.server.state
        delay = int(state.faults.get("slow", {}).get("delay_ms", 0)) if state.faults.get("slow") else 0
        if delay:
            await asyncio.sleep(delay / 1000)
        self.job["progress"] = {"percent": percent, "description": description, "extra": None}
        await self.server.emit("core.get_jobs", "changed", self.job["id"], copy.deepcopy(self.job))
        if not checkpoint:
            return
        method = self.job["method"]
        fail = state.faults.get("job_fail")
        if fail and fail.get("method") in (method, "*") and fail.get("count", 1) > 0:
            fail["count"] = fail.get("count", 1) - 1
            if fail["count"] <= 0:
                state.faults.pop("job_fail", None)
            raise call_error(5, str(fail.get("error", f"[EFAULT] gesimuleerde fout in {method}")))
        reboot = state.faults.get("reboot_during_apply")
        if reboot and reboot.get("method") in (method, "*"):
            state.faults.pop("reboot_during_apply", None)
            raise Reboot(int(reboot.get("downtime_ms", 500)))


class SimServer:
    def __init__(self, version: str | None = None) -> None:
        self.state = SimState.initial(version or os.environ.get("TRUENAS_SIM_VERSION", "25.10.8"))
        self.sessions: set[Session] = set()
        self.job_tasks: dict[int, asyncio.Task[None]] = {}
        self.job_done: dict[int, asyncio.Event] = {}
        self.unreachable_until: float | None = None

    # ------------------------------------------------------------------ helpers
    def reachable(self) -> bool:
        if self.state.faults.get("api_unreachable"):
            return False
        return not (self.unreachable_until and time.monotonic() < self.unreachable_until)

    async def emit(self, collection: str, msg: str, ident: Any, fields: dict[str, Any] | None) -> None:
        params: dict[str, Any] = {"msg": msg, "collection": collection, "id": ident}
        if fields is not None and msg in ("added", "changed"):
            params["fields"] = fields
        for session in list(self.sessions):
            if not any(s in (collection, "*") for s in session.subscriptions.values()):
                continue
            if collection == "core.get_jobs" and fields is not None and job_access_error(session, fields):
                continue  # jobs van een andere gebruiker zijn niet zichtbaar (ook niet als event)
            await session.send({"jsonrpc": "2.0", "method": "collection_update", "params": params})

    def new_job(self, method: str, params: tuple[Any, ...], runner: Callable[[JobHandle], Awaitable[Any]],
                session: Session) -> int:
        jid = self.state.next_job_id
        self.state.next_job_id += 1
        args = [{k: ("********" if k in SECRET_ARG_KEYS else v) for k, v in p.items()} if isinstance(p, dict) else p
                for p in params]
        job: dict[str, Any] = {
            "id": jid, "message_ids": [], "method": method, "arguments": args, "transient": False,
            "description": None, "abortable": False, "logs_path": None, "logs_excerpt": None,
            "progress": {"percent": 0, "description": "", "extra": None}, "result": None,
            "result_encoding_error": None, "error": None, "exception": None, "exc_info": None, "state": "WAITING",
            "time_started": None, "time_finished": None,
            "credentials": {"type": "API_KEY", "data": {"username": session.username}},
        }
        self.state.jobs[jid] = job
        self.job_done[jid] = asyncio.Event()
        self.job_tasks[jid] = asyncio.create_task(self._run_job(job, runner))
        return jid

    async def _run_job(self, job: dict[str, Any], runner: Callable[[JobHandle], Awaitable[Any]]) -> None:
        jid = job["id"]
        boot = self.state.boot_id
        await self.emit("core.get_jobs", "added", jid, copy.deepcopy(job))
        job["state"], job["time_started"] = "RUNNING", date()
        try:
            job["result"] = await runner(JobHandle(self, job))
            job["state"] = "SUCCESS"
            job["progress"]["percent"] = 100
        except Reboot as reboot:
            await self.reboot(int(reboot.args[0]) if reboot.args else 500)
            return
        except asyncio.CancelledError:
            job["state"], job["error"] = "ABORTED", "Job aborted"
        except RpcError as exc:
            job["state"] = "FAILED"
            data = exc.data or {}
            name = data.get("errname", "EFAULT")
            job["error"] = f"[{name}] {exc.reason}"
            job["exception"] = exc.reason
            job["exc_info"] = {"type": "VALIDATION" if exc.code == INVALID_PARAMS else "CallError",
                               "extra": data.get("extra"), "errno": data.get("error"), "repr": exc.reason}
        except Exception as exc:  # pragma: no cover - simulatorfout
            job["state"], job["error"] = "FAILED", f"[EFAULT] {exc!r}"
        if self.state.boot_id != boot:
            return
        job["time_finished"] = date()
        await self.emit("core.get_jobs", "changed", jid, copy.deepcopy(job))
        self.job_done[jid].set()

    async def wait_job(self, jid: int) -> Any:
        if jid not in self.state.jobs:
            raise call_error(ENOENT, f"Job {jid} not found")
        await self.job_done[jid].wait()
        job = self.state.jobs.get(jid)
        if job is None:
            raise call_error(ENOENT, f"Job {jid} not found (systeem herstart?)")
        if job["state"] != "SUCCESS":
            info = job.get("exc_info") or {}
            raise call_error(int(info.get("errno") or 5), job.get("error") or "job failed", info.get("extra"))
        return job["result"]

    def abort_job(self, jid: int) -> None:
        task = self.job_tasks.get(jid)
        if task is None or task.done():
            raise call_error(EINVAL, f"Job {jid} is niet actief")
        task.cancel()

    async def reboot(self, downtime_ms: int = 500) -> None:
        """Gesimuleerde herstart: alle verbindingen dicht, lopende jobs weg, nieuw boot_id, even onbereikbaar."""
        self.state.boot_id = str(uuid.uuid4())
        self.state.boot_ms = ms_now()
        self.state.jobs.clear()
        for jid, task in list(self.job_tasks.items()):
            if task is not asyncio.current_task():
                task.cancel()
            self.job_done.get(jid, asyncio.Event()).set()
        self.job_tasks.clear()
        self.unreachable_until = time.monotonic() + downtime_ms / 1000
        for app in self.state.apps.values():
            if app["state"] == "DEPLOYING":
                app["state"] = "CRASHED"
        for session in list(self.sessions):
            with contextlib.suppress(Exception):
                await session.ws.close(code=1012, reason="system reboot (sim)")
        self.sessions.clear()

    # ------------------------------------------------------------------ dispatch
    async def handle(self, session: Session, raw: str) -> None:
        try:
            message = json.loads(raw)
        except ValueError:
            await session.send({"jsonrpc": "2.0", "error": {"code": INVALID_JSON, "message": "Parse error"},
                                "id": None})
            return
        has_id = isinstance(message, dict) and "id" in message
        ident = message.get("id") if isinstance(message, dict) else None
        try:
            if not isinstance(message, dict):
                raise RpcError(INVALID_REQUEST, "Request must be an object")
            if message.get("jsonrpc") != "2.0":
                raise RpcError(INVALID_REQUEST, "'jsonrpc' member must be of type string and must be exactly '2.0'")
            if not isinstance(message.get("method"), str) or not message["method"]:
                raise RpcError(INVALID_REQUEST, "Missing 'method' member")
            params = message.get("params", [])
            if not isinstance(params, list):
                raise RpcError(INVALID_REQUEST, "'params' member must be of type array")
            if session.inflight >= MAX_CONCURRENT_CALLS:
                raise RpcError(TOO_MANY_CALLS, f"Maximum number of concurrent calls ({MAX_CONCURRENT_CALLS}) has "
                                               "exceeded")
            session.inflight += 1
            try:
                result = await self.call(session, message["method"], params)
            finally:
                session.inflight -= 1
            if has_id:
                delay = int(self.state.faults.get("slow", {}).get("delay_ms", 0)) if self.state.faults.get(
                    "slow") else 0
                if delay:
                    await asyncio.sleep(delay / 1000)
                await session.send({"jsonrpc": "2.0", "result": result, "id": ident})
        except RpcError as exc:
            if has_id:
                await session.send({"jsonrpc": "2.0", "error": exc.to_json(), "id": ident})
        except Exception as exc:  # pragma: no cover
            if has_id:
                await session.send({"jsonrpc": "2.0", "error": {"code": INTERNAL_ERROR, "message": repr(exc)},
                                    "id": ident})

    async def call(self, session: Session, name: str, params: list[Any]) -> Any:
        available = methods_for(session.family)
        method = available.get(name)
        if method is None:
            raise RpcError(METHOD_NOT_FOUND, "Method does not exist")
        key = self.state.api_keys.get(session.key_id) if session.key_id else None
        if key is not None and key.revoked:
            session.logout()
        self.state.calls.append({"method": name, "user": session.username, "at_ms": ms_now(),
                                 "write": bool(method.roles and not all(r.endswith("_READ") for r in method.roles))})
        if not method.no_auth and not session.authenticated:
            raise call_error(ENOTAUTHENTICATED, "Not authenticated")
        if method.roles and not (expand_roles(session.roles) & method.roles):
            raise call_error(13, "Not authorized")
        ctx = Ctx(self, session, available)
        try:
            if method.job:
                if method.pre is not None:
                    method.pre(ctx, *params)
                runner = method.fn(ctx, *params)
                return self.new_job(name, tuple(params), runner, session)
            return await method.fn(ctx, *params)
        except TypeError as exc:
            raise validation_error([(name, f"Ongeldige parameters: {exc}")]) from exc


def create_app(server: SimServer | None = None) -> FastAPI:
    sim = server or SimServer()
    app = FastAPI(title="TrueNAS-simulator", docs_url=None, redoc_url=None)
    app.state.sim = sim

    def versions() -> list[str]:
        return list(PROFILES[sim.state.version]["api_versions"])

    @app.get("/api/versions")
    def api_versions() -> JSONResponse:
        if not sim.reachable():
            return JSONResponse({"error": "unreachable (sim)"}, status_code=503)
        return JSONResponse(versions())

    @app.get("/api/boot_id")
    def boot_id() -> JSONResponse:
        if not sim.reachable():
            return JSONResponse({"error": "unreachable (sim)"}, status_code=503)
        return JSONResponse(sim.state.boot_id)

    @app.websocket("/api/{api_version}")
    async def rpc(ws: WebSocket, api_version: str) -> None:
        all_versions = versions()
        if api_version == "current":
            api_version = all_versions[-1]
        if api_version not in all_versions or not sim.reachable():
            await ws.close(code=1013 if not sim.reachable() else 1008)
            return
        await ws.accept()
        family = api_version[1:6]
        session = Session(ws=ws, api_version=api_version, family=family)
        sim.sessions.add(session)
        tasks: set[asyncio.Task[None]] = set()
        try:
            while True:
                raw = await ws.receive_text()
                task = asyncio.create_task(sim.handle(session, raw))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            sim.sessions.discard(session)
            for task in tasks:
                task.cancel()

    # ------------------------------------------------------------------ test-beheer (alleen simulator)
    @app.get("/_sim/state")
    def sim_state() -> dict[str, Any]:
        s = sim.state
        return {"version": s.version, "family": s.family, "api_versions": versions(), "boot_id": s.boot_id,
                "pools": {p["name"]: p["status"] for p in s.pools},
                "apps": {n: {"state": a["state"], "version": a["version"], "catalog_app": a["metadata"]["name"],
                             "image": a["active_workloads"]["images"][0], "config": a["config"]}
                         for n, a in s.apps.items()},
                "datasets": sorted(s.datasets), "snapshots": sorted(s.snapshots), "faults": s.faults,
                "jobs": len(s.jobs), "preferred_trains": s.preferred_trains,
                "keys": {k.id: {"username": k.username, "revoked": k.revoked} for k in s.api_keys.values()}}

    @app.get("/_sim/keys")
    def sim_keys() -> dict[str, str]:
        keys = {k.username: k.full for k in sim.state.api_keys.values()}
        return {"readonly": keys["hub-readonly"], "write": keys["executor"]}

    @app.get("/_sim/violations")
    def sim_violations() -> list[dict[str, Any]]:
        return sim.state.violations

    @app.get("/_sim/calls")
    def sim_calls() -> list[dict[str, Any]]:
        return sim.state.calls

    @app.post("/_sim/reset")
    async def sim_reset(request: Request) -> dict[str, Any]:
        body = await _json(request)
        version = str(body.get("version", sim.state.version))
        if version not in PROFILES:
            raise HTTPException(400, f"onbekende versie; kies uit {sorted(PROFILES)}")
        await sim.reboot(0)
        sim.state = SimState.initial(version)
        sim.unreachable_until = None
        return sim_state()

    @app.post("/_sim/reboot")
    async def sim_reboot(request: Request) -> dict[str, Any]:
        body = await _json(request)
        await sim.reboot(int(body.get("downtime_ms", 500)))
        return {"boot_id": sim.state.boot_id}

    fault_names = {"pool_degraded", "api_unreachable", "key_revoked", "slow", "job_fail", "reboot_during_apply",
                   "smart_fail", "insecure_transport"}

    @app.post("/_sim/faults/{name}")
    async def set_fault(name: str, request: Request) -> dict[str, Any]:
        if name not in fault_names:
            raise HTTPException(404, f"onbekende storing; kies uit {sorted(fault_names)}")
        body = await _json(request)
        s = sim.state
        if name == "pool_degraded":
            pool = next((p for p in s.pools if p["name"] == body.get("pool", "simpool")), None)
            if pool is None:
                raise HTTPException(404, "pool onbekend")
            status = str(body.get("status", "DEGRADED"))
            pool.update({"status": status, "healthy": status == "ONLINE", "warning": status != "ONLINE",
                         "status_code": "OK" if status == "ONLINE" else "UNHEALTHY",
                         "status_detail": None if status == "ONLINE" else "One or more devices are faulted (sim)."})
            pool["topology"]["data"][0]["status"] = status
        elif name == "key_revoked":
            key = s.api_keys.get(int(body.get("key_id", 1)))
            if key is None:
                raise HTTPException(404, "key onbekend")
            key.revoked = True
        elif name == "smart_fail":
            disk = str(body.get("disk", "sda"))
            s.smart_results[disk] = [{"num": 1, "description": "Short offline", "status": "FAILED",
                                      "status_verbose": "Completed: read failure (sim)", "remaining": 0,
                                      "lifetime": 12001, "lba_of_first_error": 123456}]
        elif name == "api_unreachable":
            s.faults[name] = True
            for session in list(sim.sessions):
                with contextlib.suppress(Exception):
                    await session.ws.close(code=1013, reason="unreachable (sim)")
            sim.sessions.clear()
        else:
            s.faults[name] = body or True
        if name in ("pool_degraded", "key_revoked", "smart_fail"):
            s.faults[name] = body or True
        return {"faults": s.faults}

    @app.delete("/_sim/faults/{name}")
    def clear_fault(name: str) -> dict[str, Any]:
        s = sim.state
        s.faults.pop(name, None)
        if name == "pool_degraded":
            for pool in s.pools:
                pool.update({"status": "ONLINE", "healthy": True, "warning": False, "status_code": "OK",
                             "status_detail": None})
                pool["topology"]["data"][0]["status"] = "ONLINE"
        return {"faults": s.faults}

    @app.post("/_sim/fixtures")
    async def fixtures(request: Request) -> dict[str, Any]:
        body = await _json(request)
        s = sim.state
        for key in ("cloudsync_tasks", "replication_tasks", "snapshot_tasks", "extra_alerts"):
            if key in body:
                setattr(s, key, list(body[key]))
        if "gpus" in body:
            s.gpus = dict(body["gpus"])
        for preinstalled in body.get("apps", []):
            from truenas_sim.methods import _app_entry

            entry = _app_entry(preinstalled["app_name"], preinstalled.get("train", "stable"),
                               preinstalled.get("catalog_app", "ix-app"), preinstalled["version"],
                               preinstalled.get("values", {}), state=preinstalled.get("state", "RUNNING"))
            entry["_history"] = list(preinstalled.get("history", [preinstalled["version"]]))
            s.apps[preinstalled["app_name"]] = entry
        for name in body.get("datasets", []):
            s.add_dataset(str(name))
        return sim_state()

    return app


async def _json(request: Request) -> dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


__all__ = ["CALL_ERROR", "SimServer", "create_app"]
