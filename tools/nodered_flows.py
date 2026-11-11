"""Node-RED (I10): instellingen, flowbundels, controle, import, export en herstel — zonder shell in de container.

Wat hier staat:

- ``render``     maakt uit ``install/node-red/settings-overrides.json`` de waarde van de omgevingsvariabele ``FLOWS``
                 die het manifest aan de container meegeeft (``--check``: het manifest moet precies die waarde hebben);
- ``lint``       controleert de flowbundels in ``install/node-red/flows/``: alleen toegestane nodes (geen exec,
                 function, bestand of contrib), alleen verkeer naar vaste Hub-routes, geen geheimen of adressen in Git,
                 een flow die een actie aanvraagt staat standaard uit;
- ``lock``       legt per bundel versie en sha256 vast in ``install/node-red/flows.lock.json`` (``--check``);
- ``importeer``  zet bundels via de Admin-API in een draaiende Node-RED (beheerder vereist), geeft het service-token
                 van de Hub alleen als versleuteld node-credential mee en leest terug of de checksums kloppen en of
                 de eigen tabbladen opnieuw door ``lint`` komen (zo niet: de vorige flows gaan terug). Een
                 oudere versie herstellen is hetzelfde commando met het oudere bestand (``--bundel naam=pad``);
- ``exporteer``  schrijft de flows uit Node-RED per tabblad naar bestanden (Git-export) en vergelijkt met het lock
                 (beheerder vereist: zonder aanmelding geeft Node-RED niets, besluit C-11).

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
# Een eigenschapsnaam in een change-regel is alleen een eenvoudige naam: ASCII-letters, cijfers, _ en punten, en nooit
# met de wortel 'msg'. Node-RED (RED.util.setMessageProperty) haalt 'msg.' er zelf af en verstaat aanhalingstekens en
# haken (msg['url'], 'url', "url"); elke andere schrijfwijze dan de eenvoudige naam wordt daarom geweigerd, vóór de
# url- en VERBODEN-controles (herreview P2, R-VEILIG-1, herstelpoging 2).
EIGENSCHAP_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
# Segmenten die in JavaScript naar het prototype van elk object leiden. Op de gepinde image vervuilt een bundel daarmee
# Object.prototype (a = msg.__proto__; a.env.http_proxy = …): geweigerd in de eigenschap (p) en in de bron (to) bij
# een bron uit msg, flow of global (S-P2-410 bouwpunt 9b; R-VEILIG-3 van S-P2-395).
VERBODEN_SEGMENTEN = frozenset({"__proto__", "constructor", "prototype"})
CONTEXT_BRONNEN = frozenset({"msg", "flow", "global"})
# De enige toegestane regel op msg.followRedirects, en verplicht in de route-node van elke http request: gemeten op de
# gepinde image stuurt de http request-node bij een doorverwijzing (307/302) naar een andere host of poort de
# bearer-kop met het service-token mee (Node-RED zet de kop terug als 'Authorization', zodat got hem bij een andere
# herkomst niet meer weghaalt). Zonder doorverwijzingen gaat het token alleen naar HUB_URL (S-P2-410 bouwpunt 9d).
REDIRECT_UIT = {"t": "set", "p": "followRedirects", "pt": "msg", "to": "false", "tot": "bool"}
ID_RE = re.compile(r"^jlp-[a-z0-9-]{1,60}$")
VERSIE_RE = re.compile(r"^versie (\d+\.\d+\.\d+)\n")
EVENT_RE = re.compile(r'"(flow\.[a-z0-9_]+\.[a-z0-9_]+)"')
GEHEIM_RE = re.compile(r"(svc_[A-Za-z0-9_-]{20,}|enr_[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}|[Bb]earer\s+\S+"
                       r"|-----BEGIN|\$2[aby]\$\d\d\$)")
ADRES_RE = re.compile(r"([a-z][a-z0-9+.-]*://|\b\d{1,3}(\.\d{1,3}){3}\b)")
ONVEILIG_IN_FLOWS_ENV = re.compile(r"[\s$*?\\`'~<>|;&()]")
# Besluit C-11 (gebruiker, 2026-11-11): geen anonieme toegang tot de editor en de Admin-API, ook niet lezen; en volgens
# besluit C-12 geen gebruiker of geheim in het manifest. adminAuth is daarom precies dit (geen ``default``).
ADMIN_AUTH = {"type": "credentials", "users": []}
GEEN_BEHEERDER = ("Node-RED geeft zonder aanmelding niets (besluit C-11): geef --gebruiker op en zet het wachtwoord "
                  "van die beheerder in NODERED_BEHEER_WACHTWOORD.")

Node = dict[str, Any]


class Fout(Exception):
    """Een fout die de gebruiker moet lezen (Nederlands), geen programmeerfout."""


# ---------------------------------------------------------------------- instellingen → FLOWS
def lees_overrides(pad: Path = OVERRIDES) -> dict[str, Any]:
    data = json.loads(pad.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("overrides"), dict) \
            or not isinstance(data.get("flows_bestand"), str):
        raise Fout(f"{pad.name}: verwacht een object met 'flows_bestand' en 'overrides'")
    if data["overrides"].get("adminAuth") != ADMIN_AUTH:
        raise Fout(f"{pad.name}: adminAuth moet precies {json.dumps(ADMIN_AUTH)} zijn: geen 'default' (geen anonieme "
                   "rechten, besluit C-11) en geen gebruiker of geheim in het manifest (besluit C-12)")
    return data


def render_flows_env(data: dict[str, Any]) -> str:
    """De waarde van ``FLOWS``: de entrypoint van de image geeft ``$FLOWS`` ongequoot door aan Node-RED, dus elk
    woord wordt een eigen argument (``flows.json -D sleutel=waarde …``). Daarom: compacte JSON, geen spaties en geen
    ``$``, ``*``, ``?``, aanhalingstekens of andere shelltekens.

    Eerlijk (review P2, R-VEILIG-3): ``[`` en ``]`` zijn ook jokertekens, maar blijven toegestaan omdat de JSON-lijsten
    (``nodesIncludes``) ze nodig hebben. De shell vervangt zo'n woord alleen als er in de werkmap van de entrypoint
    (``/usr/src/node-red``) een bestand met precies die naam staat; bij deze digest kan 568 daar niet schrijven
    (gemeten in de review). Het restrisico hangt dus aan de digest: elke nieuwe digest gaat eerst door de rooktest."""
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


def _regels(node: Node) -> list[Node]:
    """De regels van een change-node die objecten zijn (zonder te crashen op andere vormen; die meldt ``lint``)."""
    regels = node.get("rules")
    return [r for r in regels if isinstance(r, dict)] if isinstance(regels, list) else []


def _redirect_uit(regel: Node) -> bool:
    return all(regel.get(k) == v for k, v in REDIRECT_UIT.items())


def routes_van(nodes: list[Node]) -> list[str]:
    uit = set()
    for node in nodes:
        if node.get("type") == "change":
            for regel in _regels(node):
                if regel.get("p") == "url" and regel.get("to") in HUB_ROUTES:
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
        draden = node.get("wires", [])
        if not isinstance(draden, list) or not all(isinstance(u, list) and all(isinstance(d, str) for d in u)
                                                   for u in draden):
            uit.append(f"draad_ongeldig: {node.get('id')} — 'wires' is een lijst van lijsten met node-ids")
            draden = []
        for uitgang in draden:
            for doel in uitgang:
                if doel not in per_id:
                    uit.append(f"draad_naar_buiten: {node.get('id')} → {doel} bestaat niet in deze bundel")
                naar.setdefault(doel, []).append(node)
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
            props = node.get("props")
            velden = [p.get("p") for p in props] if isinstance(props, list) and all(
                isinstance(p, dict) for p in props) else None
            if velden not in (["payload"], ["payload", "topic"]):
                uit.append(f"inject_velden: {node.get('id')} mag alleen payload (en topic) zetten")
        elif ntype == "debug":
            if node.get("console") or node.get("tostatus"):
                uit.append(f"debug_naar_log: {node.get('id')} schrijft alleen naar de zijbalk, niet naar het log")
        elif ntype == "http request":
            uit += _lint_http(node, naar.get(str(node.get("id")), []))
    if ACTIE_ROUTE in " ".join(routes_van(nodes)) and tab.get("disabled") is not True:
        uit.append("actieflow_moet_uit_staan: een flow die een actie aanvraagt staat in Git uitgeschakeld")
    return uit


def _segment_fout(naam: str) -> str | None:
    """Waarom ``naam`` geen eenvoudige, veilige eigenschapsnaam is (None = in orde)."""
    if not EIGENSCHAP_RE.fullmatch(naam):
        return "is geen eenvoudige naam (alleen letters, cijfers, _ en punten; geen aanhalingstekens of haken)"
    if verboden := VERBODEN_SEGMENTEN & set(naam.split(".")):
        return f"bevat {', '.join(sorted(verboden))} (leidt naar het prototype van elk object)"
    return None


def _lint_change(node: Node) -> list[str]:
    nid = node.get("id")
    regels = node.get("rules")
    if not isinstance(regels, list):
        # Zonder lijst 'rules' maakt Node-RED (15-change.js) zelf een regel uit action/property/to: die oude vorm
        # wordt niet gecontroleerd en dus geweigerd (S-P2-410 bouwpunt 9c).
        return [f"change_regel: {nid} — alleen de vorm met een lijst 'rules' (de oude vorm met action/property/to "
                "wordt geweigerd)"]
    uit = []
    for regel in regels:
        if not isinstance(regel, dict):
            uit.append(f"change_regel: {nid} — een regel is geen object")
            continue
        veld, soort, bak, bron = str(regel.get("p", "")), regel.get("t"), regel.get("pt"), regel.get("tot")
        wortel = veld.split(".", 1)[0]
        if soort not in ("set", "delete") or bak not in ("msg", "flow"):
            uit.append(f"change_regel: {nid} — alleen set/delete op msg of de eigen flowcontext")
        elif (reden := _segment_fout(veld)) is not None or wortel == "msg":
            uit.append(f"change_regel: {nid} — eigenschap '{veld[:40]}' "
                       f"{reden or 'begint met msg (Node-RED haalt msg. er zelf af)'}; Node-RED leest andere "
                       "schrijfwijzen als msg.url of msg.headers")
        elif soort == "set" and bron in CONTEXT_BRONNEN and (reden := _segment_fout(str(regel.get("to", "")))):
            uit.append(f"change_regel: {nid} — bron '{str(regel.get('to', ''))[:40]}' ({bron}) {reden}")
        elif bak == "msg" and wortel == "url":
            if soort != "set" or bron != "jsonata" or regel.get("to") not in HUB_ROUTES or veld != "url":
                uit.append(f"url_niet_toegestaan: {nid} zet msg.url op iets anders dan een vaste Hub-route")
        elif bak == "msg" and wortel == "followRedirects":
            if not _redirect_uit(regel):
                uit.append(f"verzoek_overschreven: {nid} — msg.followRedirects mag alleen op false (bool): een "
                           "doorverwijzing neemt het token mee naar een ander adres")
        elif bak == "msg" and wortel in VERBODEN_MSG_VELDEN and soort != "delete":
            uit.append(f"verzoek_overschreven: {nid} zet msg.{wortel} (methode en koppen liggen vast)")
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
        routes = [str(r.get("to")) for r in _regels(voor)
                  if r.get("p") == "url" and r.get("pt") == "msg" and r.get("t") == "set"]
        if voor.get("type") != "change" or len(routes) != 1 or routes[0] not in HUB_ROUTES:
            uit.append(f"http_zonder_route: {nid} wordt gevoed door {voor.get('id')} zonder vaste Hub-route")
        elif HUB_ROUTES[routes[0]][0] != methode:
            uit.append(f"http_methode: {nid} is {methode}, de route van {voor.get('id')} vraagt "
                       f"{HUB_ROUTES[routes[0]][0]}")
        if voor.get("type") == "change" and not any(_redirect_uit(r) for r in _regels(voor)):
            uit.append(f"redirect_niet_uit: {nid} — {voor.get('id')} zet msg.followRedirects niet op false (bool); "
                       "bij een doorverwijzing gaat het token anders mee naar een ander adres")
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
    """Dunne client voor de Admin-API (v2). Zonder ``login`` krijgt het hulpmiddel niets: anoniem lezen en schrijven
    geven 401 (besluit C-11). Eerst ``login`` met een beheerder; die levert het manifest (nog) niet (ADR-0023)."""

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
        if r.status_code == 401:
            raise Fout(f"Flows lezen vraagt een aanmelding (HTTP 401). {GEEN_BEHEERDER}")
        if r.status_code != 200:
            raise Fout(f"Flows lezen mislukt (HTTP {r.status_code}).")
        try:
            body = r.json()
            rev, flows = body["rev"], body["flows"]
        except (ValueError, KeyError, TypeError) as exc:
            raise Fout("Node-RED gaf geen leesbare flows terug (verwacht een object met 'rev' en 'flows').") from exc
        if not isinstance(flows, list) or not all(isinstance(n, dict) for n in flows):
            raise Fout("Node-RED gaf geen leesbare flows terug (verwacht een lijst van nodes).")
        return str(rev), flows

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
    vorige = copy.deepcopy(huidig)  # om terug te zetten als het teruglezen na de import iets vindt
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
    nieuwe_rev = nr.zet_flows(rev, nieuw)
    try:
        return readback(nr, bundels, verboden=(token,), hub_url=hub_url)
    except Exception as exc:  # fail-closed: elke fout bij het teruglezen zet de vorige flows terug
        # Wat teruggelezen is, mag niet blijven draaien met het token: de flows van vóór de import gaan terug, ook bij
        # een netwerkfout of onverwachte teruggelezen gegevens (R-VEILIG-5 van S-P2-395). De credentials van nodes die
        # daarin al stonden bewaart Node-RED apart; ``huidig`` (uit GET /flows) bevat er geen.
        reden = str(exc) if isinstance(exc, Fout) else f"Het teruglezen na de import is mislukt ({_kort(exc)})."
        try:
            nr.zet_flows(nieuwe_rev or nr.flows()[0], vorige)
        except Exception as terug:
            raise Fout(f"{reden} De vorige flows zijn NIET teruggezet ({_kort(terug)}); zet ze zelf terug of schakel "
                       "de eigen tabbladen uit.") from exc
        raise Fout(f"{reden} De flows van vóór de import zijn teruggezet.") from exc


def _kort(exc: BaseException) -> str:
    """Een fout als korte tekst voor een melding (soort en eerste 200 tekens), zonder traceback."""
    return str(exc)[:200] if isinstance(exc, Fout) else f"{type(exc).__name__}: {str(exc)[:200]}"


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


def _lint_gelezen(gelezen: list[Node]) -> list[str]:
    """De eigen controle over de teruggelezen nodes van één eigen tabblad. Aan of uit is een bedieningskeuze (zoals bij
    ``checksum``): het tabblad telt als uitgeschakeld, zodat alleen de inhoud wordt beoordeeld."""
    kopie = copy.deepcopy(gelezen)
    for node in kopie:
        if node.get("type") == "tab":
            node["disabled"] = True
    return lint(kopie)


def readback(nr: NodeRed, bundels: dict[str, list[Node]], *, verboden: tuple[str, ...] = (),
             hub_url: str | None = None) -> dict[str, Any]:
    """Leest terug en vergelijkt per bundel de checksum. Het token mag nergens in de teruggelezen flows staan. De
    teruggelezen nodes van elk eigen tabblad gaan opnieuw door de eigen controle (``lint``): één bevinding is een
    Fout, want wat daar staat draait met het token (herreview P2, R-VEILIG-1). Met ``hub_url`` (bij de import): de
    eigen tabbladen kunnen nergens anders heen dan naar die Hub."""
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
    fouten: list[str] = []
    for naam, nodes in bundels.items():
        tab = tab_van(nodes) or {}
        gelezen = tabs.get(str(tab.get("id")), [])
        gelezen_tab = tab_van(gelezen) or {}
        controle = _lint_gelezen(gelezen) if gelezen else []
        uit[naam] = {"tab": tab.get("id"), "verwacht": checksum(nodes), "gelezen": checksum(gelezen) if gelezen
                     else None, "gelijk": bool(gelezen) and checksum(gelezen) == checksum(nodes),
                     "ingeschakeld": bool(gelezen) and gelezen_tab.get("disabled") is not True,
                     "eigen_controle": controle}
        fouten += [f"{naam}: {b}" for b in controle]
    if fouten:
        raise Fout("De teruggelezen flows van een eigen tabblad komen niet door de eigen controle: "
                   + "; ".join(fouten))
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
        s.add_argument("--gebruiker", default=None,
                       help="beheerder van Node-RED (verplicht; wachtwoord uit NODERED_BEHEER_WACHTWOORD)")
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
            if not args.gebruiker:
                raise Fout(f"{args.cmd.capitalize()} kan alleen met een beheerder. {GEEN_BEHEERDER}")
            wachtwoord = os.environ.get("NODERED_BEHEER_WACHTWOORD", "")
            if not wachtwoord:
                raise Fout("NODERED_BEHEER_WACHTWOORD is leeg: zet daarin het wachtwoord van de beheerder "
                           f"'{args.gebruiker}' (nooit op de opdrachtregel).")
            nr = NodeRed(args.url)
            nr.login(args.gebruiker, wachtwoord)
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
