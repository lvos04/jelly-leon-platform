"""tools/upstream_rooktest.py: dekt elk upstream-manifest en leest de formulierwaarden uit het manifest zelf.

De rooktest zelf (docker, echte upstream-images) draait in CI-job ``rooktest`` en lokaal met ``make upstream-rooktest``;
hier alleen wat zonder Docker te controleren is.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import upstream_rooktest as ur  # noqa: E402


def test_every_upstream_custom_app_manifest_is_covered() -> None:
    gevonden = ur.upstream_custom_apps()
    assert gevonden, "geen upstream-manifesten gevonden"
    assert sorted(gevonden) == sorted([*ur.APPS, *ur.ELDERS_GEDEKT]), (
        "nieuw upstream-manifest: neem het op in tools/upstream_rooktest.py (APPS of ELDERS_GEDEKT)")
    for app_id, hulpmiddel in ur.ELDERS_GEDEKT.items():
        assert app_id not in ur.APPS and (ROOT / hulpmiddel).is_file(), hulpmiddel


@pytest.mark.parametrize("app_id", ur.APPS)
def test_app_values_come_from_the_manifest(app_id: str) -> None:
    m = yaml.safe_load((ROOT / "install/manifests" / f"{app_id}.manifest.yaml").read_text(encoding="utf-8"))
    app = ur.App.uit_manifest(app_id, {"KEY": "ssh-ed25519 test"} if app_id == "beszel-agent" else None)
    assert app.image == f"{m['image']['repository']}:{m['image']['tag']}@{m['image']['digest']}"
    assert "latest" not in app.image and app.digest.startswith("sha256:")
    assert app.mount == m["storage"][0]["mount"] and app.container_port == m["network"]["ports"][0]["container_port"]
    vaste_env = {e["name"]: e["value"] for e in m["custom_app"]["env"] if "value" in e}
    assert vaste_env.items() <= app.env.items()
    assert m["custom_app"]["run_as"] == {"mode": "custom", "user": 568, "group": 568}  # de rooktest start als 568:568


def test_a_parameter_without_value_is_refused() -> None:
    with pytest.raises(ur.Mislukt, match="KEY"):
        ur.App.uit_manifest("beszel-agent")


def test_report_guard_recognises_secrets() -> None:
    assert ur.GEHEIM_RE.search("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA")
    assert ur.GEHEIM_RE.search('"token": "eyJhbGciOiJIUzI1NiJ9.eyJ"')
    assert not ur.GEHEIM_RE.search('{"digest": "sha256:8514a69d", "readback_klopt": true}')


def test_saved_report_is_complete_and_honest() -> None:
    """Bewaard rapport (``docs/platform-v2/bewijs/upstream-rooktest-p1.json``) is bewijs in EVIDENCE.json."""
    import json

    r = json.loads((ROOT / "docs/platform-v2/bewijs/upstream-rooktest-p1.json").read_text(encoding="utf-8"))
    assert r["uitkomst"] == "geslaagd" and r["validatie"] == "Sim-getest; NIET GEVALIDEERD (echte NAS)"
    assert "geen TrueNAS" in r["omgeving"]
    stappen = {s["id"]: s for s in r["stappen"]}
    assert list(stappen) == ["B1", "B2", "B3", "G1", "U1"]
    assert {s["uitkomst"] for s in stappen.values()} == {"geslaagd"}
    for sid in ("B1", "B3", "G1", "U1"):
        assert stappen[sid]["details"]["processen_uid"] == ["568"] and stappen[sid]["details"]["readback_klopt"]
    assert stappen["B3"]["details"]["na_herstart"] == {"systemen": 1, "status": "up"}
    for app_id, image in r["images"].items():  # het rapport hoort bij de huidige manifesten
        assert image == ur.App.uit_manifest(app_id, {"KEY": "x"}).image
    assert not ur.GEHEIM_RE.search(json.dumps(r))
