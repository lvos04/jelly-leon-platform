"""De leesproef moet de vastgelegde bewijsrij en scenariometingen echt vergelijken."""

import json

import pytest

from e2e.integral import controle_mediabewijs as controle


@pytest.mark.parametrize(("veld", "scenario"), [("status", "EX03"), ("metingen", "EX05"), ("gelijk", "alle")])
def test_leescontrole_detecteert_afwijking(tmp_path, veld, scenario):
    root = tmp_path / "repo"
    run = tmp_path / "proefrun"
    run.mkdir()
    map_ = root / "docs/platform-v2"
    (map_ / "bewijs").mkdir(parents=True)
    voorstellen = []
    rijen = []
    dossiers = {}
    for rid in controle.SELECTIE:
        sub = run / rid
        sub.mkdir()
        bestand = f"e2e/integral/scenarios/{rid}_proef.py"
        bron = root / bestand
        bron.parent.mkdir(parents=True, exist_ok=True)
        bron.touch()
        rij = {"id": rid, "status": "Bezig", "validatie": "Sim-geslaagd", "bijgewerkt": "2026-11-27"}
        voorstellen.append(rij)
        rijen.append({**rij, "bewijs": f"{bestand}, {controle.BEWIJS} ({rid}, run {run.name})"})
        proef = {"id": rid, "broncommit": "a" * 40, "uitkomst": rij["validatie"], "exitcode": 0,
                 "pytest_exitcode": 0, "images": {}}
        data = {"voor": {"proef": 1}}
        for naam, inhoud in [("proef", proef), ("pytest", {"getoetst": True, "overgeslagen": False, "exitcode": 0}),
                             ("inhoud", data)]:
            (sub / f"{naam}.json").write_text(json.dumps(inhoud))
        dossiers[rid] = {"bestand": bestand, "validatie": rij["validatie"], "exitcode": 0,
                        "diensten": [], "metingen": data}
    (run / "EVIDENCE-voorstellen.json").write_text(json.dumps(voorstellen))
    if veld == "status":
        rijen[0]["status"] = "Geblokkeerd"
    if veld == "metingen":
        dossiers[scenario]["metingen"] = {}
    (map_ / "EVIDENCE.json").write_text(json.dumps(rijen))
    (root / controle.BEWIJS).write_text(json.dumps({"run": run.name, "broncommit": "a" * 40,
                                                    "selectie": controle.SELECTIE,
                                                    "images": {}, "scenarios": dossiers}))
    if veld == "gelijk":
        controle.vergelijk(root, run)
    else:
        with pytest.raises(ValueError, match=scenario):
            controle.vergelijk(root, run)
