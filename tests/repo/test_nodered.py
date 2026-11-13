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
# Vaste testkopie van elke vorige uitgebrachte versie van de bundels (byte voor byte uit Git: git show
# <commit>:install/node-red/flows/<bundel>.json), met de sha256 uit flows.lock.json van die commit (R-TECH-2,
# herstelpoging 2 van S-P2-410).
VERSIES_MAP = ROOT / "tests/repo/fixtures/node-red-versies"
VORIGE_VERSIES = {
    ("hartslag", "1.0.0"): "sha256:5653547b0322d1334507d58890b5ab9df31b5dca084453dd253e4e056570d5c0",  # 3e351a0
    ("mediaguard-offline", "1.0.0"): "sha256:aaa38e2272cd07a19347f9b92062ca12d5a87b51b0a88dbeb6d4fdb0e9ba53f4",
    ("testflow-herstart", "1.0.0"): "sha256:eb756371f4743541d011213d7b2c847f90cb42c5e38dd22d685d91bee4f036e8",
    ("hartslag", "1.1.0"): "sha256:319451308eb0c5ef20ce24f61d23d8fcfc2ee1c126f103578681473e5f356980",  # 69408c8
    ("mediaguard-offline", "1.1.0"): "sha256:c88682bec0d5f8ca7000107e45b868d3c3730a27eec3290121ec7d0b2ec0c1bf",
    ("testflow-herstart", "1.1.0"): "sha256:849caa32307801f6f2c55fe20564cdf37f01d594d8e264a662401ba3abe52c37",
    ("hartslag", "1.1.1"): "sha256:ff5b847d8c7d8bdf68a878d7748a7afa38bbabb79762322eb58e85feb14a1407",  # fd64562
    ("mediaguard-offline", "1.1.1"): "sha256:deba58ace395fbd859a8f15b0fc463cff6aad7c6eb8ad589c32d2210039f25ad",
    ("testflow-herstart", "1.1.1"): "sha256:0bba18b0d647c9ddd998840a5c767c51caf4b4e11723f4687c574898c06593a3",
}
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
    # R-VEILIG-2 (S-P2-410): een proxyvariabele in de omgeving van de container (http_proxy, all_proxy …) mag het
    # verzoek met het service-token niet via een proxy leiden; met een eigen, lege env leest de http request-node er
    # geen.
    assert o["proxyOptions"] == {"env": {}}


def test_the_manifest_turns_off_proxies_from_the_environment() -> None:
    """R-VEILIG-2: FLOWS in het manifest zet ``proxyOptions`` met een lege ``env`` (gemeten in rooktest N10: met
    http_proxy in de omgeving krijgt een proxy dan niets, zonder de instelling krijgt hij CONNECT en de bearer-kop)."""
    flows = nf.manifest_flows_env() or ""
    assert ' -D proxyOptions={"env":{}}' in flows
    assert flows.count("proxyOptions") == 1


TOEGESTANE_OVERRIDES = {"adminAuth", "nodesIncludes", "externalModules", "functionExternalModules",
                        "editorTheme.projects.enabled", "editorTheme.tours", "telemetry", "diagnostics", "proxyOptions"}
ONBEKENDE_OVERRIDES = {
    # R-VEILIG-4 (S-P2-410): Node-RED maakt van '-D adminAuth.default=…' een eigen instelling naast adminAuth; op de
    # image gaf dat anoniem lezen ('/flows' 200). Elke sleutel buiten de vaste lijst wordt geweigerd.
    "adminAuth.default": {"permissions": "read"},
    "adminAuth.default.permissions": "read",
    "adminAuth.users": [{"username": "x", "password": "y", "permissions": "*"}],
    "adminAuth.type": "credentials",
    "httpStatic": "/data",
    "httpAdminRoot": "/beheer",
    "httpNodeAuth": {"user": "x", "pass": "y"},
    "httpNodeRoot": "/x",
    "editorTheme": {"projects": {"enabled": True}},
    "proxyOptions.env.http_proxy": "http://elders.invalid",
    "credentialSecret": "x",
}


@pytest.mark.parametrize("sleutel", sorted(ONBEKENDE_OVERRIDES))
def test_overrides_only_accept_the_fixed_keys(sleutel: str, tmp_path: Path) -> None:
    """R-VEILIG-4: ``lees_overrides`` laat alleen een vaste lijst sleutels toe; ``adminAuth.default`` (en elke andere
    sleutel) geeft een ``Fout``, dus ``render`` en ``render --check`` falen ook."""
    data = copy.deepcopy(nf.lees_overrides())
    assert set(data["overrides"]) == TOEGESTANE_OVERRIDES
    data["overrides"][sleutel] = ONBEKENDE_OVERRIDES[sleutel]
    pad = tmp_path / "settings-overrides.json"
    pad.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(nf.Fout, match=re.escape(sleutel)):
        nf.lees_overrides(pad)


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
    # R-MANIFEST-2 (S-P2-410): de tekst zegt dat deze controle tot besluit C-12 open blijft en Node-RED zo lang niet
    # gezond is (bedoelde stand), en wat de beheerder nu wel kan nagaan (digest gelijk aan die van het rooktestrapport).
    tekst = " ".join(exec_check["beschrijving_nl"].split())
    for woord in ("C-12", "open", "niet gezond", "bedoelde stand", "digest", "nodered-rooktest-p2.json"):
        assert woord in tekst, woord
    assert 10 <= len(exec_check["beschrijving_nl"]) <= 1000  # grens van het schema (PostInstallCheck)
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


def _regel_zonder_tot(to: str) -> list[dict[str, Any]]:
    """Een set-regel zonder ``tot``: Node-RED (15-change.js) maakt daar zelf ``msg`` van als ``to`` met 'msg.' begint,
    en anders ``str``."""
    nodes = _bundel("hartslag")
    next(n for n in nodes if n["id"] == "jlp-hs-bouw")["rules"].append({"t": "set", "p": "meting", "pt": "msg",
                                                                        "to": to})
    return nodes


def _inject(*props: dict[str, Any]) -> list[dict[str, Any]]:
    """De hartslagbundel met andere ``props`` op de inject-node."""
    return _wijzig("hartslag", "jlp-hs-tik", props=[dict(p) for p in props])


def _switch(**velden: Any) -> list[dict[str, Any]]:
    """De bundel mediaguard-offline met andere velden op de switch-node (herstelpoging 2 van S-P2-410)."""
    return _wijzig("mediaguard-offline", "jlp-mg-kies", **velden)


def _switch_zonder(veld: str) -> list[dict[str, Any]]:
    nodes = _bundel("mediaguard-offline")
    next(n for n in nodes if n["id"] == "jlp-mg-kies").pop(veld)
    return nodes


def _debug(**velden: Any) -> list[dict[str, Any]]:
    """De hartslagbundel met andere velden op de debug-node."""
    return _wijzig("hartslag", "jlp-hs-toon", **velden)


def _omweg_r_tech() -> list[dict[str, Any]]:
    """R-TECH-1, de volledige omweg uit de review: de inject leest ``flow['__proto__']`` (op de image
    ``Object.prototype`` zodra de flowcontext gevuld is), de eerste regel vult de flowcontext, de tweede zet
    ``payload.headers.X_Proef``: dan erft elk object die kop, ook ``msg.headers`` van de http request-node."""
    nodes = _inject({"p": "payload", "v": "__proto__", "vt": "flow"})
    bouw = next(n for n in nodes if n["id"] == "jlp-hs-bouw")
    bouw["rules"] = [{"t": "set", "p": "staat", "pt": "flow", "to": "1", "tot": "num"},
                     {"t": "set", "p": "payload.headers.X_Proef", "pt": "msg", "to": "y", "tot": "str"},
                     *bouw["rules"][1:]]
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
    # (Sinds het herstel van S-P2-410 met een eigenschap van de witte lijst, zodat de weigering echt uit 'to' komt.)
    "proto-in-to-msg": (lambda: _regel("jlp-hs-bouw", "meting", to="__proto__", tot="msg"), "change_regel"),
    "constructor-in-to-msg": (lambda: _regel("jlp-hs-bouw", "meting", to="payload.constructor", tot="msg"),
                              "change_regel"),
    "prototype-in-to-flow": (lambda: _regel("jlp-hs-bouw", "meting", to="x.prototype", tot="flow"), "change_regel"),
    "proto-in-to-global": (lambda: _regel("jlp-hs-bouw", "meting", to="__proto__", tot="global"), "change_regel"),
    "constructor-in-to-global": (lambda: _regel("jlp-hs-bouw", "meting", to="constructor", tot="global"),
                                 "change_regel"),
    "prototype-in-to-msg": (lambda: _regel("jlp-hs-bouw", "meting", to="prototype", tot="msg"), "change_regel"),
    "proto-haken-in-to": (lambda: _regel("jlp-hs-bouw", "meting", to="payload['__proto__']", tot="msg"),
                          "change_regel"),
    # S-P2-410, herstel na de review (R-TECH-1 en R-VEILIG-1, blokkerend; V-086): de zwarte lijst van drie segmenten
    # liet andere wegen naar het prototype open. msg.hasOwnProperty = msg.isPrototypeOf zette msg.followRedirects
    # buiten werking (de doorverwijzing ging met het token naar een andere host), en een inject die flow['__proto__']
    # leest plus 'payload.headers.X' vervuilde Object.prototype (kop langs verzoek_overschreven). Nu een witte lijst:
    # msg-eigenschappen alleen precies de namen die de bundels gebruiken, flow/global-namen alleen [a-z_]+ zonder
    # eigenschap van het prototype, waardesoorten alleen str/num/bool/json/date (en jsonata alleen de uitdrukkingen
    # uit de bundels), in 'p', in 'to' en in de inject (ook de oude velden payload/payloadType).
    "omweg-r-veilig-1": (lambda: _regel("jlp-hs-bouw", "hasOwnProperty", to="isPrototypeOf", tot="msg"),
                         "change_regel"),
    "omweg-r-veilig-1-lookupgetter": (lambda: _regel("jlp-hs-bouw", "hasOwnProperty", to="__lookupGetter__",
                                                     tot="msg"), "change_regel"),
    "omweg-r-tech-1": (lambda: _omweg_r_tech(), "inject_bron"),
    "omweg-r-tech-1-alleen-change": (lambda: _omweg_r_tech(), "change_regel"),
    "p-niet-op-witte-lijst": (lambda: _regel("jlp-hs-bouw", "kopie"), "change_regel"),
    "p-pad-onder-witte-naam": (lambda: _regel("jlp-hs-bouw", "payload.headers.X_Proef", to="y"), "change_regel"),
    "p-flow-hoofdletters": (lambda: _regel("jlp-hs-bouw", "Staat", pt="flow", to="1", tot="num"), "change_regel"),
    "p-flow-pad": (lambda: _regel("jlp-hs-bouw", "staat.x", pt="flow", to="1", tot="num"), "change_regel"),
    "to-msg-niet-op-witte-lijst": (lambda: _regel("jlp-hs-bouw", "meting", to="isPrototypeOf", tot="msg"),
                                   "change_regel"),
    "to-msg-pad": (lambda: _regel("jlp-hs-bouw", "meting", to="payload.items", tot="msg"), "change_regel"),
    "to-flow-pad": (lambda: _regel("jlp-hs-bouw", "meting", to="staat.x", tot="flow"), "change_regel"),
    "to-global-hoofdletters": (lambda: _regel("jlp-hs-bouw", "meting", to="valueOf", tot="global"), "change_regel"),
    "tot-env": (lambda: _regel("jlp-hs-bouw", "meting", to="__proto__", tot="env"), "change_regel"),
    "tot-env-gewone-naam": (lambda: _regel("jlp-hs-bouw", "meting", to="HOME", tot="env"), "change_regel"),
    "tot-ontbreekt-bron-msg": (lambda: _regel_zonder_tot("msg.__proto__"), "change_regel"),
    "tot-ontbreekt-tekst": (lambda: _regel_zonder_tot("x"), "change_regel"),
    "tot-bin": (lambda: _regel("jlp-hs-bouw", "meting", to="[1]", tot="bin"), "change_regel"),
    "to-geen-tekst": (lambda: _regel("jlp-hs-bouw", "meting", to=["__proto__"], tot="flow"), "change_regel"),
    "tot-jsonata-flowcontext": (lambda: _regel("jlp-hs-bouw", "meting", to="$flowContext('__proto__')",
                                               tot="jsonata"), "change_regel"),
    "tot-jsonata-lookup": (lambda: _regel("jlp-hs-bouw", "meting", to="$lookup($$, '__proto__')", tot="jsonata"),
                           "change_regel"),
    "tot-jsonata-onbekend": (lambda: _regel("jlp-hs-bouw", "meting", to="payload", tot="jsonata"), "change_regel"),
    "tot-jsonata-hub-route-buiten-url": (lambda: _regel("jlp-hs-bouw", "meting",
                                                        to="$env('HUB_URL') & '/api/v1/apps?limit=200'",
                                                        tot="jsonata"), "change_regel"),
    "inject-vt-flow": (lambda: _inject({"p": "payload", "v": "__proto__", "vt": "flow"}), "inject_bron"),
    "inject-vt-global": (lambda: _inject({"p": "payload", "v": "constructor", "vt": "global"}), "inject_bron"),
    "inject-oude-velden-global": (lambda: _wijzig("hartslag", "jlp-hs-tik", payload="__proto__",
                                                  payloadType="global"), "inject_bron"),
    "inject-oude-velden-flow": (lambda: _wijzig("hartslag", "jlp-hs-tik", payload="hasOwnProperty",
                                                payloadType="flow"), "inject_bron"),
    "inject-oude-velden-env": (lambda: _wijzig("hartslag", "jlp-hs-tik", payload="HOME", payloadType="env"),
                               "inject_bron"),
    "inject-vt-env": (lambda: _inject({"p": "payload", "v": "HOME", "vt": "env"}), "inject_bron"),
    "inject-vt-jsonata": (lambda: _inject({"p": "payload", "v": "$flowContext('__proto__')", "vt": "jsonata"}),
                          "inject_bron"),
    "inject-vt-msg": (lambda: _inject({"p": "payload", "v": "__proto__", "vt": "msg"}), "inject_bron"),
    "inject-vt-bin": (lambda: _inject({"p": "payload", "v": "[1]", "vt": "bin"}), "inject_bron"),
    "inject-topic-flow": (lambda: _inject({"p": "payload"}, {"p": "topic", "v": "__proto__", "vt": "flow"}),
                          "inject_bron"),
    "inject-flow-pad": (lambda: _inject({"p": "payload", "v": "staat.x", "vt": "flow"}), "inject_bron"),
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
    # Herstelpoging 2 van S-P2-410 (R-VEILIG-2): zonder persist: true gebruikt de http request-node de globale agent
    # van Node.js, en die volgt met NODE_USE_ENV_PROXY=1 of NODE_OPTIONS=--use-env-proxy de proxy uit de omgeving
    # (gemeten: het verzoek met het token ging naar de proxy, ook met proxyOptions in FLOWS).
    "http-zonder-persist": (lambda: _wijzig("hartslag", "jlp-hs-stuur", persist=False), "http_instelling"),
    "http-persist-als-tekst": (lambda: _wijzig("hartslag", "jlp-hs-stuur", persist="true"), "http_instelling"),
    # Herstelpoging 2 (vervolgpunt van de herreviews): de switch-node valt onder de witte lijst. Hij leest alleen msg
    # met een naam van de witte lijst of meting.<naam>, en vergelijkt alleen met een vaste soort of met JSONata uit de
    # bundels; de debug-node voert nooit JSONata uit.
    "switch-bron-jsonata": (lambda: _switch(property="$flowContext('__proto__')", propertyType="jsonata"),
                            "switch_bron"),
    "switch-bron-env": (lambda: _switch(property="HOME", propertyType="env"), "switch_bron"),
    "switch-bron-flow": (lambda: _switch(property="mediaguard_staat", propertyType="flow"), "switch_bron"),
    "switch-bron-global": (lambda: _switch(property="staat", propertyType="global"), "switch_bron"),
    "switch-bron-soort-ontbreekt": (lambda: _switch_zonder("propertyType"), "switch_bron"),
    "switch-msg-proto": (lambda: _switch(property="__proto__"), "switch_bron"),
    "switch-msg-niet-op-witte-lijst": (lambda: _switch(property="kopie"), "switch_bron"),
    "switch-msg-pad-onder-payload": (lambda: _switch(property="payload.items"), "switch_bron"),
    "switch-meting-proto": (lambda: _switch(property="meting.__proto__"), "switch_bron"),
    "switch-meting-constructor": (lambda: _switch(property="meting.staat.constructor"), "switch_bron"),
    "switch-meting-hoofdletter": (lambda: _switch(property="meting.Staat"), "switch_bron"),
    "switch-meting-haken": (lambda: _switch(property="meting['staat']"), "switch_bron"),
    "switch-regel-jsonata-vrij": (lambda: _switch(rules=[{"t": "jsonata_exp", "v": "$flowContext('__proto__')",
                                                          "vt": "jsonata"}]), "switch_regel"),
    "switch-regel-vt-env": (lambda: _switch(rules=[{"t": "eq", "v": "HOME", "vt": "env"}]), "switch_regel"),
    "switch-regel-vt-msg": (lambda: _switch(rules=[{"t": "eq", "v": "payload", "vt": "msg"}]), "switch_regel"),
    "switch-regel-vt-flow": (lambda: _switch(rules=[{"t": "eq", "v": "__proto__", "vt": "flow"}]), "switch_regel"),
    "switch-regel-vt-global": (lambda: _switch(rules=[{"t": "eq", "v": "x", "vt": "global"}]), "switch_regel"),
    "switch-regel-vt-prev": (lambda: _switch(rules=[{"t": "eq", "v": "", "vt": "prev"}]), "switch_regel"),
    "switch-regel-v2t-env": (lambda: _switch(rules=[{"t": "btwn", "v": "1", "vt": "num", "v2": "HOME",
                                                     "v2t": "env"}]), "switch_regel"),
    "switch-regel-v2t-jsonata": (lambda: _switch(rules=[{"t": "btwn", "v": "1", "vt": "num",
                                                         "v2": "$lookup($$, '__proto__')", "v2t": "jsonata"}]),
                                 "switch_regel"),
    "switch-regel-v-geen-tekst": (lambda: _switch(rules=[{"t": "eq", "v": ["__proto__"], "vt": "str"}]),
                                  "switch_regel"),
    "switch-regel-onbekende-operator": (lambda: _switch(rules=[{"t": "exec"}]), "switch_regel"),
    "switch-rules-als-object": (lambda: _switch(rules={"t": "nnull"}), "switch_regel"),
    "switch-regel-geen-object": (lambda: _switch(rules=["nnull"]), "switch_regel"),
    "debug-targettype-jsonata": (lambda: _debug(targetType="jsonata", complete="$flowContext('__proto__')"),
                                 "debug_jsonata"),
    "debug-statustype-jsonata": (lambda: _debug(statusType="jsonata", statusVal="payload"), "debug_jsonata"),
    # KLEIN-50 (vervolgpunt R-VEILIG-3 van S-P2-410): Node-RED vervangt elke tekstwaarde die precies ${NAAM} of
    # $(NAAM) is door een instelling of omgevingsvariabele (flows/util.js mapEnvVarProperties). Zo kon een bundel een
    # omgevingsvariabele van de container lezen en in de payload naar de Hub sturen, ook met een vaste soort 'str'.
    "switch-regel-v-env-substitutie": (lambda: _switch(rules=[{"t": "eq", "v": "${HOME}", "vt": "str"}]),
                                       "env_substitutie"),
    "switch-regel-v-env-substitutie-oud": (lambda: _switch(rules=[{"t": "eq", "v": "$(HOME)", "vt": "str"}]),
                                           "env_substitutie"),
    "change-to-env-substitutie": (lambda: _regel("jlp-hs-bouw", "meting", to="${HOME}", tot="str"),
                                  "env_substitutie"),
    "change-to-env-substitutie-oud": (lambda: _regel("jlp-hs-bouw", "meting", to="$(HOME)", tot="str"),
                                      "env_substitutie"),
    "inject-v-env-substitutie": (lambda: _inject({"p": "payload", "v": "${HOME}", "vt": "str"}), "env_substitutie"),
    "inject-v-env-substitutie-oud": (lambda: _inject({"p": "payload", "v": "$(HOME)", "vt": "str"}),
                                     "env_substitutie"),
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


def test_env_substitution_is_refused_only_in_the_form_node_red_replaces() -> None:
    """KLEIN-50: alleen een tekst die precies ${NAAM} of $(NAAM) is (zonder witruimte) vervangt Node-RED; die wordt
    geweigerd, in elk veld. Een tekst die het patroon alleen bevat en de bundels uit Git blijven toegestaan."""
    assert all(nf.lint(nodes) == [] for nodes in nf.bundels_in_git().values())
    for tekst in ("${HOME}", "$(HOME)", "${a.b}", "$(x-y)", "${a}b}"):
        assert "env_substitutie" in _codes(_regel("jlp-hs-bouw", "meting", to=tekst, tot="str")), tekst
        assert "env_substitutie" in _codes(_met("c", type="comment", info=tekst)), tekst
    for tekst in ("x${HOME}", "${HOME} ", "${ HOME}", "${}", "$()", "$HOME", "${HOME}\n", "{HOME}"):
        assert nf.lint(_regel("jlp-hs-bouw", "meting", to=tekst, tot="str")) == [], tekst


@pytest.mark.parametrize("geval", sorted(AFGEWEZEN))
def test_own_check_refuses(geval: str) -> None:
    maak, code = AFGEWEZEN[geval]
    assert code in _codes(maak()), nf.lint(maak())


def test_the_whitelist_keeps_what_the_bundles_use_and_nothing_more() -> None:
    """Witte lijst (S-P2-410, herstel R-TECH-1/R-VEILIG-1) zonder te veel te weigeren: de bundels uit Git en gewone
    bronnen blijven toegestaan, ook een flow-naam die op een verboden woord lijkt; een pad of een andere naam niet."""
    assert all(nf.lint(nodes) == [] for nodes in nf.bundels_in_git().values())
    goed = {
        "msg-bron": _regel("jlp-hs-bouw", "meting", to="payload", tot="msg"),
        "flow-bron": _regel("jlp-hs-bouw", "meting", to="mediaguard_staat", tot="flow"),
        "flow-bron-lijkt-op-proto": _regel("jlp-hs-bouw", "meting", to="proto_type", tot="flow"),
        "global-bron-lijkt-op-constructor": _regel("jlp-hs-bouw", "meting", to="constructor_naam", tot="global"),
        "flow-schrijven": _regel("jlp-hs-bouw", "staat", pt="flow", to="1", tot="num"),
        "koppen-weg": _regel("jlp-hs-bouw", "headers", t="delete"),
        **{f"vaste-soort-{tot}": _regel("jlp-hs-bouw", "meting", to=to, tot=tot)
           for to, tot in (("x", "str"), ("1", "num"), ("true", "bool"), ("{}", "json"), ("", "date"))},
        "inject-flow-naam": _inject({"p": "payload", "v": "mediaguard_staat", "vt": "flow"}),
        "inject-met-topic": _inject({"p": "payload", "v": "1", "vt": "num"}, {"p": "topic", "v": "x", "vt": "str"}),
        "inject-zonder-vt": _inject({"p": "payload", "v": "x"}),  # Node-RED (20-inject.js): zonder vt is het 'str'
        # switch en debug (herstelpoging 2): wat de bundels doen en gewone vergelijkingen blijven toegestaan
        "switch-payload-tekst": _switch(property="payload", rules=[{"t": "eq", "v": "x", "vt": "str"}]),
        "switch-meting-pad": _switch(property="meting.staat", rules=[{"t": "eq", "v": "down", "vt": "str"},
                                                                     {"t": "else"}]),
        "switch-tussen-getallen": _switch(rules=[{"t": "btwn", "v": "1", "vt": "num", "v2": "5", "v2t": "num"}]),
        "switch-zonder-vt": _switch(rules=[{"t": "eq", "v": "1"}]),  # Node-RED (10-switch.js): dan num of str
        "switch-jsonata-uit-de-bundels": _switch(rules=[{"t": "jsonata_exp", "vt": "jsonata", "v": next(
            r["to"] for n in _bundel("mediaguard-offline") if n["id"] == "jlp-mg-oordeel" for r in n["rules"]
            if r["p"] == "mediaguard_staat")}]),
        "debug-heel-bericht": _debug(targetType="full", complete="true"),
        "debug-msg-eigenschap": _debug(targetType="msg", complete="meting"),
    }
    for naam, nodes in goed.items():
        assert nf.lint(nodes) == [], (naam, nf.lint(nodes))
    assert {"payload", "url", "followRedirects", "meting", "headers"} == nf.MSG_NAMEN
    assert {"__proto__", "constructor", "prototype"} <= nf.PROTOTYPE_NAMEN
    for geval in ("proto-in-p", "proto-in-to-msg", "constructor-in-to-global", "prototype-in-to-flow"):
        assert any("__proto__" in b or "constructor" in b or "prototype" in b
                   for b in nf.lint(AFGEWEZEN[geval][0]())), geval


# Alle eigenschappen van Object.prototype, plus 'prototype' (van een functie). Elk ervan wordt geweigerd op elke plek
# waar een bundel een naam in msg, flow of global schrijft of leest (S-P2-410, herstel R-TECH-1/R-VEILIG-1): de
# witte lijst laat ze vanzelf vallen; deze test bewijst dat per naam en per plek.
PROTOTYPE_NAMEN = ("__proto__", "constructor", "prototype", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable",
                   "toString", "toLocaleString", "valueOf", "__defineGetter__", "__defineSetter__", "__lookupGetter__",
                   "__lookupSetter__")
PLEKKEN: dict[str, Any] = {
    "p-msg": lambda naam: _regel("jlp-hs-bouw", naam),
    "p-flow": lambda naam: _regel("jlp-hs-bouw", naam, pt="flow"),
    "to-msg": lambda naam: _regel("jlp-hs-bouw", "meting", to=naam, tot="msg"),
    "to-flow": lambda naam: _regel("jlp-hs-bouw", "meting", to=naam, tot="flow"),
    "to-global": lambda naam: _regel("jlp-hs-bouw", "meting", to=naam, tot="global"),
    "inject-flow": lambda naam: _inject({"p": "payload", "v": naam, "vt": "flow"}),
    "inject-global": lambda naam: _inject({"p": "payload", "v": naam, "vt": "global"}),
    "inject-topic-flow": lambda naam: _inject({"p": "payload"}, {"p": "topic", "v": naam, "vt": "flow"}),
    "inject-oude-velden": lambda naam: _wijzig("hartslag", "jlp-hs-tik", payload=naam, payloadType="global"),
    # herstelpoging 2: ook de switch-node leest geen eigenschap van het prototype (msg-naam of meting.<naam>)
    "switch-msg": lambda naam: _switch(property=naam),
    "switch-meting": lambda naam: _switch(property=f"meting.{naam}"),
}


@pytest.mark.parametrize("plek", sorted(PLEKKEN))
@pytest.mark.parametrize("naam", PROTOTYPE_NAMEN)
def test_every_prototype_name_is_refused_in_every_place(naam: str, plek: str) -> None:
    bevindingen = nf.lint(PLEKKEN[plek](naam))
    assert any(b.split(":", 1)[0] in ("change_regel", "inject_bron", "switch_bron") and naam in b
               for b in bevindingen), bevindingen


def _jsonata_van(nodes: list[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """sha256 → (node, veld) van elke JSONata in een change-regel (niet de vaste Hub-routes voor msg.url)."""
    return {nf.jsonata_som(str(r["to"])): (str(n["id"]), f"{r['pt']}.{r['p']}") for n in nodes
            if n.get("type") == "change" for r in n["rules"] if r.get("tot") == "jsonata" and r.get("p") != "url"}


def _vorige_versie(naam: str, versie: str) -> list[dict[str, Any]]:
    return nf.lees_bundel(VERSIES_MAP / f"{naam}-{versie}.json")


def test_jsonata_is_limited_to_the_expressions_of_the_bundles() -> None:
    """Witte lijst voor JSONata (R-TECH-1: ``$flowContext('__proto__')``, ``$lookup`` en elke andere uitdrukking):
    alleen de uitdrukkingen uit de bundels in Git (vastgelegd als sha256 in het hulpmiddel) en, voor msg.url, de vaste
    Hub-routes. Eén teken anders en de regel wordt geweigerd, tot de lijst in het hulpmiddel bewust is bijgewerkt."""
    in_git = {som for nodes in nf.bundels_in_git().values() for som in _jsonata_van(nodes)}
    assert in_git <= set(nf.TOEGESTANE_JSONATA), "een uitdrukking uit de bundels staat niet in de lijst"
    for naam, node_id in (("hartslag", "jlp-hs-bouw"), ("mediaguard-offline", "jlp-mg-oordeel")):
        nodes = _bundel(naam)
        regel = next(n for n in nodes if n["id"] == node_id)["rules"][0]
        regel["to"] = str(regel["to"]) + " "
        assert "change_regel" in _codes(nodes), naam


def test_the_previous_versions_are_fixed_copies_from_git() -> None:
    """De testkopieën zijn precies de vorige uitgebrachte versies: elke versie uit de historie (1.0.0, 1.1.0, 1.1.1)
    van elke bundel staat erin, met de versie in het tabblad en de sha256 uit flows.lock.json van die commit."""
    bestanden = sorted(p.name for p in VERSIES_MAP.glob("*.json"))
    assert bestanden == sorted(f"{naam}-{versie}.json" for naam, versie in VORIGE_VERSIES)
    for (naam, versie), som in VORIGE_VERSIES.items():
        nodes = _vorige_versie(naam, versie)
        assert str((nf.tab_van(nodes) or {}).get("info", "")).startswith(f"versie {versie}\n"), (naam, versie)
        assert nf.checksum(nodes) == som, (naam, versie)
    huidig = {rij["versie"] for rij in nf.lees_lock()["bundels"].values()}
    assert huidig == {"1.2.0"} and not huidig & {versie for _naam, versie in VORIGE_VERSIES}


def test_the_jsonata_list_holds_every_released_version() -> None:
    """R-TECH-2 (herstelpoging 2 van S-P2-410): ``TOEGESTANE_JSONATA`` houdt de JSONata van ELKE uitgebrachte versie
    bij (toevoegen, niet vervangen), met per sha256 bundel, node, veld en versies. De uitdrukkingen in Git zitten
    erin, en elke andere sha256 hoort bij een vorige versie uit de historie: de lijst is precies wat de testkopieën en
    de huidige bundels samen gebruiken."""
    verwacht: dict[str, tuple[str, str, str, tuple[str, ...]]] = {}
    bronnen = [(naam, versie, _vorige_versie(naam, versie)) for naam, versie in VORIGE_VERSIES]
    bronnen += [(naam, nf.lees_lock()["bundels"][naam]["versie"], nodes) for naam, nodes in nf.bundels_in_git().items()]
    for naam, versie, nodes in bronnen:
        for som, (node_id, veld) in _jsonata_van(nodes).items():
            bundel, nid, vld, versies = verwacht.get(som, (naam, node_id, veld, ()))
            assert (bundel, nid, vld) == (naam, node_id, veld), f"{som}: dezelfde uitdrukking op twee plekken"
            verwacht[som] = (naam, node_id, veld, tuple(sorted({*versies, versie})))
    assert verwacht == nf.TOEGESTANE_JSONATA
    in_git = {som for nodes in nf.bundels_in_git().values() for som in _jsonata_van(nodes)}
    assert set(nf.TOEGESTANE_JSONATA) - in_git, "de historie voegt niets toe: de test zegt dan niets"
    # mediaguard-offline 1.1.0 (uit de review van R-TECH-2): flow_versie in de JSONata maakt elke versie anders
    oud = _jsonata_van(_vorige_versie("mediaguard-offline", "1.1.0"))
    assert any(som not in in_git and nf.TOEGESTANE_JSONATA[som][3] == ("1.1.0",) for som in oud)


def _met_latere_veiligheidseisen(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Een vorige versie met alleen de veiligheidseisen die later kwamen: ``persist: true`` (1.2.0) en
    ``msg.followRedirects = false`` in de route-node (1.1.0). Niets anders verandert."""
    kopie = copy.deepcopy(nodes)
    http_ids = {n["id"] for n in kopie if n.get("type") == "http request"}
    for node in kopie:
        if node.get("type") == "http request":
            node["persist"] = True
        if node.get("type") == "change" and http_ids & {d for u in node.get("wires", []) for d in u} \
                and nf.REDIRECT_UIT not in node["rules"]:
            node["rules"].append(dict(nf.REDIRECT_UIT))
    return kopie


@pytest.mark.parametrize("naam,versie", sorted(VORIGE_VERSIES))
def test_a_previous_version_is_only_refused_for_a_later_security_rule(naam: str, versie: str) -> None:
    """R-TECH-2 (herstelpoging 2): lint weigert een vorige versie nooit om haar JSONata. Een vorige versie wordt alleen
    geweigerd door een veiligheidseis die later kwam (``persist``: alle versies vóór 1.2.0; ``redirect_niet_uit``:
    1.0.0) en komt met alleen die eisen erbij door lint. Herstellen naar een versie vóór 1.2.0 kan dus niet; dat is
    bewust (ADR-0023 punt 9)."""
    nodes = _vorige_versie(naam, versie)
    bevindingen = nf.lint(nodes)
    assert bevindingen, "een vorige versie zonder persist komt door lint"
    assert not any("JSONata" in b for b in bevindingen), bevindingen
    toegestaan = {"http_instelling"} | ({"redirect_niet_uit"} if versie == "1.0.0" else set())
    assert {b.split(":", 1)[0] for b in bevindingen} <= toegestaan, bevindingen
    assert all("persist" in b for b in bevindingen if b.startswith("http_instelling")), bevindingen
    assert nf.lint(_met_latere_veiligheidseisen(nodes)) == []


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


def test_every_http_request_in_git_has_its_own_connection() -> None:
    """R-VEILIG-2 (herstelpoging 2 van S-P2-410): elke http request-node in de bundels heeft ``persist: true``, dus een
    eigen agent. Zonder die agent gebruikt got de globale agent van Node.js, en die volgt met NODE_USE_ENV_PROXY=1 of
    NODE_OPTIONS=--use-env-proxy de proxy uit de omgeving, ook met ``proxyOptions`` in FLOWS (gemeten in de herreview
    en in rooktest N10). ``lint`` eist het."""
    gezien = 0
    for naam, nodes in nf.bundels_in_git().items():
        for node in nodes:
            if node["type"] == "http request":
                assert node["persist"] is True, (naam, node["id"])
                gezien += 1
    assert gezien == 4  # hartslag, mediaguard-offline (2), testflow-herstart
    bevindingen = nf.lint(_wijzig("hartslag", "jlp-hs-stuur", persist=False))
    assert bevindingen == ["http_instelling: jlp-hs-stuur.persist moet True zijn"]


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
                  "change-url-dubbele-aanhalingstekens", "change-msg-headers",  # R-VEILIG-1 (review P2)
                  # S-P2-410, herstel R-VEILIG-1 en R-TECH-1: de omwegen uit beide rapporten
                  "omweg-r-veilig-1", "omweg-r-tech-1", "tot-jsonata-flowcontext", "tot-env",
                  "inject-oude-velden-global", "tot-ontbreekt-bron-msg",
                  # herstelpoging 2: persist, switch en debug
                  "http-zonder-persist", "switch-bron-jsonata", "switch-regel-vt-env", "debug-targettype-jsonata"):
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


VREEMDE_DRAAD: list[dict[str, Any]] = [
    {"id": "vreemd-tab", "type": "tab", "label": "van iemand anders", "disabled": False, "info": ""},
    {"id": "vreemd-zet", "type": "change", "z": "vreemd-tab",
     "rules": [{"t": "set", "p": "url", "pt": "msg", "to": "elders.invalid/x", "tot": "str"}],
     "wires": [["jlp-hs-stuur"]]},
]


def test_import_refuses_a_foreign_node_wired_into_an_own_node() -> None:
    """R-VEILIG-3 (S-P2-410): een node buiten de eigen tabbladen met een draad naar een eigen node (hier de http
    request-node met het token) kan msg.url zetten buiten ``lint`` om. Staat zo'n draad er al: de import weigert vóór
    Node-RED iets verandert. Komt hij er na het schrijven bij: het teruglezen ziet hem en de vorige flows gaan terug.
    Een vreemde node die alleen naar vreemde nodes draadt, blijft gewoon staan."""
    bundels = {"hartslag": _bundel("hartslag")}
    al_daar = _NepNodeRed([*VORIGE_FLOWS, *VREEMDE_DRAAD])
    with pytest.raises(nf.Fout, match="vreemd-zet") as fout:
        nf.importeer(al_daar, bundels, hub_url="https://hub.example", token="t" * 30)
    assert "jlp-hs-stuur" in str(fout.value) and al_daar.geschreven == []

    def vreemde_draad(flows: list[dict[str, Any]]) -> None:
        flows.extend(copy.deepcopy(VREEMDE_DRAAD))

    erna = _NepNodeRed(VORIGE_FLOWS, vreemde_draad)
    with pytest.raises(nf.Fout, match="teruggezet") as fout:
        nf.importeer(erna, bundels, hub_url="https://hub.example", token="t" * 30)
    assert "vreemd-zet" in str(fout.value)
    assert len(erna.geschreven) == 2 and erna.staat == VORIGE_FLOWS
    onschuldig = copy.deepcopy(VREEMDE_DRAAD)
    onschuldig[1]["wires"] = [["vreemd-ander"]]
    onschuldig.append({"id": "vreemd-ander", "type": "debug", "z": "vreemd-tab", "wires": []})
    uit = nf.importeer(_NepNodeRed([*VORIGE_FLOWS, *onschuldig]), bundels, hub_url="https://hub.example",
                       token="t" * 30)
    assert uit["hartslag"]["gelijk"] is True


def _vreemde_inject(nid: str, draden: Any) -> dict[str, Any]:
    """Een vreemde inject op een vreemd tabblad die msg.url op een ander adres zet (zoals in de herreview)."""
    return {"id": nid, "type": "inject", "z": "vreemd-tab", "props": [
        {"p": "payload", "v": "{}", "vt": "json"}, {"p": "url", "v": "elders.invalid/vreemd", "vt": "str"}],
            "repeat": "2", "once": True, "wires": draden}


VREEMDE_VORMEN: dict[str, list[dict[str, Any]]] = {
    # R-VEILIG-3 van de herreview (herstelpoging 2): Node-RED gebruikt ook deze vormen als draad, of geeft een vreemde
    # node met een eigen id het credential van de eigen node; de eerste controle zag ze niet.
    "geneste-lijst": [VREEMDE_DRAAD[0], _vreemde_inject("vreemd-tik", [[["jlp-hs-stuur"]]])],
    "array-achtig-object": [VREEMDE_DRAAD[0], _vreemde_inject("vreemd-tik", [{"0": "jlp-hs-stuur", "length": 1}])],
    "dubbel-id-inject": [VREEMDE_DRAAD[0], _vreemde_inject("jlp-hs-toon", [["jlp-hs-stuur"]])],
    "dubbel-id-http-request": [
        VREEMDE_DRAAD[0], _vreemde_inject("jlp-hs-tik", [["jlp-hs-stuur"]]),
        {"id": "jlp-hs-stuur", "type": "http request", "z": "vreemd-tab", "method": "POST", "ret": "obj",
         "paytoqs": "ignore", "url": "https://elders.invalid/dubbel", "tls": "", "persist": True, "proxy": "",
         "authType": "bearer", "headers": [], "wires": [[]]}],
    # dicht bij twijfel: een draad in een andere vorm (ook zonder eigen id), en een eigen id in een ander veld
    "draad-andere-vorm": [VREEMDE_DRAAD[0], _vreemde_inject("vreemd-tik", [[{"id": "vreemd-ander"}]])],
    "eigen-id-in-ander-veld": [VREEMDE_DRAAD[0], {"id": "vreemd-link", "type": "link out", "z": "vreemd-tab",
                                                  "mode": "link", "links": ["jlp-hs-stuur"], "wires": []}],
}


@pytest.mark.parametrize("vorm", sorted(VREEMDE_VORMEN))
def test_import_refuses_every_form_of_a_foreign_node_that_reaches_an_own_node(vorm: str) -> None:
    """R-VEILIG-3 (herstelpoging 2 van S-P2-410), per vorm: staat de vreemde node er vóór de import, dan weigert de
    import zonder te schrijven; komt hij er bij het teruglezen bij, dan gaan de flows van vóór de import terug."""
    bundels = {"hartslag": _bundel("hartslag")}
    al_daar = _NepNodeRed([*VORIGE_FLOWS, *VREEMDE_VORMEN[vorm]])
    with pytest.raises(nf.Fout, match="geweigerd"):
        nf.importeer(al_daar, bundels, hub_url="https://hub.example", token="t" * 30)
    assert al_daar.geschreven == []

    def erbij(flows: list[dict[str, Any]]) -> None:
        flows.extend(copy.deepcopy(VREEMDE_VORMEN[vorm]))

    erna = _NepNodeRed(VORIGE_FLOWS, erbij)
    with pytest.raises(nf.Fout, match="teruggezet"):
        nf.importeer(erna, bundels, hub_url="https://hub.example", token="t" * 30)
    assert len(erna.geschreven) == 2 and erna.staat == VORIGE_FLOWS
    assert "t" * 30 not in json.dumps(erna.geschreven[1])


def test_the_foreign_node_check_finds_the_reason() -> None:
    """De melding noemt per vorm de reden: een dubbel id, een draad in een andere vorm, of een eigen id als tekst."""
    eigen = _bundel("hartslag")
    redenen = {vorm: " ".join(nf._vreemde_draden([*copy.deepcopy(nodes), *eigen], {"jlp-hartslag"}))
               for vorm, nodes in VREEMDE_VORMEN.items()}
    assert "dubbel id" in redenen["dubbel-id-inject"] and "dubbel id" in redenen["dubbel-id-http-request"]
    for vorm in ("geneste-lijst", "array-achtig-object", "draad-andere-vorm"):
        assert "'wires'" in redenen[vorm], vorm
    for vorm in ("geneste-lijst", "array-achtig-object", "eigen-id-in-ander-veld"):
        assert "noemt jlp-hs-stuur" in redenen[vorm], vorm
    # de eigen tabbladen van de andere bundels en de globale instellingen zijn geen vreemde node die iets raakt
    alle = [n for nodes in nf.bundels_in_git().values() for n in nodes]
    assert nf._vreemde_draden([*alle, *copy.deepcopy(VORIGE_FLOWS)], {"jlp-hartslag"}) == []


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
    assert "change_regel" in weigering["hasOwnProperty_isPrototypeOf"]["codes"]  # herstel R-VEILIG-1
    for naam in ("env_op_tabblad", "tweede_regel_op_msg_url", "hasOwnProperty_isPrototypeOf"):
        assert weigering[naam]["geweigerd"] is True and weigering[naam]["flows_in_node_red_ongewijzigd"] is True
    # Controleproef bij R-VEILIG-1: dezelfde regel via de rauwe Admin-API (buiten de eigen controle om) zet
    # msg.followRedirects buiten werking; het doel van de doorverwijzing krijgt de bearer-kop wel.
    hop = n10["controleproef_hasOwnProperty"]
    assert hop["via"] == "rauwe Admin-API" and hop["doorverwijzing_gevolgd"] is True
    assert hop["doel_kreeg_bearer"] is True and hop["readback_ziet_het"] == "change_regel"
    # R-VEILIG-2: met http_proxy in de omgeving en proxyOptions uit het manifest krijgt de proxy niets; zonder de
    # instelling (controleproef) gaat het verzoek met de bearer-kop via de proxy (CONNECT-tunnel).
    proxy = n10["proxy_uit_de_omgeving"]
    met, zonder = proxy["met_proxyOptions_uit_het_manifest"], proxy["controleproef_zonder_proxyOptions"]
    assert met["hub_kreeg_de_heartbeat_met_token"] is True and met["proxy_ontving"] == 0
    assert zonder["proxy_ontving"] >= 1 and zonder["proxy_kreeg_bearer"] is True and zonder["hub_ontving"] == 0
    assert "CONNECT" in zonder["methoden_bij_de_proxy"]
    # R-VEILIG-2 (herstelpoging 2): ook met de ingebouwde proxy van Node.js aan (NODE_USE_ENV_PROXY=1 of
    # NODE_OPTIONS=--use-env-proxy) krijgt een proxy niets van de drie bundels uit Git (persist: true); een eigen
    # Hub-simulator krijgt heartbeat, inventaris, event en voorstel met het token, en na 8 s stilte staat daar geen
    # verbinding meer open. Controleproef met persist: false (rauwe Admin-API): de proxy krijgt de bearer-kop, en het
    # teruglezen ziet het.
    node_proxy = proxy["ingebouwde_proxy_van_node"]
    assert set(node_proxy) == {"NODE_USE_ENV_PROXY", "NODE_OPTIONS"}
    for naam, uitkomst in node_proxy.items():
        met_p, zonder_p = uitkomst["met_persist_uit_git"], uitkomst["controleproef_persist_false"]
        assert met_p["proxy_ontving"] == 0, naam
        assert met_p["hub_kreeg_heartbeat_met_token"] is True and met_p["hub_kreeg_event_met_token"] is True, naam
        assert met_p["hub_kreeg_voorstel_met_token"] is True and met_p["inventaris_aanroepen_bij_de_hub"] >= 1, naam
        verbindingen = met_p["verbindingen_bij_de_hub"]
        assert verbindingen["direct_na_het_verkeer"] <= 4 and verbindingen["na_8_s_stilte"] == 0, naam
        assert zonder_p["persist"] is False and zonder_p["via"] == "rauwe Admin-API", naam
        assert zonder_p["proxy_ontving"] >= 1 and zonder_p["proxy_kreeg_bearer"] is True, naam
        assert "/api/v1/monitoring/heartbeat" in zonder_p["paden_bij_de_proxy"], naam
        assert zonder_p["readback_ziet_het"] == "http_instelling", naam
    # R-VEILIG-3 (herstelpoging 2): een vreemd tabblad in een vorm die de eerste controle niet zag, wordt geweigerd vóór
    # Node-RED iets verandert, en geen luisteraar ontvangt iets
    vreemd = n10["vreemde_nodes"]
    vormen = {"geneste_lijst", "draad_als_object", "dubbel_id_inject", "dubbel_id_http_request"}
    assert set(vreemd) == vormen | {"luisteraar_ontving"} and vreemd["luisteraar_ontving"] == 0
    assert all(vreemd[v] == {"geweigerd": True, "flows_in_node_red_ongewijzigd": True} for v in vormen)
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
