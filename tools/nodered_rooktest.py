"""Rooktest van Node-RED (I10-10-01…06) als standaard Custom App, tegen de Hub-simulator.

Start de gepinde image **op digest** zoals ``install/manifests/node-red.manifest.yaml`` hem beschrijft: één
``docker run`` als 568:568 met een lege eigen map op ``/data``, poort 1880 en de omgevingsvariabele ``FLOWS`` uit het
manifest; zonder extra rechten, zonder Docker-socket. De Hub is de simulator uit ``e2e/simulators/hub`` (in dit
proces, luisterend op het adres van de Docker-bridge zodat de container hem kan bereiken).

Stappen:

- N1  container en beveiliging zoals het manifest ze levert: geen anonieme toegang (besluit C-11: anoniem
      ``GET /flows``, ``/nodes``, ``/settings`` en ``/diagnostics`` geven 401, ``POST /flows`` ook; exporteren zonder
      aanmelding wordt geweigerd);
- N2  controleproef: dezelfde image **zonder** ``FLOWS`` is onbeveiligd, en de monitor uit het manifest ziet dat;
- N3  beheerdersmodus met een wegwerpaccount (dezelfde ``FLOWS``, alleen met één gebruiker): alleen kern-nodes geladen
      (``GET /nodes`` kan na C-11 alleen aangemeld); een exec-/function-node voert niets uit; geen extra nodes te
      installeren;
- N4  flows importeren: token alleen als versleuteld credential, checksums gelijk aan het lock en de teruggelezen
      tabbladen door de eigen controle (``eigen_controle``), export (als beheerder) gelijk;
- N5  MediaGuard gestopt → event ``flow.app_watch.down``; weer gestart → ``flow.app_watch.recovered``;
- N6  testflow "herstart": staat standaard uit; ingeschakeld vraagt hij bij de Hub aan en beslist de Hub (R1/R2);
- N7  hartslag met flowvertraging komt bij de Hub aan;
- N8  een andere flowversie zetten en de versie uit Git herstellen (checksum);
- N9  herstart zonder verlies; stoppen is zichtbaar (monitor faalt, heartbeat verouderd → ``unknown``).

Wat dit **niet** is: een installatie op TrueNAS, de echte Hub of de echte uitvoerder. Het beheerdersaccount van N3–N9
kan het manifest (nog) niet leveren; de rooktest zet het met een wegwerpwaarde in ``FLOWS``. Uitkomst: **Sim-getest;
NIET GEVALIDEERD (echte NAS)**. Het rapport bevat geen tokens, wachtwoorden of hashes.

Gebruik::

    python tools/nodered_rooktest.py [--rapport nodered-rooktest.json] [--houd]
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import re
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import nodered_flows as nf
import uvicorn
from upstream_rooktest import ROOT, App, Mislukt, UpstreamRooktest, eis, lees_manifest, sh, vrije_poort, wacht

sys.path.insert(0, str(ROOT / "e2e" / "simulators" / "hub"))
import contract8 as c8
import hub_sim
from hub_p2 import P2Error, app_record

APP_ID = "node-red"
SCOPES = ["inventaris.lezen", "acties.voorstellen"]
GEVAARLIJKE_TYPES = {"exec", "function", "file", "file in", "watch", "tcp in", "tcp out", "tcp request", "udp in",
                     "udp out", "http in", "http response", "websocket in", "websocket out", "mqtt in", "mqtt out"}
GEHEIM_RE = re.compile(r"(svc_[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}|\$2[aby]\$\d\d\$)")
# Besluit C-11: zonder aanmelding geen flows, nodes of instellingen (dezelfde paden als de controle anoniem_geweigerd
# van home-control-hub).
ANONIEME_PADEN = ("/flows", "/nodes", "/settings")


class HubSim:
    """De Hub-simulator in dit proces, bereikbaar voor de container via het adres van de Docker-bridge."""

    def __init__(self, host: str) -> None:
        self.state = hub_sim.HubState()
        with socket.socket() as s:
            s.bind((host, 0))
            self.poort = int(s.getsockname()[1])
        self.url = f"http://{host}:{self.poort}"
        self.server = uvicorn.Server(uvicorn.Config(hub_sim.create_hub_app(self.state), host=host, port=self.poort,
                                                    log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        p2 = self.state.p2

        def etag(target: Any) -> str:
            """Stand-in voor de readback die de echte Hub via de uitvoerder doet: de ETag van het inventarisrecord."""
            record = p2.apps.get(str(target.get("app_id")))
            if record is None:
                raise P2Error(404, "not_found", "Doel-app staat niet in de inventaris.")
            return c8.sha({"app": record})

        p2.etag_lezer = etag

    def start(self) -> None:
        self.thread.start()
        wacht("Hub-simulator luistert", lambda: bool(self.server.started), 20, 0.1)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(10)

    def zet_apps(self, mediaguard: str | None) -> None:
        records = [app_record("node-red", app_id="node-red"), app_record("authentik", app_id="authentik",
                                                                         catalogus_app="authentik", trein="community")]
        if mediaguard is not None:
            records.append(app_record("mediaguard", app_id="mediaguard", staat=mediaguard))
        self.state.p2.zet_apps(*records)

    def events(self) -> list[tuple[str, str]]:
        with self.state.lock:
            return [(str(e["event_type"]), str(e["entity"]["id"])) for e in self.state.events]


class NoderedRooktest(UpstreamRooktest):
    def __init__(self, houd: bool) -> None:
        super().__init__(houd)
        self.app = App.uit_manifest(APP_ID)
        self.helper = self.app.image  # Alpine met sh/chown: maakt de mappen van 568 en ruimt ze op
        self.manifest_flows = self.app.env["FLOWS"]
        self.gebruiker = "rooktest-" + secrets.token_hex(3)
        self.wachtwoord = secrets.token_urlsafe(18)
        self.bridge = sh("docker", "network", "inspect", "bridge", "--format",
                         "{{(index .IPAM.Config 0).Gateway}}").strip()
        self.hub = HubSim(self.bridge)
        self.token = ""
        self.poort = 0
        self.cname = ""
        self.nr: nf.NodeRed | None = None

    # ------------------------------------------------------------------ hulpmiddelen
    def variant(self, naam: str, flows: str) -> App:
        return dataclasses.replace(self.app, app_id=f"{APP_ID}-{naam}", env={**self.app.env, "FLOWS": flows})

    def klaar(self, poort: int) -> bool:
        return self.get(f"http://127.0.0.1:{poort}/auth/login").status_code == 200

    def status(self, poort: int, pad: str, methode: str = "GET", **kw: Any) -> int:
        return self.http.request(methode, f"http://127.0.0.1:{poort}{pad}", **kw).status_code

    def node_types(self, poort: int, kop: dict[str, str] | None = None) -> set[str]:
        """De geladen nodetypes (``GET /nodes``). Met FLOWS kan dat na besluit C-11 alleen aangemeld (``kop``)."""
        r = self.get(f"http://127.0.0.1:{poort}/nodes", headers={**(kop or {}), "Accept": "application/json"})
        eis(r.status_code == 200, f"GET /nodes: HTTP {r.status_code}")
        return {str(t) for s in r.json() for t in s.get("types", [])}

    def monitor_ok(self, poort: int) -> bool:
        """De monitor uit het manifest: precies de verwachte status op het monitorpad."""
        mon = lees_manifest(APP_ID)["healthcheck"]["monitor"]
        try:
            return self.status(poort, str(mon["pad"])) == int(mon["verwachte_status"])
        except httpx.HTTPError:
            return False

    def beheer(self) -> nf.NodeRed:
        eis(self.nr is not None, "geen beheerderssessie (stap N3 is niet geslaagd)")
        assert self.nr is not None
        return self.nr

    def tik(self, node_id: str, props: list[dict[str, Any]] | None = None) -> None:
        code = self.beheer().inject(node_id, props)
        eis(code == 200, f"inject {node_id}: HTTP {code}")

    # ------------------------------------------------------------------ stappen
    def n1_manifest(self) -> None:
        with self.stap("N1", "Container en beveiliging zoals het manifest ze levert (I10-10-01, -02)") as s:
            s.details.update(self.digest_readback(self.app))
            poort = vrije_poort()
            cname = self.start(self.app, self.lege_map("manifest"), poort)
            wacht("Node-RED antwoordt", lambda: self.klaar(poort), 180)
            s.details["uids"] = self.alleen_568(cname)
            cfg = json.loads(sh("docker", "inspect", cname))[0]
            mounts = sorted(m["Destination"] for m in cfg["Mounts"])
            eis(mounts == ["/data"], f"onverwachte mounts: {mounts}")
            eis(cfg["HostConfig"]["Privileged"] is False and not cfg["HostConfig"]["CapAdd"], "extra rechten gezet")
            eis("docker.sock" not in json.dumps(cfg["Mounts"]), "Docker-socket gekoppeld")
            login = self.get(f"http://127.0.0.1:{poort}/auth/login").json()
            eis(login.get("type") == "credentials", f"/auth/login meldt geen login: {login}")
            mon = lees_manifest(APP_ID)["healthcheck"]["monitor"]
            proeven = {
                f"GET {mon['pad']} (monitor: {mon['verwachte_status']})": self.status(poort, str(mon["pad"])),
                **{f"GET {pad} anoniem (401)": self.status(poort, pad) for pad in ANONIEME_PADEN},
                "POST /flows anoniem (401)": self.status(poort, "/flows", "POST", json=[]),
                "POST /nodes anoniem (geen 200)": self.status(poort, "/nodes", "POST", json={"module": "x"}),
                "POST /auth/token zonder beheerder (geen 200)": self.status(
                    poort, "/auth/token", "POST", data={"client_id": "node-red-admin", "grant_type": "password",
                                                        "scope": "*", "username": "admin", "password": "admin"}),
            }
            s.details["proeven"] = proeven
            eis(self.monitor_ok(poort), f"monitor klopt niet: {mon['pad']} gaf niet {mon['verwachte_status']}")
            eis(all(proeven[f"GET {pad} anoniem (401)"] == 401 for pad in ANONIEME_PADEN),
                "een anonieme lezer krijgt toegang (besluit C-11)")
            eis(proeven["POST /flows anoniem (401)"] == 401, "anoniem schrijven is niet geweigerd")
            eis(proeven["POST /nodes anoniem (geen 200)"] not in (200, 201), "anoniem nodes installeren kan")
            eis(proeven["POST /auth/token zonder beheerder (geen 200)"] != 200, "er bestaat een standaardaccount")
            s.details["anoniem_geweigerd"] = all(proeven[f"GET {pad} anoniem (401)"] == 401 for pad in ANONIEME_PADEN)
            try:
                nf.exporteer(nf.NodeRed(f"http://127.0.0.1:{poort}", self.http), self.werk / "export-anoniem")
                raise Mislukt("exporteren zonder aanmelding lukt")
            except nf.Fout as exc:
                eis("HTTP 401" in str(exc), f"onverwachte melding: {exc}")
                s.details["export_zonder_aanmelding"] = "geweigerd (HTTP 401)"
            eis(not (self.werk / "export-anoniem").exists(), "export zonder aanmelding schreef bestanden")
            sh("docker", "rm", "-f", cname)

    def n2_controle(self) -> None:
        with self.stap("N2", "Controleproef: zonder FLOWS is Node-RED onbeveiligd en de monitor ziet dat") as s:
            poort = vrije_poort()
            cname = self.start(self.variant("controle", "flows.json"), self.lege_map("controle"), poort)
            wacht("Node-RED (controle) antwoordt", lambda: self.klaar(poort), 180)
            types = self.node_types(poort)
            s.details.update({
                "GET /diagnostics": self.status(poort, "/diagnostics"),
                **{f"GET {pad} anoniem": self.status(poort, pad) for pad in ANONIEME_PADEN},
                "/auth/login": self.get(f"http://127.0.0.1:{poort}/auth/login").json(),
                "gevaarlijke_nodes_geladen": sorted(types & GEVAARLIJKE_TYPES),
                "monitor_slaagt": self.monitor_ok(poort),
            })
            eis(s.details["/auth/login"] == {}, "controleproef heeft onverwacht een login")
            eis({"exec", "function"} <= types, "controleproef laadt exec/function niet (proef zegt dan niets)")
            eis(s.details["monitor_slaagt"] is False, "de monitor slaagt op een onbeveiligde Node-RED")
            sh("docker", "rm", "-f", cname)

    def n3_beheer(self) -> None:
        with self.stap("N3", "Beheerdersmodus (wegwerpaccount); exec/function voeren niets uit (I10-10-02)") as s:
            hash_ = sh("docker", "run", "--rm", "--network", "none", "--entrypoint", "node", self.app.image, "-e",
                       "console.log(require('bcryptjs').hashSync(process.argv[1], 8))", self.wachtwoord).strip()
            leeg = '"users":[]'
            eis(self.manifest_flows.count(leeg) == 1, "FLOWS uit het manifest heeft geen lege gebruikerslijst")
            gebruiker = json.dumps({"username": self.gebruiker, "password": hash_, "permissions": "*"},
                                   separators=(",", ":"))
            self.poort = vrije_poort()
            self.cname = self.start(self.variant("beheer", self.manifest_flows.replace(leeg, f'"users":[{gebruiker}]')),
                                    self.lege_map("beheer"), self.poort)
            wacht("Node-RED (beheer) antwoordt", lambda: self.klaar(self.poort), 180)
            eis(self.monitor_ok(self.poort), "monitor klopt niet in beheerdersmodus")
            fout = nf.NodeRed(f"http://127.0.0.1:{self.poort}", self.http)
            try:
                fout.login(self.gebruiker, "fout-" + self.wachtwoord)
                raise Mislukt("inloggen met een fout wachtwoord lukt")
            except nf.Fout:
                s.details["fout_wachtwoord"] = "geweigerd"
            nr = nf.NodeRed(f"http://127.0.0.1:{self.poort}", self.http)
            nr.login(self.gebruiker, self.wachtwoord)
            s.details["POST /flows anoniem"] = self.status(self.poort, "/flows", "POST", json=[])
            eis(s.details["POST /flows anoniem"] == 401, "anoniem schrijven is niet geweigerd")
            s.details["GET /flows anoniem"] = self.status(self.poort, "/flows")
            eis(s.details["GET /flows anoniem"] == 401, "een anonieme lezer krijgt flows (besluit C-11)")
            types = self.node_types(self.poort, nr.kop)
            s.details["node_types"] = sorted(types)
            eis(not types & GEVAARLIJKE_TYPES, f"gevaarlijke nodes geladen: {sorted(types & GEVAARLIJKE_TYPES)}")
            eis({"inject", "change", "switch", "http request", "debug", "catch"} <= types, "kern-nodes ontbreken")
            installeren = self.http.post(f"{nr.url}/nodes", headers=nr.kop, json={"module": "node-red-contrib-x"})
            s.details["POST /nodes als beheerder"] = installeren.status_code
            eis(installeren.status_code not in (200, 201), "een beheerder kan extra nodes installeren")
            # Buiten de eigen controle om (rauwe Admin-API) een flow met exec en function zetten: niets mag draaien.
            z = "jlp-proef"
            kwaad: list[dict[str, Any]] = [
                {"id": z, "type": "tab", "label": "proef", "disabled": False, "info": ""},
                {"id": "jlp-proef-tik", "type": "inject", "z": z, "props": [{"p": "payload"}], "repeat": "",
                 "once": True, "onceDelay": "0.5", "payload": "", "payloadType": "date", "wires": [
                     ["jlp-proef-exec", "jlp-proef-fn"]]},
                {"id": "jlp-proef-exec", "type": "exec", "z": z, "command": "touch /data/exec-liep", "addpay": "",
                 "append": "", "useSpawn": "false", "wires": [[], [], []]},
                {"id": "jlp-proef-fn", "type": "function", "z": z, "func":
                 "require('fs').writeFileSync('/data/function-liep','x'); return msg;", "outputs": 1, "wires": [[]]},
            ]
            rev, flows = nr.flows()
            nr.zet_flows(rev, [*flows, *kwaad])
            time.sleep(6)
            bestanden = self.helper_sh("ls -A /w/beheer").split()
            s.details["bestanden_na_proef"] = sorted(bestanden)
            eis("exec-liep" not in bestanden and "function-liep" not in bestanden, "een exec/function-node liep")
            eis(not self.node_types(self.poort, nr.kop) & GEVAARLIJKE_TYPES, "gevaarlijke nodes geladen")
            s.details["eigen_controle"] = nf.lint(kwaad)[:3]
            eis(any(b.startswith("nodetype_niet_toegestaan") for b in nf.lint(kwaad)), "lint laat exec door")
            rev, flows = nr.flows()
            nr.zet_flows(rev, [n for n in flows if n.get("id") != z and n.get("z") != z])
            self.nr = nr

    def n4_import(self) -> None:
        with self.stap("N4", "Flows importeren: token versleuteld, checksums gelijk aan het lock (I10-10-03)") as s:
            self.hub.start()
            _sid, self.token = self.hub.state.register_service(APP_ID)
            self.hub.state.p2.scopes[APP_ID] = set(SCOPES)
            self.hub.zet_apps("RUNNING")
            bereik = sh("docker", "exec", self.cname, "node", "-e",
                        "fetch(process.argv[1]+'/api/v1/auth/jwks').then(r=>console.log(r.status))", self.hub.url)
            eis(bereik.strip() == "200", f"de container bereikt de Hub-simulator niet: {bereik.strip()}")
            bundels = nf.bundels_in_git()
            lock = nf.lees_lock()["bundels"]
            uit = nf.importeer(self.beheer(), bundels, hub_url=self.hub.url, token=self.token)
            s.details["readback"] = uit
            eis(all(v["gelijk"] and v["gelezen"] == lock[n]["sha256"] for n, v in uit.items()),
                "teruggelezen checksums wijken af van het lock")
            eis(uit["testflow-herstart"]["ingeschakeld"] is False, "de testflow staat niet standaard uit")
            schijf = self.helper_sh(f"cat /w/beheer/flows.json | grep -c '{self.token}'; "
                                    f"cat /w/beheer/flows_cred.json | grep -c '{self.token}'; "
                                    "ls -A /w/beheer; true")
            regels = schijf.split()
            s.details["token_in_flows_json"], s.details["token_in_flows_cred_json"] = int(regels[0]), int(regels[1])
            eis(regels[0] == "0" and regels[1] == "0", "het token staat leesbaar op schijf")
            eis("flows_cred.json" in regels and ".config.runtime.json" in regels, "credentialsbestand ontbreekt")
            export = nf.exporteer(self.beheer(), self.werk / "export")
            s.details["export"] = export
            eis(all(export[n]["vergelijking"] == "gelijk_aan_lock" for n in bundels), "export wijkt af van het lock")
            eis(self.token not in "".join(p.read_text(encoding="utf-8") for p in (self.werk / "export").iterdir()),
                "het token staat in de export")

    def n5_mediaguard(self) -> None:
        with self.stap("N5", "MediaGuard gestopt → melding; hersteld → melding; onbekend → niets (I10-10-03)") as s:
            p2 = self.hub.state.p2

            def meet(verwacht: int) -> None:
                self.tik("jlp-mg-tik")
                time.sleep(2.5)
                eis(len(self.hub.events()) == verwacht, f"verwacht {verwacht} events, gezien {self.hub.events()}")

            wacht("eerste meting na de import", lambda: self.hub.state.calls["apps"] >= 1, 60)
            meet(0)  # draait: geen event
            self.hub.zet_apps("STOPPED")
            meet(1)
            meet(1)  # nog steeds gestopt: geen tweede event
            p2.inventaris_storing = (503, "service_unavailable", None)
            meet(1)  # Hub onbereikbaar: unknown, geen event en geen 'hersteld'
            p2.inventaris_storing = None
            self.hub.zet_apps(None)
            meet(1)  # app niet in de lijst: unknown, geen event
            self.hub.zet_apps("DEPLOYING")
            meet(1)  # overgangstoestand: unknown
            self.hub.zet_apps("RUNNING")
            meet(2)
            self.hub.zet_apps("CRASHED")
            meet(3)
            self.hub.zet_apps("RUNNING")
            meet(4)
            s.details["events"] = self.hub.events()
            eis(self.hub.events() == [("flow.app_watch.down", "mediaguard"), ("flow.app_watch.recovered", "mediaguard"),
                                      ("flow.app_watch.down", "mediaguard"),
                                      ("flow.app_watch.recovered", "mediaguard")], "onverwachte events")
            with self.hub.state.lock:
                eerste = dict(self.hub.state.events[0])
            eis(eerste["source_app"] == APP_ID and eerste["actor"] == {"type": "flow", "id": "mediaguard-offline"},
                "bron of actor klopt niet")
            s.details["voorbeeld_event"] = {k: eerste[k] for k in ("event_type", "source_app", "entity", "actor",
                                                                   "classification", "payload")}

    def n6_testflow(self) -> None:
        with self.stap("N6", "Testflow herstart: standaard uit; de Hub beslist R1/R2 (I10-10-04)") as s:
            p2 = self.hub.state.p2
            s.details["inject_op_uitgeschakelde_flow"] = self.beheer().inject("jlp-th-start")
            eis(s.details["inject_op_uitgeschakelde_flow"] != 200 and not p2.verzoeken,
                "de uitgeschakelde testflow reageert")
            bundel = {"testflow-herstart": nf.bundels_in_git()["testflow-herstart"]}
            uit = nf.importeer(self.beheer(), bundel, hub_url=self.hub.url, token=self.token,
                               inschakelen=frozenset({"testflow-herstart"}))
            eis(uit["testflow-herstart"]["gelijk"] and uit["testflow-herstart"]["ingeschakeld"], "inschakelen faalt")
            self.tik("jlp-th-start")
            wacht("voorstel voor node-red", lambda: len(p2.verzoeken) == 1, 20, 0.5)
            self.tik("jlp-th-start", [{"p": "payload", "v": '{"doel_app":"authentik"}', "vt": "json"}])
            wacht("voorstel voor authentik", lambda: len(p2.verzoeken) == 2, 20, 0.5)
            per_doel = {str(v.proposal["target"]["app_id"]): v for v in p2.verzoeken.values()}
            s.details["besluiten"] = {doel: {"status": v.status, "risk_class": v.risk_class,
                                             "actor": v.proposal["actor"], "action_id": v.proposal["action_id"]}
                                      for doel, v in per_doel.items()}
            eis(per_doel["node-red"].status == "toegestaan" and per_doel["node-red"].risk_class == "R1",
                "herstart van een gewone app is niet R1/toegestaan")
            eis(per_doel["authentik"].status == "wacht_op_goedkeuring" and per_doel["authentik"].risk_class == "R2",
                "herstart van een kritieke app wacht niet op goedkeuring")
            eis(all(v.proposal["actor"] == {"type": "flow", "id": "node-red:testflow-herstart"}
                    for v in per_doel.values()), "de actor is niet door de Hub vastgezet")
            routes = {k: v for k, v in self.hub.state.calls.items() if v}
            s.details["aanroepen_bij_de_hub"] = sorted(routes)
            s.details["uitgevoerd"] = "niets: er is geen uitvoerder in deze proef; de flow kan alleen aanvragen"
            # terug naar de toestand uit Git (uit)
            uit = nf.importeer(self.beheer(), bundel, hub_url=self.hub.url, token=self.token)
            eis(uit["testflow-herstart"]["ingeschakeld"] is False, "uitschakelen faalt")

    def n7_hartslag(self) -> None:
        with self.stap("N7", "Hartslag met flowvertraging komt bij de Hub aan (I10-10-06)") as s:
            wacht("heartbeat", lambda: APP_ID in self.hub.state.heartbeats, 90)
            hb = dict(self.hub.state.heartbeats[APP_ID])
            s.details["heartbeat"] = {k: hb[k] for k in ("gemeld", "beoordeling", "verwacht_interval_s", "checks")}
            eis(hb["beoordeling"]["status"] == "healthy" and hb["verwacht_interval_s"] == 60, "beoordeling klopt niet")
            (check,) = hb["checks"]
            eis(check["name"] == "flow_vertraging" and re.fullmatch(r"\d+ ms", check["detail"]) is not None,
                f"flowvertraging ontbreekt: {check}")

    def n8_herstel(self) -> None:
        with self.stap("N8", "Andere flowversie zetten en de versie uit Git herstellen (I10-10-05)") as s:
            git = nf.bundels_in_git()["hartslag"]
            lock = nf.lees_lock()["bundels"]["hartslag"]["sha256"]
            versie = nf.lees_lock()["bundels"]["hartslag"]["versie"]
            ander = json.loads(json.dumps(git).replace(f"versie {versie}", "versie 9.9.9").replace("elke 60 s",
                                                                                                "proefversie"))
            pad = self.werk / "hartslag-proef.json"
            pad.write_text(json.dumps(ander), encoding="utf-8")
            eis(nf.checksum(ander) != lock, "de proefversie is gelijk aan het lock")
            uit = nf.importeer(self.beheer(), {"hartslag": nf.lees_bundel(pad)}, hub_url=self.hub.url,
                               token=self.token)
            s.details["na_wijziging"] = {"gelezen": uit["hartslag"]["gelezen"], "vergelijking": nf.exporteer(
                self.beheer(), self.werk / "export2")["hartslag"]["vergelijking"]}
            eis(s.details["na_wijziging"] == {"gelezen": nf.checksum(ander), "vergelijking": "afwijkend"},
                "de afwijking is niet zichtbaar")
            uit = nf.importeer(self.beheer(), {"hartslag": git}, hub_url=self.hub.url, token=self.token)
            s.details["na_herstel"] = {"gelezen": uit["hartslag"]["gelezen"], "vergelijking": nf.exporteer(
                self.beheer(), self.werk / "export3")["hartslag"]["vergelijking"]}
            eis(s.details["na_herstel"] == {"gelezen": lock, "vergelijking": "gelijk_aan_lock"}, "herstel faalt")
            alles = nf.readback(self.beheer(), nf.bundels_in_git())
            eis(all(v["gelijk"] for v in alles.values()), "andere bundels zijn door het herstel geraakt")

    def n9_herstart_en_stop(self) -> None:
        with self.stap("N9", "Herstart zonder verlies; stoppen is zichtbaar (I10-10-05, -06)") as s:
            hb = self.hub.state.heartbeats
            aantal = len(self.hub.events())
            self.hub.zet_apps("STOPPED")
            self.tik("jlp-mg-tik")
            wacht("melding vóór de herstart", lambda: len(self.hub.events()) == aantal + 1, 30, 0.5)
            self.herstart(self.cname, lambda: self.klaar(self.poort), 180)
            self.beheer().login(self.gebruiker, self.wachtwoord)
            alles = nf.readback(self.beheer(), nf.bundels_in_git())
            eis(all(v["gelijk"] for v in alles.values()), "flows zijn na de herstart anders")
            voor = hb[APP_ID]["observed_at"]
            wacht("heartbeat na herstart (credentials overleven de herstart)",
                  lambda: hb[APP_ID]["observed_at"] != voor, 90)
            s.details["heartbeat_na_herstart"] = hb[APP_ID]["beoordeling"]
            # De bundel 'mediaguard-offline' is sinds N4 niet opnieuw geïmporteerd: zijn versleutelde token moet de
            # deelimports van N6/N8 en de herstart hebben overleefd. De toestand van de flow staat in het geheugen:
            # na de herstart meldt de eerste (automatische) meting de nog gestopte app opnieuw.
            wacht("melding na de herstart (eerste automatische meting)",
                  lambda: len(self.hub.events()) == aantal + 2, 90, 0.5)
            s.details["meldingen_rond_herstart"] = [list(e) for e in self.hub.events()[aantal:]]
            eis(s.details["meldingen_rond_herstart"] == [["flow.app_watch.down", "mediaguard"]] * 2,
                "onverwachte meldingen rond de herstart")
            eis(self.monitor_ok(self.poort), "monitor klopt niet na herstart")
            sh("docker", "stop", self.cname)
            s.details["monitor_na_stop"] = self.monitor_ok(self.poort)
            eis(s.details["monitor_na_stop"] is False, "de monitor slaagt terwijl Node-RED gestopt is")
            (nu,) = self.get(f"{self.hub.url}/_sim/heartbeats").json()
            (later,) = self.get(f"{self.hub.url}/_sim/heartbeats", params={"over_s": 181}).json()
            s.details["hub_direct_na_stop"] = {"status": nu["status"], "verouderd": nu["verouderd"]}
            s.details["hub_na_3x_interval"] = {k: later[k] for k in ("status", "reason_code", "verouderd")}
            s.details["tijd"] = "3 × het interval is niet gewacht maar gevraagd aan de simulator (over_s=181)"
            eis(later["status"] == "unknown" and later["reason_code"] == "meting_verouderd",
                "na 3 × het interval zonder heartbeat is de dienst niet 'unknown'")
            s.details["incident_in_echte_hub_of_uptime_kuma"] = "NIET GEVALIDEERD"

    # ------------------------------------------------------------------ draaien
    def draai(self) -> dict[str, Any]:
        sh("docker", "pull", "-q", self.app.image)
        try:
            self.n1_manifest()
            self.n2_controle()
            self.n3_beheer()
            self.n4_import()
            self.n5_mediaguard()
            self.n6_testflow()
            self.n7_hartslag()
            self.n8_herstel()
            self.n9_herstart_en_stop()
        finally:
            if self.hub.thread.is_alive():
                self.hub.stop()
            self.opruimen()
        geslaagd = bool(self.stappen) and all(s.uitkomst == "geslaagd" for s in self.stappen)
        rapport = {
            "soort": "rooktest Node-RED als standaard Custom App tegen de Hub-simulator (P2: I10-10-01…06)",
            "gestart": self.gestart,
            "klaar": dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "validatie": "Sim-getest; NIET GEVALIDEERD (echte NAS)",
            "omgeving": "Docker (geen TrueNAS): --user 568:568, eigen lege map op /data, poort op 127.0.0.1, zonder "
                        "--cap-drop of no-new-privileges; Hub = simulator op het adres van de Docker-bridge",
            "images": {APP_ID: self.app.image},
            "niet_gedekt": [
                "beheerder, flows en service-token op een echte NAS (het manifest kan ze niet leveren; ADR-0023)",
                "flow 'my_house sensor verouderd → melding' (geen contractroute voor sensorversheid)",
                "incident in de echte Hub of Uptime Kuma na het stoppen van Node-RED",
                "uitvoering van een goedgekeurde herstart door de echte uitvoerder",
            ],
            "uitkomst": "geslaagd" if geslaagd else "mislukt",
            "stappen": [s.__dict__ for s in self.stappen],
        }
        tekst = json.dumps(rapport, ensure_ascii=False)
        if GEHEIM_RE.search(tekst) or (self.token and self.token in tekst) or self.wachtwoord in tekst:
            raise RuntimeError("rapport bevat iets dat op een geheim lijkt; niet opgeslagen")
        return rapport


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    p.add_argument("--rapport", type=Path, default=None)
    p.add_argument("--houd", action="store_true", help="containers en werkmap laten staan (debuggen)")
    args = p.parse_args(argv)
    rapport = NoderedRooktest(args.houd).draai()
    if args.rapport:
        args.rapport.write_text(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for stap in rapport["stappen"]:
        if stap["uitkomst"] != "geslaagd":
            print(f"  {stap['id']}: {stap['uitkomst']} — {stap['details'].get('fout', '')}", file=sys.stderr)
    print(f"uitkomst: {rapport['uitkomst']}")
    return 0 if rapport["uitkomst"] == "geslaagd" else 1


if __name__ == "__main__":
    raise SystemExit(main())
