"""Selectie, bewijs, weigeringen en opruiming van de integrale E2E-runner."""

from __future__ import annotations

import json
import secrets
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
import yaml

from e2e.integral import bewijs, project, run
from e2e.integral.project import KERN, MAP, ROOT, Proeffout, Project
from e2e.integral.testnet import Testnet as InternTransport

ROOK = run.selectie(run.catalogus(MAP / "scenarios"), "rook", None)[0]
HARDWARE = "NIET GEVALIDEERD (hardware)"


def voorbeeld(map_: Path, naam: str, groep: str, dienst: str) -> None:
    data = {"eigenaar": "testteam", "verwachting": "Gezonde dienst", "groepen": (groep,), "diensten": (dienst,)}
    (map_ / naam).write_text(f"SCENARIO = {data!r}\n\ndef test_proef():\n    pass\n")


def test_selectie_gebruikt_expliciete_bestanden_en_extra_diensten(tmp_path: Path) -> None:
    voorbeeld(tmp_path, "EX01_media.py", "media", "mso")
    voorbeeld(tmp_path, "EX02_huis.py", "huis", "my-house")
    aanbod = run.catalogus(tmp_path)
    assert run.selectie(aanbod, None, "huis") == [aanbod[1]]
    gekozen = run.selectie(aanbod, "EX02,EX01,EX02", None)
    assert [s.pad.name for s in gekozen] == ["EX02_huis.py", "EX01_media.py"]
    assert gekozen[0].diensten == ("my-house",)
    assert run.selectie(aanbod, None, None) == aanbod
    voorbeeld(tmp_path, "EX01_dubbel.py", "media", "mso")
    with pytest.raises(ValueError, match="Dubbel scenario"):
        run.catalogus(tmp_path)


@pytest.mark.parametrize("args", [("--scenario", "EX99"), ("--scenario", ""), ("--groep", "ontbreekt")])
def test_onbekende_selectie_geeft_cli_fout(args: tuple[str, str]) -> None:
    cmd = [sys.executable, str(MAP / "run.py"), *args]
    resultaat = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert resultaat.returncode == 2
    assert "Onbekend" in resultaat.stderr


def test_profielen_en_isolatie() -> None:
    compose = yaml.safe_load((MAP / "compose.yaml").read_text())
    assert compose["networks"] == {"default": {"internal": True}}
    diensten = compose["services"]
    assert {n for n, d in diensten.items() if "kern" in d["profiles"]} == set(KERN)
    assert diensten["jelly-leon"]["image"] == "jelly-leon:p6" and "build" not in diensten["jelly-leon"]
    assert all(p.startswith("127.0.0.1:") for d in diensten.values() for p in d.get("ports", []))
    assert all(not d.get("network_mode") and not d.get("privileged") for d in diensten.values())
    assert all(not v for v in compose["volumes"].values())
    assert all("ports" not in diensten[n] for n in ("truenas", "truenas-twee", "onedrive", "gotify-sim"))
    for n in ("node-red", "uptime-kuma", "gotify"):
        pin = yaml.safe_load((ROOT / f"install/manifests/{n}.manifest.yaml").read_text())["image"]
        assert diensten[n]["image"] == f"{pin['repository']}:{pin['tag']}@{pin['digest']}"


def test_mso_bouw_gebruikt_alleen_de_gepinde_mocksubmap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "bron"
    mocks = repo / "apps/mocks"
    mocks.mkdir(parents=True)
    dockerfile = mocks / "Dockerfile"
    dockerfile.write_text("gepinde testbron")
    (repo / "andere-app").write_text("valt buiten de bouwcontext")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "apps/mocks/Dockerfile", "andere-app"], check=True)
    boom = subprocess.check_output(["git", "-C", str(repo), "write-tree"], text=True).strip()
    dockerfile.write_text("lokale wijziging")
    pinmap = tmp_path / "simulators"
    pinmap.mkdir()
    (pinmap / "mso-mocks.commit").write_text(boom)
    monkeypatch.setattr(project, "MAP", tmp_path / "integral")
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    proef = Mock(spec=Project)

    def lees_bouwcontext(*args: str, env: dict[str, str], tijd: int) -> None:
        bron = Path(env["INTEGRAAL_MSO_MOCKS_BRON"])
        assert list(bron.iterdir()) == [bron / "Dockerfile"]
        assert (bron / "Dockerfile").read_text() == "gepinde testbron"
        assert env["INTEGRAAL_BRONCOMMIT"] == boom

    proef.compose.side_effect = lees_bouwcontext
    Project.bouw_mso_simulator(proef, "sabnzbd-sim", mocks)
    proef.compose.assert_called_once()


def test_schema_images_delen_alleen_het_eigen_volume_en_starten_na_elkaar(tmp_path: Path) -> None:
    scenario, = run.selectie(run.catalogus(MAP / "scenarios"), "EX15", None)
    p = proefproject()
    p.diensten.update(dict.fromkeys(scenario.diensten))
    p.start.side_effect = Proeffout("Einde startmeting")
    code, _ = run.draai(p, scenario, tmp_path / "EX15", "c" * 40, False)
    assert code == 1
    media = ("emby-opslag", "mg-inhoud", "arr-inhoud")
    p.images.assert_called_once_with((*KERN, "schema-release", "schema-nieuw", *media))
    p.start.assert_called_once_with(("hub", "executor", "truenas", *media))
    diensten = yaml.safe_load((MAP / "compose.yaml").read_text())["services"]
    assert diensten["schema-release"]["volumes"] == diensten["schema-nieuw"]["volumes"] == ["schemaproef:/appdata"]
    assert diensten["lifecycle"]["volumes"] == ["lifecycle:/appdata"]


@pytest.mark.parametrize("status", ["Geslaagd", "Gereed", "Niet gestart", "Mislukt", "Geblokkeerd"])
def test_weigert_eindstatus(status: str) -> None:
    rij = bewijs.voorstel(replace(ROOK, id="EX01"), "Sim-geslaagd", "2026-01-01", "proef.json")
    with pytest.raises(ValueError, match="Ongeldige combinatie"):
        bewijs.controleer({**rij, "status": status}, "Sim")


def test_status_sim_plus_echt_en_niet_uitgevoerde_simulatie() -> None:
    scenario = replace(ROOK, id="EX38", soort="Sim+", blokkade=HARDWARE, toelichting="Afstemming met een tuner")
    rij = bewijs.voorstel(scenario, "Sim-mislukt", "2026-01-01", "proef.json")
    assert (rij["status"], rij["validatie"], rij["blokkade"]) == ("Geblokkeerd", HARDWARE + "; Sim-mislukt", HARDWARE)
    assert bewijs.voorstel(scenario, None, "2026-01-01", "proef.json")["validatie"] == HARDWARE
    with pytest.raises(ValueError):
        bewijs.controleer({**rij, "blokkade": "tuner"}, "Sim+")
    with pytest.raises(ValueError):
        bewijs.controleer({**rij, "status": "Bezig"}, "Sim+")
    with pytest.raises(ValueError):
        bewijs.controleer(rij, "Echt")
    with pytest.raises(ValueError):
        bewijs.voorstel(ROOK, None, "2026-01-01", "proef.json")
    assert bewijs.voorstel(replace(scenario, soort="Echt"), None, "2026-01-01", "proef.json")["validatie"] == HARDWARE


@pytest.mark.parametrize("rid", [f"EX{nummer}" for nummer in range(16, 22)])
@pytest.mark.parametrize("sim", ["Sim-geslaagd", "Sim-mislukt"])
def test_voorstel_hubveiligheid_behoudt_praktijkvoorbehoud(rid: str, sim: str) -> None:
    scenario, = run.selectie(run.catalogus(MAP / "scenarios"), rid, None)
    rij = bewijs.voorstel(scenario, sim, "2026-01-01", "proef.json")
    bestaand = next(r for r in json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text()) if r["id"] == rid)
    praktijk = bestaand["blokkade"]
    assert praktijk and rij["blokkade"] == praktijk and rij["validatie"] == f"{sim}; {praktijk}"
    assert rij["status"] == "Bezig" and "N-P6-03" in rij["opmerking"]
    if rid in {"EX17", "EX21"}:
        assert "versie 2" in rij["opmerking"] and "account" in praktijk


def test_ex22_voorstel_bewaart_zowel_nas_als_telefoon() -> None:
    scenario, = run.selectie(run.catalogus(MAP / "scenarios"), "EX22", None)
    rij = bewijs.voorstel(scenario, "Sim-geslaagd", "2026-01-01", "proef.json")
    assert rij["status"] == "Geblokkeerd" and "N-P6-04" in rij["opmerking"]
    assert set(rij["validatie"].split("; ")) == {"NIET GEVALIDEERD (echte NAS)", HARDWARE, "Sim-geslaagd"}
    with pytest.raises(ValueError):
        bewijs.controleer({**rij, "validatie": HARDWARE + "; Sim-geslaagd"}, "Sim+")


def test_onbekend_kort_geheim_ontbreekt_in_het_hele_dossier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = secrets.token_hex(7)  # ook te kort voor de vrije-tekstheuristiek
    body = {"items": [{"status": "active", "app_id": marker, marker: marker}], "verbonden": True,
            "log": f"GET /sessie/{marker}", "headers": {"X-CSRF": marker}, "setup_code": marker}
    stapel = Mock(diensten={"hub": {}})
    headers = [("set-cookie", f"sessie={marker}")]
    stapel.compose.return_value = json.dumps([200, headers, json.dumps(body).encode().hex()])
    map_ = tmp_path / "EX01"
    map_.mkdir()
    with httpx.Client(transport=InternTransport(stapel, map_)) as client:
        res = client.post(f"http://hub:30400/koppelen/{marker}?sleutel={marker}", json=body,
                          headers={"Authorization": f"Bearer {marker}", "Cookie": f"sessie={marker}"})
        assert res.json()["verbonden"] is True
    verkeer = json.loads((map_ / "verkeer.json").read_text())[0]
    assert verkeer["http"] == 200 and verkeer["antwoord"]["items"][0]["status"] == "active"
    assert verkeer["pad"] == "***"

    mislukt = subprocess.CompletedProcess(["pytest", marker], 1, f"log van de proef: {marker}", marker)
    monkeypatch.setattr(run, "opdracht", lambda *a, **kw: mislukt)
    stapel = proefproject()
    code, rij = run.draai(stapel, ROOK, tmp_path / "rook", "c" * 40, False)
    assert code == 1 and rij is None
    assert marker.encode() not in b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    with pytest.raises(Proeffout) as fout:
        project.gecontroleerd(mislukt, "Subproces mislukt")
    assert marker not in str(fout.value)


def proefproject() -> Mock:
    p = Mock(spec=Project)
    p.naam = "jlp-it-eigen-proef"
    p.env = {}
    p.diensten = dict.fromkeys([*KERN, "jelly-leon", "mso"])
    p.images.side_effect = lambda namen: {n: {"image_id": n} for n in namen if n != "jelly-leon"}
    return p


def test_ontbrekende_mediaserver_slaat_alleen_afhankelijke_proef_over(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    p = proefproject()
    monkeypatch.setattr(run, "ROOT", tmp_path)
    scenario = replace(ROOK, id="EX36", diensten=("jelly-leon",))
    code, rij = run.draai(p, scenario, tmp_path / "EX36", "c" * 40, False)
    assert code == 0 and rij is None
    verslag = json.loads((tmp_path / "EX36/proef.json").read_text())
    assert verslag["uitkomst"] == "overgeslagen" and "S-P6-30" in verslag["overslagreden"]
    p.start.assert_not_called()

    def pytest_uitvoeren(args, *, env, tijd):
        assert args[-1] == str(scenario.pad)
        bewijs.schrijf(Path(env["INTEGRAAL_DOSSIER"]) / "pytest.json", {"getoetst": True})
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(run, "opdracht", pytest_uitvoeren)
    code, rij = run.draai(p, replace(scenario, id="EX01", diensten=("mso",)), tmp_path / "EX01", "c" * 40, False)
    assert code == 0 and rij["validatie"] == "Sim-geslaagd"
    p.start.assert_called_once_with(("hub", "executor", "truenas", "mso"))
    p.ruim.assert_called_once()


@pytest.mark.parametrize(("fout", "bewaar"), [(Proeffout("Start mislukt", 7), False), (KeyboardInterrupt(), False),
                                              (Proeffout("Start mislukt", 7), True)])
def test_opruimen_bij_gedeeltelijke_start_of_onderbreking(tmp_path: Path, fout: BaseException, bewaar: bool) -> None:
    p = proefproject()
    p.start.side_effect = fout
    if isinstance(fout, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            run.draai(p, ROOK, tmp_path / "rook", "c" * 40, bewaar)
    else:
        assert run.draai(p, ROOK, tmp_path / "rook", "c" * 40, bewaar) == (7, None)
    assert p.ruim.call_count == int(not bewaar)
    assert (tmp_path / "rook/proef.json").is_file()


def test_image_ontbreekt_of_daemon_weigert(monkeypatch: pytest.MonkeyPatch) -> None:
    p = Project.__new__(Project)
    p.diensten = {"hub": {"image": "eigen-hub", "profiles": ["kern"]}, "jelly-leon": {"image": "jelly-leon:p6"}}
    fout = subprocess.CompletedProcess([], 1, "", "Error: No such image")
    monkeypatch.setattr(project, "opdracht", lambda *a, **kw: fout)
    with pytest.raises(Proeffout, match="--bouw --profiel kern"):
        p.images(("hub",))
    assert p.images(("jelly-leon",)) == {}
    fout.stderr = "daemon niet bereikbaar"
    with pytest.raises(Proeffout, match="inspectie mislukt"):
        p.images(("jelly-leon",))


def test_start_zonder_build_of_pull_en_alleen_eigen_down(monkeypatch: pytest.MonkeyPatch) -> None:
    p = Project.__new__(Project)
    p.env = {}
    p.naam = "jlp-it-eigen-proef"
    inspectie = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(project, "opdracht", inspectie)
    compose = Mock()
    monkeypatch.setattr(p, "compose", compose)
    monkeypatch.setattr(p, "controleer_images", Mock())
    with socket.socket() as bezet:
        bezet.bind(("localhost", 0))
        p.diensten = {"hub": {"ports": [{"published": bezet.getsockname()[1]}]}}
        p.start(("hub",))
        assert p.env["INTEGRAAL_HUB_POORT"] == "0"
        p.env["INTEGRAAL_HUB_POORT"] = str(bezet.getsockname()[1])
        with pytest.raises(Proeffout, match="opgegeven hostpoort voor hub is bezet"):
            p.start(("hub",))
    compose.assert_called_once_with("up", "-d", "--no-build", "--pull", "never", "--no-deps", "hub", env={})
    p.ruim()
    assert compose.call_args.args == ("--profile", "*", "down", "-v", "--timeout", "15")
    assert all(c.args[0][-1] == "label=com.docker.compose.project=jlp-it-eigen-proef" for c in inspectie.call_args_list)
    inspectie.return_value = subprocess.CompletedProcess([], 0, "eigen-volume", "")
    with pytest.raises(Proeffout, match="achtergebleven"):
        p.ruim()


def test_workflowselectie_en_geen_versiebeheer_van_runmappen() -> None:
    ci = yaml.safe_load((ROOT / ".forgejo/workflows/ci.yml").read_text())
    assert "conformiteit-hub" in ci["jobs"]
    handmatig = yaml.safe_load((ROOT / ".forgejo/workflows/integrale-kern.yml").read_text())
    assert set(handmatig.get("on", handmatig.get(True))) == {"workflow_dispatch"}
    assert list(handmatig["jobs"]) == ["kernrook"]
    probe = MAP / "evidence/proef/rook/proef.json"
    assert subprocess.run(["git", "check-ignore", str(probe)], cwd=ROOT, stdout=subprocess.DEVNULL).returncode == 0


@pytest.mark.parametrize(("profiel", "verwacht"), [("kern", ["hub"]), ("derden", ["beszel"]),
                                                   (None, ["hub", "beszel"])])
def test_bouw_per_profiel_een_keer_per_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                             profiel: str | None, verwacht: list[str]) -> None:
    (tmp_path / "Dockerfile").touch()
    bouw = {"context": str(tmp_path)}
    p = Project.__new__(Project)
    p.diensten = {"hub": {"build": bouw, "image": "lokaal/hub", "profiles": ["kern"]},
                  "beszel": {"build": bouw, "image": "lokaal/sim", "profiles": ["derden"]},
                  "gotify": {"build": bouw, "image": "lokaal/sim", "profiles": ["derden"]},
                  "jelly-leon": {"image": "jelly-leon:p6", "profiles": ["mediaserver"]}}
    compose = Mock()
    monkeypatch.setattr(p, "compose", compose)
    monkeypatch.setattr(p, "images", lambda namen: {namen[0]: {"image_id": "lokaal"}})
    monkeypatch.setattr(project, "opdracht", lambda *a, **kw: subprocess.CompletedProcess([], 0, "", ""))
    monkeypatch.setattr(project, "broncommit", lambda _map: "d" * 40)
    p.bouw(profiel)
    assert [call.args for call in compose.call_args_list] == [("build", naam) for naam in verwacht]
    with pytest.raises(Proeffout, match="Onbekend bouwprofiel"):
        p.bouw("onbekend")


def test_ongeldige_validatie_maakt_de_proef_rood(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = replace(ROOK, id="EX38", soort="Sim+", blokkade="onbekend", toelichting="Tuner op locatie")
    p = proefproject()
    p.diensten["jelly-leon"] = {}
    monkeypatch.setattr(run, "ROOT", tmp_path)
    code, rij = run.draai(p, replace(scenario, diensten=("jelly-leon",)), tmp_path / "EX38", "d" * 40, False)
    verslag = json.loads((tmp_path / "EX38/proef.json").read_text())
    assert code == verslag["exitcode"] == 2
    assert verslag["uitkomst"] == "proeffout" and rij is None


def test_bewaar_levert_de_werkelijke_projectnaam_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = proefproject()

    def maak_project(naam):
        p.naam = naam
        return p

    def pytest_uitvoeren(args, *, env, tijd):
        bewijs.schrijf(Path(env["INTEGRAAL_DOSSIER"]) / "pytest.json", {"getoetst": True})
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(run, "MAP", tmp_path)
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(run, "Project", maak_project)
    monkeypatch.setattr(run, "broncommit", lambda _: "f" * 40)
    monkeypatch.setattr(run, "catalogus", lambda _: [ROOK])
    monkeypatch.setattr(run, "opdracht", pytest_uitvoeren)
    assert run.main(["--groep", "rook", "--bewaar"]) == 0
    verslag = json.loads(next(tmp_path.glob("evidence/*/rook/proef.json")).read_text())
    assert verslag["project"] == p.naam and p.naam.startswith("jlp-it-")
    p.ruim.assert_not_called()


def test_vereiste_image_stopt_de_selectie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = proefproject()
    p.images.side_effect = project.BouwNodig("Image ontbreekt voor hub. Voer eerst --bouw --profiel kern uit")
    factory = Mock(return_value=p)
    monkeypatch.setattr(run, "MAP", tmp_path)
    monkeypatch.setattr(run, "ROOT", tmp_path)
    monkeypatch.setattr(run, "Project", factory)
    monkeypatch.setattr(run, "broncommit", lambda _: "d" * 40)
    monkeypatch.setattr(run, "catalogus", lambda _: [replace(ROOK, id="EX01"), replace(ROOK, id="EX02")])
    assert run.main(["--alles"]) == 1
    factory.assert_called_once()
    p.start.assert_not_called()
    assert json.loads(next(tmp_path.rglob("EVIDENCE-voorstellen.json")).read_text()) == []
    assert "--bouw --profiel kern" in json.loads(next(tmp_path.rglob("proef.json")).read_text())["melding"]


def test_overzicht_noemt_de_kernselectie_en_mediaserverimage() -> None:
    tekst = (MAP.parent / "README.md").read_text()
    inleiding = tekst.split("##", 1)[0].lower()
    assert "kern" in inleiding and "profiel" in inleiding
    rij, = [regel for regel in tekst.splitlines() if regel.startswith("| `jelly-leon` |")]
    dienst = yaml.safe_load((MAP / "compose.yaml").read_text())["services"]["jelly-leon"]
    assert "build" not in dienst and f"`{dienst['image']}`" in rij
