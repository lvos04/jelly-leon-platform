"""Kloon uitsluitend de oude configuratie via de door de Hub ondertekende snapshotacties."""

from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import meet, schrijfacties, vernieuw
from e2e.integral.scenarios.opslag import (
    BRON,
    KLOON,
    SNAP,
    aanvraag,
    bereid_opslag_voor,
    inhoud,
    plaats_proefmedia,
    schrijf_inhoud,
    snapshot,
    voer_uit,
)

SCENARIO = {
    "groepen": ("opslag", "backupvoorwaarden"), "eigenaar": "jelly-leon-platform",
    "diensten": ("onedrive", "emby-opslag", "mg-inhoud", "arr-inhoud"),
    "verwachting": "De kloon krijgt de oude configuratie. De bron, kijkgeschiedenis en voortgang blijven.",
}


def test_alleen_configuratie_terugzetten(kern: Kern) -> None:
    bereid_opslag_voor(kern)
    media = plaats_proefmedia(kern)
    schrijf_inhoud(kern, BRON, instelling="oud")
    rij = snapshot(kern)
    assert rij["status"] == "klaar" and rij["blokkades"] == []
    snap = voer_uit(kern, rij).raise_for_status().json()
    assert snap["status"] == "uitgevoerd" and snap["resultaat"]["methode"] == "pool.snapshot.create"
    media.meet("na_snapshot")
    schrijf_inhoud(kern, BRON, instelling="nieuw")
    media.speel_verder()
    media.meet("voor_kloon")
    voor = inhoud(kern)["datasets"]
    assert KLOON not in voor
    doel = {"snapshot": SNAP}
    fout = aanvraag(kern, "zfs.snapshot.clone_to_sandbox", doel, {"doel_dataset": BRON})
    assert fout.status_code in {403, 422}
    media.meet("bron_geweigerd")
    assert schrijfacties(kern) == ["pool.snapshot.create"]
    kloon = aanvraag(kern, "zfs.snapshot.clone_to_sandbox", doel, {"doel_dataset": KLOON}).raise_for_status().json()
    assert kloon["status"] == "klaar" and kloon["blokkades"] == []
    uit = voer_uit(kern, kloon).raise_for_status().json()
    assert uit["status"] == "uitgevoerd" and uit["resultaat"]["methode"] == "pool.snapshot.clone"
    na = inhoud(kern)
    assert na["datasets"] == {**voor, KLOON: {"instelling": "oud"}}
    assert na["snapshots"] == {SNAP: {"instelling": "oud"}}
    assert na["datasets"][BRON] == {"instelling": "nieuw"}
    media.vervolg()
    assert schrijfacties(kern) == ["pool.snapshot.create", "pool.snapshot.clone"]
    assert kern.sim.get("/_sim/violations").raise_for_status().json() == []
    vernieuw(kern)
    apps = kern.hub.get("/api/v1/inventory/apps").raise_for_status().json()["items"]
    assert any(a["naam"] == "emby" and a["staat"] == "RUNNING" for a in apps)
    meet(kern, kloon_op_bron_geweigerd_http=fout.status_code, oude_config_in_kloon=True, brondata_behouden=True,
         kijkstanden=4, hervatpunt_ticks=8_700_000, taak_vervolgd=True, emby_sim_draait=True, schrijfacties=2)
