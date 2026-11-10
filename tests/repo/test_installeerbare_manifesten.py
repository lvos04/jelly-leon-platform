"""Installeerbare manifesten per I-document (P1: I07, I08, I09, I13, I23) — contract 1.2 §9, ADR-0014.

- Alle manifesten in ``install/manifests/`` worden gecontroleerd met de validator van de eigenaar van de standaard
  (``appstore-en-fork``: ``jl-manifest validate --strict``) via ``tools/manifesten.py``: zonder vermelding in
  ``validatie-uitzonderingen.yaml`` moet ``--strict`` schoon zijn; met vermelding precies die waarschuwingen.
- Eigen controles die altijd gelden: geen compose/YAML, geen ``latest``, upstream-images met digest en eigen gebruiker,
  geen host network, geen Docker-socket, geen voorbeeldwaarden, parameters uit wizard/datasetkiezer/geheimen.
- Vertaling naar ``app.create`` (``jl-manifest translate``) levert een standaard Custom App (``ix-app``) of de
  catalogusapp met vaste versie, met de tag als ``<versie>@sha256:<digest>`` (contract 1.2 §2.2).

Validator: ``$JL_MANIFEST`` of ``.venv-validator/bin/jl-manifest``. In CI zet de workflow
``MANIFEST_VALIDATOR_VERPLICHT=1``: dan faalt de test als de validator ontbreekt in plaats van over te slaan.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import manifesten as mf  # noqa: E402

VERPLICHT = os.environ.get("MANIFEST_VALIDATOR_VERPLICHT") == "1"
VERWACHT = {
    "beszel-hub": ("custom_app", "I07_BESZEL"),
    "beszel-agent": ("custom_app", "I07_BESZEL"),
    "uptime-kuma": ("custom_app", "I08_UPTIME_KUMA"),
    "authentik": ("catalog_app", "I09_AUTHENTIK"),
    "gotify": ("custom_app", "I13_GOTIFY"),
    "ai-server-manager": ("custom_app", "I23_HOME_CONTROL_HUB"),
    "ai-server-manager-executor": ("custom_app", "I23_HOME_CONTROL_HUB"),
}
# Gecontroleerd op 2026-11-09 met `docker buildx imagetools inspect` (index-digest, multi-arch).
DIGESTS = {
    "ghcr.io/henrygd/beszel/beszel:0.21.1": "sha256:8514a69d9bbbcb74f371a3205561ee231fc0289739cea64e8131f341d695b770",
    "ghcr.io/henrygd/beszel/beszel-agent:0.21.1":
        "sha256:cb6868012964d7a68debaa2c84bb72285e5f08531e81f506bd4c493bf052e41c",
    "ghcr.io/louislam/uptime-kuma:2.5.9": "sha256:e88415a90dd2f7efecdbc18de0490aff292764a3ac471531bf1d0481725591bd",
    "ghcr.io/gotify/server:3.1.1": "sha256:44fc5bbd1c0618878e33073d38c2c13126b48f245c3fe12236b1da48eb203b86",
}
# Parameterwaarden zoals de wizard/datasetkiezer ze tegen de TrueNAS-simulator zou meten (alleen voor de vertaaltest;
# 'simpool' is de pool van de simulator, geen echte poolnaam).
SIM_PARAMS: dict[str, Any] = {
    "timezone": "Europe/Amsterdam",
    "rpo": "P1D",
    "rto": "PT4H",
    "beszel_hub_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGVlbmxlZXNiYXJlc2xldXRlbHZvb3JkZXNpbXVsYXRvcg",
    "db_password": "sim-gegenereerd-db",
    "secret_key": "sim-gegenereerd-sleutel",
}


def _validator() -> Path:
    try:
        return mf.validator_pad(None)
    except FileNotFoundError as exc:
        if VERPLICHT:
            pytest.fail(str(exc))
        pytest.skip(str(exc))


def _load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((mf.MANIFEST_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_every_p1_document_has_its_manifests() -> None:
    gevonden = {_load(p.name)["app_id"]: _load(p.name) for p in mf.manifesten()}
    assert set(VERWACHT) <= set(gevonden), sorted(set(VERWACHT) - set(gevonden))
    for app_id, (methode, doc) in VERWACHT.items():
        m = gevonden[app_id]
        assert m["install"]["method"] == methode, app_id
        assert doc in m["docs"], app_id


def test_all_manifests_pass_the_appstore_validator_in_strict_mode() -> None:
    """Contract 1.2 §9: gevalideerd met de 1.2-validator van appstore-en-fork in --strict (met de vastgelegde,
    beredeneerde uitzonderingen; die moeten exact kloppen)."""
    uitkomsten = mf.controleer(_validator())
    assert uitkomsten
    fouten = {u.bestand: u.afwijkingen for u in uitkomsten if not u.in_orde}
    assert fouten == {}
    schoon = {u.app_id for u in uitkomsten if not u.waarschuwingen}
    assert {"beszel-hub", "beszel-agent"} <= schoon  # zonder enige melding in --strict


def test_upstream_digests_are_the_measured_ones() -> None:
    for pad in mf.manifesten():
        m = _load(pad.name)
        if m["install"]["method"] != "custom_app" or m["image"]["repository"].startswith("ghcr.io/lvos04/"):
            continue
        ref = f"{m['image']['repository']}:{m['image']['tag']}"
        assert DIGESTS.get(ref) == m["image"]["digest"], ref
        assert m["version"] == m["image"]["tag"]


def test_authentik_catalog_pin_and_image_digests_documented() -> None:
    m = _load("authentik.manifest.yaml")
    assert m["catalog"] == {"train": "community", "app": "authentik", "version": "1.3.64"}
    text = (mf.MANIFEST_DIR / "authentik.manifest.yaml").read_text(encoding="utf-8")
    assert "2026.8.5@sha256:602227a86455d6772e3d16b7d852713c4f864f40df5df894abd1285bb53979a5" in text
    assert m["values"]["authentik"]["mount_docker_socket"] is False
    params = {p["name"]: p for p in m["parameters"]}
    assert params["db_password"]["source"] == params["secret_key"]["source"] == "generated"
    for name in ("data_path", "templates_path", "certs_path", "postgres_path"):
        assert params[name]["source"] == "dataset_picker" and params[name]["type"] == "path"


@pytest.mark.parametrize("bestand", [p.name for p in mf.manifesten()])
def test_own_rules_independent_of_the_validator(bestand: str) -> None:
    m = _load(bestand)
    assert mf.eigen_controles(m) == []
    text = (mf.MANIFEST_DIR / bestand).read_text(encoding="utf-8")
    body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "docker.sock" not in body and "/var/run" not in body
    assert not re.search(r"\b(tank|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+)\b", body), "voorbeeldwaarde"
    for p in m["parameters"]:
        if p["type"] == "path":
            assert p["source"] == "dataset_picker", p["name"]
        if p["source"] in ("wizard", "dataset_picker", "generated", "secret"):
            assert "default" not in p, p["name"]
    if m["install"]["method"] == "custom_app":
        assert "ports" not in m["custom_app"], "contract 1.2 §9: poorten alleen in network.ports"
        for env in m["custom_app"]["env"]:
            assert not env["name"].startswith("HUB_"), "HUB_* geeft de Hub zelf mee bij app.install"
    assert "joins_platform_network" not in m["network"]


def _translate(validator: Path, bestand: str, params: dict[str, Any], tmp_path: Path) -> dict[str, Any]:
    pfile = tmp_path / "params.json"
    pfile.write_text(json.dumps(params), encoding="utf-8")
    res = subprocess.run(
        [str(validator), "translate", str(mf.MANIFEST_DIR / bestand), "--params", str(pfile),
         "--truenas-version", "25.10.8", "--ix-app-version", "1.4.8"],
        capture_output=True, text=True, check=False,
    )
    out: dict[str, Any] = json.loads(res.stdout)
    return out


@pytest.mark.parametrize("bestand", ["gotify.manifest.yaml", "uptime-kuma.manifest.yaml", "beszel-hub.manifest.yaml",
                                     "beszel-agent.manifest.yaml"])
def test_custom_app_translation_is_the_standard_form_with_digest_tag(bestand: str, tmp_path: Path) -> None:
    validator = _validator()
    m = _load(bestand)
    params = dict(SIM_PARAMS)
    for p in m["parameters"]:
        if p["type"] == "path":
            params[p["name"]] = f"/mnt/simpool/apps/{m['app_id']}"
    params = {k: v for k, v in params.items() if k in {p["name"] for p in m["parameters"]}}
    out = _translate(validator, bestand, params, tmp_path)
    assert out["ready_for_install"] is True, out
    payload = out["payload"]
    assert payload["catalog_app"] == "ix-app" and payload["train"] == "stable" and payload["custom_app"] is False
    assert "custom_compose_config" not in payload and "custom_compose_config_string" not in payload
    values = payload["values"]
    assert values["image"]["tag"] == f"{m['image']['tag']}@{m['image']['digest']}"
    assert values["run_as_custom_user"] is True and values["run_as"] == {"user": 568, "group": 568}
    assert values["privileged"] is False and values["host_network"] is False and values["capabilities"]["add"] == []
    assert values["restart_policy"] == "unless-stopped"
    assert out["digest_readback"]["expected_digest"] == m["image"]["digest"]


def test_authentik_translation_is_blocked_until_the_executor_fills_secrets(tmp_path: Path) -> None:
    """Eerlijk: de vertaling van appstore-en-fork geeft geheimen nooit via app.create-waarden door; vullen vanuit de
    vault door de executor bestaat nog niet (P2) → niet installeerbaar, met capability_unavailable in de melding.
    Er wordt nooit een voorbeeldgeheim gebruikt."""
    validator = _validator()
    m = _load("authentik.manifest.yaml")
    params = {k: v for k, v in SIM_PARAMS.items() if k in {p["name"] for p in m["parameters"]}}
    for p in m["parameters"]:
        if p["type"] == "path":
            params[p["name"]] = f"/mnt/simpool/apps/authentik/{p['name'].removesuffix('_path')}"
    out = _translate(validator, "authentik.manifest.yaml", params, tmp_path)
    assert out["ready_for_install"] is False and out["payload"] is None
    errors = {(f["code"], f["path"]) for f in out["findings"] if f["severity"] == "error"}
    assert errors == {("parameter_value_not_allowed", "values.authentik.db_password"),
                      ("parameter_value_not_allowed", "values.authentik.secret_key")}
    assert all("capability_unavailable" in f["message"] for f in out["findings"] if f["severity"] == "error")
    assert "sim-gegenereerd" not in json.dumps(out)  # het geheim komt niet in de uitvoer terug
