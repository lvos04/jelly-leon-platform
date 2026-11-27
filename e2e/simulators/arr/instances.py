"""De vier geïsoleerde arr-instanties en de vaste catalogus voor MediaGuard."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

Json = dict[str, Any]

# De TMDB- en TVDB-nummers horen uitsluitend bij deze testcatalogus.
CATALOGUS: dict[int, Json] = {
    101: {"tmdbId": 101, "tvdbId": 701, "title": "De vuurtoren", "seriesType": "standard"},
    102: {"tmdbId": 102, "tvdbId": 702, "title": "De maanreis", "seriesType": "anime"},
    103: {"tmdbId": 103, "title": "De overtocht"},
}
INSTANTIES = ("sonarr-series", "sonarr-anime", "radarr-films", "radarr-animefilms")


@dataclass
class Instantie:
    naam: str
    sleutel: str = field(default_factory=lambda: secrets.token_hex(16))
    titels: dict[int, Json] = field(default_factory=dict)
    releases: dict[str, Json] = field(default_factory=dict)
    wachtrij: list[Json] = field(default_factory=list)
    opdrachten: dict[int, Json] = field(default_factory=dict)
    omgekeerd: set[int] = field(default_factory=set)

    staging: Path | None = None

    @property
    def serie(self) -> bool:
        return self.naam.startswith("sonarr")

    @property
    def profiel(self) -> Json:
        if "anime" in self.naam:
            return {"id": 2, "name": "Anime", "formatItems": [{"format": 1, "name": "Dual Audio", "score": 100}]}
        return {"id": 1, "name": "Standaard", "formatItems": []}

    @property
    def root(self) -> str:
        return "/media/" + self.naam

    def afleveringen(self, sid: int) -> list[Json]:
        serie = self.titels.get(sid)
        if not serie or not self.serie:
            return []
        anime = serie["seriesType"] == "anime"
        rows = []
        for n in (1, 2):
            eid = sid * 100 + n
            aanwezig = anime
            if self.staging is not None and serie["tmdbId"] == 101:
                aanwezig = (self.staging / f"S01E{n:02}.mkv").is_file()
            rows.append({"id": eid, "seriesId": sid, "seasonNumber": 1, "episodeNumber": n,
                         "airDateUtc": f"2020-01-0{n}T20:00:00Z",
                         "absoluteEpisodeNumber": 12 + n if anime else None,
                         "sceneAbsoluteEpisodeNumber": 15 - n if anime else None,
                         "sceneSeasonNumber": 2 if anime else None, "sceneEpisodeNumber": 3 - n if anime else None,
                         "title": f"Aflevering {n}", "monitored": serie["seasons"][0]["monitored"],
                         "hasFile": aanwezig, "episodeFileId": eid + 10000 if aanwezig else 0})
        return rows[::-1] if sid in self.omgekeerd else rows


def heeft_toegang(request: Request, instantie: Instantie) -> bool:
    header = request.headers.get("X-Api-Key", request.headers.get("Authorization", "").removeprefix("Bearer "))
    sleutel = next((v for k, v in request.query_params.multi_items() if k.lower() == "apikey"), header)
    return sleutel == instantie.sleutel


def instantie_routes(naam: str, instanties: dict[str, Instantie], sab_url: str | None,
                     sab_key: str | None, sab_transport: httpx.AsyncBaseTransport | None) -> APIRouter:
    async def toegang(request: Request) -> None:
        if not heeft_toegang(request, instanties[naam]):
            raise HTTPException(401, "API-sleutel geweigerd")

    r = APIRouter(prefix=f"/{naam}/api/v3", dependencies=[Depends(toegang)])
    serie = instanties[naam].serie
    soort = "series" if serie else "movie"
    veld = "tvdbId" if serie else "tmdbId"
    catalogus = {t[veld]: t for t in CATALOGUS.values() if ("tvdbId" in t) == serie}

    @r.get("/system/status")
    async def status() -> Json:
        return {"appName": "Sonarr" if serie else "Radarr", "instanceName": naam,
                "version": "4.0.20.3014" if serie else "6.4.4.10685", "urlBase": "/" + naam}

    @r.get("/qualityprofile")
    async def profielen() -> list[Json]:
        return [instanties[naam].profiel]

    @r.get("/customformat")
    async def formaten() -> list[Json]:
        return [{"id": 1, "name": "Dual Audio"}] if "anime" in naam else []

    @r.get("/rootfolder")
    async def mappen() -> list[Json]:
        return [{"id": 1, "path": instanties[naam].root, "accessible": True}]

    @r.get("/indexer")
    async def indexers() -> list[Json]:
        return [{"id": 1, "name": "Testindexer", "protocol": "usenet",
                 "enableAutomaticSearch": True, "enableInteractiveSearch": True, "enableRss": False}]

    @r.get("/downloadclient")
    async def clients() -> list[Json]:
        if not sab_url or not sab_key:
            return []
        return [{"id": 1, "name": "SAB-test", "implementation": "Sabnzbd",
                 "protocol": "usenet", "enable": True, "priority": 1}]

    @r.get("/indexerstatus")
    @r.get("/downloadclientstatus")
    @r.get("/tag")
    @r.get("/health")
    async def leeg() -> list[Json]:
        return []

    @r.get(f"/{soort}/lookup")
    async def zoek_titel(term: str) -> list[Json]:
        return [t for k, t in catalogus.items() if term in {t["title"], f"{veld[:-2]}:{k}"}]

    @r.get(f"/{soort}")
    async def titels(tvdbId: int | None = None, tmdbId: int | None = None) -> list[Json]:
        nummer = tvdbId if serie else tmdbId
        return [t for t in instanties[naam].titels.values() if nummer is None or t[veld] == nummer]

    @r.get(f"/{soort}/{{nummer}}")
    async def titel(nummer: int) -> Json:
        if nummer not in instanties[naam].titels:
            raise HTTPException(404, "Titel ontbreekt")
        return instanties[naam].titels[nummer]

    @r.post(f"/{soort}", status_code=201)
    async def voeg_toe(body: Json) -> Json:
        inst = instanties[naam]
        nr = body.get(veld)
        if type(nr) is not int or nr not in catalogus or body.get("qualityProfileId") != inst.profiel["id"]:
            raise HTTPException(400, "Onbekende testtitel of kwaliteitsprofiel")
        if body.get("rootFolderPath") != inst.root or any(t[veld] == nr for t in inst.titels.values()):
            raise HTTPException(400, "Ongeldige rootmap of titel bestaat al")
        seizoen = body.get("seasons")
        if serie and (not isinstance(seizoen, list) or len(seizoen) != 1 or not isinstance(seizoen[0], dict)
                      or seizoen[0].get("seasonNumber") != 1 or type(seizoen[0].get("monitored")) is not bool):
            raise HTTPException(400, "De testtitel heeft één seizoen")
        nr_instantie = len(inst.titels) + 1
        item = {**catalogus[nr], "id": nr_instantie, "qualityProfileId": inst.profiel["id"],
                "rootFolderPath": inst.root, "path": f"{inst.root}/{catalogus[nr]['title']}",
                "monitored": True, "tags": []}
        if serie:
            item.update(seasons=seizoen, monitorNewItems="all")
        else:
            item["hasFile"] = False
        inst.titels[nr_instantie] = item
        return item

    if serie:
        @r.get("/episode")
        async def episodes(seriesId: int, seasonNumber: int | None = None) -> list[Json]:
            return [e for e in instanties[naam].afleveringen(seriesId)
                    if seasonNumber is None or seasonNumber == e["seasonNumber"]]

        @r.get("/episode/{nummer}")
        async def episode(nummer: int) -> Json:
            for e in instanties[naam].afleveringen(nummer // 100):
                if e["id"] == nummer:
                    return e
            raise HTTPException(404, "Aflevering ontbreekt")

        @r.get("/episodefile")
        async def bestanden(seriesId: int) -> list[Json]:
            inst = instanties[naam]
            map_ = inst.staging if inst.staging and inst.titels.get(seriesId, {}).get("tmdbId") == 101 else None
            # De bestandslijst heeft een eigen volgorde, onafhankelijk van de afleveringen na RefreshSeries.
            episodes_op_id = sorted(inst.afleveringen(seriesId), key=lambda e: e["episodeFileId"])
            return [{"id": e["episodeFileId"], "seriesId": seriesId,
                     "path": str(map_ or inst.titels[seriesId]["path"]) + f"/S01E{e['episodeNumber']:02d}.mkv",
                     "relativePath": f"S01E{e['episodeNumber']:02d}.mkv",
                     "size": (map_ / f"S01E{e['episodeNumber']:02d}.mkv").stat().st_size if map_ else 2048,
                     "languages": [{"id": 1, "name": "English"}, {"id": 8, "name": "Japanese"}]}
                    for e in episodes_op_id if e["hasFile"]]

        @r.get("/episodefile/{nummer}")
        async def bestand(nummer: int) -> Json:
            for f in await bestanden((nummer - 10000) // 100):
                if f["id"] == nummer:
                    return f
            raise HTTPException(404, "Bestand ontbreekt")

        @r.delete("/episodefile/{nummer}")
        async def verwijder_proefbestand(nummer: int) -> Json:
            f = await bestand(nummer)
            inst = instanties[naam]
            # Alleen de twee vaste namen van de optionele stagingfixture hebben een schijfhandeling.
            if inst.staging is None or inst.titels[f["seriesId"]]["tmdbId"] != 101:
                raise HTTPException(409, "Dit bestand valt buiten de stagingproef")
            pad = inst.staging / f["relativePath"]
            if pad.is_symlink():
                raise HTTPException(409, "De proef vereist een gewoon bestand")
            pad.unlink()
            return {}

    @r.get("/release")
    async def zoek_release(episodeId: int | None = None, seriesId: int | None = None,
                          seasonNumber: int | None = None, movieId: int | None = None) -> list[Json]:
        inst = instanties[naam]
        if serie:
            sid = episodeId // 100 if episodeId is not None else seriesId
            afleveringen = [e for e in inst.afleveringen(sid or 0) if
                            (episodeId is None or e["id"] == episodeId)
                            and (seasonNumber is None or e["seasonNumber"] == seasonNumber)]
            if not afleveringen or (episodeId is None and seasonNumber is None):
                return []
            doel = {"seriesId": sid, "episodeId": afleveringen[0]["id"]}
        else:
            if movieId is None or movieId not in inst.titels:
                return []
            sid = movieId
            doel = {"movieId": movieId}
        guid = f"sim-{naam}-{doel.get('episodeId', sid)}"
        release = {"guid": guid, "indexerId": 1, "indexer": "Testindexer", "title": inst.titels[int(sid or 0)]["title"],
                   "size": 2048, "protocol": "usenet", "approved": True, "downloadAllowed": True,
                   "rejections": [], **doel}
        inst.releases[guid] = release
        return [release]

    @r.post("/release")
    async def ophalen(body: Json) -> Json:
        inst = instanties[naam]
        release = inst.releases.get(str(body.get("guid")))
        if release is None or body.get("indexerId") != release["indexerId"]:
            raise HTTPException(404, "Zoek de release eerst op")
        if not sab_url or not sab_key:
            raise HTTPException(409, "Configureer de SAB-testsimulator voor het ophalen")
        categorie = {"sonarr-series": "tv", "sonarr-anime": "tv-anime",
                     "radarr-films": "movies", "radarr-animefilms": "movies-anime"}[naam]
        try:
            async with httpx.AsyncClient(base_url=sab_url, transport=sab_transport, trust_env=False) as client:
                antwoord = await client.post("/api", params={"mode": "addfile", "output": "json",
                                            "cat": categorie, "apikey": sab_key},
                                            files={"name": ("proef.nzb", b"<nzb><file subject='proef'/></nzb>")})
                antwoord.raise_for_status()
                data = antwoord.json()
        except (ValueError, httpx.HTTPError):
            raise HTTPException(409, "De SAB-testsimulator bevestigde de download niet") from None
        ids = data.get("nzo_ids") if isinstance(data, dict) and data.get("status") is True else None
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
            raise HTTPException(409, "De SAB-testsimulator gaf geen download-ID")
        inst.wachtrij.append({**release, "id": len(inst.wachtrij) + 1, "downloadId": ids[0],
                              "status": "queued", "sizeleft": release["size"]})
        return release

    @r.post("/command", status_code=201)
    async def opdracht_start(body: Json) -> Json:
        inst = instanties[naam]
        actie = body.get("name")
        sid = body.get("seriesId")
        gevonden: list[Json] = []
        if serie and actie in ("SeriesSearch", "SeasonSearch", "RefreshSeries", "RescanSeries"):
            if type(sid) is not int or sid not in inst.titels:
                raise HTTPException(400, "Onbekende serie")
            if actie == "SeasonSearch" and body.get("seasonNumber") != 1:
                raise HTTPException(400, "Onbekende serie of seizoen")
            if actie == "RescanSeries":
                if inst.staging is None:
                    raise HTTPException(400, "Herscan vereist de stagingproef")
            elif actie == "RefreshSeries":
                inst.omgekeerd.symmetric_difference_update({sid})
            else:
                for ep in inst.afleveringen(sid):
                    if ep["monitored"] and not ep["hasFile"]:
                        gevonden.extend(await zoek_release(episodeId=ep["id"]))
        elif (serie and actie == "EpisodeSearch") or (not serie and actie == "MoviesSearch"):
            ids = body.get("episodeIds" if serie else "movieIds")
            if not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids):
                raise HTTPException(400, "Geef de te zoeken afleveringen of films op")
            for nummer in dict.fromkeys(ids):
                opties = await zoek_release(episodeId=nummer) if serie else await zoek_release(movieId=nummer)
                if not opties:
                    raise HTTPException(400, "Onbekende aflevering of film")
                gevonden.extend(opties)
        else:
            raise HTTPException(400, "Opdracht valt buiten het testprotocol")
        cid = len(inst.opdrachten) + 1
        command: Json = {"id": cid, "name": actie, "body": body, "status": "started"}
        inst.opdrachten[cid] = command
        try:
            for release in gevonden:
                await ophalen({"guid": release["guid"], "indexerId": release["indexerId"]})
            command["status"] = "completed"
        except HTTPException:
            command["status"] = "failed"
            command["message"] = "De SAB-testsimulator bevestigde de download niet"
        return command

    @r.get("/command/{nummer}")
    async def opdracht(nummer: int) -> Json:
        if nummer not in instanties[naam].opdrachten:
            raise HTTPException(404, "Opdracht ontbreekt")
        return instanties[naam].opdrachten[nummer]

    @r.get("/queue")
    async def queue(page: int = 1, pageSize: int = 10) -> Json:
        if page < 1 or pageSize < 1:
            raise HTTPException(400, "Ongeldige pagina")
        items = instanties[naam].wachtrij
        return {"page": page, "pageSize": pageSize, "totalRecords": len(items),
                "records": items[(page - 1) * pageSize:page * pageSize]}

    return r
