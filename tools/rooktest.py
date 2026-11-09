"""Installatie-rooktest zonder YAML (I23, P1) — lokaal, met Docker, tegen de TrueNAS-simulator.

Speelt de eerste installatie uit ``install/truenas/INSTALLATIE.md`` na zoals TrueNAS een **standaard Custom App**
start: per app één ``docker run`` met precies één map → ``/appdata`` en één poort, zonder extra rechten of
beveiligingsopties (het formulier zet geen ``no-new-privileges`` en geen ``cap_drop``), zonder compose of YAML.

1. Hub (``ai-server-manager``) en executor (``ai-server-manager-executor``) starten; setupcode uit de containerlog.
2. Eerste beheerder via de API; wizard: besluiten D01–D18, gates, D03 (NAS-adres van de simulator), alleen-lezen-key,
   TLS-vingerafdruk bevestigen, verbindingstest, metingen D01/D02/D03/D06/D07, inventaris en catalogus.
3. Executor koppelen met een koppelcode + vingerafdruk via zijn statuspagina (contract 1.2 §3.9).
4. Lifecycle-dienst (deze repo, ``jelly-leon-platform``) handmatig "installeren" met ``HUB_URL`` +
   ``HUB_ENROLLMENT_TOKEN`` + ``HUB_KEY_FINGERPRINT`` (aanbevolen route, geen wedloop) en een run vastleggen.
5. Plan/preview van een app als standaard Custom App (Gotify-manifest → ``app.create``-waarden via de validator van
   appstore-en-fork), met conflictcontrole tegen de simulator. Uitvoering via de lifecycle bestaat pas in P2: de
   lifecycle moet dan eerlijk ``501 capability_unavailable`` geven.
6. Simulatorcontrole van de preview: de **testharnas** (niet het platform) stuurt precies die waarden met de
   executor-key van de simulator naar ``app.create`` en leest de digest terug — om te bewijzen dat de vertaling
   geldige ``ix-app``-waarden oplevert. Dat is géén installatie via het platform.

Het rapport (JSON) bevat alleen statussen, redencodes, versies, publieke vingerafdrukken en digests — nooit setupcode,
wachtwoord, tokens of keys. Op een echte NAS is hiermee niets gevalideerd (**NIET GEVALIDEERD (echte NAS)**).

Gebruik (images eerst lokaal bouwen, zie ``make rooktest``)::

    python tools/rooktest.py --hub-image … --executor-image … --lifecycle-image … --rapport rooktest.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "install" / "manifests" / "gotify.manifest.yaml"
SETUP_RE = re.compile(r"SETUPCODE \(eenmalig[^)]*\): (\S+)")
GEHEIM_RE = re.compile(r"(enr_[A-Za-z0-9_-]{20,}|svc_[A-Za-z0-9_-]{20,}|\b\d+-[A-Za-z0-9]{40,}\b)")


@dataclass
class Stap:
    id: str
    titel: str
    uitkomst: str = "niet_uitgevoerd"  # geslaagd | mislukt | verwacht_niet_beschikbaar | overgeslagen
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Rapport:
    gestart: str
    images: dict[str, str]
    bron: dict[str, str]
    stappen: list[Stap] = field(default_factory=list)

    def stap(self, sid: str, titel: str) -> Stap:
        s = Stap(sid, titel)
        self.stappen.append(s)
        return s

    def view(self) -> dict[str, Any]:
        tekst = json.dumps([s.__dict__ for s in self.stappen], ensure_ascii=False, default=str)
        if GEHEIM_RE.search(tekst):  # verdediging in de diepte: nooit een token of key in het rapport
            raise RuntimeError("rapport bevat iets dat op een geheim lijkt; niet opgeslagen")
        geslaagd = all(s.uitkomst in ("geslaagd", "verwacht_niet_beschikbaar") for s in self.stappen)
        return {"soort": "installatie-rooktest zonder YAML (P1, I23)", "gestart": self.gestart,
                "klaar": dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                "validatie": "Sim-getest; NIET GEVALIDEERD (echte NAS)", "images": self.images, "bron": self.bron,
                "uitkomst": "geslaagd" if geslaagd else "mislukt",
                "stappen": [s.__dict__ for s in self.stappen]}


class Mislukt(Exception):
    pass


def sh(*args: str, check: bool = True) -> str:
    res = subprocess.run(list(args), capture_output=True, text=True, check=False)  # noqa: S603
    if check and res.returncode != 0:
        raise Mislukt(f"{' '.join(args[:3])}…: {res.stderr.strip()[:400]}")
    return res.stdout


def bridge_gateway() -> str:
    gw = sh("docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}").strip()
    ipaddress.ip_address(gw)
    return gw


def vrije_poort(host: str) -> int:
    with socket.socket() as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


def maak_certificaat(map_: Path, ip: str) -> tuple[Path, Path, str]:
    """Zelfondertekend certificaat voor de simulator; geeft (cert, sleutel, sha256-vingerafdruk van de DER)."""
    sleutel = ec.generate_private_key(ec.SECP256R1())
    naam = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "truenas-sim")])
    nu = dt.datetime.now(dt.UTC)
    cert = (x509.CertificateBuilder().subject_name(naam).issuer_name(naam).public_key(sleutel.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(nu - dt.timedelta(minutes=5))
            .not_valid_after(nu + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(ip))]), critical=False)
            .sign(sleutel, hashes.SHA256()))
    cpad, kpad = map_ / "sim.crt", map_ / "sim.key"
    cpad.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kpad.write_bytes(sleutel.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cpad, kpad, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def wacht(beschrijving: str, check: Callable[[], bool], timeout: float = 120.0, stap: float = 1.0) -> None:
    eind = time.monotonic() + timeout
    while time.monotonic() < eind:
        try:
            if check():
                return
        except (httpx.HTTPError, OSError, Mislukt):
            pass
        time.sleep(stap)
    raise Mislukt(f"time-out: {beschrijving}")


def container_status(naam: str) -> str:
    fmt = "{{if .State.Health}}{{.State.Health.Status}}{{else}}geen{{end}}"
    return sh("docker", "inspect", "-f", fmt, naam).strip()


class Rooktest:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.tag = secrets.token_hex(3)
        self.werk = Path(tempfile.mkdtemp(prefix="jlp-rooktest-"))
        self.gw = bridge_gateway()
        self.containers: list[str] = []
        self.sim: subprocess.Popen[bytes] | None = None
        self.rapport = Rapport(
            dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            {"hub": args.hub_image, "executor": args.executor_image, "lifecycle": args.lifecycle_image},
            {"ai-server-manager": args.hub_commit or "onbekend", "jelly-leon-platform": args.eigen_commit or "onbekend",
             "appstore-en-fork (validator)": args.validator_commit or "onbekend"},
        )

    # ------------------------------------------------------------------ hulpmiddelen
    def run_app(self, naam: str, image: str, hostpoort: int, containerpoort: int, env: dict[str, str]) -> str:
        """Zoals een standaard Custom App: één map → /appdata, één poort, geen extra rechten of opties."""
        data = self.werk / naam
        data.mkdir()
        cname = f"jlp-rooktest-{naam}-{self.tag}"
        cmd = ["docker", "run", "-d", "--name", cname, "-p", f"{self.gw}:{hostpoort}:{containerpoort}",
               "-v", f"{data}:/appdata"]
        for k, v in env.items():
            cmd += ["-e", f"{k}={v}"]
        sh(*cmd, image)
        self.containers.append(cname)
        return cname

    def opruimen(self) -> None:
        if self.args.houd:
            print(f"containers blijven staan: {self.containers}; werkmap {self.werk}")
            return
        for c in self.containers:
            sh("docker", "rm", "-f", c, check=False)
        if self.sim is not None:
            self.sim.terminate()
            try:
                self.sim.wait(10)
            except subprocess.TimeoutExpired:
                self.sim.kill()
        # bestanden van 568 zonder root op de host weghalen: via een wegwerpcontainer (zoals tests/e2e)
        sh("docker", "run", "--rm", "--user", "0", "--entrypoint", "python", "-v", f"{self.werk}:/w",
           self.args.lifecycle_image, "-c",
           "import os,shutil\nfor n in os.listdir('/w'):\n p=os.path.join('/w',n)\n "
           "shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)", check=False)
        shutil.rmtree(self.werk, ignore_errors=True)

    @staticmethod
    def procescontrole(container: str) -> dict[str, Any]:
        """Uid, CapEff en NoNewPrivs van elk proces, behalve dit controleproces zelf (docker exec)."""
        code = ("import os,json\nme=os.getpid();out=[]\nfor p in os.listdir('/proc'):\n if not p.isdigit() or "
                "int(p)==me: continue\n try: st=open(f'/proc/{p}/status').read()\n except OSError: continue\n "
                "f=dict(l.split(':',1) for l in st.splitlines() if ':' in l)\n out.append({'pid':int(p),"
                "'uid':f['Uid'].split()[0],'cap':f['CapEff'].strip(),'nnp':f.get('NoNewPrivs','').strip()})\n"
                "print(json.dumps(out))")
        procs = json.loads(sh("docker", "exec", "-u", "568", container, "python", "-c", code))
        uids = sorted({p["uid"] for p in procs})
        in_orde = (all(p["uid"] in ("568", "569") and p["cap"] == "0000000000000000" and p["nnp"] == "1"
                       for p in procs) and any(p["pid"] == 1 for p in procs))
        return {"processen": len(procs), "uids": uids, "in_orde": in_orde}

    @staticmethod
    def idem(csrf: str) -> dict[str, str]:
        return {"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())}

    def sim_rpc(self, key: str, *calls: tuple[str, list[Any]]) -> list[Any]:
        """Directe JSON-RPC naar de simulator (testharnas), over WSS met het eigen testcertificaat."""
        import asyncio

        import websockets

        ctx = ssl.create_default_context(cafile=str(self.cert))
        ctx.check_hostname = False  # certificaat op IP; de vingerafdruk is al gecontroleerd

        async def go() -> list[Any]:
            async with websockets.connect(f"wss://{self.gw}:{self.sim_poort}/api/current", ssl=ctx) as ws:
                out: list[Any] = []
                for i, (method, params) in enumerate([("auth.login_with_api_key", [key]), *calls], start=1):
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}))
                    while True:
                        msg = json.loads(await ws.recv())
                        if msg.get("id") == i:
                            break
                    if "error" in msg:
                        reden = (msg["error"].get("data") or {}).get("reason") or msg["error"].get("message")
                        raise Mislukt(f"{method}: {str(reden)[:300]}")
                    out.append(msg["result"])
                return out[1:]

        return asyncio.run(go())

    # ------------------------------------------------------------------ de test
    def uitvoeren(self) -> dict[str, Any]:
        try:
            self._uitvoeren()
        except Mislukt as exc:
            open_stap = next((s for s in reversed(self.rapport.stappen) if s.uitkomst == "niet_uitgevoerd"), None)
            if open_stap is not None:
                open_stap.uitkomst = "mislukt"
                open_stap.details["fout"] = str(exc)
            else:
                self.rapport.stap("fout", "Onverwachte fout").details["fout"] = str(exc)
        finally:
            self.opruimen()
        return self.rapport.view()

    def _uitvoeren(self) -> None:
        r = self.rapport
        # -- TrueNAS-simulator (WSS, zelfondertekend), alleen bereikbaar op de Docker-bridge van deze machine
        s = r.stap("S0", "TrueNAS-simulator starten (WSS, zelfondertekend certificaat, profiel 25.10.8)")
        self.cert, key, fp = maak_certificaat(self.werk, self.gw)
        self.sim_poort = vrije_poort(self.gw)
        self.sim = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "truenas_sim", "--host", self.gw, "--port", str(self.sim_poort), "--version",
             "25.10.8", "--ssl-certfile", str(self.cert), "--ssl-keyfile", str(key)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sim_http = httpx.Client(base_url=f"https://{self.gw}:{self.sim_poort}", verify=False, trust_env=False,  # noqa: S501
                                timeout=10)
        wacht("simulator", lambda: sim_http.get("/api/versions").status_code == 200, 30, 0.3)
        sleutels = sim_http.get("/_sim/keys").json()
        s.uitkomst, s.details = "geslaagd", {"tls_vingerafdruk_sha256": fp, "adres": "docker-bridge van de testmachine"}

        # -- twee Custom Apps
        s = r.stap("C1", "Custom App 'ai-server-manager' en 'ai-server-manager-executor' starten (docker run: één "
                         "map → /appdata, één poort, geen extra opties)")
        hub_poort, exec_poort = vrije_poort(self.gw), vrije_poort(self.gw)
        hub = self.run_app("hub", self.args.hub_image, hub_poort, 30400, {})
        exe = self.run_app("executor", self.args.executor_image, exec_poort, 30410, {})
        wacht("Hub healthy", lambda: container_status(hub) == "healthy", 240, 2)
        wacht("executor healthy", lambda: container_status(exe) == "healthy", 120, 2)
        s.uitkomst = "geslaagd"
        s.details = {"healthcheck": {"hub": container_status(hub), "executor": container_status(exe)},
                     "docker_run_opties": "alleen -p <poort> en -v <map>:/appdata (geen --cap-drop, geen "
                                          "no-new-privileges, geen env)"}
        hub_url = f"http://{self.gw}:{hub_poort}"
        api = httpx.Client(base_url=hub_url, trust_env=False, timeout=30)

        s = r.stap("C2", "Zonder no-new-privileges/cap_drop van het formulier: elk proces (ook PID 1) niet-root, "
                         "CapEff 0 en NoNewPrivs 1 (contract 1.2 §2.2, R11)")
        s.details = {"hub": self.procescontrole(hub), "executor": self.procescontrole(exe)}
        s.uitkomst = "geslaagd" if all(v["in_orde"] for v in s.details.values()) else "mislukt"

        s = r.stap("W1", "Setupcode alleen in de app-log; setup vereist")
        logs = subprocess.run(["docker", "logs", hub], capture_output=True, text=True, check=False)  # noqa: S603,S607
        codes = SETUP_RE.findall(logs.stdout + logs.stderr)
        status = api.get("/api/v1/setup/status")
        if not codes or status.json().get("setup_vereist") is not True:
            raise Mislukt("geen setupcode in de log of setup niet vereist")
        s.uitkomst = "geslaagd" if codes[-1] not in status.text else "mislukt"
        s.details = {"setup_vereist": True, "setupcode_in_log": True, "setupcode_in_api": codes[-1] in status.text}

        s = r.stap("W2", "Eerste beheerder aanmaken met de setupcode (API)")
        wachtwoord = "Rooktest-" + secrets.token_urlsafe(18)
        res = api.post("/api/v1/setup/admin", json={"setup_code": codes[-1], "username": "beheer",
                                                    "password": wachtwoord})
        if res.status_code != 200:
            raise Mislukt(f"setup/admin: {res.status_code} {res.json().get('code')}")
        csrf = res.json()["csrf_token"]
        again = api.post("/api/v1/setup/admin", json={"setup_code": codes[-1], "username": "tweede",
                                                      "password": wachtwoord})
        rol = api.get("/api/v1/auth/me").json().get("user", {}).get("role")
        s.uitkomst = "geslaagd" if again.status_code == 409 and rol == "admin" else "mislukt"
        s.details = {"tweede_setup": again.status_code, "rol": rol}

        s = r.stap("W3", "Wizard: besluiten D01–D18 en gates vóór de koppeling (onbekend is nooit groen)")
        besluiten = api.get("/api/v1/decisions").json()["items"]
        gates = api.get("/api/v1/gates").json()["items"]
        groen = [g["id"] for g in gates if str(g.get("status")) in ("groen", "green", "ok", "toegestaan")]
        s.details = {"besluiten": len(besluiten), "status": sorted({b["status"] for b in besluiten}),
                     "gates": {g["id"]: g.get("status") for g in gates}, "groen_zonder_meting": groen}
        s.uitkomst = "geslaagd" if len(besluiten) == 18 else "mislukt"

        s = r.stap("W4", "D03 (NAS-adres = simulator) invullen, alleen-lezen-key opslaan, verbinding testen")
        d03 = api.get("/api/v1/decisions/D03")
        res = api.put("/api/v1/decisions/D03/waarde", json={"waarde": {"nas_adres": self.gw,
                                                                        "https_poort": self.sim_poort}},
                      headers={**self.idem(csrf), "If-Match": d03.headers.get("etag", "")})
        if res.status_code != 200:
            raise Mislukt(f"D03: {res.status_code} {res.text[:200]}")
        res = api.put("/api/v1/truenas/api-key", json={"api_key": sleutels["readonly"]}, headers=self.idem(csrf))
        if res.status_code != 200 or res.json()["api_key"]["ingesteld"] is not True:
            raise Mislukt(f"api-key: {res.status_code}")
        eerste = api.post("/api/v1/truenas/test", headers=self.idem(csrf)).json()
        s.details = {"eerste_test": {"verbonden": eerste.get("verbonden"), "reason_code": eerste.get("reason_code"),
                                     "tls_modus": eerste.get("tls_modus")},
                     "key_laatste4_alleen": "api_key" not in json.dumps(eerste)}
        s.uitkomst = "geslaagd" if eerste.get("verbonden") is False else "mislukt"  # zelfondertekend: eerst weigeren

        s = r.stap("W5", "TLS-vingerafdruk van de simulator bevestigen en opnieuw testen")
        fout = api.post("/api/v1/truenas/tls/trust", json={"fingerprint_sha256": "00" * 32}, headers=self.idem(csrf))
        goed = api.post("/api/v1/truenas/tls/trust", json={"fingerprint_sha256": fp}, headers=self.idem(csrf))
        test = api.post("/api/v1/truenas/test", headers=self.idem(csrf)).json()
        s.details = {"verkeerde_vingerafdruk": fout.status_code, "vertrouwd": goed.json().get("vertrouwd"),
                     "verbonden": test.get("verbonden"), "nas_versie": test.get("nas_versie"),
                     "key_scope": test.get("key_scope"), "tls_modus": test.get("tls_modus"),
                     "reason_code": test.get("reason_code")}
        s.uitkomst = "geslaagd" if fout.status_code == 409 and test.get("verbonden") is True else "mislukt"
        nas_versie = str(test.get("nas_versie") or "")

        s = r.stap("W6", "Metingen via TrueNAS-readback: D01, D02, D03, D06, D07")
        metingen: dict[str, Any] = {}
        for did in ("D01", "D02", "D03", "D06", "D07"):
            res = api.post(f"/api/v1/decisions/{did}/meting", headers=self.idem(csrf))
            body = res.json()
            metingen[did] = {"http": res.status_code, "meting": (body.get("meting") or {}).get("status"),
                             "reason_code": (body.get("meting") or {}).get("reason_code") or body.get("code"),
                             "besluit": (body.get("besluit") or {}).get("status")}
        if metingen["D07"]["meting"] != "geslaagd":
            # Een NAS zonder apps: de Hub meldt dat als protocol_error ("Geen apps gevonden"). Bevinding vastleggen,
            # dan een bestaande app als simulatorfixture toevoegen (de echte NAS heeft bestaande apps) en opnieuw meten.
            metingen["D07_zonder_apps"] = metingen.pop("D07")
            sim_http.post("/_sim/fixtures", json={"apps": [{
                "app_name": "bestaande-app", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8",
                "values": {"image": {"repository": "ghcr.io/sim/bestaande-app", "tag": "1.0.0"},
                           "ports": [{"bind_mode": "published", "port_number": 38080, "container_port": 8080,
                                      "protocol": "tcp", "host_ips": []}],
                           "storage": [{"type": "host_path", "mount_path": "/config", "read_only": False,
                                        "host_path_config": {"acl_enable": False,
                                                             "path": "/mnt/simpool/apps/bestaande-app"}}]}}]})
            res = api.post("/api/v1/decisions/D07/meting", headers=self.idem(csrf))
            body = res.json()
            metingen["D07_met_bestaande_app"] = {
                "http": res.status_code, "meting": (body.get("meting") or {}).get("status"),
                "reason_code": (body.get("meting") or {}).get("reason_code"),
                "besluit": (body.get("besluit") or {}).get("status")}
        s.details = metingen
        s.uitkomst = "geslaagd" if all(m["http"] == 200 for m in metingen.values()) else "mislukt"

        s = r.stap("W6b", "Gebruiker maakt in de TrueNAS-web-UI een dataset voor de nieuwe app (I01-01-07; hier als "
                          "simulatorfixture — het platform maakt in v1 geen datasets)")
        sim_http.post("/_sim/fixtures", json={"datasets": ["simpool/apps/gotify"]})
        s.uitkomst, s.details = "geslaagd", {"dataset": "simpool/apps/gotify (simulatorpool)"}

        s = r.stap("W7", "Inventaris vernieuwen (alleen lezen) en catalogus bijwerken")
        job = api.post("/api/v1/inventory/refresh", headers=self.idem(csrf)).json()
        job_id = job.get("job_id") or job.get("id")
        wacht("inventaris", lambda: api.get(f"/api/v1/jobs/{job_id}").json().get("status") in ("done", "failed"),
              90, 1)
        overzicht = api.get("/api/v1/inventory").json()
        datasets = api.get("/api/v1/inventory/datasets").json()
        catalogus = api.post("/api/v1/apps/discover", headers=self.idem(csrf))
        apps = api.get("/api/v1/apps").json()
        s.details = {"job": api.get(f"/api/v1/jobs/{job_id}").json().get("status"),
                     "laatste_run": (overzicht.get("laatste_run") or {}).get("status"),
                     "soorten": {str(i.get("pad", "")).rsplit("/", 1)[-1]: i.get("status")
                                 for i in overzicht.get("soorten", [])},
                     "catalogus_discover": catalogus.status_code,
                     "catalogus_apps": sorted(str(a.get("naam") or a.get("name") or a.get("app_naam"))
                                              for a in apps.get("items", []))}
        s.uitkomst = "geslaagd" if catalogus.status_code in (200, 202) else "mislukt"

        s = r.stap("W8", "Gates na de wizard (wat mag nu?)")
        gates = api.get("/api/v1/gates").json()["items"]
        s.details = {g["id"]: g.get("status") for g in gates}
        s.uitkomst = "geslaagd"

        # -- executor koppelen (contract 1.2 §3.9)
        s = r.stap("E1", "Executor koppelen: koppelcode + vingerafdruk uit de Hub op de statuspagina van de executor")
        exe_url = f"http://{self.gw}:{exec_poort}"
        ex = httpx.Client(base_url=exe_url, trust_env=False, timeout=30)
        voor = ex.get("/readyz").json()
        code = api.post("/api/v1/services/pairing-codes", json={"app_id": "ai-server-manager-executor"},
                        headers={"X-CSRF-Token": csrf}).json()
        pagina = ex.get("/")
        m = re.search(r'name="csrf" value="([^"]+)"', pagina.text)
        if not m:
            raise Mislukt("geen koppelformulier op de statuspagina van de executor")
        host = f"{self.gw}:{exec_poort}"
        res = ex.post("/koppelen", data={"csrf": m.group(1), "hub_url": hub_url, "koppelcode": code["koppelcode"],
                                         "vingerafdruk": code["keyset_fingerprint"]},
                      headers={"Origin": f"http://{host}"})
        na = ex.get("/readyz").json()
        nogmaals = ex.get("/")
        s.details = {"voor": {"gekoppeld": voor.get("gekoppeld")}, "http": res.status_code,
                     "na": {"gekoppeld": na.get("gekoppeld")}, "hub_vingerafdruk": code["keyset_fingerprint"],
                     "formulier_na_koppelen": 'name="koppelcode"' in nogmaals.text,
                     "execute_zonder_allowlist": ex.post("/api/v1/execute", json={"action_id": "app.restart"}
                                                         ).status_code}
        s.uitkomst = "geslaagd" if na.get("gekoppeld") is True and not s.details["formulier_na_koppelen"] else "mislukt"

        # -- lifecycle-dienst handmatig als Custom App met HUB_* in het formulier
        s = r.stap("L1", "Lifecycle-dienst (jelly-leon-platform) als Custom App met HUB_URL + HUB_ENROLLMENT_TOKEN + "
                         "HUB_KEY_FINGERPRINT; koppelt zichzelf")
        grant = api.post("/api/v1/services/enrollment-tokens", json={"app_id": "jelly-leon-platform"},
                         headers={"X-CSRF-Token": csrf}).json()
        lc_poort = vrije_poort(self.gw)
        lc = self.run_app("lifecycle", self.args.lifecycle_image, lc_poort, 30440,
                          {"HUB_URL": hub_url, "HUB_ENROLLMENT_TOKEN": grant["enrollment_token"],
                           "HUB_KEY_FINGERPRINT": grant["keyset_fingerprint"]})

        def gekoppeld() -> bool:
            diensten = api.get("/api/v1/services").json()
            rijen = diensten.get("items", diensten)
            return any(d.get("app_id") == "jelly-leon-platform" and d.get("status") == "active" for d in rijen)

        wacht("lifecycle healthy", lambda: container_status(lc) == "healthy", 180, 2)
        wacht("lifecycle gekoppeld", gekoppeld, 90, 2)
        lc_api = httpx.Client(base_url=f"http://{self.gw}:{lc_poort}", trust_env=False, timeout=30)
        processen = self.procescontrole(lc)
        s.details = {"readyz": lc_api.get("/readyz").json().get("status"), "gekoppeld_volgens_hub": True,
                     "processen": processen}
        s.uitkomst = "geslaagd" if processen["in_orde"] else "mislukt"

        # -- plan/preview
        s = r.stap("P1", "Preview: Gotify-manifest → app.create-waarden (standaard Custom App, ix-app) met parameters "
                         "uit wizard/datasetkiezer, conflictcontrole tegen de simulator")
        manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
        # datasetkiezer: een bestaande dataset (geen pool-root) uit de alleen-lezen-inventaris van de Hub
        kandidaten = [str(d.get("mountpoint") or "/mnt/" + str(d["naam"])) for d in datasets.get("items", [])
                      if isinstance(d, dict) and str(d.get("naam", "")).endswith("/gotify")]
        ro_calls = self.sim_rpc(sleutels["readonly"], ("app.used_ports", []),
                                ("app.query", [[["name", "=", "gotify"]]]),
                                ("catalog.get_app_details", ["ix-app", {"train": "stable"}]))
        bezet, bestaand, ix = ro_calls
        hostpoort = next(p["default"] for p in manifest["parameters"] if p["name"] == "host_port")
        params = {"data_path": kandidaten[0].rstrip("/") if kandidaten else None,
                  "timezone": "Etc/UTC", "rpo": "P1D", "rto": "PT4H", "host_port": hostpoort}
        if params["data_path"] is None:
            raise Mislukt("geen dataset in de inventaris van de Hub (datasetkiezer leeg)")
        pfile = self.werk / "params.json"
        pfile.write_text(json.dumps(params), encoding="utf-8")
        ix_versie = str(ix.get("latest_version"))
        out = subprocess.run(  # noqa: S603
            [self.args.validator, "translate", str(MANIFEST), "--params", str(pfile), "--truenas-version",
             nas_versie.removeprefix("TrueNAS-").split("-")[0] or "25.10.8", "--ix-app-version", ix_versie],
            capture_output=True, text=True, check=False)
        preview = json.loads(out.stdout)
        payload = preview.get("payload") or {}
        s.details = {"ready_for_install": preview.get("ready_for_install"), "preview_hash": preview.get("preview_hash"),
                     "catalog_app": payload.get("catalog_app"), "ix_app_versie": ix_versie,
                     "image_tag": (payload.get("values") or {}).get("image", {}).get("tag"),
                     "hostpoort_vrij": hostpoort not in bezet, "appnaam_vrij": not bestaand,
                     "dataset_uit_inventaris": True, "custom_compose": "custom_compose_config" in json.dumps(payload),
                     "bevindingen": [f["code"] for f in preview.get("findings", [])]}
        s.uitkomst = ("geslaagd" if preview.get("ready_for_install") and payload.get("catalog_app") == "ix-app"
                      and not s.details["custom_compose"] and s.details["hostpoort_vrij"] else "mislukt")

        s = r.stap("P2", "Uitvoering via de lifecycle: run vastleggen (discovered); verder plannen bestaat pas in P2")
        tok = api.post("/api/v1/auth/service-jwt", json={"audience": "jelly-leon-platform"},
                       headers={"X-CSRF-Token": csrf}).json()["token"]
        hdr = {"Authorization": f"Bearer {tok}"}
        body = {"app_id": "gotify", "kind": "install", "target_version": "3.1.1",
                "reason": "rooktest P1: preview " + str(preview.get("preview_hash"))}
        run = lc_api.post("/lifecycle/v1/runs", json=body, headers={**hdr, "Idempotency-Key": str(uuid.uuid4())})
        if run.status_code != 201:
            raise Mislukt(f"lifecycle run: {run.status_code} {run.text[:200]}")
        tok2 = api.post("/api/v1/auth/service-jwt", json={"audience": "jelly-leon-platform"},
                        headers={"X-CSRF-Token": csrf}).json()["token"]
        verder = lc_api.post(f"/lifecycle/v1/runs/{run.json()['id']}/transitions",
                             json={"to_state": "inventory", "reason": "rooktest: plannen"},
                             headers={"Authorization": f"Bearer {tok2}", "Idempotency-Key": str(uuid.uuid4()),
                                      "If-Match": run.headers.get("etag", "")})
        s.details = {"run": {"http": run.status_code, "state": run.json().get("state")},
                     "plannen": {"http": verder.status_code, "code": verder.json().get("code")}}
        eerlijk = verder.status_code == 501 and verder.json().get("code") == "capability_unavailable"
        s.uitkomst = "verwacht_niet_beschikbaar" if run.json().get("state") == "discovered" and eerlijk else "mislukt"

        s = r.stap("P3", "Simulatorcontrole van de preview door de testharnas (executor-key van de simulator; géén "
                         "installatie via het platform): app.create → app.query → digest-readback")
        job_id = self.sim_rpc(sleutels["write"], ("app.create", [payload]))[0]
        app_res, query, images = self.sim_rpc(sleutels["write"], ("core.job_wait", [job_id]),
                                              ("app.query", [[["name", "=", "gotify"]]]), ("app.image.query", []))
        verwacht = manifest["image"]["digest"]
        digests = [d for img in images for d in img.get("repo_digests", [])]
        s.details = {"app_state": (query[0] if query else {}).get("state"), "versie_ix_app": app_res.get("version"),
                     "digest_readback_klopt": any(d.endswith("@" + verwacht) for d in digests),
                     "verwachte_digest": verwacht}
        s.uitkomst = "geslaagd" if query and s.details["digest_readback_klopt"] else "mislukt"

        s = r.stap("P4", "Negatieve controle: dezelfde preview met een hostpad dat (nog) geen dataset is, faalt in "
                         "app.create — het plan moet de dataset eerst laten bestaan")
        fout_payload = json.loads(json.dumps(payload))
        fout_payload["app_name"] = "gotify-zonder-dataset"
        fout_payload["values"]["storage"][0]["host_path_config"]["path"] = "/mnt/simpool/apps/bestaat-niet"
        fout_payload["values"]["ports"][0]["port_number"] = vrije_poort(self.gw) % 10000 + 40000
        try:
            fout_job = self.sim_rpc(sleutels["write"], ("app.create", [fout_payload]))[0]
            self.sim_rpc(sleutels["write"], ("core.job_wait", [fout_job]))
            s.uitkomst, s.details = "mislukt", {"verwacht": "EINVAL op het hostpad", "kreeg": "geslaagd"}
        except Mislukt as exc:
            s.uitkomst = "geslaagd" if "does not exist" in str(exc) else "mislukt"
            s.details = {"app_create": "geweigerd", "reden": str(exc)[:200]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Installatie-rooktest zonder YAML (P1)")
    p.add_argument("--hub-image", required=True)
    p.add_argument("--executor-image", required=True)
    p.add_argument("--lifecycle-image", required=True)
    p.add_argument("--validator", default=str(ROOT / ".venv-validator" / "bin" / "jl-manifest"))
    p.add_argument("--hub-commit", default=None)
    p.add_argument("--eigen-commit", default=None)
    p.add_argument("--validator-commit", default=None)
    p.add_argument("--rapport", type=Path, default=None)
    p.add_argument("--houd", action="store_true", help="containers en werkmap laten staan (debuggen)")
    args = p.parse_args(argv)
    rapport = Rooktest(args).uitvoeren()
    tekst = json.dumps(rapport, ensure_ascii=False, indent=2) + "\n"
    if args.rapport:
        args.rapport.write_text(tekst, encoding="utf-8")
    print(tekst)
    return 0 if rapport["uitkomst"] == "geslaagd" else 1


if __name__ == "__main__":
    raise SystemExit(main())
