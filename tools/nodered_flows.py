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
                 oudere versie herstellen is hetzelfde commando met het oudere bestand (``--bundel naam=pad``), als
                 die versie door ``lint`` komt (versies vóór 1.2.0 missen ``persist`` en worden geweigerd);
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
# Witte lijst (V-086; S-P2-410, herstel van R-TECH-1 en R-VEILIG-1). Een zwarte lijst van drie segmenten liet andere
# wegen naar het prototype van elk object open: msg.hasOwnProperty = msg.isPrototypeOf zette msg.followRedirects buiten
# werking (de http request-node leest die met msg.hasOwnProperty), en een inject die flow['__proto__'] leest gaf
# Object.prototype als payload (gemeten op de gepinde image). Daarom geldt nu alleen wat de bundels echt gebruiken:
# - een msg-eigenschap (in p, en in to bij tot msg) is precies één van deze namen, zonder pad met punten; url alleen met
#   een vaste Hub-route, followRedirects alleen false, headers alleen met delete;
MSG_NAMEN = frozenset({"payload", "url", "followRedirects", "meting", "headers"})
# - een naam in de flow- of globale context (p bij pt flow; to bij tot flow/global; v van een inject) is alleen
#   kleine letters en _ (dus geen punt, haak of hoofdletter) en geen eigenschap van Object.prototype of 'prototype';
CONTEXT_NAAM_RE = re.compile(r"^[a-z_]+$")
PROTOTYPE_NAMEN = frozenset({"__proto__", "constructor", "prototype", "hasOwnProperty", "isPrototypeOf",
                             "propertyIsEnumerable", "toString", "toLocaleString", "valueOf", "__defineGetter__",
                             "__defineSetter__", "__lookupGetter__", "__lookupSetter__"})
CONTEXT_SOORTEN = frozenset({"flow", "global"})
# - een waarde heeft een vaste soort (tot van een change-regel, vt van een inject); env, bin, msg in een inject en elke
#   onbekende of ontbrekende soort worden geweigerd. Een change-regel mag daarnaast lezen uit msg/flow/global (met de
#   namen hierboven) en JSONata gebruiken, maar alleen precies de uitdrukkingen uit de bundels in Git (op sha256; de
#   vaste Hub-routes voor msg.url staan in HUB_ROUTES). Een bundel met een andere uitdrukking wordt geweigerd tot deze
#   lijst bewust is bijgewerkt (en de review dat ziet).
VASTE_SOORTEN = frozenset({"str", "num", "bool", "json", "date"})
# De JSONata van ELKE uitgebrachte versie van de bundels in Git: sha256 → (bundel, node, veld, versies). Alleen
# toevoegen, nooit vervangen (herstelpoging 2 van S-P2-410, R-TECH-2): zo weigert lint een oudere versie nooit om haar
# JSONata. De vorige versies staan als vaste testkopie in tests/repo/fixtures/node-red-versies/; de repotest eist dat
# elke sha hier bij een versie uit die historie of uit install/node-red/flows hoort, en andersom.
TOEGESTANE_JSONATA: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "sha256:629026bf4e16bed15e985d5604d5bb51993e4e9ef5caaf0c853b621f2274b09f":
        ("hartslag", "jlp-hs-bouw", "msg.payload", ("1.0.0", "1.1.0", "1.1.1", "1.2.0")),
    "sha256:bf38cdf357bc0fdca4ee24195bbc6f62930fef71782ecfcc1b2768a1a412f6ad":
        ("mediaguard-offline", "jlp-mg-oordeel", "msg.meting", ("1.0.0", "1.1.0", "1.1.1", "1.2.0")),
    "sha256:1556051bc7dffcad1355e051c3442475a04567a5ea1d960ac5ed7181689815f1":
        ("mediaguard-offline", "jlp-mg-oordeel", "flow.mediaguard_staat", ("1.0.0", "1.1.0", "1.1.1", "1.2.0")),
    "sha256:46b05c9366672233d5f15c1e946c3caa2e50a5a76fe9e8966db882e4e6e49787":
        ("mediaguard-offline", "jlp-mg-event", "msg.payload", ("1.0.0",)),
    "sha256:3122dbdf841feb723d052bce6756475d95d1c096f683de9a0b6acbcce66bccd8":
        ("mediaguard-offline", "jlp-mg-event", "msg.payload", ("1.1.0",)),
    "sha256:9a9bbcb91e8bf37775f3cfb37f0143f37b45cb1fd99e5dac2c338241d4d0d968":
        ("mediaguard-offline", "jlp-mg-event", "msg.payload", ("1.1.1",)),
    "sha256:bb1e804948712d2b265ad2f279bf60c9b88e80e2524a67d0bd1e6383333f2465":
        ("mediaguard-offline", "jlp-mg-event", "msg.payload", ("1.2.0",)),
    "sha256:145177ef97b336f7465fd3e7782c13b4a98b6ba0ba9fb2ece3d51b08aad9fff6":
        ("testflow-herstart", "jlp-th-bouw", "msg.payload", ("1.0.0", "1.1.0", "1.1.1", "1.2.0")),
}
# Een switch-node (herstelpoging 2 van S-P2-410; vervolgpunt van de herreviews) leest alleen msg (propertyType msg) met
# een naam uit MSG_NAMEN of meting.<naam>[.<naam>…] (kleine letters en _, geen eigenschap van het prototype), en een
# regel vergelijkt alleen met een vaste soort (VASTE_SOORTEN; zonder vt maakt Node-RED er zelf num of str van) of met
# JSONata uit TOEGESTANE_JSONATA; nooit env, msg, flow, global of prev. Een onbekende operator wordt geweigerd.
SWITCH_METING_RE = re.compile(r"^meting(\.[a-z_]+)+$")
SWITCH_OPERATOREN = frozenset({"eq", "neq", "lt", "lte", "gt", "gte", "btwn", "cont", "regex", "true", "false", "null",
                               "nnull", "empty", "nempty", "istype", "head", "index", "tail", "hask", "jsonata_exp",
                               "else"})
# De enige toegestane regel op msg.followRedirects, en verplicht in de route-node van elke http request: gemeten op de
# gepinde image stuurt de http request-node bij een doorverwijzing (307/302) naar een andere host of poort de
# bearer-kop met het service-token mee (Node-RED zet de kop terug als 'Authorization', zodat got hem bij een andere
# herkomst niet meer weghaalt). Proxy (herstel R-VEILIG-2, herstelpoging 2): proxyOptions uit FLOWS laat de http
# request-node http_proxy en all_proxy uit de omgeving negeren, en ``persist: true`` (verplicht in ``_lint_http``) geeft
# elke node een eigen agent, zodat ook de ingebouwde proxy van Node.js (NODE_USE_ENV_PROXY=1 of
# NODE_OPTIONS=--use-env-proxy) niet geldt. Zo ging het token in rooktest N10 alleen naar HUB_URL (gemeten bij deze
# digest, Hub over HTTP; S-P2-410 bouwpunt 9d en herstel R-VEILIG-2).
REDIRECT_UIT = {"t": "set", "p": "followRedirects", "pt": "msg", "to": "false", "tot": "bool"}
ID_RE = re.compile(r"^jlp-[a-z0-9-]{1,60}$")
VERSIE_RE = re.compile(r"^versie (\d+\.\d+\.\d+)\n")
EVENT_RE = re.compile(r'"(flow\.[a-z0-9_]+\.[a-z0-9_]+)"')
GEHEIM_RE = re.compile(r"(svc_[A-Za-z0-9_-]{20,}|enr_[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}|[Bb]earer\s+\S+"
                       r"|-----BEGIN|\$2[aby]\$\d\d\$)")
ADRES_RE = re.compile(r"([a-z][a-z0-9+.-]*://|\b\d{1,3}(\.\d{1,3}){3}\b)")
ONVEILIG_IN_FLOWS_ENV = re.compile(r"[\s$*?\\`'~<>|;&()]")
# Node-RED vervangt bij het starten van een node elke tekstwaarde die precies ${NAAM} of $(NAAM) is (zonder witruimte)
# door een instelling of omgevingsvariabele (flows/util.js mapEnvVarProperties), in elk veld en ongeacht de soort. Een
# bundel kon zo een omgevingsvariabele van de container lezen en naar de Hub sturen; lint weigert die vorm daarom in
# elke tekst (KLEIN-50, vervolgpunt R-VEILIG-3 van S-P2-410). Een tekst die het patroon alleen bevat blijft toegestaan.
ENV_SUBSTITUTIE_RE = re.compile(r"\$\{\S+\}|\$\(\S+\)")
# Besluit C-11 (gebruiker, 2026-11-11): geen anonieme toegang tot de editor en de Admin-API, ook niet lezen; en volgens
# besluit C-12 geen gebruiker of geheim in het manifest. adminAuth is daarom precies dit (geen ``default``).
ADMIN_AUTH = {"type": "credentials", "users": []}
# De enige instellingen die FLOWS mag zetten (R-VEILIG-4 van S-P2-410): Node-RED maakt van elke '-D sleutel=waarde'
# een instelling, ook van 'adminAuth.default' (op de image gaf dat weer anoniem lezen). proxyOptions met een lege env:
# de http request-node zoekt dan zelf geen proxy in http_proxy of all_proxy van de container (R-VEILIG-2 van S-P2-410);
# tegen de ingebouwde proxy van Node.js helpt alleen persist (zie REDIRECT_UIT en _lint_http).
TOEGESTANE_OVERRIDES = ("adminAuth", "nodesIncludes", "externalModules", "functionExternalModules",
                        "editorTheme.projects.enabled", "editorTheme.tours", "telemetry", "diagnostics", "proxyOptions")
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
    if onbekend := sorted(set(data["overrides"]) - set(TOEGESTANE_OVERRIDES)):
        raise Fout(f"{pad.name}: instelling {', '.join(onbekend)} is niet toegestaan (alleen "
                   f"{', '.join(TOEGESTANE_OVERRIDES)}); een sleutel als 'adminAuth.default' geeft weer anonieme "
                   "rechten (besluit C-11)")
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


def manifest_flows_env(pad: Path | None = None) -> str | None:
    manifest = yaml.safe_load((pad or MANIFEST).read_text(encoding="utf-8"))
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
        if ENV_SUBSTITUTIE_RE.fullmatch(tekst):
            uit.append(f"env_substitutie: '{tekst[:40]}' — Node-RED vervangt een tekst die precies ${{NAAM}} of "
                       "$(NAAM) is door een instelling of omgevingsvariabele; nooit env")
    for node in nodes:
        ntype = node.get("type")
        if ntype == "change":
            uit += _lint_change(node)
        elif ntype == "inject":
            uit += _lint_inject(node)
        elif ntype == "switch":
            uit += _lint_switch(node)
        elif ntype == "debug":
            if node.get("console") or node.get("tostatus"):
                uit.append(f"debug_naar_log: {node.get('id')} schrijft alleen naar de zijbalk, niet naar het log")
            if "jsonata" in (node.get("targetType"), node.get("statusType")):
                # 21-debug.js voert een JSONata in 'complete' (targetType) of 'statusVal' (statusType) uit; die komt
                # niet uit TOEGESTANE_JSONATA (herstelpoging 2 van S-P2-410)
                uit.append(f"debug_jsonata: {node.get('id')} — een debug-node toont een msg-eigenschap of het hele "
                           "bericht, nooit JSONata (targetType en statusType)")
        elif ntype == "http request":
            uit += _lint_http(node, naar.get(str(node.get("id")), []))
    if ACTIE_ROUTE in " ".join(routes_van(nodes)) and tab.get("disabled") is not True:
        uit.append("actieflow_moet_uit_staan: een flow die een actie aanvraagt staat in Git uitgeschakeld")
    return uit


def jsonata_som(uitdrukking: str) -> str:
    """De sha256 waarmee een JSONata-uitdrukking in ``TOEGESTANE_JSONATA`` staat."""
    return "sha256:" + hashlib.sha256(uitdrukking.encode("utf-8")).hexdigest()


def _naam_fout(naam: Any, bak: str) -> str | None:
    """Waarom ``naam`` geen toegestane naam in ``bak`` (msg, flow of global) is; None = in orde (witte lijst)."""
    if not isinstance(naam, str):
        return "is geen tekst"
    if bak == "msg":
        return None if naam in MSG_NAMEN else f"staat niet op de witte lijst ({', '.join(sorted(MSG_NAMEN))})"
    if not CONTEXT_NAAM_RE.fullmatch(naam):
        return "is geen naam van alleen kleine letters en _ (geen pad, haak of hoofdletter)"
    if naam in PROTOTYPE_NAMEN:
        return "is een eigenschap van het prototype van elk object"
    return None


def _waarde_fout(soort: Any, waarde: Any, *, change: bool) -> str | None:
    """Waarom een waarde van soort ``soort`` (``tot`` of ``vt``) niet mag; None = in orde. JSONata en een bron uit msg
    mogen alleen in een change-regel."""
    if soort in VASTE_SOORTEN:
        return None if isinstance(waarde, str) else "is geen tekst"
    if soort in CONTEXT_SOORTEN or (change and soort == "msg"):
        reden = _naam_fout(waarde, str(soort))
        return None if reden is None else f"'{str(waarde)[:40]}' ({soort}) {reden}"
    if change and soort == "jsonata":
        if isinstance(waarde, str) and jsonata_som(waarde) in TOEGESTANE_JSONATA:
            return None
        som = jsonata_som(waarde) if isinstance(waarde, str) else "geen tekst"
        return (f"JSONata die niet uit de bundels in Git komt ({som[:19]}…; alleen de uitdrukkingen in "
                "TOEGESTANE_JSONATA)")
    toegestaan = sorted(VASTE_SOORTEN | CONTEXT_SOORTEN | ({"msg", "jsonata"} if change else set()))
    return f"soort '{soort}' is niet toegestaan (alleen {', '.join(toegestaan)}; nooit env)"


def _lint_inject(node: Node) -> list[str]:
    """Een inject zet alleen payload (en topic), met een vaste soort of een naam in flow/global. Zoals Node-RED
    (20-inject.js): een payload zonder eigen ``v`` neemt de oude velden ``payload``/``payloadType``, een topic met
    ``vt`` str zonder ``v`` het veld ``topic``, en zonder ``vt`` is de soort ``str``. De oude velden worden daarnaast
    altijd zelf gecontroleerd."""
    nid = node.get("id")
    ruw = node.get("props")
    props: list[Node] = ruw if isinstance(ruw, list) and all(isinstance(p, dict) for p in ruw) else []
    if [p.get("p") for p in props] not in (["payload"], ["payload", "topic"]):
        return [f"inject_velden: {nid} mag alleen payload (en topic) zetten"]
    paren: list[tuple[str, Any, Any]] = []
    for prop in props:
        if prop["p"] == "payload" and "v" not in prop:
            paren.append(("payload", node.get("payloadType"), node.get("payload")))
        elif prop["p"] == "topic" and prop.get("vt") == "str" and "v" not in prop:
            paren.append(("topic", "str", node.get("topic", "")))
        else:
            paren.append((str(prop["p"]), prop.get("vt"), prop.get("v", "")))
    if "payloadType" in node or "payload" in node:
        paren.append(("payload (oude velden)", node.get("payloadType"), node.get("payload", "")))
    uit = []
    for wat, soort, waarde in paren:
        if (reden := _waarde_fout("str" if soort is None else soort, "" if waarde is None else waarde,
                                  change=False)) is not None:
            uit.append(f"inject_bron: {nid}.{wat} — {reden}")
    return uit


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
        elif not EIGENSCHAP_RE.fullmatch(veld) or wortel == "msg":
            vorm = ("begint met msg (Node-RED haalt msg. er zelf af)" if EIGENSCHAP_RE.fullmatch(veld) else
                    "is geen eenvoudige naam (alleen letters, cijfers, _ en punten; geen aanhalingstekens of haken)")
            uit.append(f"change_regel: {nid} — eigenschap '{veld[:40]}' {vorm}; Node-RED leest andere "
                       "schrijfwijzen als msg.url of msg.headers")
        elif bak == "msg" and wortel == "url":
            if soort != "set" or bron != "jsonata" or regel.get("to") not in HUB_ROUTES or veld != "url":
                uit.append(f"url_niet_toegestaan: {nid} zet msg.url op iets anders dan een vaste Hub-route")
        elif bak == "msg" and wortel == "followRedirects":
            if not _redirect_uit(regel):
                uit.append(f"verzoek_overschreven: {nid} — msg.followRedirects mag alleen op false (bool): een "
                           "doorverwijzing neemt het token mee naar een ander adres")
        elif bak == "msg" and wortel in VERBODEN_MSG_VELDEN and soort != "delete":
            uit.append(f"verzoek_overschreven: {nid} zet msg.{wortel} (methode en koppen liggen vast)")
        elif (reden := _naam_fout(veld, str(bak))) is not None:
            uit.append(f"change_regel: {nid} — eigenschap '{veld[:40]}' ({bak}) {reden}")
        elif soort == "set" and (reden := _waarde_fout(bron, regel.get("to"), change=True)) is not None:
            # Zonder tot maakt Node-RED er zelf msg (to begint met 'msg.') of str van; daarom moet tot er altijd staan.
            uit.append(f"change_regel: {nid} — bron van '{veld[:40]}': {reden}")
    return uit


def _lint_switch(node: Node) -> list[str]:
    """Een switch-node leest alleen msg met een naam van de witte lijst en vergelijkt alleen met vaste soorten of met
    JSONata uit TOEGESTANE_JSONATA (10-switch.js: ``property``/``propertyType``, per regel ``t``, ``v``/``vt`` en
    ``v2``/``v2t``; zonder ``vt`` maakt Node-RED er zelf num of str van)."""
    nid = node.get("id")
    uit = []
    naam, bak = node.get("property"), node.get("propertyType")
    goed = isinstance(naam, str) and (naam in MSG_NAMEN or (SWITCH_METING_RE.fullmatch(naam) is not None
                                                            and not set(naam.split(".")) & PROTOTYPE_NAMEN))
    if bak != "msg" or not goed:
        uit.append(f"switch_bron: {nid} — leest alleen msg (propertyType msg) met {', '.join(sorted(MSG_NAMEN))} of "
                   f"meting.<naam>; gezien '{str(naam)[:40]}' ({bak})")
    regels = node.get("rules")
    if not isinstance(regels, list) or not all(isinstance(r, dict) for r in regels):
        return [*uit, f"switch_regel: {nid} — 'rules' is een lijst van objecten"]
    for regel in regels:
        if regel.get("t") not in SWITCH_OPERATOREN:
            uit.append(f"switch_regel: {nid} — onbekende operator '{str(regel.get('t'))[:20]}'")
        for veld, soortveld in (("v", "vt"), ("v2", "v2t")):
            if veld not in regel and soortveld not in regel:
                continue
            soort, waarde = regel.get(soortveld, "str"), regel.get(veld, "")
            if soort == "jsonata":
                if not (isinstance(waarde, str) and jsonata_som(waarde) in TOEGESTANE_JSONATA):
                    uit.append(f"switch_regel: {nid}.{veld} — JSONata die niet uit de bundels in Git komt (alleen de "
                               "uitdrukkingen in TOEGESTANE_JSONATA)")
            elif soort not in VASTE_SOORTEN or not isinstance(waarde, str):
                uit.append(f"switch_regel: {nid}.{veld} — soort '{soort}' is niet toegestaan (alleen "
                           f"{', '.join(sorted(VASTE_SOORTEN))} met een tekst, of JSONata uit de bundels; nooit env, "
                           "msg, flow, global of prev)")
    return uit


def _lint_http(node: Node, voorgangers: list[Node]) -> list[str]:
    uit = []
    nid = node.get("id")
    # persist: true geeft de node een eigen agent (21-httprequest.js); zonder die agent gebruikt got de globale agent
    # van Node.js, en die volgt met NODE_USE_ENV_PROXY=1 of NODE_OPTIONS=--use-env-proxy de proxy uit de omgeving,
    # ook met proxyOptions in FLOWS (gemeten in de herreview en in rooktest N10; R-VEILIG-2, herstelpoging 2).
    vast = {"url": "", "tls": "", "proxy": "", "persist": True, "authType": "bearer", "headers": [],
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
    if vreemd := _vreemde_draden(nieuw, eigen_tabs, _bundel_ids(bundels)):  # R-VEILIG-3: weigeren vóór het schrijven
        raise Fout("De import is geweigerd: een node buiten de eigen tabbladen kan bij een eigen node komen (draad, "
                   "dubbel id of een andere verwijzing) en zo het verzoek met het token sturen: " + "; ".join(vreemd))
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


def _bundel_ids(bundels: dict[str, list[Node]]) -> set[str]:
    return {str(n.get("id")) for nodes in bundels.values() for n in nodes}


def _is_eigen(node: Node, eigen_tabs: set[str]) -> bool:
    """Een eigen node is een eigen tabblad of een node op een eigen tabblad (op ``z``, niet op het id: een vreemde node
    kan hetzelfde id hebben)."""
    return (node.get("type") == "tab" and str(node.get("id")) in eigen_tabs) or str(node.get("z")) in eigen_tabs


def _vreemde_draden(flows: list[Node], eigen_tabs: set[str], eigen_ids: set[str] | None = None) -> list[str]:
    """Nodes buiten de eigen tabbladen die bij een eigen node kunnen komen (R-VEILIG-3 van S-P2-410; dicht bij twijfel,
    herstelpoging 2). Zo'n node kan buiten ``lint`` om bijvoorbeeld msg.url zetten en het bericht dan naar de eigen
    http request-node met het token sturen. Geweigerd wordt een vreemde node
    - met een id dat ook een eigen id is (een node op een eigen tabblad of in de bundels): Node-RED gaf zo'n dubbele
      http request het credential van de eigen node (gemeten in de herreview);
    - waarvan ``wires`` niet precies een lijst van lijsten met teksten is (Node-RED leest ook een geneste lijst of een
      object met ``length`` als draad);
    - waarin een eigen id ergens als tekst staat (een draad, ook genest of in een object, of een ander veld zoals
      ``links`` of ``scope``).
    Draden tussen vreemde nodes onderling tellen niet."""
    eigen = {str(n.get("id")) for n in flows if _is_eigen(n, eigen_tabs)} | set(eigen_tabs) | (eigen_ids or set())
    uit = []
    for node in flows:
        if _is_eigen(node, eigen_tabs):
            continue
        nid = str(node.get("id"))
        if nid in eigen:
            uit.append(f"{nid} (buiten de eigen tabbladen) heeft het id van een eigen node (dubbel id)")
            continue
        draden = node.get("wires", [])
        if not isinstance(draden, list) or not all(isinstance(u, list) and all(isinstance(d, str) for d in u)
                                                   for u in draden):
            uit.append(f"{nid} (buiten de eigen tabbladen) heeft 'wires' die geen lijst van lijsten met node-ids is")
        if geraakt := sorted(set(_teksten(node)) & eigen):
            uit.append(f"{nid} (buiten de eigen tabbladen) draadt naar of noemt {', '.join(geraakt)}")
    return uit


def _omleidingen(flows: list[Node], eigen_tabs: set[str], hub_url: str, eigen_ids: set[str] | None = None
                 ) -> list[str]:
    """Wat ná de import een flow van een eigen tabblad naar een ander adres dan de Hub kan sturen: een env op het
    tabblad of op een node daarin (of een groep), een globale HUB_URL die niet precies de opgegeven Hub-URL is, of een
    node buiten de eigen tabbladen die bij een eigen node kan komen (R-VEILIG-3, ``_vreemde_draden``)."""
    uit = []
    for node in flows:
        eigen = str(node.get("id")) in eigen_tabs or str(node.get("z")) in eigen_tabs
        if eigen and (node.get("env") or node.get("g")):
            uit.append(f"{node.get('id')} heeft een eigen env of groep")
    globale = [e for n in flows if n.get("type") == "global-config" for e in n.get("env") or []
               if isinstance(e, dict) and e.get("name") == "HUB_URL"]
    if [e.get("value") for e in globale] != [hub_url]:
        uit.append("de globale HUB_URL is niet precies de opgegeven Hub-URL")
    return uit + _vreemde_draden(flows, eigen_tabs, eigen_ids)


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
    Fout, want wat daar staat draait met het token (herreview P2, R-VEILIG-1). Met ``hub_url`` (bij de import): geen
    env of groep op de eigen tabbladen, de globale HUB_URL is precies die Hub, en geen node buiten de eigen tabbladen
    kan bij een eigen node komen (draad, dubbel id of een andere verwijzing; ``_omleidingen``). Wat een beheerder ná
    de import zelf in de editor wijzigt, valt daarbuiten (ADR-0023, "Uitgaand verkeer")."""
    _rev, terug = nr.flows()
    tekst = json.dumps(terug, ensure_ascii=False)
    if any(g and g in tekst for g in verboden) or '"credentials"' in tekst:
        raise Fout("De teruggelezen flows bevatten inloggegevens; dat hoort niet.")
    if hub_url is not None:
        eigen_tabs = {str((tab_van(n) or {}).get("id")) for n in bundels.values()}
        if omleidingen := _omleidingen(terug, eigen_tabs, hub_url, _bundel_ids(bundels)):
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
