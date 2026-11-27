"""Een gewone seizoenaanvraag volgt de standaardinstantie tot in de MediaGuard-bibliotheek."""

from e2e.integral.media_inhoud import inhoud
from e2e.integral.scenarios.conftest import Kern

SCENARIO = {
    "groepen": ("media", "media-inhoud"), "diensten": ("mg-inhoud", "kijkserver-inhoud", "arr-inhoud"),
    "eigenaar": "media-guard-ultimate",
    "verwachting": "Seerr vraagt seizoen één aan bij Sonarr Series en MediaGuard koppelt precies die aanvraag.",
}


def test_gewone_serie(kern: Kern) -> None:
    with inhoud(kern, "serie") as proef:
        rij = proef.aanvraag(anime=False)
        serie = proef.sonarr("sonarr-series", f"series/{rij['provider_id']}")
        assert serie["seasons"] == [{"seasonNumber": 1, "monitored": True}]
        assert serie["rootFolderPath"] == "/media/sonarr-series"
