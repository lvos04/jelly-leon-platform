"""Node-RED (I10-10-01…06, ADR-0023): manifest, instellingen, flowbundels, lock en het bewaarde rooktestrapport.

Wat hier zonder Docker te controleren is. De proef met de echte image tegen de Hub-simulator draait in CI-job
``rooktest`` en lokaal met ``make nodered-rooktest`` (``tools/nodered_rooktest.py``).
"""

from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import nodered_flows as nf  # noqa: E402

RAPPORT = ROOT / "docs/platform-v2/bewijs/nodered-rooktest-p2.json"
# Bestanden van kern-nodes die code, bestanden of inkomend/ruw netwerkverkeer mogelijk maken: nooit laden.
GEVAARLIJKE_NODEBESTANDEN = {"90-exec.js", "10-function.js", "10-file.js", "23-watch.js", "21-httpin.js",
                             "22-websocket.js", "31-tcpin.js", "32-udp.js", "10-mqtt.js", "06-httpproxy.js"}


def _manifest() -> dict[str, Any]:
    data = yaml.safe_load(nf.MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _bundel(naam: str) -> list[dict[str, Any]]:
    return copy.deepcopy(nf.bundels_in_git()[naam])


def _codes(nodes: list[dict[str, Any]]) -> set[str]:
    return {regel.split(":", 1)[0] for regel in nf.lint(nodes)}


# ---------------------------------------------------------------------- manifest en instellingen
def test_manifest_flows_is_exactly_the_rendered_overrides() -> None:
    assert nf.manifest_flows_env() == nf.render_flows_env(nf.lees_overrides())


def test_overrides_harden_the_editor_and_the_palette() -> None:
    o = nf.lees_overrides()["overrides"]
    assert o["adminAuth"] == {"type": "credentials", "users": [], "default": {"permissions": "read"}}
    assert not set(o["nodesIncludes"]) & GEVAARLIJKE_NODEBESTANDEN
    assert {"20-inject.js", "15-change.js", "10-switch.js", "21-httprequest.js", "21-debug.js", "25-catch.js",
            "91-global-config.js"} <= set(o["nodesIncludes"])
    assert o["externalModules"] == {"autoInstall": False, "palette": {"allowInstall": False, "allowUpdate": False,
                                                                     "allowUpload": False},
                                    "modules": {"allowInstall": False}}
    assert o["functionExternalModules"] is False and o["editorTheme.projects.enabled"] is False
    assert o["diagnostics"] == {"enabled": False, "ui": False}  # de monitor (403 op /diagnostics) leunt hierop


@pytest.mark.parametrize("waarde", ["met spatie", "$(id)", "a;b", "`x`", "a|b", "~root", "a*"])
def test_render_refuses_values_the_shell_would_expand(waarde: str) -> None:
    with pytest.raises(nf.Fout):
        nf.render_flows_env({"flows_bestand": "flows.json", "overrides": {"sleutel": waarde}})
    with pytest.raises(nf.Fout):
        nf.render_flows_env({"flows_bestand": "flows.json", "overrides": {"sleutel met spatie": 1}})


def test_render_keeps_brackets_only_because_json_lists_need_them() -> None:
    """R-VEILIG-3 (review P2): ``[`` en ``]`` zijn jokertekens voor de shell, maar ``nodesIncludes`` is een JSON-lijst.
    Ze blijven daarom toegestaan; veilig zolang 568 de werkmap van de entrypoint niet kan beschrijven (gemeten bij de
    gepinde digest; zie ``render_flows_env``). Elk ander jokerteken blijft geweigerd."""
    waarde = nf.render_flows_env({"flows_bestand": "flows.json", "overrides": {"lijst": ["a", "b"]}})
    assert waarde == 'flows.json -D lijst=["a","b"]'
    assert "[" in (nf.manifest_flows_env() or "")  # het manifest heeft ze echt nodig (nodesIncludes)
    for teken in "*?":
        with pytest.raises(nf.Fout):
            nf.render_flows_env({"flows_bestand": "flows.json", "overrides": {"sleutel": f"a{teken}"}})


def test_manifest_is_a_standard_custom_app_without_extra_rights_or_secrets() -> None:
    m = _manifest()
    assert m["install"]["method"] == "custom_app" and m["version"] == m["image"]["tag"]
    assert m["image"]["digest"].startswith("sha256:") and "latest" not in m["image"]["tag"]
    assert m["custom_app"]["run_as"] == {"mode": "custom", "user": 568, "group": 568}
    assert [e["name"] for e in m["custom_app"]["env"]] == ["FLOWS"]  # geen geheimen, geen HUB_* in het formulier
    assert [s["mount"] for s in m["storage"]] == ["/data"]
    monitor = m["healthcheck"]["monitor"]
    assert monitor["pad"] == "/diagnostics" and monitor["verwachte_status"] == 403
    assert {d["app_id"] for d in m["dependencies"]} == {"ai-server-manager"}
    assert m["validation_status"] == "unvalidated"
    tekst = nf.MANIFEST.read_text(encoding="utf-8")
    assert not nf.GEHEIM_RE.search(tekst)
    assert "NODE_RED_CREDENTIAL_SECRET" not in tekst and "credentialSecret" not in tekst


# ---------------------------------------------------------------------- bundels en lock
def test_bundles_pass_the_own_check_and_the_lock_is_current() -> None:
    bundels = nf.bundels_in_git()
    assert set(bundels) == {"hartslag", "mediaguard-offline", "testflow-herstart"}
    for naam, nodes in bundels.items():
        assert nf.lint(nodes) == [], naam
    assert nf.lock_inhoud(bundels) == nf.lees_lock(), "draai: python tools/nodered_flows.py lock"


def test_only_the_test_flow_requests_an_action_and_it_is_off_by_default() -> None:
    lock = nf.lees_lock()["bundels"]
    assert lock["testflow-herstart"]["soort"] == "actie_aanvragen"
    assert lock["testflow-herstart"]["standaard_aan"] is False
    assert lock["testflow-herstart"]["routes"] == ["POST /api/v1/actions/proposals"]
    for naam in ("hartslag", "mediaguard-offline"):
        assert lock[naam]["soort"] == "alleen_lezen_en_melden" and lock[naam]["standaard_aan"] is True
        assert not any(nf.ACTIE_ROUTE in r for r in lock[naam]["routes"])
    assert lock["mediaguard-offline"]["routes"] == ["GET /api/v1/apps", "POST /api/v1/events/inbox"]
    assert lock["mediaguard-offline"]["event_types"] == ["flow.app_watch.down", "flow.app_watch.recovered"]
    assert lock["hartslag"]["routes"] == ["POST /api/v1/monitoring/heartbeat"]


def test_checksum_ignores_on_off_and_credentials_but_nothing_else() -> None:
    nodes = _bundel("hartslag")
    basis = nf.checksum(nodes)
    ander = copy.deepcopy(nodes)
    for node in ander:
        if node["type"] == "tab":
            node["disabled"] = True
        if node["type"] == "http request":
            node["credentials"] = {"password": "x"}
    assert nf.checksum(ander) == basis and nf.checksum(list(reversed(ander))) == basis
    ander[1]["name"] = str(ander[1].get("name", "")) + "!"
    assert nf.checksum(ander) != basis


def _met(naam: str, **extra: Any) -> list[dict[str, Any]]:
    """De hartslagbundel met één extra node op het tabblad."""
    nodes = _bundel("hartslag")
    nodes.append({"id": f"jlp-hs-{naam}", "z": "jlp-hartslag", "wires": [], **extra})
    return nodes


def _wijzig(naam: str, node_id: str, **velden: Any) -> list[dict[str, Any]]:
    nodes = _bundel(naam)
    (node,) = [n for n in nodes if n["id"] == node_id]
    node.update(velden)
    return nodes


def _regel(node_id: str, veld: str, **regel: Any) -> list[dict[str, Any]]:
    """De hartslagbundel met één extra regel in een change-node."""
    nodes = _bundel("hartslag")
    (node,) = [n for n in nodes if n["id"] == node_id]
    node["rules"].append({"t": "set", "p": veld, "pt": "msg", "to": "x", "tot": "str", **regel})
    return nodes


AFGEWEZEN = {
    "exec-node": (lambda: _met("exec", type="exec", command="id"), "nodetype_niet_toegestaan"),
    "function-node": (lambda: _met("fn", type="function", func="return msg;"), "nodetype_niet_toegestaan"),
    "bestand-node": (lambda: _met("file", type="file", filename="/data/x"), "nodetype_niet_toegestaan"),
    "http-in": (lambda: _met("in", type="http in", url="/x", method="post"), "nodetype_niet_toegestaan"),
    "contrib-node": (lambda: _met("tg", type="telegram sender"), "nodetype_niet_toegestaan"),
    "template-node": (lambda: _met("tpl", type="template", template="{{payload}}"), "nodetype_niet_toegestaan"),
    "vast-adres": (lambda: _wijzig("hartslag", "jlp-hs-stuur", url="https://elders.example/x"), "adres_in_bundel"),
    "ip-adres": (lambda: _met("c", type="comment", info="zie 192.0.2.5"), "adres_in_bundel"),
    "andere-url": (lambda: _regel("jlp-hs-bouw", "url", to="payload.url", tot="jsonata"), "url_niet_toegestaan"),
    "url-deel": (lambda: _regel("jlp-hs-bouw", "url.x"), "url_niet_toegestaan"),
    "koppen-zetten": (lambda: _regel("jlp-hs-bouw", "headers", to="{}", tot="json"), "verzoek_overschreven"),
    "methode-zetten": (lambda: _regel("jlp-hs-bouw", "method", to="DELETE"), "verzoek_overschreven"),
    "globale-context": (lambda: _regel("jlp-hs-bouw", "x", pt="global"), "change_regel"),
    "andere-env": (lambda: _regel("jlp-hs-bouw", "x", to="$env('PATH')", tot="jsonata"), "jsonata_bron"),
    "token-in-git": (lambda: _met("c", type="comment", info="Bearer abcdefghijklmnop"), "geheim_in_bundel"),
    "credentials-in-git": (lambda: _wijzig("hartslag", "jlp-hs-stuur", credentials={"password": "x"}),
                           "credentials_in_bundel"),
    "http-zonder-route": (lambda: _met("los", type="http request", method="GET", url="", tls="", proxy="",
                                       persist=False, authType="bearer", headers=[], paytoqs="ignore", ret="obj"),
                          "http_zonder_route"),
    "http-methode": (lambda: _wijzig("hartslag", "jlp-hs-stuur", method="DELETE"), "http_methode"),
    "http-methode-uit-msg": (lambda: _wijzig("hartslag", "jlp-hs-stuur", method="use"), "http_methode"),
    "http-zonder-token": (lambda: _wijzig("hartslag", "jlp-hs-stuur", authType=""), "http_instelling"),
    "http-proxy": (lambda: _wijzig("hartslag", "jlp-hs-stuur", proxy="jlp-proxy"), "http_instelling"),
    "debug-naar-log": (lambda: _wijzig("hartslag", "jlp-hs-toon", console=True), "debug_naar_log"),
    "actieflow-aan": (lambda: _wijzig("testflow-herstart", "jlp-testflow-herstart", disabled=False),
                      "actieflow_moet_uit_staan"),
    "draad-naar-buiten": (lambda: _wijzig("hartslag", "jlp-hs-tik", wires=[["jlp-elders"]]), "draad_naar_buiten"),
    "vreemd-id": (lambda: _met("X", type="comment"), "id_ongeldig"),
    "zonder-versie": (lambda: _wijzig("hartslag", "jlp-hartslag", info="geen versie"), "tab_versie"),
    # Review P2 (S-P2-395, R-VEILIG-1): $env('HUB_URL') leest eerst de env van het tabblad (of de groep) en pas daarna
    # de globale; met HUB_URL op het tabblad ging het service-token naar een ander adres. Een hostnaam zonder schema of
    # een IPv4-adres als één getal herkent de adrescontrole niet; daarom: geen env op een node en een witte lijst van
    # velden op het tabblad.
    "tab-env-hostnaam": (lambda: _wijzig("hartslag", "jlp-hartslag", env=[
        {"name": "HUB_URL", "value": "aanvaller.example.net", "type": "str"}]), "veld_niet_toegestaan"),
    "tab-env-ip-als-getal": (lambda: _wijzig("hartslag", "jlp-hartslag", env=[
        {"name": "HUB_URL", "value": "3232235777:8080", "type": "str"}]), "veld_niet_toegestaan"),
    "tab-env-hex": (lambda: _wijzig("hartslag", "jlp-hartslag", env=[
        {"name": "HUB_URL", "value": "0xc0a80101", "type": "str"}]), "veld_niet_toegestaan"),
    "tab-env-leeg-van-naam": (lambda: _wijzig("hartslag", "jlp-hartslag", env=[
        {"name": "X", "value": "y", "type": "str"}]), "veld_niet_toegestaan"),
    "tab-onbekend-veld": (lambda: _wijzig("hartslag", "jlp-hartslag", locked=True), "veld_niet_toegestaan"),
    "node-env": (lambda: _wijzig("hartslag", "jlp-hs-bouw", env=[{"name": "HUB_URL", "value": "x", "type": "str"}]),
                 "veld_niet_toegestaan"),
    "node-in-groep": (lambda: _wijzig("hartslag", "jlp-hs-stuur", g="jlp-groep"), "veld_niet_toegestaan"),
    # Herreview P2 (R-VEILIG-1, herstelpoging 2): Node-RED haalt bij het zetten van een eigenschap zelf 'msg.' eraf en
    # verstaat aanhalingstekens en haken; een tweede regel met p = msg.url (of 'url', "url", msg['url']) na de vaste
    # route zette msg.url op een adres zonder schema, en de controle vergeleek alleen de letterlijke wortel. Daarom is
    # een eigenschapsnaam nu alleen een eenvoudige naam (letters, cijfers, _ en punten) en nooit 'msg…'.
    "change-msg-url": (lambda: _regel("jlp-hs-bouw", "msg.url", to="aanvaller.example.net/api/v1/monitoring/heartbeat"),
                       "change_regel"),
    "change-msg-url-ip-als-getal": (lambda: _regel("jlp-hs-bouw", "msg.url", to="3232235777:8080/x"), "change_regel"),
    "change-url-enkele-aanhalingstekens": (lambda: _regel("jlp-hs-bouw", "'url'", to="aanvaller.example.net/x"),
                                           "change_regel"),
    "change-url-dubbele-aanhalingstekens": (lambda: _regel("jlp-hs-bouw", '"url"', to="0xc0a80101/x"), "change_regel"),
    "change-msg-url-haken": (lambda: _regel("jlp-hs-bouw", "msg['url']", to="aanvaller.example.net/x"), "change_regel"),
    "change-msg-url-deel": (lambda: _regel("jlp-hs-bouw", "msg.url.x"), "change_regel"),
    "change-msg-headers": (lambda: _regel("jlp-hs-bouw", "msg.headers", to="{}", tot="json"), "change_regel"),
    "change-msg-method": (lambda: _regel("jlp-hs-bouw", "msg.method", to="DELETE"), "change_regel"),
    "change-eigenschap-met-spatie": (lambda: _regel("jlp-hs-bouw", " url"), "change_regel"),
    "change-eigenschap-niet-ascii": (lambda: _regel("jlp-hs-bouw", "urÄ±"), "change_regel"),
    "change-wortel-msg": (lambda: _regel("jlp-hs-bouw", "msg", to="{}", tot="json"), "change_regel"),
}


def test_property_names_in_change_rules_are_plain_or_refused() -> None:
    """R-VEILIG-1 (herstelpoging 2): elke schrijfwijze die Node-RED als msg.url of msg.headers leest, wordt geweigerd;
    de bundels uit Git gebruiken alleen eenvoudige namen en blijven in orde."""
    for geval in ("change-msg-url", "change-url-enkele-aanhalingstekens", "change-url-dubbele-aanhalingstekens",
                  "change-msg-url-haken", "change-msg-headers"):
        bevindingen = nf.lint(AFGEWEZEN[geval][0]())
        assert any(b.startswith("change_regel") for b in bevindingen), (geval, bevindingen)
    assert all(nf.lint(nodes) == [] for nodes in nf.bundels_in_git().values())
    assert nf.EIGENSCHAP_RE.fullmatch("payload.items") and nf.EIGENSCHAP_RE.fullmatch("mediaguard_staat")
    for fout in ("'url'", '"url"', "msg['url']", "url[0]", " url", "url ", "", "urÄ±", "a..b", ".url", "url."):
        assert not nf.EIGENSCHAP_RE.fullmatch(fout), fout
    # 'msg.url' heeft wel de eenvoudige vorm; de wortel 'msg' wordt apart geweigerd (Node-RED haalt 'msg.' eraf)
    assert nf.EIGENSCHAP_RE.fullmatch("msg.url") and "change_regel" in _codes(AFGEWEZEN["change-msg-url"][0]())


@pytest.mark.parametrize("geval", sorted(AFGEWEZEN))
def test_own_check_refuses(geval: str) -> None:
    maak, code = AFGEWEZEN[geval]
    assert code in _codes(maak()), nf.lint(maak())


def test_import_refuses_before_touching_node_red() -> None:
    """Een afgekeurde bundel, een Hub-URL met inloggegevens of een leeg token: er gaat niets naar Node-RED."""

    class Dicht(nf.NodeRed):
        def __init__(self) -> None:
            self.url, self.kop = "http://ongebruikt.invalid", {}
            self.aanroepen = 0

        def flows(self) -> tuple[str, list[dict[str, Any]]]:
            self.aanroepen += 1
            raise AssertionError("Node-RED is aangeroepen")

    nr = Dicht()
    goed = {"hartslag": _bundel("hartslag")}
    with pytest.raises(nf.Fout):
        nf.importeer(nr, {"hartslag": _met("exec", type="exec", command="id")}, hub_url="https://hub.example",
                     token="t" * 30)
    for geval in ("tab-env-hostnaam", "tab-env-ip-als-getal", "node-env", "change-msg-url",
                  "change-msg-url-ip-als-getal", "change-url-enkele-aanhalingstekens",
                  "change-url-dubbele-aanhalingstekens", "change-msg-headers"):  # R-VEILIG-1
        with pytest.raises(nf.Fout):
            nf.importeer(nr, {"hartslag": AFGEWEZEN[geval][0]()}, hub_url="https://hub.example", token="t" * 30)
    for hub_url in ("https://gebruiker:geheim@hub.example", "ftp://hub.example", "https://hub.example/pad?x=1", ""):
        with pytest.raises(nf.Fout):
            nf.importeer(nr, goed, hub_url=hub_url, token="t" * 30)
    assert nr.aanroepen == 0


class _NepNodeRed(nf.NodeRed):
    """Node-RED zonder netwerk: ``flows`` geeft wat er staat, ``zet_flows`` slaat op; ``na_import`` kan de teruggelezen
    flows na de EERSTE schrijfactie veranderen (zoals een Node-RED die iets anders bewaart dan gestuurd); een latere
    schrijfactie (het terugzetten door het hulpmiddel) wordt ongewijzigd bewaard."""

    def __init__(self, flows: list[dict[str, Any]], na_import: Any = None) -> None:
        self.url, self.kop = "http://ongebruikt.invalid", {}
        self.staat = copy.deepcopy(flows)
        self.na_import = na_import
        self.geschreven: list[list[dict[str, Any]]] = []

    def flows(self) -> tuple[str, list[dict[str, Any]]]:
        return f"rev-{len(self.geschreven) + 1}", copy.deepcopy(self.staat)

    def zet_flows(self, rev: str, flows: list[dict[str, Any]]) -> str:
        # zoals Node-RED: credentials worden versleuteld apart bewaard en komen nooit terug via GET /flows
        self.staat = [{k: v for k, v in n.items() if k != "credentials"} for n in copy.deepcopy(flows)]
        self.geschreven.append(copy.deepcopy(self.staat))
        if self.na_import is not None and len(self.geschreven) == 1:
            self.na_import(self.staat)
        return f"rev-{len(self.geschreven) + 1}"


VORIGE_FLOWS: list[dict[str, Any]] = [
    {"id": "ander-tab", "type": "tab", "label": "van iemand anders", "disabled": False, "info": ""},
    {"id": "ander-n1", "type": "comment", "z": "ander-tab", "name": "blijft staan"},
    {"id": "jlp-global-config", "type": "global-config", "name": "global-config",
     "env": [{"name": "HUB_URL", "value": "https://vorige-hub.example", "type": "str"}]},
]


def _knoei_msg_url(flows: list[dict[str, Any]]) -> None:
    """Zoals een Node-RED (of iemand met schrijfrecht, tussen de import en het teruglezen) die na de vaste route een
    tweede regel op msg.url bewaart: de eigen controle moet dat bij het teruglezen zien."""
    bouw = next(n for n in flows if n["id"] == "jlp-hs-bouw")
    bouw["rules"].append({"t": "set", "p": "msg.url", "pt": "msg", "to": "aanvaller.example.net/x", "tot": "str"})


def test_import_checks_after_reading_back_that_only_the_global_hub_url_counts() -> None:
    """R-VEILIG-1: ná de import leest de import terug dat op de eigen tabbladen geen env staat (die gaat in Node-RED
    vóór de globale HUB_URL), dat de globale HUB_URL precies de opgegeven Hub-URL is en dat de teruggelezen nodes van
    elk eigen tabblad opnieuw door de eigen controle komen. Eén bevinding: Fout, en de flows van vóór de import staan
    terug (herstelpoging 2)."""
    bundels = {"hartslag": _bundel("hartslag")}
    goed = _NepNodeRed(VORIGE_FLOWS)
    uit = nf.importeer(goed, bundels, hub_url="https://hub.example", token="t" * 30)
    assert uit["hartslag"]["gelijk"] is True and uit["hartslag"]["eigen_controle"] == []
    assert len(goed.geschreven) == 1
    for node in goed.staat:
        if node["type"] == "global-config":
            assert node["env"] == [{"name": "HUB_URL", "value": "https://hub.example", "type": "str"}]
        else:
            assert not node.get("env")
    assert [n["id"] for n in goed.staat if n.get("z") == "ander-tab"] == ["ander-n1"]  # andermans tabblad blijft

    def tab_env(flows: list[dict[str, Any]]) -> None:
        next(n for n in flows if n["id"] == "jlp-hartslag")["env"] = [
            {"name": "HUB_URL", "value": "aanvaller.example.net", "type": "str"}]

    def andere_hub(flows: list[dict[str, Any]]) -> None:
        g = next(n for n in flows if n["type"] == "global-config")
        g["env"] = [{"name": "HUB_URL", "value": "https://elders.example", "type": "str"}]

    def leeg_env(flows: list[dict[str, Any]]) -> None:  # ook een leeg env-veld hoort niet op een eigen tabblad
        next(n for n in flows if n["id"] == "jlp-hartslag").update(env=[])

    def exec_node(flows: list[dict[str, Any]]) -> None:
        flows.append({"id": "jlp-hs-x", "type": "exec", "z": "jlp-hartslag", "command": "id", "wires": []})

    for knoei in (tab_env, andere_hub, leeg_env, _knoei_msg_url, exec_node):
        nep = _NepNodeRed(VORIGE_FLOWS, knoei)
        with pytest.raises(nf.Fout, match="teruggezet"):
            nf.importeer(nep, bundels, hub_url="https://hub.example", token="t" * 30)
        assert len(nep.geschreven) == 2 and nep.staat == VORIGE_FLOWS, knoei.__name__  # de vorige flows staan terug
        assert "t" * 30 not in json.dumps(nep.geschreven[1])


def test_import_reports_when_restoring_the_previous_flows_fails_too() -> None:
    class Dicht(_NepNodeRed):
        def zet_flows(self, rev: str, flows: list[dict[str, Any]]) -> str:
            if self.geschreven:
                raise nf.Fout("Flows schrijven geweigerd (HTTP 409).")
            return super().zet_flows(rev, flows)

    nep = Dicht(VORIGE_FLOWS, _knoei_msg_url)
    with pytest.raises(nf.Fout, match="NIET teruggezet"):
        nf.importeer(nep, {"hartslag": _bundel("hartslag")}, hub_url="https://hub.example", token="t" * 30)


def test_readback_runs_the_own_check_on_the_tabs_it_reads() -> None:
    """Ook los van een import (herstel, rooktest): staat er op een eigen tabblad iets dat de eigen controle weigert,
    dan geeft ``readback`` een Fout. Aan of uit (bedieningskeuze) telt daarbij niet mee."""
    bundels = {"testflow-herstart": _bundel("testflow-herstart")}
    aan = nf.importeer(_NepNodeRed(VORIGE_FLOWS), bundels, hub_url="https://hub.example", token="t" * 30,
                       inschakelen=frozenset({"testflow-herstart"}))
    assert aan["testflow-herstart"]["ingeschakeld"] is True and aan["testflow-herstart"]["eigen_controle"] == []
    kwaad = _NepNodeRed(_regel("jlp-hs-bouw", "msg.url", to="aanvaller.example.net/x"))
    with pytest.raises(nf.Fout, match="change_regel"):
        nf.readback(kwaad, {"hartslag": _bundel("hartslag")})
    assert nf.readback(_NepNodeRed(_bundel("hartslag")), {"hartslag": _bundel("hartslag")})["hartslag"]["gelijk"]


@pytest.mark.parametrize("tab_id", ["../../buiten/ontsnapt", "a/b", "<absoluut>", "..", "C:\\x", "jlp-x/../../y"])
def test_export_never_writes_outside_the_target_directory(tmp_path: Path, tab_id: str) -> None:
    """R-VEILIG-2: een tabblad-id uit Node-RED is invoer van buiten; ``exporteer`` gebruikt het nooit als pad. Een id
    dat niet in het lock staat en niet de vorm ``jlp-…`` heeft, krijgt een vaste veilige naam."""
    if tab_id == "<absoluut>":  # een absoluut pad, binnen de testmap zodat een rode test niets daarbuiten schrijft
        (tmp_path / "abs").mkdir()
        tab_id = str(tmp_path / "abs" / "x")
    doel = tmp_path / "werk" / "export"
    nr = _NepNodeRed([{"id": tab_id, "type": "tab", "label": "x"},
                      {"id": "n1", "type": "comment", "z": tab_id}])
    uit = nf.exporteer(nr, doel)
    alle = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert alle and all(p.startswith("werk/export/") and p.count("/") == 2 for p in alle), alle
    ((naam, rij),) = uit.items()
    assert rij["tab"] == tab_id and rij["vergelijking"] == "onbekend"
    assert re.fullmatch(r"tab-[0-9a-f]{16}", naam) and (doel / f"{naam}.json").is_file()


def test_export_keeps_lock_names_and_own_ids(tmp_path: Path) -> None:
    hartslag = _bundel("hartslag")
    eigen = {"id": "jlp-eigen", "type": "tab", "label": "JLP · eigen"}
    uit = nf.exporteer(_NepNodeRed([*hartslag, eigen]), tmp_path / "export")
    assert set(uit) == {"hartslag", "jlp-eigen"} and uit["hartslag"]["vergelijking"] == "gelijk_aan_lock"
    assert sorted(p.name for p in (tmp_path / "export").iterdir()) == ["hartslag.json", "jlp-eigen.json"]


# ---------------------------------------------------------------------- documentatie en bewaard bewijs
def test_readme_documents_bundles_event_schema_and_limits() -> None:
    tekst = (nf.NR_DIR / "README.md").read_text(encoding="utf-8")
    for naam, rij in nf.lees_lock()["bundels"].items():
        assert f"flows/{naam}.json" in tekst and rij["versie"] in tekst
        for event_type in rij["event_types"]:
            assert event_type in tekst
    for woord in ("NIET GEVALIDEERD", "capability_unavailable", "my_house", "herstel", "flows.lock.json"):
        assert woord in tekst, woord


def test_saved_report_is_complete_and_honest() -> None:
    """Bewaard rapport is bewijs in EVIDENCE.json; het hoort bij precies deze image en deze bundels."""
    tekst = RAPPORT.read_text(encoding="utf-8")
    r = json.loads(tekst)
    m = _manifest()
    assert r["uitkomst"] == "geslaagd" and r["validatie"] == "Sim-getest; NIET GEVALIDEERD (echte NAS)"
    assert "geen TrueNAS" in r["omgeving"] and len(r["niet_gedekt"]) >= 4
    assert r["images"] == {"node-red": f"{m['image']['repository']}:{m['image']['tag']}@{m['image']['digest']}"}
    stappen = {s["id"]: s for s in r["stappen"]}
    assert list(stappen) == [f"N{i}" for i in range(1, 10)]
    assert {s["uitkomst"] for s in stappen.values()} == {"geslaagd"}
    assert stappen["N1"]["details"]["readback_klopt"] is True and stappen["N1"]["details"]["uids"] == ["568"]
    assert not {"exec", "function", "file", "http in"} & set(stappen["N1"]["details"]["node_types"])
    assert stappen["N2"]["details"]["monitor_slaagt"] is False  # de controleproef onderscheidt echt
    lock = nf.lees_lock()["bundels"]
    for naam, rij in stappen["N4"]["details"]["readback"].items():
        assert rij["gelezen"] == lock[naam]["sha256"], f"{naam}: rapport hoort bij een andere bundelversie"
    assert stappen["N4"]["details"]["token_in_flows_json"] == 0
    besluiten = stappen["N6"]["details"]["besluiten"]
    assert besluiten["node-red"]["risk_class"] == "R1" and besluiten["authentik"]["status"] == "wacht_op_goedkeuring"
    assert stappen["N6"]["details"]["inject_op_uitgeschakelde_flow"] != 200
    assert stappen["N9"]["details"]["meldingen_rond_herstart"] == [["flow.app_watch.down", "mediaguard"]] * 2
    assert stappen["N9"]["details"]["hub_na_3x_interval"]["status"] == "unknown"
    assert stappen["N9"]["details"]["incident_in_echte_hub_of_uptime_kuma"] == "NIET GEVALIDEERD"
    assert not nf.GEHEIM_RE.search(tekst) and "Gereed" not in tekst
