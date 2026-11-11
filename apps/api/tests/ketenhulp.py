"""Lijm voor de ketentest plan → Hub → executor → NAS (contract 1.3 §8) — alleen tests.

De keten bestaat uit vier delen die elk hun eigen werk doen:

- deze dienst maakt het plan (``/lifecycle/v1/plans/...``) en geeft stappen vrij op gemelde readback;
- de Hub-stand-in (``hub_p2``) meet de inventaris, classificeert, vraagt goedkeuring en ondertekent;
- de executor-stand-in (``executor_sim``) controleert het ondertekende verzoek opnieuw, voert uit en leest terug;
- de TrueNAS-simulator is de NAS (JSON-RPC over WebSocket).

De lijm hieronder doet alleen wat in het echt de Hub doet: de inventaris uit ``app.query`` halen, de ETag van het doel
uit een verse readback binden en het resultaat van de executor terugmelden bij het plan. Namen en paden zijn die van
de simulator (pool ``simpool``); de NAS-gegevens D01–D18 zijn onbekend. Sim-getest.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import planhulp as h
from executor_sim import ExecutorSim
from hub_p2 import app_record
from starlette.testclient import TestClient

from truenas_sim.server import SimServer, create_app

DATASET = "simpool/apps/gotify"
PAD = f"/mnt/{DATASET}"
SANDBOX = "simpool/asm-sandbox"
GOTIFY = {**h.GOTIFY, "data_path": PAD}
EIGENAAR = {"type": "user", "id": "eigenaar-1"}
READBACK_VELDEN = ("staat", "versie", "digests", "images", "etag")


class Rpc:
    """JSON-RPC 2.0 over de WebSocket van de TrueNAS-simulator; ``job`` wacht op de job (``core.job_wait``)."""

    def __init__(self, ws: Any) -> None:
        self.ws, self.ids = ws, itertools.count(1)

    def call(self, method: str, *params: Any) -> Any:
        ident = next(self.ids)
        self.ws.send_text(json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": list(params)}))
        while True:
            msg = json.loads(self.ws.receive_text())
            if msg.get("id") == ident:
                break
        if "error" in msg:
            raise RuntimeError(json.dumps(msg["error"]))
        return msg["result"]

    def job(self, method: str, *params: Any) -> Any:
        return self.call("core.job_wait", self.call(method, *params))


@contextmanager
def nas_simulator(versie: str = "25.10.8") -> Iterator[tuple[Rpc, TestClient]]:
    """Een verse TrueNAS-simulator; de RPC-sessie is ingelogd met de schrijfsleutel van de uitvoerder."""
    with TestClient(create_app(SimServer(versie))) as http:
        keys = http.get("/_sim/keys").json()
        with http.websocket_connect("/api/current") as ws:
            rpc = Rpc(ws)
            assert rpc.call("auth.login_with_api_key", keys["write"])
            yield rpc, http


def formulier(tag: str, *, host_port: int = 30215, pad: str = PAD) -> dict[str, Any]:
    """Waarden van het standaard Custom App-formulier voor Gotify (image, poort, opslag) — geen YAML, geen compose."""
    return {
        "image": {"repository": h.GOTIFY_REPO, "tag": tag, "pull_policy": "missing"}, "TZ": "Europe/Amsterdam",
        "envs": [], "restart_policy": "unless-stopped", "privileged": False, "capabilities": {"add": []},
        "host_network": False,
        "ports": [{"bind_mode": "published", "port_number": host_port, "container_port": 8080, "protocol": "tcp",
                   "host_ips": []}],
        "storage": [{"type": "host_path", "mount_path": "/app/data", "read_only": False,
                     "host_path_config": {"acl_enable": False, "path": pad}}],
    }


@dataclass
class Uitkomst:
    verzoek: Any  # hub_p2.Verzoek
    request: dict[str, Any]  # het ondertekende ExecutionRequest
    resultaat: dict[str, Any]  # antwoord van de executor
    plan: dict[str, Any]  # het plan na de melding


@dataclass
class Keten:
    client: Any  # TestClient van deze dienst
    auth: Any
    hub: Any  # de Hub-simulator (FastAPI-app; toestand in hub.state.sim)
    rpc: Rpc
    nas: TestClient  # testbediening van de TrueNAS-simulator (/_sim/...)
    executor: ExecutorSim
    platform_apps: set[str] = field(default_factory=lambda: {"gotify"})  # apps met een manifest in de app-store

    @property
    def p2(self) -> Any:
        return self.hub.state.sim.p2

    # ------------------------------------------------------------------ wat de Hub meet
    def meet(self) -> None:
        """Inventaris en catalogusbeschikbaarheid zoals de Hub ze meet: uit ``app.query`` en de catalogus."""
        datasets = {d["id"] for d in self.rpc.call("pool.dataset.query")}
        records = []
        for app in self.rpc.call("app.query"):
            wl = app["active_workloads"]
            naam_tag, _, digest = str(wl["images"][0]).partition("@")
            repo, _, tag = naam_tag.rpartition(":")
            poorten = [(int(p["container_port"]), int(hp["host_port"]), str(p["protocol"]))
                       for p in wl["used_ports"] for hp in p["host_ports"]]
            opslag = [(str(v["source"]), str(v["destination"]),
                       ds if (ds := str(v["source"]).removeprefix("/mnt/")) in datasets else None)
                      for v in wl["volumes"]]
            records.append(app_record(
                app["name"], repository=repo, tag=tag, digest=digest or None, catalogus_app=app["metadata"]["name"],
                trein=app["metadata"]["train"], versie=app["version"], staat=app["state"],
                app_id=app["name"] if app["name"] in self.platform_apps else None, poorten=poorten, opslag=opslag))
        self.p2.zet_apps(*records)
        for versie in self.rpc.call("catalog.get_app_details", "ix-app", {"train": "stable"})["versions"]:
            self.p2.zet_beschikbaar("stable", "ix-app", versie, "ja")

    def doel(self) -> dict[str, str]:
        """TrueNAS-versie en ``ix-app``-versie zoals de simulator ze meldt (geen voorbeeldwaarden)."""
        ix_app = self.rpc.call("catalog.get_app_details", "ix-app", {"train": "stable"})["latest_version"]
        return {"truenas_version": str(self.rpc.call("system.version_short")), "ix_app_version": str(ix_app)}

    # ------------------------------------------------------------------ plannen bij deze dienst
    def plan(self, soort: str, body: dict[str, Any]) -> dict[str, Any]:
        self.meet()
        r = self.client.post(f"/lifecycle/v1/plans/{soort}/preview", json=body,
                             headers=self.auth("operator", idem=True))
        assert r.status_code == 201, r.text
        view: dict[str, Any] = r.json()
        return view

    def body(self, naam: str = "gotify", parameters: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        return {"manifest_yaml": h.manifest(naam), "parameters": GOTIFY if parameters is None else parameters,
                "doel": self.doel(), **extra}

    def lees(self, plan_id: str) -> dict[str, Any]:
        view: dict[str, Any] = self.client.get(f"/lifecycle/v1/plans/{plan_id}", headers=self.auth("viewer")).json()
        return view

    # ------------------------------------------------------------------ één stap door de keten
    def voorstel(self, view: dict[str, Any], step_id: str) -> dict[str, Any]:
        """Het ``ActionProposal`` zoals het bij de Hub aankomt: het sjabloon uit het plan plus de actor; de ETag van
        een app bindt de Hub zelf uit een verse readback (nooit van de aanvrager)."""
        sjabloon = h.stap(view["plan"], step_id)["voorstel"]
        proposal = {k: v for k, v in sjabloon.items() if k != "expected_version_bron"}
        return dict(self.p2.vul_expected_version({**proposal, "actor": EIGENAAR}))

    def dien_in(self, view: dict[str, Any], step_id: str) -> Any:
        return self.p2.propose(self.voorstel(view, step_id))

    def keur_goed(self, view: dict[str, Any], step_id: str, verzoek: Any) -> dict[str, Any]:
        """De eigenaar keurt precies deze diff goed; het herstelpunt uit het plan gaat mee in de goedkeuring."""
        goedkeuring: dict[str, Any] = self.p2.approve(
            verzoek.request_id, backup_checkpoint_id=h.stap(view["plan"], step_id).get("herstelpunt"))
        return goedkeuring

    def meld(self, plan_id: str, step_id: str, resultaat: dict[str, Any]) -> Any:
        return self.client.post(
            f"/lifecycle/v1/plans/{plan_id}/steps/{step_id}/result", headers=self.auth("operator", idem=True),
            json={"status": resultaat["status"], "execution_id": resultaat["execution_id"],
                  "readback": {k: resultaat["readback"][k] for k in READBACK_VELDEN}, "reden": resultaat["reden"]})

    def voer_uit(self, view: dict[str, Any], step_id: str) -> Uitkomst:
        """Voorstel → (goedkeuring) → ondertekend verzoek → executor → readback → melding bij het plan."""
        verzoek = self.dien_in(view, step_id)
        if verzoek.status == "wacht_op_goedkeuring":
            self.keur_goed(view, step_id, verzoek)
        request = self.hub.state.sim.execution_request(verzoek.request_id)
        resultaat = self.executor.execute(request)
        gemeld = self.meld(view["id"], step_id, resultaat)
        assert gemeld.status_code == 200, gemeld.text
        return Uitkomst(verzoek, request, resultaat, gemeld.json())

    def loop(self, view: dict[str, Any], *, tot: str | None = None) -> dict[str, Any]:
        """Voert telkens de eerste vrijgegeven actiestap uit tot er geen meer is (of tot en met ``tot``)."""
        for _ in range(40):
            vrij = [s for s in view["vrijgegeven_stappen"] if h.stap(view["plan"], s)["soort"] == "actie"]
            if not vrij:
                break
            view = {**view, **self.voer_uit(view, vrij[0]).plan}
            if vrij[0] == tot:
                break
        return view

    # ------------------------------------------------------------------ wat de NAS-simulator laat zien
    def app(self, naam: str) -> dict[str, Any] | None:
        rijen = self.rpc.call("app.query", [["id", "=", naam]])
        return dict(rijen[0]) if rijen else None

    def inhoud(self) -> dict[str, Any]:
        inhoud: dict[str, Any] = self.nas.get("/_sim/inhoud").json()
        return inhoud

    def overtredingen(self) -> list[dict[str, Any]]:
        lijst: list[dict[str, Any]] = self.nas.get("/_sim/violations").json()
        return lijst

    def aanroepen(self) -> list[str]:
        return [str(c.get("method")) for c in self.nas.get("/_sim/calls").json()]


def bouw_keten(client: Any, auth: Any, hub: Any, rpc: Rpc, nas: TestClient) -> Keten:
    """Koppelt de delen: de executor pint de policy-sleutel van de Hub, de Hub leest ETags via de executor."""
    sim = hub.state.sim
    executor = ExecutorSim(nas=rpc, keys=[key.public_key() for _kid, key in sim.policy_keys], clock=sim.p2.clock)
    sim.p2.etag_lezer = executor.etag
    nas.post("/_sim/fixtures", json={"datasets": [DATASET, SANDBOX]})
    return Keten(client, auth, hub, rpc, nas, executor)


def gotify_draait(keten: Keten, *, tag: str = f"3.0.0@{h.OUDE_DIGEST}") -> None:
    """Een bestaande Gotify als Custom App op de simulator, met een (gesimuleerde) database in de dataset."""
    versie = keten.doel()["ix_app_version"]
    keten.nas.post("/_sim/fixtures", json={"apps": [{"app_name": "gotify", "version": versie,
                                                     "values": formulier(tag)}]})
    r = keten.nas.post("/_sim/inhoud", json={"dataset": DATASET,
                                             "inhoud": {"schema": "3.0.0", "berichten": 42, "beschadigd": False}})
    assert r.status_code == 200, r.text
