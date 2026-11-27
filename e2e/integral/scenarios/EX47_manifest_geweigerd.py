"""De gekoppelde appstore geeft alleen voor het geldige manifest een installatieopdracht vrij in de preview."""

from pathlib import Path
from typing import Any

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import meet, oproepen, vernieuw
from e2e.integral.scenarios.opslag import beheerheaders

SCENARIO = {
    "groepen": ("opslag",), "diensten": ("appstore",), "later_starten": ("appstore",),
    "eigenaar": "jelly-leon-platform",
    "verwachting": "Privileged en hostpad blokkeren de preview vóór app.create. Een geldig manifest mag door.",
}


def test_manifest_en_geldige_tegenproef(kern: Kern) -> None:
    project = kern.net.project
    app = "appstore-en-fork"
    grant = kern.wijzig("POST", "/api/v1/services/enrollment-tokens", json={"app_id": app}).json()
    project.start(("appstore",), INTEGRAAL_APPSTORE_ENROLLMENT=grant["enrollment_token"],
                  INTEGRAAL_APPSTORE_AFDRUK=grant["keyset_fingerprint"])
    client = kern.client("appstore", 30430)
    wacht("appstore actief", lambda: any(d["app_id"] == app and d["status"] == "active" for d in
          kern.hub.get("/api/v1/services").raise_for_status().json()["items"]))
    fixtures = Path(project.env["INTEGRAAL_BRONMAP"]) / app / "apps/api/tests/fixtures/manifests"
    kern.sim.post("/_sim/fixtures", json={"datasets": ["simpool/proeffotos"]}).raise_for_status()
    vernieuw(kern)
    inventaris = kern.hub.get("/api/v1/inventory/apps").raise_for_status().json()
    assert inventaris["status"] == "ok" and inventaris["items"] == []

    def preview(bestand: str, params: dict[str, str]) -> dict[str, Any]:
        rij: dict[str, Any] = client.post("/catalog/v1/preview", headers=beheerheaders(kern, app), json={
            "manifest_yaml": (fixtures / bestand).read_text(), "parameters": params,
            "target": {"truenas_version": "25.10.8", "ix_app_version": "1.4.8"},
            "inventory": {"observed_at": inventaris["observed_at"], "source": "simulator", "apps": inventaris["items"]},
        }).raise_for_status().json()
        return rij

    fout = preview("ex47-privileged-hostmount.manifest.yaml", {})
    codes = {f["code"] for f in fout["validation"]["findings"]}
    assert {"privileged_forbidden", "forbidden_host_path"} <= codes
    assert fout["ready_for_install"] is False and fout["translation"] is None and fout["executed"] is False
    bewijs = client.get("/connector/v1/evidence/" + fout["evidence_id"], headers=beheerheaders(kern, app))
    assert {"privileged_forbidden", "forbidden_host_path"} <= set(bewijs.raise_for_status().json()["summary"][
        "blocking_codes"])
    assert all(r["method"] != "app.create" for r in oproepen(kern))
    goed = preview("valid-catalog.manifest.yaml", {"timezone": "Etc/UTC", "library_path": "/mnt/simpool/proeffotos"})
    assert goed["ready_for_install"] is True and goed["executed"] is False
    assert goed["translation"]["payload"]["catalog_app"] == "voorbeeldapp"
    assert all(r["method"] != "app.create" for r in oproepen(kern))
    meet(kern, verboden_privileges=True, verboden_hostpad=True, geldige_preview=True, app_create=0)
