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
- N9  herstart zonder verlies; stoppen is zichtbaar (monitor faalt, heartbeat verouderd → ``unknown``);
- N10 het service-token gaat alleen naar de Hub (ADR-0025, S-P2-410 bouwpunt 9d en het herstel na de review): een
      bundel met een ``env`` op het tabblad, met een tweede regel op ``msg.url`` of met ``msg.hasOwnProperty =
      msg.isPrototypeOf`` wordt geweigerd vóór Node-RED iets verandert, en een luisteraar ontvangt niets; stuurt de
      "Hub" door (307/302) naar een andere poort of een andere host, dan volgt de flow uit Git dat niet
      (``msg.followRedirects = false``). Controleproeven via de rauwe Admin-API: zonder die regel volgt Node-RED de
      doorverwijzing wel (het rapport noemt of de bearer-kop meegaat; bij deze digest: ja), en met de regel maar ook
      ``msg.hasOwnProperty = msg.isPrototypeOf`` krijgt het doel de bearer-kop (geëist). Proxy: met ``http_proxy`` in
      de omgeving en de ``FLOWS`` uit het manifest (``proxyOptions`` met een lege env) krijgt een proxy niets en de
      Hub de heartbeat; controleproef zonder ``proxyOptions``: het verzoek met de bearer-kop gaat via de proxy.

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
import ipaddress
import json
import re
import secrets
import socket
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
# Omweg uit de review van S-P2-410 (R-VEILIG-1): de http request-node leest msg.followRedirects met
# msg.hasOwnProperty; met deze regel ziet hij hem niet meer en volgt hij een doorverwijzing met het token.
HASOWN_REGEL = {"t": "set", "p": "hasOwnProperty", "pt": "msg", "to": "isPrototypeOf", "tot": "msg"}


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


class Luisteraar:
    """Een HTTP-luisteraar op een adres dat de container kan bereiken. Noteert per verzoek alleen methode, pad en of er
    een bearer-kop was (en of dat het service-token was; het token zelf nooit). Met ``doorverwijzing`` antwoordt hij
    met die status en ``Location: <doel><pad>`` (de doorstuurder in N10). Met ``proxy`` speelt hij een proxy: op
    ``CONNECT`` antwoordt hij 200 en houdt de verbinding open, zodat het verzoek in de tunnel (bij een http-doel platte
    tekst) hier ook binnenkomt en genoteerd wordt (N10, R-VEILIG-2)."""

    def __init__(self, host: str, token: Callable[[], str], *, proxy: bool = False) -> None:
        self.ontvangen: list[dict[str, Any]] = []
        self.doorverwijzing: tuple[int, str] | None = None
        luisteraar = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1" if proxy else "HTTP/1.0"

            def do_CONNECT(self) -> None:  # de naam komt uit BaseHTTPRequestHandler
                # het doel (host:poort) wordt niet genoteerd: het is een adres van deze machine (V-097)
                luisteraar.ontvangen.append({"methode": "CONNECT", "pad": "", "bearer": False,
                                             "is_het_service_token": False})
                if not proxy:
                    self.send_error(405)
                    return
                self.send_response(200, "Connection established")
                self.end_headers()

            def _vang(self) -> None:
                lengte = int(self.headers.get("Content-Length") or 0)
                if lengte:
                    self.rfile.read(lengte)
                kop = self.headers.get("Authorization") or ""
                geheim = token()
                pad = self.path.split("?", 1)[0]
                if pad.startswith(("http://", "https://")):  # vorm van een verzoek aan een proxy zonder tunnel
                    pad = "/" + pad.split("/", 3)[3] if pad.count("/") >= 3 else "/"
                luisteraar.ontvangen.append({"methode": self.command, "pad": pad,
                                             "bearer": kop.startswith("Bearer "),
                                             "is_het_service_token": bool(geheim) and kop == f"Bearer {geheim}"})
                if luisteraar.doorverwijzing is not None:
                    status, doel = luisteraar.doorverwijzing
                    self.send_response(status)
                    self.send_header("Location", doel + self.path)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            do_GET = do_POST = _vang

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer((host, 0), Handler)
        self.url = f"http://{host}:{int(self.server.server_address[1])}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def hartslagen(self) -> list[dict[str, Any]]:
        return [v for v in list(self.ontvangen) if v["pad"] == "/api/v1/monitoring/heartbeat"]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


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
        self.beheer_flows = ""
        self.poort = 0
        self.cname = ""
        self.nr: nf.NodeRed | None = None
        self.netwerken: list[str] = []
        self.luisteraars: list[Luisteraar] = []
        self.adressen: list[str] = [self.bridge]  # komen nooit in het rapport (V-097)

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

    def luisteraar(self, host: str, *, proxy: bool = False) -> Luisteraar:
        luisteraar = Luisteraar(host, lambda: self.token, proxy=proxy)
        self.luisteraars.append(luisteraar)
        return luisteraar

    def proxyproef(self, naam: str, flows: str) -> dict[str, Any]:
        """R-VEILIG-2 (herstel S-P2-410): een tweede Node-RED met ``FLOWS`` = ``flows`` (met de wegwerpbeheerder) en
        ``http_proxy``, ``HTTP_PROXY`` en ``all_proxy`` in de omgeving naar een luisteraar die als proxy speelt.
        Importeert de hartslag uit Git met een tweede luisteraar als Hub, tikt en wacht ook de automatische eerste tik
        af. Het rapport krijgt alleen aantallen en methoden, geen adressen (V-097)."""
        proxy = self.luisteraar(self.bridge, proxy=True)
        hub = self.luisteraar(self.bridge)
        omgeving = {"http_proxy": proxy.url, "HTTP_PROXY": proxy.url, "all_proxy": proxy.url}
        app = dataclasses.replace(self.app, app_id=f"{APP_ID}-{naam}",
                                  env={**self.app.env, "FLOWS": flows, **omgeving})
        poort = vrije_poort()
        cname = self.start(app, self.lege_map(naam), poort)
        try:
            wacht(f"Node-RED ({naam}) antwoordt", lambda: self.klaar(poort), 180)
            nr = nf.NodeRed(f"http://127.0.0.1:{poort}", self.http)
            nr.login(self.gebruiker, self.wachtwoord)
            uit = nf.importeer(nr, {"hartslag": nf.bundels_in_git()["hartslag"]}, hub_url=hub.url, token=self.token)
            eis(uit["hartslag"]["gelijk"] and uit["hartslag"]["eigen_controle"] == [], f"{naam}: import faalt")
            code = nr.inject("jlp-hs-tik")
            eis(code == 200, f"{naam}: inject jlp-hs-tik: HTTP {code}")
            wacht(f"{naam}: verzoek bij de Hub of bij de proxy", lambda: bool(hub.ontvangen or proxy.ontvangen), 30,
                  0.3)
            time.sleep(7)  # ook de automatische eerste tik na het zetten van de flows (5 s)
        finally:
            sh("docker", "rm", "-f", cname, check=False)
        return {
            "hub_kreeg_de_heartbeat_met_token": any(v["is_het_service_token"] for v in hub.hartslagen()),
            "hub_ontving": len(hub.ontvangen),
            "proxy_ontving": len(proxy.ontvangen),
            "methoden_bij_de_proxy": sorted({str(v["methode"]) for v in proxy.ontvangen}),
            "proxy_kreeg_bearer": any(v["is_het_service_token"] for v in proxy.ontvangen),
        }

    def andere_host(self) -> str:
        """Een tweede adres van deze machine dat de container kan bereiken: de gateway van een eigen, leeg
        Docker-netwerk (een andere host voor de http request-node; het adres komt niet in het rapport)."""
        naam = f"jlp-upstream-nodered-{self.tag}"
        sh("docker", "network", "create", naam)
        self.netwerken.append(naam)
        adres = sh("docker", "network", "inspect", naam, "--format", "{{(index .IPAM.Config 0).Gateway}}").strip()
        eis(bool(adres) and adres != self.bridge, "geen tweede adres voor de proef met een andere host")
        self.adressen.append(adres)
        return adres

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
            self.beheer_flows = self.manifest_flows.replace(leeg, f'"users":[{gebruiker}]')
            self.cname = self.start(self.variant("beheer", self.beheer_flows), self.lege_map("beheer"), self.poort)
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

    def n10_alleen_naar_de_hub(self) -> None:
        with self.stap("N10", "Het service-token gaat alleen naar de Hub: weigeren vóór Node-RED, geen doorverwijzing, "
                              "geen proxy uit de omgeving (ADR-0025; I10-10-02)") as s:
            sh("docker", "start", self.cname)
            wacht("Node-RED (beheer) weer gestart", lambda: self.klaar(self.poort), 180)
            nr = self.beheer()
            nr.login(self.gebruiker, self.wachtwoord)
            hb = self.hub.state.heartbeats
            git = nf.bundels_in_git()["hartslag"]
            buur = self.luisteraar(self.bridge)  # zelfde host, andere poort
            elders = self.luisteraar(self.andere_host())  # andere host
            doorstuurder = self.luisteraar(self.bridge)  # speelt de Hub die doorverwijst

            # a. een bundel die het token naar de buur zou sturen, wordt geweigerd vóór Node-RED iets verandert
            als_getal = f"{int(ipaddress.IPv4Address(self.bridge))}:{buur.url.rsplit(':', 1)[1]}"  # zoals de review
            env_op_tab = json.loads(json.dumps(git))
            next(n for n in env_op_tab if n["type"] == "tab")["env"] = [
                {"name": "HUB_URL", "value": als_getal, "type": "str"}]
            tweede_url = json.loads(json.dumps(git))
            next(n for n in tweede_url if n["id"] == "jlp-hs-bouw")["rules"].append(
                {"t": "set", "p": "msg.url", "pt": "msg", "to": f"{als_getal}/api/v1/monitoring/heartbeat",
                 "tot": "str"})
            # herstel R-VEILIG-1: msg.hasOwnProperty = msg.isPrototypeOf zet msg.followRedirects buiten werking
            eigen_has = json.loads(json.dumps(git))
            next(n for n in eigen_has if n["id"] == "jlp-hs-bouw")["rules"].append(dict(HASOWN_REGEL))
            weigering: dict[str, Any] = {}
            for naam, nodes in (("env_op_tabblad", env_op_tab), ("tweede_regel_op_msg_url", tweede_url),
                                ("hasOwnProperty_isPrototypeOf", eigen_has)):
                voor = nr.flows()[1]
                try:
                    nf.importeer(nr, {"hartslag": nodes}, hub_url=self.hub.url, token=self.token)
                    raise Mislukt(f"{naam}: de import nam de bundel aan")
                except nf.Fout as exc:
                    eis("geweigerd" in str(exc), f"{naam}: onverwachte melding")
                weigering[naam] = {"geweigerd": True, "codes": sorted({b.split(":", 1)[0] for b in nf.lint(nodes)}),
                                   "flows_in_node_red_ongewijzigd": nr.flows()[1] == voor}
                eis(weigering[naam]["flows_in_node_red_ongewijzigd"], f"{naam}: Node-RED is toch veranderd")
            vorige = (hb.get(APP_ID) or {}).get("observed_at")
            self.tik("jlp-hs-tik")
            wacht("heartbeat bij de Hub na de weigering", lambda: hb[APP_ID]["observed_at"] != vorige, 30, 0.3)
            time.sleep(2)
            weigering["hub_kreeg_de_heartbeat"] = True
            weigering["luisteraar_ontving"] = len(buur.ontvangen)
            eis(not buur.ontvangen, "de luisteraar kreeg een verzoek na een geweigerde bundel")
            s.details["weigering"] = weigering

            # b. de "Hub" stuurt door naar een andere poort of host: de flow uit Git volgt dat niet
            uit = nf.importeer(nr, {"hartslag": git}, hub_url=doorstuurder.url, token=self.token)
            eis(uit["hartslag"]["gelijk"] and uit["hartslag"]["eigen_controle"] == [], "import met doorstuurder")
            time.sleep(7)  # de automatische eerste tik na het zetten van de flows (5 s)
            doelen = {"zelfde_host_andere_poort": buur, "andere_host": elders}
            proeven: dict[str, Any] = {}
            for status, waar in ((307, "zelfde_host_andere_poort"), (302, "zelfde_host_andere_poort"),
                                 (307, "andere_host"), (302, "andere_host")):
                for luisteraar in (buur, elders, doorstuurder):
                    luisteraar.ontvangen.clear()
                doorstuurder.doorverwijzing = (status, doelen[waar].url)
                self.tik("jlp-hs-tik")
                wacht("heartbeat bij de doorstuurder", lambda: bool(doorstuurder.hartslagen()), 20, 0.3)
                time.sleep(2)
                (bij_hub, *_rest) = doorstuurder.hartslagen()
                proeven[f"{status} naar {waar}"] = {
                    "doorstuurder_kreeg_bearer": bool(bij_hub["is_het_service_token"]),
                    "doel_ontving": len(buur.ontvangen) + len(elders.ontvangen)}
                eis(bij_hub["is_het_service_token"], "de doorstuurder (de Hub) kreeg het token niet")
                eis(not buur.ontvangen and not elders.ontvangen, f"{status} naar {waar}: het doel kreeg een verzoek")
            s.details["doorverwijzing_flow_uit_git"] = proeven

            # c. controleproef: zonder msg.followRedirects = false (rauwe Admin-API, buiten de eigen controle om)
            for luisteraar in (buur, elders, doorstuurder):
                luisteraar.ontvangen.clear()
            doorstuurder.doorverwijzing = (307, elders.url)
            rev, flows = nr.flows()
            bouw = next(n for n in flows if n.get("id") == "jlp-hs-bouw")
            bouw["rules"] = [r for r in bouw["rules"] if r != nf.REDIRECT_UIT]
            nr.zet_flows(rev, flows)
            time.sleep(1)
            self.tik("jlp-hs-tik")
            wacht("controleproef: verzoek bij de andere host", lambda: bool(elders.hartslagen()), 20, 0.3)
            doorstuurder.doorverwijzing = None
            try:
                nf.readback(nr, {"hartslag": git})
                gezien = "niets"
            except nf.Fout as exc:
                gezien = "redirect_niet_uit" if "redirect_niet_uit" in str(exc) else "andere bevinding"
            s.details["controleproef_zonder_regel"] = {
                "doorverwijzing_gevolgd": True,
                "bearer_meegestuurd_naar_andere_host": any(v["is_het_service_token"] for v in elders.hartslagen()),
                "readback_ziet_het": gezien}
            eis(gezien == "redirect_niet_uit", "het teruglezen ziet de ontbrekende regel niet")

            # c2. controleproef bij de weigering van R-VEILIG-1 (herstel S-P2-410): de route-node uit Git, mét
            # msg.followRedirects = false, plus msg.hasOwnProperty = msg.isPrototypeOf, via de rauwe Admin-API (lint en
            # importeer weigeren hem, zie a). De http request-node ziet followRedirects dan niet meer: het doel van de
            # doorverwijzing krijgt de bearer-kop wel. Zo laat de proef zien dat de weigering iets tegenhoudt.
            for luisteraar in (buur, elders, doorstuurder):
                luisteraar.ontvangen.clear()
            doorstuurder.doorverwijzing = (307, elders.url)
            rev, flows = nr.flows()
            bouw = next(n for n in flows if n.get("id") == "jlp-hs-bouw")
            bouw["rules"] = [*next(n for n in git if n["id"] == "jlp-hs-bouw")["rules"], dict(HASOWN_REGEL)]
            nr.zet_flows(rev, flows)
            time.sleep(1)
            self.tik("jlp-hs-tik")
            wacht("controleproef hasOwnProperty: verzoek bij de andere host", lambda: bool(elders.hartslagen()), 20,
                  0.3)
            doorstuurder.doorverwijzing = None
            try:
                nf.readback(nr, {"hartslag": git})
                gezien = "niets"
            except nf.Fout as exc:
                gezien = "change_regel" if "change_regel" in str(exc) else "andere bevinding"
            hop = {"via": "rauwe Admin-API", "regel": "msg.hasOwnProperty = msg.isPrototypeOf",
                   "doorverwijzing_gevolgd": True,
                   "doel_kreeg_bearer": any(v["is_het_service_token"] for v in elders.hartslagen()),
                   "readback_ziet_het": gezien}
            s.details["controleproef_hasOwnProperty"] = hop
            eis(hop["doel_kreeg_bearer"] is True, "controleproef: het doel kreeg de bearer-kop niet (proef zegt niets)")
            eis(gezien == "change_regel", "het teruglezen ziet de regel msg.hasOwnProperty niet")

            # d. terug naar de bundels uit Git, met de Hub
            nf.importeer(nr, {"hartslag": git}, hub_url=self.hub.url, token=self.token)
            alles = nf.readback(nr, nf.bundels_in_git(), hub_url=self.hub.url)
            s.details["na_herstel"] = {n: {"gelijk": v["gelijk"], "eigen_controle": v["eigen_controle"]}
                                       for n, v in alles.items()}
            eis(all(v["gelijk"] and not v["eigen_controle"] for v in alles.values()), "herstel naar Git faalt")

            # e. R-VEILIG-2 (herstel S-P2-410): een proxyvariabele in de omgeving van de container. Met de FLOWS uit
            # het manifest (proxyOptions met een lege env) krijgt de proxy niets en de "Hub" de heartbeat met het
            # token; controleproef zonder proxyOptions: het verzoek met de bearer-kop gaat via de proxy.
            optie = ' -D proxyOptions={"env":{}}'
            eis(self.beheer_flows.count(optie) == 1, "FLOWS uit het manifest heeft geen proxyOptions met een lege env")
            met = self.proxyproef("proxy-met", self.beheer_flows)
            zonder = self.proxyproef("proxy-zonder", self.beheer_flows.replace(optie, ""))
            s.details["proxy_uit_de_omgeving"] = {"met_proxyOptions_uit_het_manifest": met,
                                                  "controleproef_zonder_proxyOptions": zonder}
            eis(met["hub_kreeg_de_heartbeat_met_token"] and met["proxy_ontving"] == 0,
                "met proxyOptions uit het manifest ging het verzoek toch via de proxy, of kwam het niet aan")
            eis(zonder["proxy_ontving"] >= 1 and zonder["proxy_kreeg_bearer"] and zonder["hub_ontving"] == 0,
                "controleproef: zonder proxyOptions ging het verzoek niet via de proxy (proef zegt niets)")

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
            self.n10_alleen_naar_de_hub()
        finally:
            for luisteraar in self.luisteraars:
                luisteraar.stop()
            if self.hub.thread.is_alive():
                self.hub.stop()
            self.opruimen()
            for netwerk in self.netwerken:
                sh("docker", "network", "rm", netwerk, check=False)
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
                "flows die een beheerder zelf in de editor maakt: geen egressregels, en zonder msg.followRedirects = "
                "false volgt Node-RED een doorverwijzing met het token (controleproef N10)",
                "of een proxy-instelling van TrueNAS of van het formulier in de omgeving van de container komt (de "
                "proef zet http_proxy zelf met docker run -e; proxyOptions in FLOWS negeert hem dan, N10)",
            ],
            "uitkomst": "geslaagd" if geslaagd else "mislukt",
            "stappen": [s.__dict__ for s in self.stappen],
        }
        tekst = json.dumps(rapport, ensure_ascii=False)
        if GEHEIM_RE.search(tekst) or (self.token and self.token in tekst) or self.wachtwoord in tekst:
            raise RuntimeError("rapport bevat iets dat op een geheim lijkt; niet opgeslagen")
        getallen = [str(int(ipaddress.IPv4Address(a))) for a in self.adressen if a]
        if any(a and a in tekst for a in [*self.adressen, *getallen]):
            raise RuntimeError("rapport bevat een adres van deze machine; niet opgeslagen (V-097)")
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
