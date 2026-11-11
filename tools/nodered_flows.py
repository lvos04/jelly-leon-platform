"""Node-RED (I10): instellingen, flowbundels, controle, import, export en herstel — zonder shell in de container.

Wat hier staat:

- ``render``     maakt uit ``install/node-red/settings-overrides.json`` de waarde van de omgevingsvariabele ``FLOWS``
                 die het manifest aan de container meegeeft (``--check``: het manifest moet precies die waarde hebben);
- ``lint``       controleert de flowbundels in ``install/node-red/flows/``: alleen toegestane nodes (geen exec,
                 function, bestand of contrib), alleen verkeer naar vaste Hub-routes, geen geheimen of adressen in Git,
                 een flow die een actie aanvraagt staat standaard uit;
- ``lock``       legt per bundel versie en sha256 vast in ``install/node-red/flows.lock.json`` (``--check``);
- ``importeer``  zet bundels via de Admin-API in een draaiende Node-RED (beheerder vereist), geeft het service-token
                 van de Hub alleen als versleuteld node-credential mee en leest terug of de checksums kloppen. Een
                 oudere versie herstellen is hetzelfde commando met het oudere bestand (``--bundel naam=pad``);
- ``exporteer``  schrijft de flows uit Node-RED per tabblad naar bestanden (Git-export) en vergelijkt met het lock.

Geheimen komen nooit op de opdrachtregel en nooit in Git: het wachtwoord van de beheerder en het service-token worden
uit omgevingsvariabelen gelezen (``NODERED_BEHEER_WACHTWOORD``, ``NODERED_HUB_TOKEN``). De Hub-URL wordt bij de import
gezet als globale omgevingsvariabele ``HUB_URL`` van Node-RED; in Git staat nooit een adres.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
NR_DIR = ROOT / "install" / "node-red"
OVERRIDES = NR_DIR / "settings-overrides.json"
FLOWS_DIR = NR_DIR / "flows"
LOCK = NR_DIR / "flows.lock.json"
MANIFEST = ROOT / "install" / "manifests" / "node-red.manifest.yaml"

GLOBALE_NODE = "jlp-global-config"
TOEGESTANE_TYPES = frozenset({"tab", "comment", "inject", "change", "switch", "http request", "debug", "catch"})
# Node-RED zoekt $env('HUB_URL') eerst in de env van de groep en het tabblad, pas daarna in de globale instellingen. Een
# bundel mag dus nergens een eigen env of groep hebben, en een tabblad alleen deze velden (review P2, R-VEILIG-1).
TAB_VELDEN = frozenset({"id", "type", "label", "disabled", "info"})
VERBODEN_NODE_VELDEN = ("env", "g")
# De enige adressen waar een flow heen mag: vaste routes van de Hub. Sleutel = de JSONata voor msg.url.
HUB_ROUTES: dict[str, tuple[str, str]] = {
    "$env('HUB_URL') & '/api/v1/apps?limit=200'": ("GET", "/api/v1/apps"),
    "$env('HUB_URL') & '/api/v1/events/inbox'": ("POST", "/api/v1/events/inbox"),
    "$env('HUB_URL') & '/api/v1/actions/proposals'": ("POST", "/api/v1/actions/proposals"),
    "$env('HUB_URL') & '/api/v1/monitoring/heartbeat'": ("POST", "/api/v1/monitoring/heartbeat"),
}
ACTIE_ROUTE = "/api/v1/actions/proposals"
VERBODEN_MSG_VELDEN = frozenset({"method", "headers", "cookies", "requestTimeout", "rejectUnauthorized",
                                 "followRedirects", "responseUrl", "redirectList"})
ID_RE = re.compile(r"^jlp-[a-z0-9-]{1,60}$")
VERSIE_RE = re.compile(r"^versie (\d+\.\d+\.\d+)\n")
EVENT_RE = re.compile(r'"(flow\.[a-z0-9_]+\.[a-z0-9_]+)"')
GEHEIM_RE = re.compile(r"(svc_[A-Za-z0-9_-]{20,}|enr_[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}|[Bb]earer\s+\S+"
                       r"|-----BEGIN|\$2[aby]\$\d\d\$)")
ADRES_RE = re.compile(r"([a-z][a-z0-9+.-]*://|\b\d{1,3}(\.\d{1,3}){3}\b)")
ONVEILIG_IN_FLOWS_ENV = re.compile(r"[\s$*?\\`'~<>|;&()]")

Node = dict[str, Any]


class Fout(Exception):
    """Een fout die de gebruiker moet lezen (Nederlands), geen programmeerfout."""


# ---------------------------------------------------------------------- instellingen → FLOWS
def lees_overrides(pad: Path = OVERRIDES) -> dict[str, Any]:
    data = json.loads(pad.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("overrides"), dict) \
            or not isinstance(data.get("flows_bestand"), str):
        raise Fout(f"{pad.name}: verwacht een object met 'flows_bestand' en 'overrides'")
    return data


def render_flows_env(data: dict[str, Any]) -> str:
    """De waarde van ``FLOWS``: de entrypoint van de image geeft ``$FLOWS`` ongequoot door aan Node-RED, dus elk
    woord wordt een eigen argument (``flows.json -D sleutel=waarde …``). Daarom: compacte JSON, geen spaties, geen
    tekens die de shell uitbreidt."""
    delen = [str(data["flows_bestand"])]
    for sleutel, waarde in data["overrides"].items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9.]*", sleutel):
            raise Fout(f"override '{sleutel}': alleen letters, cijfers en punten")
        delen += ["-D", f"{sleutel}={json.dumps(waarde, separators=(',', ':'), ensure_ascii=True)}"]
    for deel in delen:
        if ONVEILIG_IN_FLOWS_ENV.search(deel):
            raise Fout(f"'{deel[:40]}…' bevat een spatie of een teken dat de shell uitbreidt")
    return " ".join(delen)


def manifest_flows_env(pad: Path = MANIFEST) -> str | None:
    manifest = yaml.safe_load(pad.read_text(encoding="utf-8"))
    for env in manifest["custom_app"]["env"]:
        if env["name"] == "FLOWS":
            return str(env["value"])
    return None


# ---------------------------------------------------------------------- bundels: checksum en controle
def lees_bundel(pad: Path) -> list[Node]:
    nodes = json.loads(pad.read_text(encoding="utf-8"))
    if not isinstance(nodes, list) or not all(isinstance(n, dict) for n in nodes):
        raise Fout(f"{pad.name}: een bundel is een JSON-lijst van nodes (Node-RED-export)")
    return nodes


def checksum(nodes: list[Node]) -> str:
    """sha256 over de canonieke JSON van de nodes (op id gesorteerd). Of het tabblad aan of uit staat telt niet mee:
    dat is een bedieningskeuze, geen andere versie. Credentials horen nooit in een bundel en tellen dus ook niet."""
    schoon = []
    for node in sorted(nodes, key=lambda n: str(n.get("id"))):
        kopie = {k: v for k, v in node.items() if k != "credentials"}
        if kopie.get("type") == "tab":
            kopie.pop("disabled", None)
        schoon.append(kopie)
    tekst = json.dumps(schoon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(tekst.encode("utf-8")).hexdigest()


def _teksten(waarde: Any) -> list[str]:
    if isinstance(waarde, str):
        return [waarde]
    if isinstance(waarde, dict):
        return [t for k, v in waarde.items() for t in [str(k), *_teksten(v)]]
    if isinstance(waarde, list):
        return [t for v in waarde for t in _teksten(v)]
    return []


def tab_van(nodes: list[Node]) -> Node | None:
    tabs = [n for n in nodes if n.get("type") == "tab"]
    return tabs[0] if len(tabs) == 1 else None


def routes_van(nodes: list[Node]) -> list[str]:
    uit = set()
    for node in nodes:
        if node.get("type") == "change":
            for regel in node.get("rules") or []:
                if isinstance(regel, dict) and regel.get("p") == "url" and regel.get("to") in HUB_ROUTES:
                    methode, pad = HUB_ROUTES[str(regel["to"])]
                    uit.add(f"{methode} {pad}")
    return sorted(uit)


def lint(nodes: list[Node]) -> list[str]:
    """Bevindingen (leeg = goed). Elke regel begint met een vaste code, daarna Nederlandse uitleg."""
    uit: list[str] = []
    ids = [n.get("id") for n in nodes]
    for nid in ids:
        if not isinstance(nid, str) or not ID_RE.fullmatch(nid):
            uit.append(f"id_ongeldig: '{nid}' (verwacht jlp-…, kleine letters, cijfers, streepjes)")
    if len(set(map(str, ids))) != len(ids):
        uit.append("id_dubbel: elke node heeft een eigen id")
    tab = tab_van(nodes)
    if tab is None:
        return [*uit, "tab_ontbreekt: een bundel is precies één tabblad met zijn nodes"]
    if not str(tab.get("label", "")).startswith("JLP · "):
        uit.append("tab_label: het label begint met 'JLP · '")
    if not VERSIE_RE.match(str(tab.get("info", ""))):
        uit.append("tab_versie: de beschrijving begint met een regel 'versie x.y.z'")
    for veld in sorted(set(tab) - TAB_VELDEN):
        uit.append(f"veld_niet_toegestaan: tabblad {tab.get('id')} heeft '{veld}' (alleen "
                   f"{', '.join(sorted(TAB_VELDEN))}; een env op het tabblad gaat vóór de globale HUB_URL)")
    per_id = {str(n.get("id")): n for n in nodes}
    naar: dict[str, list[Node]] = {}
    for node in nodes:
        soort = str(node.get("type"))
        if soort not in TOEGESTANE_TYPES:
            uit.append(f"nodetype_niet_toegestaan: {node.get('id')} is '{soort}' (geen exec, function, bestand, "
                       "http in of contrib-nodes)")
        if node is not tab and node.get("z") != tab.get("id"):
            uit.append(f"buiten_tabblad: {node.get('id')} hoort niet bij tabblad {tab.get('id')}")
        if "credentials" in node:
            uit.append(f"credentials_in_bundel: {node.get('id')} — inloggegevens komen pas bij de import")
        for veld in VERBODEN_NODE_VELDEN:
            if node is not tab and veld in node:
                uit.append(f"veld_niet_toegestaan: {node.get('id')} heeft '{veld}' (geen eigen env of groep: "
                           "HUB_URL komt alleen uit de globale instellingen)")
        for uitgang in node.get("wires") or []:
            for doel in uitgang:
                if str(doel) not in per_id:
                    uit.append(f"draad_naar_buiten: {node.get('id')} → {doel} bestaat niet in deze bundel")
                naar.setdefault(str(doel), []).append(node)
    for tekst in _teksten(nodes):
        if GEHEIM_RE.search(tekst):
            uit.append("geheim_in_bundel: een waarde lijkt op een token, sleutel of wachtwoordhash")
        if ADRES_RE.search(tekst):
            uit.append(f"adres_in_bundel: '{tekst[:40]}' — adressen komen alleen uit $env('HUB_URL')")
        if "$globalContext" in tekst or re.search(r"\$env\((?!'HUB_URL'\))", tekst):
            uit.append("jsonata_bron: alleen $env('HUB_URL') en de context van de eigen flow zijn toegestaan")
    for node in nodes:
        ntype = node.get("type")
        if ntype == "change":
            uit += _lint_change(node)
        elif ntype == "inject":
            if [p.get("p") for p in node.get("props") or [] if isinstance(p, dict)] not in (["payload"], [
                    "payload", "topic"]):
                uit.append(f"inject_velden: {node.get('id')} mag alleen payload (en topic) zetten")
        elif ntype == "debug":
            if node.get("console") or node.get("tostatus"):
                uit.append(f"debug_naar_log: {node.get('id')} schrijft alleen naar de zijbalk, niet naar het log")
        elif ntype == "http request":
            uit += _lint_http(node, naar.get(str(node.get("id")), []))
    if ACTIE_ROUTE in " ".join(routes_van(nodes)) and tab.get("disabled") is not True:
        uit.append("actieflow_moet_uit_staan: een flow die een actie aanvraagt staat in Git uitgeschakeld")
    return uit


def _lint_change(node: Node) -> list[str]:
    uit = []
    for regel in node.get("rules") or []:
        veld, soort, bak = str(regel.get("p", "")), regel.get("t"), regel.get("pt")
        wortel = re.split(r"[.\[]", veld, maxsplit=1)[0]
        if soort not in ("set", "delete") or bak not in ("msg", "flow"):
            uit.append(f"change_regel: {node.get('id')} — alleen set/delete op msg of de eigen flowcontext")
        elif bak == "msg" and wortel == "url":
            if soort != "set" or regel.get("tot") != "jsonata" or regel.get("to") not in HUB_ROUTES or veld != "url":
                uit.append(f"url_niet_toegestaan: {node.get('id')} zet msg.url op iets anders dan een vaste "
                           "Hub-route")
        elif bak == "msg" and wortel in VERBODEN_MSG_VELDEN and soort != "delete":
            uit.append(f"verzoek_overschreven: {node.get('id')} zet msg.{wortel} (methode en koppen liggen vast)")
    return uit


def _lint_http(node: Node, voorgangers: list[Node]) -> list[str]:
    uit = []
    nid = node.get("id")
    vast = {"url": "", "tls": "", "proxy": "", "persist": False, "authType": "bearer", "headers": [],
            "paytoqs": "ignore", "ret": "obj"}
    for sleutel, waarde in vast.items():
        if node.get(sleutel) != waarde:
            uit.append(f"http_instelling: {nid}.{sleutel} moet {waarde!r} zijn")
    if node.get("insecureHTTPParser"):
        uit.append(f"http_instelling: {nid}.insecureHTTPParser moet uit staan")
    methode = node.get("method")
    if methode not in ("GET", "POST"):
        uit.append(f"http_methode: {nid} — alleen een vaste GET of POST")
    if not voorgangers:
        uit.append(f"http_zonder_route: {nid} heeft geen voorganger die de Hub-route zet")
    for voor in voorgangers:
        routes = [str(r.get("to")) for r in voor.get("rules") or [] if isinstance(r, dict)
                  and r.get("p") == "url" and r.get("pt") == "msg" and r.get("t") == "set"]
        if voor.get("type") != "change" or len(routes) != 1 or routes[0] not in HUB_ROUTES:
            uit.append(f"http_zonder_route: {nid} wordt gevoed door {voor.get('id')} zonder vaste Hub-route")
        elif HUB_ROUTES[routes[0]][0] != methode:
            uit.append(f"http_methode: {nid} is {methode}, de route van {voor.get('id')} vraagt "
                       f"{HUB_ROUTES[routes[0]][0]}")
    return uit


def bundels_in_git() -> dict[str, list[Node]]:
    return {pad.stem: lees_bundel(pad) for pad in sorted(FLOWS_DIR.glob("*.json"))}


def lock_inhoud(bundels: dict[str, list[Node]]) -> dict[str, Any]:
    uit: dict[str, Any] = {}
    for naam, nodes in bundels.items():
        tab = tab_van(nodes) or {}
        versie = VERSIE_RE.match(str(tab.get("info", "")))
        routes = routes_van(nodes)
        events = sorted({e for tekst in _teksten(nodes) for e in EVENT_RE.findall(tekst)})
        uit[naam] = {
            "bestand": f"flows/{naam}.json", "tab": tab.get("id"), "label": tab.get("label"),
            "versie": versie.group(1) if versie else None, "sha256": checksum(nodes),
            "soort": "actie_aanvragen" if any(ACTIE_ROUTE in r for r in routes) else "alleen_lezen_en_melden",
            "standaard_aan": tab.get("disabled") is not True, "routes": routes, "event_types": events,
        }
    return {"schema": 1, "bundels": uit}


def lees_lock() -> dict[str, Any]:
    data = json.loads(LOCK.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("bundels"), dict):
        raise Fout("flows.lock.json: onleesbaar")
    return data


# ---------------------------------------------------------------------- Admin-API van Node-RED
class NodeRed:
    """Dunne client voor de Admin-API (v2). Zonder ``login`` alleen lezen (de editor is voor anoniem alleen-lezen)."""

    def __init__(self, url: str, http: httpx.Client | None = None) -> None:
        self.url = url.rstrip("/")
        self.http = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0), follow_redirects=False,
                                         trust_env=False)
        self.kop: dict[str, str] = {"Node-RED-API-Version": "v2"}

    def login(self, gebruiker: str, wachtwoord: str) -> None:
        r = self.http.post(f"{self.url}/auth/token", data={
            "client_id": "node-red-admin", "grant_type": "password", "scope": "*", "username": gebruiker,
            "password": wachtwoord})
        if r.status_code != 200:
            raise Fout(f"Inloggen bij Node-RED mislukt (HTTP {r.status_code}).")
        self.kop["Authorization"] = f"Bearer {r.json()['access_token']}"

    def flows(self) -> tuple[str, list[Node]]:
        r = self.http.get(f"{self.url}/flows", headers=self.kop)
        if r.status_code != 200:
            raise Fout(f"Flows lezen mislukt (HTTP {r.status_code}).")
        body = r.json()
        return str(body["rev"]), list(body["flows"])

    def zet_flows(self, rev: str, flows: list[Node]) -> str:
        r = self.http.post(f"{self.url}/flows", headers={**self.kop, "Node-RED-Deployment-Type": "full"},
                           json={"rev": rev, "flows": flows})
        if r.status_code == 409:
            raise Fout("De flows zijn intussen door iemand anders gewijzigd; lees opnieuw en probeer het nog eens.")
        if r.status_code != 200:
            raise Fout(f"Flows schrijven geweigerd (HTTP {r.status_code}).")
        return str(r.json().get("rev", ""))

    def inject(self, node_id: str, props: list[dict[str, Any]] | None = None) -> int:
        body = {"__user_inject_props__": props} if props is not None else None
        return self.http.post(f"{self.url}/inject/{node_id}", headers=self.kop, json=body).status_code


def controleer_hub_url(hub_url: str) -> str:
    delen = urlsplit(hub_url)
    if delen.scheme not in ("http", "https") or not delen.hostname or delen.username or delen.password \
            or delen.path not in ("", "/") or delen.query or delen.fragment:
        raise Fout("De Hub-URL is alleen schema, host en poort (bijvoorbeeld de base-URL van de Hub), zonder pad.")
    return hub_url.rstrip("/")


def per_tab(flows: list[Node]) -> dict[str, list[Node]]:
    uit: dict[str, list[Node]] = {str(n["id"]): [n] for n in flows if n.get("type") == "tab"}
    for node in flows:
        if node.get("type") != "tab" and str(node.get("z")) in uit:
            uit[str(node["z"])].append(node)
    return uit


def importeer(nr: NodeRed, bundels: dict[str, list[Node]], *, hub_url: str, token: str,
              inschakelen: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Zet de bundels in Node-RED. Elke bundel wordt eerst gecontroleerd; één bevinding en er verandert niets."""
    hub_url = controleer_hub_url(hub_url)
    if not token:
        raise Fout("Geen service-token (omgevingsvariabele leeg).")
    for naam, nodes in bundels.items():
        bevindingen = lint(nodes)
        if bevindingen:
            raise Fout(f"Bundel '{naam}' is geweigerd: " + "; ".join(bevindingen))
    rev, huidig = nr.flows()
    eigen_tabs = {str((tab_van(n) or {})["id"]) for n in bundels.values()}
    blijft = [n for n in huidig if str(n.get("id")) not in eigen_tabs and str(n.get("z")) not in eigen_tabs]
    globaal = next((n for n in blijft if n.get("type") == "global-config"), None)
    if globaal is None:
        globaal = {"id": GLOBALE_NODE, "type": "global-config", "name": "global-config", "env": []}
        blijft.append(globaal)
    globaal["env"] = [e for e in globaal.get("env") or [] if e.get("name") != "HUB_URL"] + [
        {"name": "HUB_URL", "value": hub_url, "type": "str"}]
    nieuw = list(blijft)
    for naam, nodes in bundels.items():
        for node in copy.deepcopy(nodes):
            if node.get("type") == "tab" and naam in inschakelen:
                node["disabled"] = False
            if node.get("type") == "http request":
                node["credentials"] = {"password": token}  # Node-RED versleutelt dit; GET /flows geeft het nooit terug
            nieuw.append(node)
    nr.zet_flows(rev, nieuw)
    return readback(nr, bundels, verboden=(token,), hub_url=hub_url)


def _omleidingen(flows: list[Node], eigen_tabs: set[str], hub_url: str) -> list[str]:
    """Wat ná de import een flow van een eigen tabblad naar een ander adres dan de Hub kan sturen: een env op het
    tabblad of op een node daarin (of een groep), of een globale HUB_URL die niet precies de opgegeven Hub-URL is."""
    uit = []
    for node in flows:
        eigen = str(node.get("id")) in eigen_tabs or str(node.get("z")) in eigen_tabs
        if eigen and (node.get("env") or node.get("g")):
            uit.append(f"{node.get('id')} heeft een eigen env of groep")
    globale = [e for n in flows if n.get("type") == "global-config" for e in n.get("env") or []
               if isinstance(e, dict) and e.get("name") == "HUB_URL"]
    if [e.get("value") for e in globale] != [hub_url]:
        uit.append("de globale HUB_URL is niet precies de opgegeven Hub-URL")
    return uit


def readback(nr: NodeRed, bundels: dict[str, list[Node]], *, verboden: tuple[str, ...] = (),
             hub_url: str | None = None) -> dict[str, Any]:
    """Leest terug en vergelijkt per bundel de checksum. Het token mag nergens in de teruggelezen flows staan. Met
    ``hub_url`` (bij de import): de eigen tabbladen kunnen nergens anders heen dan naar die Hub (R-VEILIG-1)."""
    _rev, terug = nr.flows()
    tekst = json.dumps(terug, ensure_ascii=False)
    if any(g and g in tekst for g in verboden) or '"credentials"' in tekst:
        raise Fout("De teruggelezen flows bevatten inloggegevens; dat hoort niet.")
    if hub_url is not None:
        eigen_tabs = {str((tab_van(n) or {}).get("id")) for n in bundels.values()}
        if omleidingen := _omleidingen(terug, eigen_tabs, hub_url):
            raise Fout("Na de import kan een flow naar een ander adres dan de Hub: " + "; ".join(omleidingen))
    tabs = per_tab(terug)
    uit: dict[str, Any] = {}
    for naam, nodes in bundels.items():
        tab = tab_van(nodes) or {}
        gelezen = tabs.get(str(tab.get("id")), [])
        gelezen_tab = tab_van(gelezen) or {}
        uit[naam] = {"tab": tab.get("id"), "verwacht": checksum(nodes), "gelezen": checksum(gelezen) if gelezen
                     else None, "gelijk": bool(gelezen) and checksum(gelezen) == checksum(nodes),
                     "ingeschakeld": bool(gelezen) and gelezen_tab.get("disabled") is not True}
    return uit


def _bestandsnaam(tab_id: str) -> str:
    """Een tabblad-id komt van Node-RED (invoer van buiten) en is nooit een pad: alleen een eigen id (``jlp-…``) blijft
    zo; elk ander id krijgt een vaste veilige naam uit de sha256 van het id (review P2, R-VEILIG-2)."""
    if ID_RE.fullmatch(tab_id):
        return tab_id
    return "tab-" + hashlib.sha256(tab_id.encode("utf-8")).hexdigest()[:16]


def exporteer(nr: NodeRed, doel: Path) -> dict[str, Any]:
    """Git-export: één bestand per tabblad, plus de vergelijking met het lock (gelijk, afwijkend of onbekend). Een
    bestand komt altijd direct in ``doel``; een tabblad-id wordt nooit als pad gebruikt."""
    _rev, flows = nr.flows()
    lock = lees_lock()["bundels"]
    per_tab_id = {v["tab"]: (naam, v) for naam, v in lock.items()}
    doel.mkdir(parents=True, exist_ok=True)
    map_ = doel.resolve()
    uit: dict[str, Any] = {}
    for tab_id, nodes in per_tab(flows).items():
        naam, vast = per_tab_id.get(tab_id, (_bestandsnaam(tab_id), None))
        bestand = (doel / f"{naam}.json").resolve()
        if bestand.parent != map_:
            raise Fout(f"Exportnaam '{naam}' valt buiten de exportmap; niets geschreven voor dit tabblad.")
        bestand.write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        som = checksum(nodes)
        uit[naam] = {"tab": tab_id, "sha256": som, "ingeschakeld": (tab_van(nodes) or {}).get("disabled") is not True,
                     "vergelijking": "onbekend" if vast is None else
                     ("gelijk_aan_lock" if vast["sha256"] == som else "afwijkend")}
    return uit


# ---------------------------------------------------------------------- opdrachtregel
def _kies_bundels(keuzes: list[str]) -> dict[str, list[Node]]:
    if not keuzes:
        return bundels_in_git()
    uit = {}
    for keuze in keuzes:
        naam, _, pad = keuze.partition("=")
        uit[naam] = lees_bundel(Path(pad) if pad else FLOWS_DIR / f"{naam}.json")
    return uit


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Node-RED (I10): instellingen, flowbundels, import en export")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("render").add_argument("--check", action="store_true")
    sub.add_parser("lint")
    sub.add_parser("lock").add_argument("--check", action="store_true")
    for naam in ("importeer", "exporteer"):
        s = sub.add_parser(naam)
        s.add_argument("--url", required=True, help="adres van Node-RED (Admin-API)")
        s.add_argument("--gebruiker", default=None)
        if naam == "importeer":
            s.add_argument("--hub-url", required=True)
            s.add_argument("--bundel", action="append", default=[], help="naam of naam=pad (herhaalbaar)")
            s.add_argument("--inschakelen", action="append", default=[])
        else:
            s.add_argument("--naar", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.cmd == "render":
            waarde = render_flows_env(lees_overrides())
            if args.check and manifest_flows_env() != waarde:
                raise Fout("FLOWS in het manifest wijkt af van settings-overrides.json (draai: render).")
            print(waarde)
        elif args.cmd == "lint":
            fouten = {naam: lint(nodes) for naam, nodes in bundels_in_git().items()}
            for naam, lijst in fouten.items():
                print(f"{naam}: {'in orde' if not lijst else ''}")
                for regel in lijst:
                    print(f"  - {regel}")
            return 1 if any(fouten.values()) else 0
        elif args.cmd == "lock":
            inhoud = json.dumps(lock_inhoud(bundels_in_git()), ensure_ascii=False, indent=2) + "\n"
            if args.check:
                if not LOCK.exists() or LOCK.read_text(encoding="utf-8") != inhoud:
                    raise Fout("flows.lock.json loopt achter op de bundels (draai: lock).")
            else:
                LOCK.write_text(inhoud, encoding="utf-8")
            print("lock in orde" if args.check else f"geschreven: {LOCK.relative_to(ROOT)}")
        else:
            nr = NodeRed(args.url)
            if args.gebruiker:
                nr.login(args.gebruiker, os.environ.get("NODERED_BEHEER_WACHTWOORD", ""))
            if args.cmd == "importeer":
                uitkomst = importeer(nr, _kies_bundels(args.bundel), hub_url=args.hub_url,
                                     token=os.environ.get("NODERED_HUB_TOKEN", ""),
                                     inschakelen=frozenset(args.inschakelen))
            else:
                uitkomst = exporteer(nr, args.naar)
            print(json.dumps(uitkomst, ensure_ascii=False, indent=2))
            if args.cmd == "importeer" and not all(v["gelijk"] for v in uitkomst.values()):
                return 1
    except Fout as exc:
        print(f"fout: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
