"""Rooktest van de upstream-images uit ``install/manifests/`` (P1: I07 Beszel, I08 Uptime Kuma, I13 Gotify).

Start elke gepinde upstream-image **op digest** zoals het manifest hem als standaard Custom App beschrijft: één
``docker run`` met de eigen lege map op het mountpad, de containerpoort uit ``network.ports``, de omgevingsvariabelen
uit ``custom_app.env`` en **als 568:568** ("Custom User 568"), zonder extra rechten of beveiligingsopties (het
formulier zet geen ``no-new-privileges`` en geen ``cap_drop``). Daarna per app:

- digest-readback: de lokaal opgehaalde image heeft precies de digest uit het manifest (``RepoDigests``);
- elk proces in de container draait als 568 (``docker top``);
- de healthcheck-route uit het manifest antwoordt, en de kernroutes antwoorden zoals de simulator in
  ``e2e/simulators/<app>`` ze nabootst (zelfde vorm en foutcodes) — een conformiteitscontrole van de simulator;
- herstart zonder dataverlies (gegevens die vóór de herstart zijn gemaakt, bestaan daarna nog);
- Beszel: de agent koppelt in SSH-modus met alleen de **publieke** sleutel van de hub en er komen metingen binnen;
  na een herstart van de agent blijft er precies één systeem (geen dubbele host).

Wat dit **niet** is: een installatie op TrueNAS. Er draait geen TrueNAS, geen ACL van een dataset en geen
``ix-app``-template; poorten zijn alleen op 127.0.0.1 gepubliceerd. Uitkomst: **Sim-getest; NIET GEVALIDEERD (echte
NAS)**. Testaccounts en -wachtwoorden zijn wegwerpwaarden van deze run; het rapport bevat geen tokens of sleutels.

Gebruik::

    python tools/upstream_rooktest.py [--rapport upstream-rooktest.json] [--houd]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "install" / "manifests"
# Upstream-images (install.method custom_app, geen eigen GHCR-image) die deze rooktest dekt. tests/repo controleert dat
# elk zo'n manifest hier staat, zodat een nieuw manifest niet ongemerkt buiten de rooktest valt.
APPS = ("beszel-hub", "beszel-agent", "gotify", "uptime-kuma")
HELPER_APP = "gotify"  # Debian-image met sh/chown: maakt de mappen van 568 en ruimt ze op (geen root op de host)
GEHEIM_RE = re.compile(r"(ssh-ed25519 \S+|eyJ[A-Za-z0-9_-]{10,}|uk\d+_\S+)")


class Mislukt(Exception):
    pass


@dataclass
class Stap:
    id: str
    titel: str
    uitkomst: str = "niet_uitgevoerd"  # geslaagd | mislukt
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class App:
    app_id: str
    image: str  # repository:tag@digest
    digest: str
    mount: str
    container_port: int
    env: dict[str, str]
    health_path: str | None

    @classmethod
    def uit_manifest(cls, app_id: str, extra_env: dict[str, str] | None = None) -> App:
        m = lees_manifest(app_id)
        img = m["image"]
        env: dict[str, str] = {}
        for e in m["custom_app"]["env"]:
            if "value" in e:
                env[str(e["name"])] = str(e["value"])
        env.update(extra_env or {})
        ontbrekend = [str(e["name"]) for e in m["custom_app"]["env"] if str(e["name"]) not in env]
        if ontbrekend:
            raise Mislukt(f"{app_id}: geen waarde voor {ontbrekend}")
        (opslag,) = m["storage"]
        (poort,) = m["network"]["ports"]
        hc = m.get("healthcheck") or {}
        return cls(app_id, f"{img['repository']}:{img['tag']}@{img['digest']}", str(img["digest"]),
                   str(opslag["mount"]), int(poort["container_port"]), env,
                   str(hc["path"]) if hc.get("type") == "http" else None)


def lees_manifest(app_id: str) -> dict[str, Any]:
    data = yaml.safe_load((MANIFEST_DIR / f"{app_id}.manifest.yaml").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise Mislukt(f"{app_id}: manifest is geen mapping")
    return data


def upstream_custom_apps() -> list[str]:
    """Alle manifesten met een upstream-image als standaard Custom App (digest verplicht, geen eigen GHCR-image)."""
    uit = []
    for pad in sorted(MANIFEST_DIR.glob("*.manifest.yaml")):
        m = yaml.safe_load(pad.read_text(encoding="utf-8"))
        if m["install"]["method"] == "custom_app" and not m["image"]["repository"].startswith("ghcr.io/lvos04/"):
            uit.append(str(m["app_id"]))
    return uit


def sh(*args: str, check: bool = True) -> str:
    res = subprocess.run(list(args), capture_output=True, text=True, check=False)  # noqa: S603
    if check and res.returncode != 0:
        raise Mislukt(f"{' '.join(args[:3])}…: {res.stderr.strip()[:400]}")
    return res.stdout


def vrije_poort() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wacht(beschrijving: str, check: Callable[[], bool], timeout: float = 120.0, stap: float = 1.0) -> None:
    eind = time.monotonic() + timeout
    while time.monotonic() < eind:
        try:
            if check():
                return
        except (httpx.HTTPError, OSError, Mislukt, KeyError, ValueError):
            pass
        time.sleep(stap)
    raise Mislukt(f"time-out: {beschrijving}")


def luistert(poort: int) -> bool:
    with socket.create_connection(("127.0.0.1", poort), 2):
        return True


def eis(voorwaarde: bool, melding: str) -> None:
    if not voorwaarde:
        raise Mislukt(melding)


class UpstreamRooktest:
    def __init__(self, houd: bool) -> None:
        self.houd = houd
        self.tag = secrets.token_hex(3)
        self.werk = Path(tempfile.mkdtemp(prefix="jlp-upstream-"))
        self.containers: list[str] = []
        self.stappen: list[Stap] = []
        self.helper = App.uit_manifest(HELPER_APP).image
        self.http = httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False, trust_env=False)
        self.gestart = dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    # ------------------------------------------------------------------ hulpmiddelen
    @contextmanager
    def stap(self, sid: str, titel: str) -> Iterator[Stap]:
        s = Stap(sid, titel)
        self.stappen.append(s)
        try:
            yield s
        except Mislukt as exc:
            s.uitkomst, s.details["fout"] = "mislukt", str(exc)
            print(f"MISLUKT {sid}: {exc}", file=sys.stderr)
        except (httpx.HTTPError, OSError, KeyError, ValueError) as exc:
            s.uitkomst, s.details["fout"] = "mislukt", f"{type(exc).__name__}: {exc}"
            print(f"MISLUKT {sid}: {type(exc).__name__}: {exc}", file=sys.stderr)
        else:
            s.uitkomst = "geslaagd"
            print(f"ok      {sid}: {titel}")

    def helper_sh(self, script: str) -> str:
        return sh("docker", "run", "--rm", "--network", "none", "--user", "0", "--entrypoint", "sh",
                  "-v", f"{self.werk}:/w", self.helper, "-c", script)

    def lege_map(self, naam: str) -> Path:
        """Lege map van 568:568, zoals een dataset met 'Dataset Preset: Apps' voor de gebruiker apps (568)."""
        pad = self.werk / naam
        pad.mkdir()
        self.helper_sh(f"chown 568:568 /w/{naam} && chmod 0750 /w/{naam}")
        return pad

    def start(self, app: App, map_: Path, hostpoort: int) -> str:
        cname = f"jlp-upstream-{app.app_id}-{self.tag}"
        cmd = ["docker", "run", "-d", "--name", cname, "--user", "568:568", "-p",
               f"127.0.0.1:{hostpoort}:{app.container_port}", "-v", f"{map_}:{app.mount}", "-e", "TZ=UTC"]
        for k, v in app.env.items():
            cmd += ["-e", f"{k}={v}"]
        sh(*cmd, app.image)
        self.containers.append(cname)
        return cname

    def digest_readback(self, app: App) -> dict[str, Any]:
        digests = json.loads(sh("docker", "image", "inspect", "--format", "{{json .RepoDigests}}", app.image))
        repo = app.image.split("@", 1)[0].rsplit(":", 1)[0]
        eis(f"{repo}@{app.digest}" in digests, f"{app.app_id}: digest {app.digest} niet in {digests}")
        return {"digest": app.digest, "readback_klopt": True}

    @staticmethod
    def alleen_568(cname: str) -> list[str]:
        regels = sh("docker", "top", cname, "-eo", "uid,pid,comm").strip().splitlines()[1:]
        uids = sorted({r.split()[0] for r in regels})
        eis(uids == ["568"], f"{cname}: processen draaien als {uids}, verwacht alleen 568")
        return uids

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self.http.get(url, **kw)

    def herstart(self, cname: str, klaar: Callable[[], bool], timeout: float = 120.0) -> None:
        sh("docker", "restart", cname)
        wacht(f"{cname} na herstart", klaar, timeout)

    # ------------------------------------------------------------------ apps
    def beszel(self) -> None:
        hub = App.uit_manifest("beszel-hub")
        pb = vrije_poort()
        base = f"http://127.0.0.1:{pb}"
        email, wachtwoord = "rooktest@sim.invalid", secrets.token_urlsafe(18)
        token, hubsleutel = "", ""
        with self.stap("B1", "Beszel hub: als 568 met lege map; health, first-run, eerste gebruiker, lijst zonder "
                             "token leeg (zoals e2e/simulators/beszel)") as s:
            s.details.update(self.digest_readback(hub))
            cname = self.start(hub, self.lege_map("beszel-hub"), pb)
            wacht("beszel /api/health", lambda: self.get(base + (hub.health_path or "/api/health")).status_code == 200)
            r = self.get(base + "/api/health")
            eis(r.json() == {"message": "API is healthy.", "code": 200, "data": {}}, f"health: {r.text[:200]}")
            eis(self.get(base + "/api/beszel/first-run").json() == {"firstRun": True}, "first-run hoort true")
            leeg = self.get(base + "/api/collections/systems/records")
            eis(leeg.status_code == 200 and leeg.json()["items"] == [], "lijst zonder token hoort 200 en leeg")
            r = self.http.post(base + "/api/beszel/create-user", json={"email": email, "password": wachtwoord})
            eis(r.json() == {"msg": "User created"}, f"create-user: {r.text[:200]}")
            r2 = self.http.post(base + "/api/beszel/create-user", json={"email": "x@sim.invalid", "password": "y" * 12})
            s.details["tweede_create_user"] = {"http": r2.status_code, "body": r2.json()}
            eis(r2.status_code == 403, f"tweede create-user hoort 403, kreeg {r2.status_code}")
            eis(self.get(base + "/api/beszel/first-run").json() == {"firstRun": False}, "first-run hoort false")
            s.details["processen_uid"] = self.alleen_568(cname)
        with self.stap("B2", "Beszel hub: aanmelden, publieke sleutel (/api/beszel/info), herstart zonder "
                             "dataverlies") as s:
            r = self.http.post(base + "/api/collections/users/auth-with-password",
                               json={"identity": email, "password": wachtwoord})
            token = str(r.json()["token"])
            info = self.get(base + "/api/beszel/info", headers={"Authorization": token}).json()
            eis(set(info) == {"key", "v", "cu"} and info["v"] == hub.image.split(":")[1].split("@")[0],
                f"info-vorm: {sorted(info)}")
            eis(str(info["key"]).startswith("ssh-ed25519 "), "publieke sleutel hoort ssh-ed25519")
            fout = self.http.post(base + "/api/collections/users/auth-with-password",
                                  json={"identity": email, "password": "fout"})
            eis(fout.status_code == 400 and fout.json()["message"] == "Failed to authenticate.",
                f"fout wachtwoord: {fout.status_code}")
            self.herstart(f"jlp-upstream-beszel-hub-{self.tag}",
                          lambda: self.get(base + "/api/health").status_code == 200)
            eis(self.get(base + "/api/beszel/first-run").json() == {"firstRun": False}, "gebruiker weg na herstart")
            s.details["na_herstart"] = "gebruiker bestaat nog (firstRun false)"
            hubsleutel = str(info["key"])
        if any(st.uitkomst != "geslaagd" for st in self.stappen[-2:]):
            return
        agent = App.uit_manifest("beszel-agent", {"KEY": hubsleutel})
        with self.stap("B3", "Beszel agent: als 568, SSH-modus met alleen de publieke hubsleutel; hub haalt metingen "
                             "op; herstart van de agent geeft geen dubbele host") as s:
            s.details.update(self.digest_readback(agent))
            pa = vrije_poort()
            cname = self.start(agent, self.lege_map("beszel-agent"), pa)
            wacht("agent luistert", lambda: luistert(pa), 60)
            with socket.create_connection(("127.0.0.1", pa), 5) as sock:
                banner = sock.recv(64).decode(errors="replace").strip()
            eis(banner.startswith("SSH-2.0-beszel_"), f"geen SSH-banner van de agent: {banner!r}")
            s.details["ssh_banner"] = banner
            s.details["processen_uid"] = self.alleen_568(cname)
            ip = sh("docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
                    cname).strip()
            auth = {"Authorization": token}
            me = self.http.post(base + "/api/collections/users/auth-refresh", headers=auth).json()["record"]["id"]
            r = self.http.post(base + "/api/collections/systems/records", headers=auth,
                               json={"name": "rooktest-agent", "host": ip, "port": str(agent.container_port),
                                     "users": [me]})
            eis(r.status_code == 200, f"systeem toevoegen: {r.status_code}")

            def systemen() -> list[dict[str, Any]]:
                items = self.get(base + "/api/collections/systems/records", headers=auth).json()["items"]
                return list(items)

            def stats() -> int:
                return int(self.get(base + "/api/collections/system_stats/records?perPage=1&sort=-created",
                                    headers=auth).json()["totalItems"])

            wacht("systeem up", lambda: systemen()[0]["status"] == "up", 90, 3)
            wacht("eerste meting", lambda: stats() >= 1, 90, 3)
            sys0 = systemen()[0]
            s.details["systeem"] = {"status": sys0["status"], "info_velden": sorted(sys0["info"]),
                                    "versie_agent": sys0["info"].get("v")}
            laatste = self.get(base + "/api/collections/system_stats/records?perPage=1&sort=-created",
                               headers=auth).json()["items"][0]
            s.details["meting"] = {"type": laatste["type"], "velden": sorted(laatste["stats"]),
                                   "arc_zichtbaar": "mz" in laatste["stats"]}
            agentmap = self.helper_sh("ls -A /w/beszel-agent").split()
            s.details["agentmap_na_start"] = agentmap  # SSH-modus: geen vingerafdrukbestand (alleen WebSocket-modus)
            voor, t0 = stats(), time.monotonic()
            self.herstart(cname, lambda: luistert(pa), 60)
            try:  # de hub belt de agent zelf weer op (SSH-modus); gemeten: binnen één tot enkele minuten
                wacht("nieuwe meting na herstart agent", lambda: stats() > voor and systemen()[0]["status"] == "up",
                      300, 5)
            except Mislukt:
                s.details["diagnose"] = {"systemen": [(x["name"], x["status"]) for x in systemen()],
                                         "metingen_voor": voor, "metingen_na": stats()}
                raise
            s.details["herverbonden_na_s"] = round(time.monotonic() - t0)
            na = systemen()
            eis(len(na) == 1 and na[0]["status"] == "up", f"na herstart: {[(x['name'], x['status']) for x in na]}")
            s.details["na_herstart"] = {"systemen": len(na), "status": na[0]["status"]}

    def gotify(self) -> None:
        app = App.uit_manifest("gotify")
        p = vrije_poort()
        base = f"http://127.0.0.1:{p}"
        with self.stap("G1", "Gotify: als 568 met lege map; health, versie, eerste-gebruik-venster admin/admin, "
                             "applicatietoken, foutvormen (zoals e2e/simulators/gotify), herstart zonder dataverlies") \
                as s:
            s.details.update(self.digest_readback(app))
            cname = self.start(app, self.lege_map("gotify"), p)
            wacht("gotify /health", lambda: self.get(base + (app.health_path or "/health")).status_code == 200)
            eis(self.get(base + "/health").json() == {"health": "green", "database": "green"}, "health-vorm")
            versie = self.get(base + "/version").json()
            eis(set(versie) == {"version", "commit", "buildDate"} and versie["version"] == "3.1.1", f"versie {versie}")
            standaard = self.get(base + "/current/user", auth=("admin", "admin")).status_code
            s.details["admin_admin_na_installatie"] = standaard
            eis(standaard == 200, "het eerste-gebruik-venster (admin/admin) hoort te bestaan; README beschrijft het")
            r = self.http.post(base + "/application", auth=("admin", "admin"), json={"name": "hub",
                                                                                      "description": "rooktest"})
            tok = str(r.json()["token"])
            msg = self.http.post(base + "/message", headers={"X-Gotify-Key": tok},
                                 json={"message": "rooktest", "priority": 5}).json()
            eis({"id", "appid", "message", "title", "priority", "date"} <= set(msg) and msg["title"] == "hub",
                f"bericht-vorm {sorted(msg)}")
            zonder = self.http.post(base + "/message", json={"message": "x"})
            eis(zonder.status_code == 401 and zonder.json() == {
                "error": "Unauthorized", "errorCode": 401,
                "errorDescription": "you need to provide a valid access token or user credentials to access this api"},
                f"zonder token: {zonder.status_code}")
            leeg = self.http.post(base + "/message", headers={"X-Gotify-Key": tok}, json={"title": "x"})
            eis(leeg.status_code == 400 and leeg.json()["errorDescription"] == "Field 'message' is required",
                f"leeg bericht: {leeg.status_code}")
            s.details["processen_uid"] = self.alleen_568(cname)
            self.herstart(cname, lambda: self.get(base + "/health").status_code == 200)
            apps = [a["name"] for a in self.get(base + "/application", auth=("admin", "admin")).json()]
            berichten = self.get(base + "/message", auth=("admin", "admin")).json()["messages"]
            eis(apps == ["hub"] and len(berichten) == 1, f"na herstart: apps {apps}, berichten {len(berichten)}")
            s.details["na_herstart"] = {"applicaties": apps, "berichten": len(berichten)}

    def uptime_kuma(self) -> None:
        app = App.uit_manifest("uptime-kuma")
        p = vrije_poort()
        base = f"http://127.0.0.1:{p}"
        with self.stap("U1", "Uptime Kuma: als 568 met lege map; entry-page, /metrics alleen met aanmelding, "
                             "statuspagina- en pushfouten (zoals e2e/simulators/uptime_kuma), herstart zonder "
                             "dataverlies") as s:
            s.details.update(self.digest_readback(app))
            cname = self.start(app, self.lege_map("uptime-kuma"), p)
            pad = app.health_path or "/api/entry-page"
            wacht("uptime-kuma entry-page", lambda: self.get(base + pad).status_code == 200, 240, 2)
            eis(self.get(base + pad).json() == {"type": "entryPage", "entryPage": None}, "entry-page-vorm")
            m = self.get(base + "/metrics")
            eis(m.status_code == 401 and m.headers.get("www-authenticate", "").startswith("Basic"), "metrics open?")
            sp = self.get(base + "/api/status-page/bestaat-niet")
            eis(sp.status_code == 404 and sp.json() == {"status": "fail", "msg": "Status Page Not Found"},
                f"statuspagina: {sp.status_code}")
            pu = self.get(base + "/api/push/bestaat-niet")
            eis(pu.status_code == 404 and pu.json() == {"ok": False, "msg": "Monitor not found or not active."},
                f"push: {pu.status_code}")
            pi = self.get(base + "/api/push/bestaat-niet", params={"ping": "-5"})
            eis(pi.status_code == 404 and pi.json()["msg"].startswith("Invalid ping value."), f"ping: {pi.text}")
            s.details["processen_uid"] = self.alleen_568(cname)
            voor = set(self.helper_sh("ls -A /w/uptime-kuma").split())
            eis("kuma.db" in voor, f"geen kuma.db in de map: {sorted(voor)}")
            self.herstart(cname, lambda: self.get(base + pad).status_code == 200, 240)
            na = set(self.helper_sh("ls -A /w/uptime-kuma").split())
            eis("kuma.db" in na, "kuma.db weg na herstart")
            s.details["datamap"] = sorted(na)

    # ------------------------------------------------------------------ verloop
    def opruimen(self) -> None:
        self.http.close()
        if self.houd:
            print(f"containers blijven staan: {self.containers}; werkmap {self.werk}")
            return
        for c in self.containers:
            sh("docker", "rm", "-f", c, check=False)
        # bestanden van 568 zonder root op de host weghalen: via een wegwerpcontainer (zoals tests/e2e)
        with suppress(Mislukt):
            self.helper_sh("rm -rf /w/* /w/.[!.]* 2>/dev/null; true")
        shutil.rmtree(self.werk, ignore_errors=True)

    def draai(self) -> dict[str, Any]:
        for app_id in APPS:
            sh("docker", "pull", "-q", App.uit_manifest(app_id, {"KEY": "x"}).image)
        try:
            self.beszel()
            self.gotify()
            self.uptime_kuma()
        finally:
            self.opruimen()
        geslaagd = bool(self.stappen) and all(s.uitkomst == "geslaagd" for s in self.stappen)
        rapport = {
            "soort": "rooktest upstream-images als standaard Custom App (P1: I07, I08, I13)",
            "gestart": self.gestart,
            "klaar": dt.datetime.now(dt.UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "validatie": "Sim-getest; NIET GEVALIDEERD (echte NAS)",
            "omgeving": "Docker (geen TrueNAS): --user 568:568, eigen lege map, poort op 127.0.0.1, zonder "
                        "--cap-drop of no-new-privileges",
            "images": {a: App.uit_manifest(a, {"KEY": "x"}).image for a in APPS},
            "uitkomst": "geslaagd" if geslaagd else "mislukt",
            "stappen": [s.__dict__ for s in self.stappen],
        }
        tekst = json.dumps(rapport["stappen"], ensure_ascii=False)
        if GEHEIM_RE.search(tekst):  # verdediging in de diepte: nooit een token of sleutel in het rapport
            raise RuntimeError("rapport bevat iets dat op een geheim lijkt; niet opgeslagen")
        return rapport


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0] if __doc__ else None)
    p.add_argument("--rapport", type=Path, default=None)
    p.add_argument("--houd", action="store_true", help="containers en werkmap laten staan (debuggen)")
    args = p.parse_args(argv)
    ontbrekend = sorted(set(upstream_custom_apps()) - set(APPS))
    if ontbrekend:
        print(f"niet gedekt door deze rooktest: {ontbrekend}", file=sys.stderr)
        return 1
    rapport = UpstreamRooktest(args.houd).draai()
    tekst = json.dumps(rapport, ensure_ascii=False, indent=2) + "\n"
    if args.rapport:
        args.rapport.write_text(tekst, encoding="utf-8")
    print(f"uitkomst: {rapport['uitkomst']}")
    return 0 if rapport["uitkomst"] == "geslaagd" else 1


if __name__ == "__main__":
    raise SystemExit(main())
