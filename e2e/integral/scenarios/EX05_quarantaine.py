"""Laat de leveringsmotor een bewezen verwisseling veiligstellen en herstel via de gewone beheerroute."""

from typing import Any

from e2e.integral.media_inhoud import STAGING, Inhoud, inhoud
from e2e.integral.scenarios.conftest import Kern, wacht

SCENARIO = {
    "groepen": ("media", "media-inhoud"),
    "diensten": ("mg-inhoud", "inhoud-fixture", "kijkserver-inhoud", "arr-inhoud", "mg-inhoud-herstel"),
    "later_starten": ("mg-inhoud", "mg-inhoud-herstel"),
    "eigenaar": "media-guard-ultimate",
    "verwachting": "Een juist stagingbestand blijft staan. Een bewezen verwisseling is bytegelijk herstelbaar.",
}


def beoordeel(proef: Inhoud, fid: int, aflevering: int, sha: str) -> dict[str, Any]:
    res = proef.mg.post(f"/api/v1/identity/sonarr-series/episode_file/{fid}/verify",
                        json={"expected": [{"season": 1, "episode": aflevering}],
                              "path": STAGING + f"S01E{aflevering:02}.mkv",
                              "hashes": {"sha256": sha}}).raise_for_status()
    resultaat: dict[str, Any] = res.json()
    return resultaat


def lever(proef: Inhoud) -> None:
    ronde = proef.mg.post("/api/v1/delivery/run").raise_for_status().json()
    assert ronde["processed"] > 0 and ronde["errors"] == 0


def test_juiste_en_verwisselde_aflevering_en_herstel(kern: Kern) -> None:
    with inhoud(kern, "quarantaine") as proef:
        vrij = proef.mg.post("/api/v1/recovery/library-write", json={"enabled": True, "confirm": True})
        assert vrij.raise_for_status().json()["library_write_enabled"] is True
        eps = proef.sonarr("sonarr-series", "episode", seriesId=proef.metingen["koppeling"]["arr_id"])
        bestanden = {e["episodeNumber"]: e["episodeFileId"] for e in eps if e["hasFile"]}
        assert set(bestanden) == {1, 2} and len(set(bestanden.values())) == 2
        for nummer, bestands_id in bestanden.items():
            naam = f"S01E{nummer:02}.mkv"
            sha = proef.verwacht[STAGING + naam]
            assert sha == proef.verwacht["/media/referentie/" + naam]
            route = f"/api/v1/identity/sonarr-series/episode_file/{bestands_id}/evidence"
            importregel = proef.mg.get(route).raise_for_status().json()["current"]
            assert importregel["source"] == "post_import" and importregel["file_path"] == STAGING + naam
            assert beoordeel(proef, bestands_id, nummer, sha)["status"] == "verified"
        fid = bestanden[1]
        pad = STAGING + "S01E01.mkv"
        goed = proef.verwacht[pad]
        fout = proef.verwacht["/media/referentie/S01E02.mkv"]
        assert goed != fout
        lever(proef)
        assert proef.mg.get("/api/v1/quarantine").raise_for_status().json() == []
        assert not any(v["methode"] == "DELETE" for v in kern.verzoeklog(proef.arr))
        assert proef.meet("juist_bestand") == proef.verwacht

        proef.fixture("verwissel")
        proef.verwacht[pad] = fout
        assert proef.meet("na_injectie") == proef.verwacht
        oordeel = beoordeel(proef, fid, 1, fout)
        assert oordeel["status"] == "mismatch" and oordeel["actionable"] and oordeel["actual"][0]["episode"] == 2
        lever(proef)
        item, = proef.mg.get("/api/v1/quarantine").raise_for_status().json()
        assert item["original_path"] == pad and item["status"] == "restorable"
        qpad = item["quarantine_path"]
        assert qpad != pad and qpad not in proef.verwacht
        proef.verwacht[qpad] = proef.verwacht.pop(pad)
        assert proef.meet("quarantaine") == proef.verwacht
        wis, = [v for v in kern.verzoeklog(proef.arr) if v["methode"] == "DELETE"]
        assert wis["pad"] == f"/sonarr-series/api/v3/episodefile/{fid}"
        proef.noteer("voor_verwijderen", wis["details"])
        assert wis["details"] == {"bron_sha256": fout, "quarantaine_sha256": fout}

        route = f"/api/v1/quarantine/{item['id']}"
        herstel = proef.mg.post(route + "/restore", json={"confirm": True}).raise_for_status().json()
        jobroute = f"/api/v1/jobs/{herstel['job_id']}"
        wacht("herstelwerker", lambda: proef.mg.get(jobroute).raise_for_status().json()["status"] == "succeeded")
        assert proef.mg.get(route).raise_for_status().json()["status"] == "restored"
        proef.verwacht[pad] = fout
        proef.noteer("herstel", {"bestands_id": fid, "verwijderverzoek": wis["pad"], "oordeel": oordeel["status"],
                                "quarantaine": item["id"], "hersteltaak": herstel["job_id"]})
