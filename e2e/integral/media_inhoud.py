"""Koppel de inhoudsproeven aan de echte MediaGuard-kern en de lokale Seerr/arr-simulator."""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from e2e.integral.bewijs import schrijf
from e2e.integral.scenarios.conftest import Kern, wacht

FIXTURES = Path(__file__).with_name("mediafixtures")
INSTANTIES = ("sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms")
STAGING = "/media/sonarr-series/De vuurtoren/staging/"
ANIME = "/media/sonarr-anime/De maanreis/"


class Inhoud:
    def __init__(self, kern: Kern):
        self.kern = kern
        self.mg = kern.client("mg-inhoud", 8100)
        self.arr = kern.client("arr-inhoud", 30216)
        self.sleutels: dict[str, str] = {}
        self.metingen: dict[str, Any] = {}
        self.verwacht: dict[str, str] = {}
        self.voortgang: dict[str, float] = {}
        self.historie: dict[str, str] = {}

    def fixture(self, *args: str) -> str:
        return self.kern.net.project.compose("exec", "-T", "mg-inhoud", "python", "-", *args,
                                             invoer=(FIXTURES / "bibliotheek.py").read_text())

    def begin(self, soort: str) -> None:
        wacht("MediaGuard", lambda: self.mg.get("/api/v1/health").status_code == 200)
        auth = self.mg.post("/api/v1/auth/setup", json={"username": "inhoudproef",
                            "password": secrets.token_urlsafe(32)}).raise_for_status().json()
        self.mg.headers["X-CSRF-Token"] = auth["csrf_token"]
        # Alleen de wortel van het nieuwe projectvolume aan de gewone testgebruiker toekennen.
        self.kern.net.project.compose("exec", "-T", "--user", "root", "mg-inhoud", "chown", "568:568", "/media")
        self.fixture("maak", soort)
        job = self.mg.post("/api/v1/library/sync", params={"instance_id": "sonarr-series"}).raise_for_status().json()
        job_id = job["queued"]["sonarr-series"]
        self.fixture("voortgang", str(job_id))
        self.verwacht = self.meet("voor").copy()
        assert self.voortgang[str(job_id)] == 0.625
        if soort != "kwaliteit":
            self.sleutels = self.arr.get("/_sim/sleutels").raise_for_status().json()
            basis = str(self.arr.base_url).rstrip("/")
            for naam in INSTANTIES:
                self.mg.patch(f"/api/v1/instances/{naam}", json={"url": f"{basis}/{naam}",
                              "api_key": self.sleutels[naam]}).raise_for_status()
            instellingen = {"url": basis, "api_key": self.sleutels["seerr"]}
            self.mg.put("/api/v1/seerr/settings", json=instellingen).raise_for_status()

    def noteer(self, naam: str, meting: Any) -> None:
        self.metingen[naam] = meting
        schrijf(self.kern.net.dossier / "inhoud.json", self.metingen)

    def meet(self, naam: str) -> dict[str, str]:
        inventaris: dict[str, dict[str, str]] = json.loads(self.fixture("inventaris"))
        hashes = inventaris["sha256"]
        jobs = self.mg.get("/api/v1/jobs", params={"limit": 1000}).raise_for_status().json()
        assert 0 < len(jobs) < 1000 and hashes
        stand = {str(j["id"]): float(j["progress"]) for j in jobs}
        self.noteer(naam, {**inventaris, "taakvoortgang": stand})
        assert not self.historie or self.historie == inventaris["historie"]
        self.historie = inventaris["historie"]
        assert all(j in stand and stand[j] >= waarde for j, waarde in self.voortgang.items())
        self.voortgang = stand
        return hashes

    def sonarr(self, instantie: str, route: str, **params: Any) -> Any:
        return self.arr.get(f"/{instantie}/api/v3/{route}", headers={"X-Api-Key": self.sleutels[instantie]},
                            params=params).raise_for_status().json()

    def aanvraag(self, *, anime: bool) -> dict[str, Any]:
        doel = "sonarr-anime" if anime else "sonarr-series"
        body = {"mediaType": "tv", "mediaId": 102 if anime else 101, "seasons": [1]}
        if anime:
            servers = self.arr.get("/api/v1/settings/sonarr", headers={"X-Api-Key": self.sleutels["seerr"]}).json()
            body["serverId"] = next(s["id"] for s in servers if s["name"] == doel and not s["isDefault"])
        res = self.arr.post("/api/v1/request", json=body, headers={"X-Api-Key": self.sleutels["seerr"]})
        assert res.status_code == 201
        request = res.json()
        paden = [v["pad"] for v in self.kern.verzoeklog(self.arr)
                 if v["methode"] == "POST" and v["pad"].endswith("/api/v3/series")]
        self.noteer("routering", {"doel": doel, "serieposts": paden, "seizoen": request["seasons"][0]["seasonNumber"]})
        assert paden == [f"/{doel}/api/v3/series"]
        serie = self.sonarr(doel, "series", tvdbId=request["media"]["tvdbId"])
        assert len(serie) == 1 and serie[0]["id"] == request["media"]["externalServiceId"]
        assert serie[0]["seriesType"] == ("anime" if anime else "standard")
        ander = "sonarr-series" if anime else "sonarr-anime"
        assert self.sonarr(ander, "series", tvdbId=request["media"]["tvdbId"]) == []
        self.synchroniseer(doel)
        self.mg.post("/api/v1/seerr/requests/sync").raise_for_status()
        gekoppeld = self.mg.get("/api/v1/seerr/requests").raise_for_status().json()["items"]
        assert len(gekoppeld) == 1
        rij: dict[str, Any] = gekoppeld[0]
        assert rij["seerr_request_id"] == request["id"] and rij["link_status"] == "linked"
        assert rij["instance_id"] == doel and rij["provider_id"] == serie[0]["id"] and rij["lib_item_id"]
        self.noteer("koppeling", {"aanvraag": rij["seerr_request_id"], "instantie": doel,
                                 "arr_id": rij["provider_id"], "bibliotheek_id": rij["lib_item_id"],
                                 "koppelstatus": rij["link_status"]})
        return rij

    def synchroniseer(self, doel: str) -> None:
        res = self.mg.post("/api/v1/library/sync", params={"mode": "inline", "instance_id": doel})
        telling = res.raise_for_status().json()["results"][doel]
        assert telling["series"] == 1 and telling["episodes"] == 2 and telling["failed_items"] == []


@contextmanager
def inhoud(kern: Kern, soort: str) -> Iterator[Inhoud]:
    proef = Inhoud(kern)
    proef.begin(soort)
    try:
        yield proef
    finally:
        assert proef.meet("na") == proef.verwacht
