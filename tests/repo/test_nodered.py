"""Node-RED (I10-10-01…06, ADR-0023): manifest, instellingen, flowbundels, lock en het bewaarde rooktestrapport.

Wat hier zonder Docker te controleren is. De proef met de echte image tegen de Hub-simulator draait in CI-job
``rooktest`` en lokaal met ``make nodered-rooktest`` (``tools/nodered_rooktest.py``).
"""

from __future__ import annotations

import copy
import json
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
}


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
    for hub_url in ("https://gebruiker:geheim@hub.example", "ftp://hub.example", "https://hub.example/pad?x=1", ""):
        with pytest.raises(nf.Fout):
            nf.importeer(nr, goed, hub_url=hub_url, token="t" * 30)
    assert nr.aanroepen == 0


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
