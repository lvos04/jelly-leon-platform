"""Verbind de twee nummeringen met de fysieke video en ondertitel, ook na een echte bibliotheeksync."""

import json
from typing import Any

from e2e.integral.media_inhoud import ANIME, Inhoud, inhoud
from e2e.integral.scenarios.conftest import Kern

SCENARIO = {
    "groepen": ("media", "media-inhoud"), "diensten": ("mg-inhoud", "kijkserver-inhoud", "arr-inhoud"),
    "eigenaar": "media-guard-ultimate",
    "verwachting": "Twee verschillende scene- en TVDB-nummers houden na verversen hun eigen video en ondertitel.",
}


def koppelingen(proef: Inhoud, lib: int, afleveringen: list[dict[str, Any]]) -> dict[int, Any]:
    koppels = {}
    for ep in afleveringen:
        n = ep["episodeNumber"]
        assert n in (1, 2)
        detail = proef.mg.get(f"/api/v1/library/{lib}/episodes/{ep['id']}/details").raise_for_status().json()
        rij, bestand = detail["episode"], detail["file"]
        pad = ANIME + f"S01E{n:02}.mkv"
        sub = pad.removesuffix(".mkv") + ".nl.srt"
        assert (ep["absoluteEpisodeNumber"], ep["sceneAbsoluteEpisodeNumber"]) == (12 + n, 15 - n)
        assert (rij["absolute_episode_number"], rij["scene_season_number"], rij["scene_episode_number"]) == (
            12 + n, ep["sceneSeasonNumber"], 3 - n)
        assert rij["episode_number"] == n and rij["episode_file_id"] == ep["episodeFileId"] == bestand["provider_id"]
        assert bestand["path"] == pad
        assert json.loads(proef.fixture("ondertitel", bestand["path"])) == [f"Aflevering {n}"] * 2
        koppels[n] = {"aflevering": ep["id"], "bestand": bestand["provider_id"], "tvdb": 12 + n, "scene": 15 - n,
                      "video": proef.verwacht[pad], "ondertitel": proef.verwacht[sub]}
    assert set(koppels) == {1, 2} and all(koppels[1][k] != koppels[2][k] for k in ("video", "ondertitel", "bestand"))
    return koppels


def test_verversen_met_gekruiste_nummers(kern: Kern) -> None:
    with inhoud(kern, "anime") as proef:
        rij = proef.aanvraag(anime=True)
        sid = rij["provider_id"]
        eps = proef.sonarr("sonarr-anime", "episode", seriesId=sid)
        voor = koppelingen(proef, rij["lib_item_id"], eps)
        res = proef.arr.post("/sonarr-anime/api/v3/command", headers={"X-Api-Key": proef.sleutels["sonarr-anime"]},
                             json={"name": "RefreshSeries", "seriesId": sid}).raise_for_status().json()
        assert proef.sonarr("sonarr-anime", f"command/{res['id']}")["status"] == "completed"
        na_eps = proef.sonarr("sonarr-anime", "episode", seriesId=sid)
        assert [a["id"] for a in na_eps] == [a["id"] for a in reversed(eps)]
        bestandsids = [f["id"] for f in proef.sonarr("sonarr-anime", "episodefile", seriesId=sid)]
        assert [a["episodeFileId"] for a in na_eps] == list(reversed(bestandsids))
        proef.synchroniseer("sonarr-anime")
        na = koppelingen(proef, rij["lib_item_id"], na_eps)
        proef.noteer("koppelingen", {"voor": voor, "na": na})
        assert na == voor
