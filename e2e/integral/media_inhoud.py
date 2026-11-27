"""Koppel de inhoudsproeven aan de echte MediaGuard-kern en de lokale Seerr/arr-simulator."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from e2e.integral.bewijs import schrijf
from e2e.integral.scenarios.conftest import Kern, wacht

FIXTURES = Path(__file__).with_name("mediafixtures")
INSTANTIES = ("sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms")
STAGING = "/media/sonarr-series/De vuurtoren/staging/"
ANIME = "/media/sonarr-anime/De maanreis/"


BEWAARD = "/media/bestaand/bewaard.mkv"
KIJKERS = ("proefkijker-a", "proefkijker-b")
DOEL = {"serie": STAGING + "S01E01.mkv", "quarantaine": STAGING + "S01E01.mkv",
        "anime": ANIME + "S01E01.mkv", "kwaliteit": "/media/kwaliteitsproef/controle.mkv"}


def lees_kijkstand(kijkserver: httpx.Client, gebruikers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    kijkstand: dict[str, dict[str, Any]] = {}
    for u in gebruikers:
        uid = u["Id"]
        assert u["Name"] == uid
        items = kijkserver.get("/Items", params={"UserId": uid, "Fields": "Path", "Recursive": "true",
                                               "EnableUserData": "true"}).raise_for_status().json()["Items"]
        for item in items:
            per_pad = kijkstand.setdefault(item["Path"], {})
            assert uid not in per_pad and isinstance(item["UserData"], dict), "Ongeldige kijkgegevens"
            per_pad[uid] = item["UserData"]
    return kijkstand


class Inhoud:
    def __init__(self, kern: Kern):
        self.kern = kern
        self.mg = kern.client("mg-inhoud", 8100)
        self.arr = kern.client("arr-inhoud", 30216)
        self.kijkserver = kern.client("kijkserver-inhoud", 8096)
        self.sleutels: dict[str, str] = {}
        self.metingen: dict[str, Any] = {}
        self.verwacht: dict[str, str] = {}
        self.voortgang: dict[str, float] = {}
        self.historie: dict[str, dict[str, Any]] | None = None

    def fixture(self, *args: str) -> str:
        return self.kern.net.project.compose("exec", "-T", "inhoud-fixture", "python", "-", *args,
                                             invoer=(FIXTURES / "bibliotheek.py").read_text())

    def begin(self, soort: str) -> None:
        project = self.kern.net.project
        # Alleen de wortel van het nieuwe projectvolume aan de gewone testgebruiker toekennen.
        project.compose("exec", "-T", "--user", "root", "inhoud-fixture", "chown", "568:568", "/media")
        self.fixture("maak", soort)
        kijkstand = {pad: {u: {"Played": i == j, "PlaybackPositionTicks": 0 if i == j else (i + 1) * 1_200_000,
                               "IsFavorite": j == 1} for j, u in enumerate(KIJKERS)}
                     for i, pad in enumerate((DOEL[soort], BEWAARD))}
        start = self.kijkserver.post("/_sim/bibliotheek", json=kijkstand).raise_for_status().json()
        self.kijkserver.headers["Authorization"] = f'MediaBrowser Token="{start["api_key"]}"'
        if soort != "kwaliteit":
            self.sleutels = self.arr.get("/_sim/sleutels").raise_for_status().json()
        bestaand = self.vraag_bij_arr(anime=False) if soort == "quarantaine" else None
        imports = self.importmeldingen(bestaand["media"]["externalServiceId"]) if bestaand else []
        job_id = int(project.compose("exec", "-T", "inhoud-fixture", "python", "/proef/momentopname.py", soort,
                                     invoer=json.dumps({"kijksleutel": start["api_key"], "imports": imports})))
        project.compose("run", "--rm", "--no-deps", "-T", "mg-inhoud", "python", "-", "laad",
                        invoer=(FIXTURES / "momentopname.py").read_text())
        project.start(("mg-inhoud", "mg-inhoud-herstel") if soort == "quarantaine" else ("mg-inhoud",))
        wacht("MediaGuard", lambda: self.mg.get("/api/v1/health").status_code == 200)
        auth = self.mg.post("/api/v1/auth/setup", json={"username": "inhoudproef",
                            "password": secrets.token_urlsafe(32)}).raise_for_status().json()
        self.mg.headers["X-CSRF-Token"] = auth["csrf_token"]
        config = {"kind": "jellyfin", "url": str(self.kijkserver.base_url).rstrip("/"), "api_key": start["api_key"]}
        verbinding = self.mg.post("/api/v1/mediaserver/config", json=config).raise_for_status().json()
        assert verbinding["test"]["ok"] and ["/media", "/media"] in verbinding["test"]["path_map"]
        self.verwacht = self.meet("voor").copy()
        self.controleer_begin(job_id, kijkstand)
        if soort != "kwaliteit":
            basis = str(self.arr.base_url).rstrip("/")
            for naam in INSTANTIES:
                self.mg.patch(f"/api/v1/instances/{naam}", json={"url": f"{basis}/{naam}",
                              "api_key": self.sleutels[naam]}).raise_for_status()
            instellingen = {"url": basis, "api_key": self.sleutels["seerr"]}
            self.mg.put("/api/v1/seerr/settings", json=instellingen).raise_for_status()
        if bestaand:
            self.koppel("sonarr-series", bestaand)

    def noteer(self, naam: str, meting: Any) -> None:
        self.metingen[naam] = meting
        schrijf(self.kern.net.dossier / "inhoud.json", self.metingen)

    def controleer_begin(self, job: int, verwacht: dict[str, dict[str, Any]]) -> None:
        assert self.historie is not None
        assert verwacht.keys() <= self.verwacht.keys(), "Beginstand mediabestanden"
        assert self.historie["kijkgegevens"] == verwacht, "Beginstand kijkgegevens"
        sessies = self.mg.get("/api/v1/mediaserver/status").raise_for_status().json()["sessions"]["watching"]
        bekeken = [(u, p) for p, r in verwacht.items() for u, data in r.items() if data["Played"]]
        assert Counter((s["user"], s["title"], s["paused"]) for s in sessies) == Counter(
            (u, Path(pad).name, True) for u, pad in bekeken), "Beginstand sessies"
        rijen = self.historie["kijkregistraties"]
        for u, pad in bekeken:
            assert (rijen[pad]["user_name"], rijen[pad]["title"]) == (u, Path(pad).name) and rijen[pad]["last_seen"]
        detail = self.mg.get(f"/api/v1/jobs/{job}").raise_for_status().json()
        assert (detail["status"] == "retry_scheduled" and detail["attempts"] == 1 and detail["started_at"]), (
            "Beginstand taakuitvoering")
        assert detail["progress"] == self.voortgang[str(job)] == 0.625, "Beginstand taakvoortgang"
        assert not detail["logs_truncated"], "Beginstand taaklog onvolledig"
        kernlog = [r for r in self.historie["taaklog"].values() if r["job_id"] == job]
        assert [{k: r[k] for k in ("ts", "level", "message")} for r in kernlog] == detail["logs"], "Beginstand taaklog"
        meldingen = [r["message"] for r in detail["logs"] if r["level"] == "info"]
        assert {"Bestaande proefreeks ingepland", "Bestaande voortgang vastgelegd"} <= set(meldingen)
        self.noteer("dienstcontrole", {"taak": job, "voortgang": detail["progress"], "logregels": len(detail["logs"]),
                                       "status": detail["status"], "pogingen": detail["attempts"],
                                       "gestart": detail["started_at"],
                                       "sessies": len(sessies), "kijkstanden": sum(
                                           len(kijkers) for kijkers in self.historie["kijkgegevens"].values())})

    def meet(self, naam: str) -> dict[str, str]:
        inventaris = json.loads(self.fixture("inventaris"))
        assert inventaris["schrijfopening"] == "EROFS", "De fixture moet alleen leestoegang hebben"
        hashes: dict[str, str] = inventaris["sha256"]
        jobs = self.mg.get("/api/v1/jobs", params={"limit": 1000}).raise_for_status().json()
        assert 0 < len(jobs) < 1000 and hashes
        stand = {str(j["id"]): float(j["progress"]) for j in jobs}
        gebruikers = self.kijkserver.get("/Users").raise_for_status().json()
        historie = {"kijkgegevens": lees_kijkstand(self.kijkserver, gebruikers),
                    "kijkregistraties": inventaris["registraties"], "taaklog": inventaris["logregels"]}
        # Het dossier bevat per log-ID de afdruk van de hele rij, ook als een werker vrije tekst logt.
        loghashes = {k: hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
                     for k, r in historie["taaklog"].items()}
        self.noteer(naam, {"sha256": hashes, "historie": {**historie, "taaklog": loghashes},
                           "taakvoortgang": stand, "schrijfopening": inventaris["schrijfopening"]})
        if self.historie is None:
            self.historie = historie
        else:
            for soort, rijen in self.historie.items():
                for sleutel, rij in rijen.items():
                    assert historie[soort].get(sleutel) == rij, f"Bestaande {soort} ontbreekt of is gewijzigd"
        assert all(j in stand and stand[j] >= waarde for j, waarde in self.voortgang.items())
        self.voortgang = stand
        return hashes

    def sonarr(self, instantie: str, route: str, **params: Any) -> Any:
        return self.arr.get(f"/{instantie}/api/v3/{route}", headers={"X-Api-Key": self.sleutels[instantie]},
                            params=params).raise_for_status().json()

    def importmeldingen(self, serie_id: int) -> list[dict[str, Any]]:
        instantie = "sonarr-series"
        serie = self.sonarr(instantie, f"series/{serie_id}")
        bestanden = {f["id"]: f for f in self.sonarr(instantie, "episodefile", seriesId=serie_id)}
        eps = self.sonarr(instantie, "episode", seriesId=serie_id)
        assert len(eps) == len(bestanden) == 2
        assert {e["episodeNumber"] for e in eps} == {1, 2}
        return [{"eventType": "Download", "series": serie, "episodes": [ep],
                 "episodeFile": bestanden[ep["episodeFileId"]]} for ep in eps]

    def vraag_bij_arr(self, *, anime: bool) -> dict[str, Any]:
        doel = "sonarr-anime" if anime else "sonarr-series"
        body = {"mediaType": "tv", "mediaId": 102 if anime else 101, "seasons": [1]}
        if anime:
            servers = self.arr.get("/api/v1/settings/sonarr", headers={"X-Api-Key": self.sleutels["seerr"]}).json()
            body["serverId"] = next(s["id"] for s in servers if s["name"] == doel and not s["isDefault"])
        res = self.arr.post("/api/v1/request", json=body, headers={"X-Api-Key": self.sleutels["seerr"]})
        assert res.status_code == 201
        request: dict[str, Any] = res.json()
        paden = [v["pad"] for v in self.kern.verzoeklog(self.arr)
                 if v["methode"] == "POST" and v["pad"].endswith("/api/v3/series")]
        self.noteer("routering", {"doel": doel, "serieposts": paden, "seizoen": request["seasons"][0]["seasonNumber"]})
        assert paden == [f"/{doel}/api/v3/series"]
        serie = self.sonarr(doel, "series", tvdbId=request["media"]["tvdbId"])
        assert len(serie) == 1 and serie[0]["id"] == request["media"]["externalServiceId"]
        assert serie[0]["seriesType"] == ("anime" if anime else "standard")
        ander = "sonarr-series" if anime else "sonarr-anime"
        assert self.sonarr(ander, "series", tvdbId=request["media"]["tvdbId"]) == []
        return request

    def aanvraag(self, *, anime: bool) -> dict[str, Any]:
        return self.koppel("sonarr-anime" if anime else "sonarr-series", self.vraag_bij_arr(anime=anime))

    def koppel(self, doel: str, request: dict[str, Any]) -> dict[str, Any]:
        self.synchroniseer(doel)
        self.mg.post("/api/v1/seerr/requests/sync").raise_for_status()
        gekoppeld = self.mg.get("/api/v1/seerr/requests").raise_for_status().json()["items"]
        assert len(gekoppeld) == 1
        rij: dict[str, Any] = gekoppeld[0]
        assert rij["seerr_request_id"] == request["id"] and rij["link_status"] == "linked"
        assert rij["instance_id"] == doel and rij["provider_id"] == request["media"]["externalServiceId"]
        assert rij["lib_item_id"]
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
