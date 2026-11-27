"""Eén herkenbare aflevering tussen MediaGuard, de arr-simulator en de SAB-wachtrij."""

from __future__ import annotations

import httpx

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.mso import sab_api, sab_client

INSTANTIE = "proef-sonarr"
SONARR = "/sonarr-series/api/v3"


class Downloadketen:
    def __init__(self, kern: Kern, mg: httpx.Client):
        self.arr = kern.client("arr-sim", 30216)
        self.sab = sab_client(kern)
        wacht("arr-simulator", lambda: self.arr.get("/_sim/sleutels").is_success)
        sleutels = self.arr.get("/_sim/sleutels").raise_for_status().json()
        self.headers = {"X-Api-Key": sleutels["sonarr-series"]}
        tv = self.arr.post("/api/v1/request", headers={"X-Api-Key": sleutels["seerr"]},
                           json={"mediaType": "tv", "mediaId": 101, "seasons": [1]}).raise_for_status().json()
        serie = tv["media"]["externalServiceId"]
        afleveringen = self.arr.get(SONARR + "/episode", headers=self.headers,
                                    params={"seriesId": serie}).raise_for_status().json()
        self.aflevering = afleveringen[0]["id"]
        self.tweede_aflevering = afleveringen[1]["id"]
        mg.post("/api/v1/instances", json={"id": INSTANTIE, "name": "Proefserie", "app": "sonarr", "media_type": "tv",
                "url": str(self.arr.base_url).rstrip("/") + "/sonarr-series",
                "api_key": self.headers["X-Api-Key"]}).raise_for_status()
        sync = mg.post("/api/v1/library/sync", params={"mode": "inline", "instance_id": INSTANTIE})
        assert sync.raise_for_status().json()["results"][INSTANTIE]["episodes"] == len(afleveringen)
        wanted = mg.get("/api/v1/wanted", params={"instance_id": INSTANTIE, "reason": "missing"}).raise_for_status()
        self.wanted, = (item["id"] for item in wanted.json()["items"] if item["provider_id"] == self.aflevering)

    def sab_ids(self) -> set[str]:
        slots = sab_api(self.sab, "queue")["queue"]["slots"]
        return {slot["nzo_id"] for slot in slots}

    def aflevering_ids(self) -> set[str]:
        records = self.arr.get(SONARR + "/queue", headers=self.headers).raise_for_status().json()["records"]
        return {r["downloadId"] for r in records if r["episodeId"] == self.aflevering}

    def importkandidaat(self) -> str:
        """Zet de andere aflevering in SAB als bestaand doel van het importverzoek."""
        vooraf = self.sab_ids()
        self.arr.post(SONARR + "/command", headers=self.headers,
                      json={"name": "EpisodeSearch", "episodeIds": [self.tweede_aflevering]}).raise_for_status()
        download, = self.sab_ids() - vooraf
        return download
