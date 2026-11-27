"""Een expliciete anime-aanvraag behoudt het Dual Audio-formaat en het gewicht van het gekozen profiel."""

from typing import Any

from e2e.integral.media_inhoud import Inhoud, inhoud
from e2e.integral.scenarios.conftest import Kern

SCENARIO = {
    "groepen": ("media", "media-inhoud"), "diensten": ("mg-inhoud", "kijkserver-inhoud", "arr-inhoud"),
    "eigenaar": "media-guard-ultimate",
    "verwachting": "De anime-server ontvangt de aanvraag met zijn bestaande gewogen Dual Audio-profiel.",
}


def audioprofiel(proef: Inhoud, serie_id: int | None = None) -> dict[str, Any]:
    params: dict[str, str | int] = {"media_type": "anime", "instance_id": "sonarr-anime"}
    if serie_id is not None:
        params["series_id"] = serie_id
    beleid = proef.mg.get("/api/v1/policy/effective", params=params).raise_for_status().json()
    audio = beleid["config"]["audio"]
    assert audio["mode"] == "anime" and audio["anime_preference"] == "dual_first"
    profiel, = proef.sonarr("sonarr-anime", "qualityprofile")
    formaat, = [f for f in proef.sonarr("sonarr-anime", "customformat") if f["name"] == "Dual Audio"]
    regel, = [r for r in profiel["formatItems"] if r["format"] == formaat["id"]]
    assert regel["name"] == "Dual Audio" and regel["score"] > 0
    return {"profiel": profiel["id"], "formaat": formaat["id"], "gewicht": regel["score"],
            "beleid": beleid["profile_id"], "voorkeur": audio["anime_preference"]}


def test_anime_met_dubbele_audio(kern: Kern) -> None:
    with inhoud(kern, "anime") as proef:
        voor = audioprofiel(proef)
        rij = proef.aanvraag(anime=True)
        serie = proef.sonarr("sonarr-anime", f"series/{rij['provider_id']}")
        assert serie["qualityProfileId"] == voor["profiel"] and serie["rootFolderPath"] == "/media/sonarr-anime"
        na = audioprofiel(proef, rij["provider_id"])
        proef.noteer("dubbele_audio", {"voor": voor, "na": na})
        assert na == voor
