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

import httpx
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
    # Besluit C-11 (gebruiker, 2026-11-11): geen anonieme toegang; geen gebruiker of geheim in het manifest (C-12)
    assert o["adminAuth"] == {"type": "credentials", "users": []}
    assert not set(o["nodesIncludes"]) & GEVAARLIJKE_NODEBESTANDEN
    assert {"20-inject.js", "15-change.js", "10-switch.js", "21-httprequest.js", "21-debug.js", "25-catch.js",
            "91-global-config.js"} <= set(o["nodesIncludes"])
    assert o["externalModules"] == {"autoInstall": False, "palette": {"allowInstall": False, "allowUpdate": False,
                                                                     "allowUpload": False},
                                    "modules": {"allowInstall": False}}
    assert o["functionExternalModules"] is False and o["editorTheme.projects.enabled"] is False
    assert o["diagnostics"] == {"enabled": False, "ui": False}


ANONIEME_RECHTEN = {
    "default-read": {"type": "credentials", "users": [], "default": {"permissions": "read"}},
    "default-alles": {"type": "credentials", "users": [], "default": {"permissions": "*"}},
    "default-lijst": {"type": "credentials", "users": [], "default": {"permissions": ["flows.read"]}},
    "default-leeg-object": {"type": "credentials", "users": [], "default": {}},
    "default-leeg": {"type": "credentials", "users": [], "default": None},
    "default-tekst": {"type": "credentials", "users": [], "default": "read"},
}
NIET_C11 = {
    **ANONIEME_RECHTEN,
    "gebruiker-in-manifest": {"type": "credentials", "users": [
        {"username": "beheer", "password": "x", "permissions": "*"}]},
    "geen-adminauth": None,
    "ander-type": {"type": "strategy", "users": []},
    "extra-sleutel": {"type": "credentials", "users": [], "tokens": "x"},
}


@pytest.mark.parametrize("geval", sorted(NIET_C11))
def test_admin_auth_without_anonymous_rights_or_users_is_enforced(geval: str, tmp_path: Path) -> None:
    """Besluit C-11 (negatief, V-086): elke ``default`` in ``adminAuth`` (welke rechten ook), een gebruiker in het
    manifest, of een andere vorm dan precies ``{"type": "credentials", "users": []}``: ``lees_overrides`` weigert het
    bestand, dus ``render``, ``render --check`` en de repotests hierboven falen."""
    data = copy.deepcopy(nf.lees_overrides())
    if NIET_C11[geval] is None:
        del data["overrides"]["adminAuth"]
    else:
        data["overrides"]["adminAuth"] = NIET_C11[geval]
    pad = tmp_path / "settings-overrides.json"
    pad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(nf.Fout, match="adminAuth"):
        nf.lees_overrides(pad)
    assert nf.main(["render"]) == 0  # het echte bestand blijft in orde


def test_the_manifest_gives_no_anonymous_rights() -> None:
    """Besluit C-11 in het manifest zelf: FLOWS zet ``adminAuth`` zonder ``default`` en zonder gebruikers."""
    flows = nf.manifest_flows_env() or ""
    assert '-D adminAuth={"type":"credentials","users":[]} ' in flows
    assert '"default"' not in flows and "permissions" not in flows
    assert nf.render_flows_env(nf.lees_overrides()) == flows


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
    # Gemeten in de rooktest (N1/N2, 2026-11-11, deze digest): met FLOWS zonder anonieme rechten 401, zonder FLOWS 200
    assert monitor["pad"] == "/diagnostics" and monitor["verwachte_status"] == 401
    assert {d["app_id"] for d in m["dependencies"]} == {"ai-server-manager"}
    assert m["validation_status"] == "unvalidated"
    tekst = nf.MANIFEST.read_text(encoding="utf-8")
    assert not nf.GEHEIM_RE.search(tekst)
    assert "NODE_RED_CREDENTIAL_SECRET" not in tekst and "credentialSecret" not in tekst


def test_post_install_checks_follow_decision_c11() -> None:
    """Besluit C-11: na de installatie controleert de Hub dat een anonieme aanvraag niets krijgt (401 op dezelfde paden
    als de controle ``anoniem_geweigerd`` van home-control-hub). "Exec en function niet geladen" (ADR-0025) kan alleen
    met een beheerder (``GET /nodes``) en is daarom een handmatige controle (tekst)."""
    checks = {c["id"]: c for c in _manifest()["post_install_checks"]}
    assert list(checks) == ["beveiliging_actief", "anoniem_geweigerd", "schrijven_vraagt_login",
                            "exec_en_function_niet_geladen", "beheerder_en_flows"]
    assert checks["beveiliging_actief"]["soort"] == "http"
    assert "GET /diagnostics" in checks["beveiliging_actief"]["beschrijving_nl"]
    assert "401" in checks["beveiliging_actief"]["beschrijving_nl"]
    anoniem = checks["anoniem_geweigerd"]
    assert anoniem["soort"] == "http" and "401" in anoniem["beschrijving_nl"] and "C-11" in anoniem["beschrijving_nl"]
    for pad in ("/flows", "/nodes", "/settings"):
        assert f"GET {pad}" in anoniem["beschrijving_nl"], pad
    assert "POST /flows" in checks["schrijven_vraagt_login"]["beschrijving_nl"]
    exec_check = checks["exec_en_function_niet_geladen"]
    assert exec_check["soort"] == "handmatig"
    for woord in ("GET /nodes", "beheerder", "exec", "function", "N3"):
        assert woord in exec_check["beschrijving_nl"], woord
    assert checks["beheerder_en_flows"]["soort"] == "handmatig"
    assert "dicht" in checks["beheerder_en_flows"]["beschrijving_nl"]


def test_no_text_promises_anonymous_reading_or_403() -> None:
    """Besluit C-11: geen tekst belooft nog anoniem lezen of 403 (manifest, README van Node-RED, het hulpmiddel, en de
    Node-RED-regels van ``install/manifests/README.md``). R-MANIFEST-2: ``description_nl`` zegt wat het manifest
    (nog) niet levert."""
    m = _manifest()
    for pad in (nf.MANIFEST, nf.NR_DIR / "README.md", ROOT / "tools" / "nodered_flows.py",
                ROOT / "tools" / "nodered_rooktest.py"):
        tekst = pad.read_text(encoding="utf-8")
        for fout in ("alleen-lezen", "anoniem alleen", "verwacht 403", "403 op", "(403)"):
            assert fout not in tekst, (pad.name, fout)
    regels = [r for r in (ROOT / "install/manifests/README.md").read_text(encoding="utf-8").splitlines()
              if "Node-RED" in r or "node-red" in r]
    assert len(regels) >= 4
    for regel in regels:
        assert "alleen-lezen" not in regel and "403" not in regel, regel
    beschrijving = " ".join(m["description_nl"].split())
    for woord in ("beheerder", "flows", "Hub-token", "aanmelding"):
        assert woord in beschrijving, woord
    assert "aanmelding" in m["network"]["ports"][0]["purpose_nl"]


# ---------------------------------------------------------------------- bundels en lock
def test_bundles_pass_the_own_check_and_the_lock_is_current() -> None:
    bundels = nf.bundels_in_git()
    assert set(bundels) == {"hartslag", "mediaguard-offline", "testflow-herstart"}
    for naam, nodes in bundels.items():
        assert nf.lint(nodes) == [], naam
    assert nf.lock_inhoud(bundels) == nf.lees_lock(), "draai: python tools/nodered_flows.py lock"


def test_make_nodered_fails_on_a_changed_bundle_a_bad_bundle_or_a_stale_manifest(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Negatief voor ``make nodered`` (en de CI-stap die het draait): een gewijzigde bundel zonder nieuw lock laat
    ``lock --check`` falen, een bundel met een exec-node laat ``lint`` falen, en een manifest met een andere ``FLOWS``
    laat ``render --check`` falen. Op een kopie in een eigen map; de repo blijft ongewijzigd."""
    flows = tmp_path / "flows"
    flows.mkdir()
    for naam, nodes in nf.bundels_in_git().items():
        (flows / f"{naam}.json").write_text(json.dumps(nodes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lock = tmp_path / "flows.lock.json"
    lock.write_text(nf.LOCK.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = tmp_path / "node-red.manifest.yaml"
    manifest.write_text(nf.MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(nf, "FLOWS_DIR", flows)
    monkeypatch.setattr(nf, "LOCK", lock)
    monkeypatch.setattr(nf, "MANIFEST", manifest)
    assert [nf.main(["render", "--check"]), nf.main(["lint"]), nf.main(["lock", "--check"])] == [0, 0, 0]
    gewijzigd = _wijzig("hartslag", "jlp-hs-toon", name="ander antwoord")
    (flows / "hartslag.json").write_text(json.dumps(gewijzigd), encoding="utf-8")
    assert nf.main(["lint"]) == 0 and nf.main(["lock", "--check"]) == 1
    (flows / "hartslag.json").write_text(json.dumps(_met("exec", type="exec", command="id")), encoding="utf-8")
    assert nf.main(["lint"]) == 1
    manifest.write_text(nf.MANIFEST.read_text(encoding="utf-8").replace('"users":[]', '"users":[],"default":{}', 1),
                        encoding="utf-8")
    assert nf.main(["render", "--check"]) == 1
    assert "loopt achter" in capsys.readouterr().err


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
    # S-P2-410 bouwpunt 9b (R-VEILIG-3, vervolgpunt): via __proto__, constructor of prototype vervuilt een bundel
    # Object.prototype op de echte image (a = msg.__proto__; a.env.http_proxy = …). Geweigerd in p en in to (bij een
    # bron msg, flow of global), per segment.
    "proto-in-p": (lambda: _regel("jlp-hs-bouw", "__proto__.env"), "change_regel"),
    "proto-diep-in-p": (lambda: _regel("jlp-hs-bouw", "a.__proto__.headers"), "change_regel"),
    "constructor-in-p": (lambda: _regel("jlp-hs-bouw", "constructor.x"), "change_regel"),
    "prototype-in-p": (lambda: _regel("jlp-hs-bouw", "a.prototype"), "change_regel"),
    "proto-in-p-flow": (lambda: _regel("jlp-hs-bouw", "__proto__.x", pt="flow"), "change_regel"),
    "constructor-in-p-flow": (lambda: _regel("jlp-hs-bouw", "a.constructor", pt="flow"), "change_regel"),
    "proto-in-to-msg": (lambda: _regel("jlp-hs-bouw", "a", to="__proto__", tot="msg"), "change_regel"),
    "constructor-in-to-msg": (lambda: _regel("jlp-hs-bouw", "a", to="payload.constructor", tot="msg"),
                              "change_regel"),
    "prototype-in-to-flow": (lambda: _regel("jlp-hs-bouw", "a", to="x.prototype", tot="flow"), "change_regel"),
    "proto-in-to-global": (lambda: _regel("jlp-hs-bouw", "a", to="__proto__", tot="global"), "change_regel"),
    "constructor-in-to-global": (lambda: _regel("jlp-hs-bouw", "a", to="constructor", tot="global"), "change_regel"),
    "prototype-in-to-msg": (lambda: _regel("jlp-hs-bouw", "a", to="prototype", tot="msg"), "change_regel"),
    "proto-haken-in-to": (lambda: _regel("jlp-hs-bouw", "a", to="payload['__proto__']", tot="msg"), "change_regel"),
    # S-P2-410 bouwpunt 9c: de oude vorm van de change-node (zonder rules, met action/property/to) maakt Node-RED zelf
    # tot een regel (15-change.js); zo kwamen msg.rejectUnauthorized, msg.followRedirects en msg.requestTimeout langs
    # verzoek_overschreven. Die vorm wordt geweigerd.
    "oude-vorm-rejectUnauthorized": (lambda: _met("oud", type="change", action="replace", property="rejectUnauthorized",
                                                  to="false", reg=False), "change_regel"),
    "oude-vorm-followRedirects": (lambda: _met("oud", type="change", action="replace", property="followRedirects",
                                               to="true", reg=False), "change_regel"),
    "oude-vorm-requestTimeout": (lambda: _met("oud", type="change", action="replace", property="requestTimeout",
                                              to="1", reg=False), "change_regel"),
    "oude-vorm-rules-null": (lambda: _wijzig("hartslag", "jlp-hs-bouw", rules=None, action="replace",
                                             property="headers", to="{}"), "change_regel"),
    # S-P2-410 bouwpunt 9a (R-VEILIG-5): gegevens die geen lijst van objecten zijn geven een bevinding, geen crash.
    "regel-geen-object": (lambda: _wijzig("hartslag", "jlp-hs-bouw", rules=["url"]), "change_regel"),
    "rules-als-object": (lambda: _wijzig("hartslag", "jlp-hs-bouw", rules={"p": "url"}), "change_regel"),
    "wires-geen-lijst": (lambda: _wijzig("hartslag", "jlp-hs-tik", wires="jlp-hs-bouw"), "draad_ongeldig"),
    "wires-uitgang-geen-lijst": (lambda: _wijzig("hartslag", "jlp-hs-tik", wires=["jlp-hs-bouw"]), "draad_ongeldig"),
    "wires-doel-geen-tekst": (lambda: _wijzig("hartslag", "jlp-hs-tik", wires=[[{"id": "jlp-hs-bouw"}]]),
                              "draad_ongeldig"),
    "props-als-object": (lambda: _wijzig("hartslag", "jlp-hs-tik", props={"p": "payload"}), "inject_velden"),
    "props-als-getal": (lambda: _wijzig("hartslag", "jlp-hs-tik", props=3), "inject_velden"),
    # S-P2-410 bouwpunt 9d: gemeten op de gepinde image: bij een doorverwijzing (307/302) naar een andere host of poort
    # stuurt de http request-node de bearer-kop mee (Node-RED zet de kop terug in hoofdletters, waardoor got hem niet
    # weghaalt). Elke http request krijgt daarom van zijn route-node msg.followRedirects = false, en alleen die waarde.
    "zonder-redirect-uit": (lambda: _zonder_redirect_uit("hartslag", "jlp-hs-bouw"), "redirect_niet_uit"),
    "redirect-aan": (lambda: _regel("jlp-hs-bouw", "followRedirects", to="true", tot="bool"), "verzoek_overschreven"),
    "redirect-als-tekst": (lambda: _regel("jlp-hs-bouw", "followRedirects", to="false", tot="str"),
                           "verzoek_overschreven"),
    "redirect-weg": (lambda: _regel("jlp-hs-bouw", "followRedirects", t="delete"), "verzoek_overschreven"),
    "redirect-deel": (lambda: _regel("jlp-hs-bouw", "followRedirects.x", to="false", tot="bool"),
                      "verzoek_overschreven"),
}


def _zonder_redirect_uit(naam: str, node_id: str) -> list[dict[str, Any]]:
    nodes = _bundel(naam)
    (node,) = [n for n in nodes if n["id"] == node_id]
    voor = len(node["rules"])
    node["rules"] = [r for r in node["rules"] if r.get("p") != "followRedirects"]
    assert len(node["rules"]) == voor - 1
    return nodes


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


def test_prototype_segments_are_refused_but_plain_sources_stay_allowed() -> None:
    """Bouwpunt 9b zonder te veel te weigeren: een gewone bron (``tot`` msg/flow met een eenvoudige naam) blijft
    toegestaan, ook met een deel dat op een verboden woord lijkt."""
    for to, tot in (("payload", "msg"), ("payload.items", "msg"), ("mediaguard_staat", "flow"),
                    ("proto_type", "msg"), ("constructor_naam", "msg")):
        assert not {"change_regel"} & _codes(_regel("jlp-hs-bouw", "kopie", to=to, tot=tot)), (to, tot)
    assert frozenset({"__proto__", "constructor", "prototype"}) == nf.VERBODEN_SEGMENTEN
    for geval in ("proto-in-p", "proto-in-to-msg", "constructor-in-to-global", "prototype-in-to-flow"):
        assert any("__proto__" in b or "constructor" in b or "prototype" in b
                   for b in nf.lint(AFGEWEZEN[geval][0]())), geval


def test_every_http_request_in_git_gets_redirects_off_from_its_route_node() -> None:
    """Bouwpunt 9d: elke http request-node in de bundels uit Git wordt gevoed door één change-node met een vaste
    Hub-route én ``msg.followRedirects = false`` (bool)."""
    gezien = 0
    for naam, nodes in nf.bundels_in_git().items():
        per_id = {n["id"]: n for n in nodes}
        for node in nodes:
            if node["type"] != "http request":
                continue
            voor = [n for n in nodes if node["id"] in [d for u in n.get("wires", []) for d in u]]
            assert len(voor) == 1 and voor[0]["type"] == "change", (naam, node["id"])
            regels = per_id[voor[0]["id"]]["rules"]
            assert {"t": "set", "p": "followRedirects", "pt": "msg", "to": "false", "tot": "bool"} in regels, naam
            gezien += 1
    assert gezien == 4  # hartslag, mediaguard-offline (2), testflow-herstart


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


class _NepNodeRedValtUit(_NepNodeRed):
    """Node-RED die de import aanneemt, maar bij het teruglezen onbereikbaar is (``httpx.ConnectError``)."""

    def __init__(self, flows: list[dict[str, Any]]) -> None:
        super().__init__(flows)
        self.gelezen = 0

    def flows(self) -> tuple[str, list[dict[str, Any]]]:
        self.gelezen += 1
        if self.gelezen == 2:
            raise httpx.ConnectError("verbinding verbroken")
        return super().flows()


def _regel_als_tekst(flows: list[dict[str, Any]]) -> None:
    """Na de import bewaart Node-RED (of iemand met schrijfrecht) in de route-node een regel die geen object is."""
    next(n for n in flows if n["id"] == "jlp-hs-bouw")["rules"].append("url")


def test_import_restores_the_previous_flows_whatever_breaks_the_readback() -> None:
    """R-VEILIG-5 (S-P2-395, bouwpunt 9a): faalt het teruglezen met iets anders dan een ``Fout`` (een netwerkfout, of
    teruggelezen gegevens waarop de eigen controle vroeger crashte), dan zet ``importeer`` de vorige flows toch terug
    en geeft een Nederlandse ``Fout`` met de reden (fail-closed; geen traceback, de flows met het token blijven niet
    staan)."""
    bundels = {"hartslag": _bundel("hartslag")}
    valt_uit = _NepNodeRedValtUit(VORIGE_FLOWS)
    with pytest.raises(nf.Fout, match="teruggezet") as fout:
        nf.importeer(valt_uit, bundels, hub_url="https://hub.example", token="t" * 30)
    assert "ConnectError" in str(fout.value) or "verbinding" in str(fout.value)
    assert len(valt_uit.geschreven) == 2 and valt_uit.staat == VORIGE_FLOWS
    tekst = _NepNodeRed(VORIGE_FLOWS, _regel_als_tekst)
    with pytest.raises(nf.Fout, match="teruggezet") as fout:
        nf.importeer(tekst, bundels, hub_url="https://hub.example", token="t" * 30)
    assert "change_regel" in str(fout.value)
    assert len(tekst.geschreven) == 2 and tekst.staat == VORIGE_FLOWS
    assert "t" * 30 not in json.dumps(tekst.geschreven[1])


def test_import_says_so_when_restoring_fails_with_any_error() -> None:
    """Ook als het terugzetten zelf met een netwerkfout faalt: een ``Fout`` die zegt dat de flows NIET zijn
    teruggezet."""

    class Weg(_NepNodeRedValtUit):
        def zet_flows(self, rev: str, flows: list[dict[str, Any]]) -> str:
            if self.geschreven:
                raise httpx.ConnectError("weg")
            return super().zet_flows(rev, flows)

    with pytest.raises(nf.Fout, match="NIET teruggezet"):
        nf.importeer(Weg(VORIGE_FLOWS), {"hartslag": _bundel("hartslag")}, hub_url="https://hub.example",
                     token="t" * 30)


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


@pytest.mark.parametrize("opdracht", ["exporteer", "importeer"])
def test_cli_asks_for_an_administrator_with_a_clear_message(opdracht: str, tmp_path: Path,
                                                            capsys: pytest.CaptureFixture[str],
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    """Besluit C-11: Node-RED geeft zonder aanmelding niets. ``exporteer`` en ``importeer`` vragen daarom een beheerder
    (``--gebruiker``, wachtwoord uit ``NODERED_BEHEER_WACHTWOORD``); zonder: een Nederlandse melding en exitcode 1,
    geen traceback en geen netwerkverkeer (het adres bestaat niet)."""
    extra = ["--naar", str(tmp_path / "x")] if opdracht == "exporteer" else ["--hub-url", "https://hub.example"]
    basis = [opdracht, "--url", "http://ongebruikt.invalid", *extra]
    monkeypatch.setenv("NODERED_BEHEER_WACHTWOORD", "")
    assert nf.main(basis) == 1
    fout = capsys.readouterr().err
    assert "--gebruiker" in fout and "C-11" in fout and "Traceback" not in fout
    assert nf.main([*basis, "--gebruiker", "beheer"]) == 1  # gebruiker zonder wachtwoord: ook vóór het netwerk
    fout = capsys.readouterr().err
    assert "NODERED_BEHEER_WACHTWOORD" in fout and "Traceback" not in fout
    assert not (tmp_path / "x").exists()


def test_reading_without_login_gives_a_clear_error() -> None:
    """Krijgt het hulpmiddel toch een 401 (bijvoorbeeld een verlopen sessie), dan zegt de melding wat er nodig is."""
    nr = nf.NodeRed("http://nodered.invalid", httpx.Client(transport=httpx.MockTransport(
        lambda verzoek: httpx.Response(401, json={"error": "unauthorized"}))))
    with pytest.raises(nf.Fout, match="aanmelding"):
        nr.flows()


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
    """Bewaard rapport is bewijs in EVIDENCE.json; het hoort bij precies deze image en deze bundels, en toont besluit
    C-11 (anoniem 401), de eigen controle bij het teruglezen en de weigering en doorverwijzing van N10 (S-P2-410)."""
    tekst = RAPPORT.read_text(encoding="utf-8")
    r = json.loads(tekst)
    m = _manifest()
    assert r["uitkomst"] == "geslaagd" and r["validatie"] == "Sim-getest; NIET GEVALIDEERD (echte NAS)"
    assert "geen TrueNAS" in r["omgeving"] and len(r["niet_gedekt"]) >= 5
    assert r["images"] == {"node-red": f"{m['image']['repository']}:{m['image']['tag']}@{m['image']['digest']}"}
    stappen = {s["id"]: s for s in r["stappen"]}
    assert list(stappen) == [f"N{i}" for i in range(1, 11)]
    assert {s["uitkomst"] for s in stappen.values()} == {"geslaagd"}
    n1 = stappen["N1"]["details"]
    assert n1["readback_klopt"] is True and n1["uids"] == ["568"]
    mon = m["healthcheck"]["monitor"]
    assert n1["proeven"][f"GET {mon['pad']} (monitor: {mon['verwachte_status']})"] == mon["verwachte_status"] == 401
    for pad in ("/flows", "/nodes", "/settings"):  # besluit C-11
        assert n1["proeven"][f"GET {pad} anoniem (401)"] == 401, pad
    assert n1["proeven"]["POST /flows anoniem (401)"] == 401 and n1["anoniem_geweigerd"] is True
    assert n1["export_zonder_aanmelding"] == "geweigerd (HTTP 401)"
    n2 = stappen["N2"]["details"]
    assert n2["monitor_slaagt"] is False and n2["GET /diagnostics"] == 200  # de controleproef onderscheidt echt
    assert all(n2[f"GET {pad} anoniem"] == 200 for pad in ("/flows", "/nodes", "/settings"))
    n3 = stappen["N3"]["details"]
    assert not {"exec", "function", "file", "http in"} & set(n3["node_types"])
    assert {"inject", "change", "http request"} <= set(n3["node_types"]) and n3["GET /flows anoniem"] == 401
    lock = nf.lees_lock()["bundels"]
    readback = stappen["N4"]["details"]["readback"]
    assert set(readback) == set(lock)
    for naam, rij in readback.items():
        assert rij["gelezen"] == lock[naam]["sha256"], f"{naam}: rapport hoort bij een andere bundelversie"
        assert rij["eigen_controle"] == [], naam
    assert stappen["N4"]["details"]["token_in_flows_json"] == 0
    besluiten = stappen["N6"]["details"]["besluiten"]
    assert besluiten["node-red"]["risk_class"] == "R1" and besluiten["authentik"]["status"] == "wacht_op_goedkeuring"
    assert stappen["N6"]["details"]["inject_op_uitgeschakelde_flow"] != 200
    assert stappen["N9"]["details"]["meldingen_rond_herstart"] == [["flow.app_watch.down", "mediaguard"]] * 2
    assert stappen["N9"]["details"]["hub_na_3x_interval"]["status"] == "unknown"
    assert stappen["N9"]["details"]["incident_in_echte_hub_of_uptime_kuma"] == "NIET GEVALIDEERD"
    n10 = stappen["N10"]["details"]
    weigering = n10["weigering"]
    assert "veld_niet_toegestaan" in weigering["env_op_tabblad"]["codes"]
    assert "change_regel" in weigering["tweede_regel_op_msg_url"]["codes"]
    for naam in ("env_op_tabblad", "tweede_regel_op_msg_url"):
        assert weigering[naam]["geweigerd"] is True and weigering[naam]["flows_in_node_red_ongewijzigd"] is True
    assert weigering["hub_kreeg_de_heartbeat"] is True and weigering["luisteraar_ontving"] == 0
    proeven = n10["doorverwijzing_flow_uit_git"]
    assert set(proeven) == {f"{s} naar {w}" for s in (307, 302) for w in ("zelfde_host_andere_poort", "andere_host")}
    assert all(p["doorstuurder_kreeg_bearer"] is True and p["doel_ontving"] == 0 for p in proeven.values())
    controle = n10["controleproef_zonder_regel"]
    assert controle["doorverwijzing_gevolgd"] is True and controle["readback_ziet_het"] == "redirect_niet_uit"
    assert isinstance(controle["bearer_meegestuurd_naar_andere_host"], bool)
    assert all(v == {"gelijk": True, "eigen_controle": []} for v in n10["na_herstel"].values())
    assert not nf.GEHEIM_RE.search(tekst) and "Gereed" not in tekst
    assert set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", tekst)) <= {"127.0.0.1"}, "adres in het rapport (V-097)"
    assert "/home/" not in tekst and "/tmp/" not in tekst
