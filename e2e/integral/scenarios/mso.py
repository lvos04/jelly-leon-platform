"""Bediening van MSO en de downloadsimulators op het projectnetwerk."""

from __future__ import annotations

import secrets
from typing import Any

import httpx

from e2e.integral.scenarios.conftest import Kern, wacht


def eerste_start(kern: Kern, modus: str) -> httpx.Client:
    api = kern.client("mso", 38081)
    api.base_url = api.base_url.join("/api/v1/")
    wacht("MSO met eigen database", lambda: api.get("setup/status").json().get("setup_required") is True)
    setup = api.post("setup/admin", json={"username": "proefbeheer", "password": "Proef-" + secrets.token_urlsafe(24)})
    api.headers["X-CSRF-Token"] = setup.raise_for_status().json()["csrf_token"]
    assert api.post("setup/complete", json={"mode": modus}).raise_for_status().json()["setup_required"] is False
    assert api.get("system/mode").raise_for_status().json()["mode"] == modus
    return api


def koppel(api: httpx.Client, naam: str, sim: httpx.Client, geheim: dict[str, str],
           **opties: str) -> None:
    antwoord = api.put(f"connectors/{naam}", json={"enabled": True, "url": str(sim.base_url).rstrip("/"),
                                                   "secrets": geheim, "options": opties})
    assert antwoord.raise_for_status().json()["enabled"] is True


def sab_client(kern: Kern, api: httpx.Client) -> httpx.Client:
    sab = kern.client("sabnzbd-sim", 30217)
    sleutel = sab.get("/_sim/sleutels").raise_for_status().json()["apikey"]
    koppel(api, "sabnzbd", sab, {"api_key": sleutel})
    sab.params = {"apikey": sleutel, "output": "json"}
    return sab


def sab_api(sab: httpx.Client, mode: str, **params: str) -> dict[str, Any]:
    data: dict[str, Any] = sab.get("/api", params={"mode": mode, **params}).raise_for_status().json()
    return data


def toepassen(api: httpx.Client, voorstel: dict[str, Any]) -> dict[str, Any]:
    pad = f"changes/{voorstel['id']}"
    assert voorstel["status"] == "preview" and voorstel["risk"] == "toestemming_nodig"
    assert api.post(pad + "/approve").raise_for_status().json()["status"] == "approved"
    wijziging: dict[str, Any] = api.post(pad + "/apply").raise_for_status().json()
    assert wijziging["status"] == "applied"
    assert all(op.get("result") == "toegepast" for op in wijziging["ops"])
    return wijziging
