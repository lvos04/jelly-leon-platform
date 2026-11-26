"""Seerr en gerouteerde Sonarr/Radarr-bibliotheken voor de lokale downloadketen."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, StrictInt
from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install

from .instances import CATALOGUS, INSTANTIES, Instantie, Json, heeft_toegang, instantie_routes


class Aanvraag(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mediaType: Literal["movie", "tv"]
    mediaId: StrictInt
    tvdbId: StrictInt | None = None
    serverId: StrictInt | None = None
    seasons: list[StrictInt] | Literal["all"] | None = None


def create_app(*, sab_url: str | None = None, sab_key: str | None = None,
               sab_transport: httpx.AsyncBaseTransport | None = None, testbibliotheek: Path | None = None) -> FastAPI:
    if sab_url:
        url = urlsplit(sab_url)
        if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1", "sabnzbd", "sabnzbd-sim"}
                or url.username is not None or url.password is not None or url.path not in {"", "/"}
                or url.query or url.fragment):
            raise ValueError("Gebruik loopback of de interne dienstnaam sabnzbd voor de SAB-testsimulator")
    app = FastAPI(title="Seerr en arr-testsimulator", docs_url=None, redoc_url=None, openapi_url=None)
    instanties: dict[str, Instantie] = {}
    aanvragen: dict[int, Json] = {}
    aanvraag_lock = asyncio.Lock()
    seerr_key = ""
    staging = testbibliotheek / "sonarr-series/De vuurtoren/staging" if testbibliotheek else None

    def reset() -> None:
        nonlocal seerr_key
        seerr_key = secrets.token_hex(16)
        instanties.update({naam: Instantie(naam) for naam in INSTANTIES})
        instanties["sonarr-series"].staging = staging
        aanvragen.clear()

    def sleutels() -> Json:
        return {"seerr": seerr_key, **{k: i.sleutel for k, i in instanties.items()}}

    async def beschrijf(request: Request) -> dict[str, str]:
        # Onafhankelijke waarnemer vóór de schijfhandeling, ook als de quarantainekopie ontbreekt.
        if request.method != "DELETE" or staging is None or testbibliotheek is None:
            return {}
        inst = instanties["sonarr-series"]
        if not heeft_toegang(request, inst):
            return {}
        for serie in inst.titels.values():
            if serie["tmdbId"] != 101:
                continue
            for ep in inst.afleveringen(serie["id"]):
                if not ep["hasFile"] or request.url.path != f"/sonarr-series/api/v3/episodefile/{ep['episodeFileId']}":
                    continue
                pad = staging / f"S01E{ep['episodeNumber']:02d}.mkv"
                if pad.is_symlink():
                    return {}
                kopieen = list((testbibliotheek / ".mediaguard-quarantine").glob(f"txn-*/{pad.name}"))
                details = {"bron_sha256": hashlib.sha256(pad.read_bytes()).hexdigest()}
                if len(kopieen) == 1 and not kopieen[0].is_symlink():
                    details["quarantaine_sha256"] = hashlib.sha256(kopieen[0].read_bytes()).hexdigest()
                return details
        return {}

    reset()
    install(app, fouten=Fouten(dict(ALGEMENE_FOUTEN)), log=Verzoeklog(), reset=reset, sleutels=sleutels,
            beschrijf=beschrijf,
            fout_body=lambda status, melding: {"message": melding})

    for naam in INSTANTIES:
        app.include_router(instantie_routes(naam, instanties, sab_url, sab_key, sab_transport))

    @app.exception_handler(HTTPException)
    async def protocolfout(request: Request, exc: HTTPException) -> Response:
        if exc.status_code == 401:
            return Response(status_code=401)
        if exc.status_code == 403:
            return JSONResponse({"status": 403, "error": exc.detail}, status_code=403)
        return JSONResponse({"message": exc.detail}, status_code=exc.status_code)

    async def toegang(request: Request) -> None:
        if request.headers.get("X-Api-Key") != seerr_key:
            raise HTTPException(403, "You do not have permission to access this endpoint")

    r = APIRouter(prefix="/api/v1", dependencies=[Depends(toegang)])

    @app.get("/api/v1/status")
    async def status() -> Json:
        return {"version": "3.5.1", "commitTag": "564bfb0"}

    @r.get("/settings/{kind}")
    async def servers(kind: Literal["sonarr", "radarr"], request: Request) -> list[Json]:
        out = []
        for server_id, inst in enumerate(i for i in instanties.values() if i.naam.startswith(kind)):
            server = {"id": server_id, "name": inst.naam, "hostname": request.url.hostname,
                      "port": request.url.port or (443 if request.url.scheme == "https" else 80),
                      "useSsl": request.url.scheme == "https", "baseUrl": "/" + inst.naam, "apiKey": inst.sleutel,
                      "isDefault": server_id == 0, "is4k": False, "activeProfileId": inst.profiel["id"],
                      "activeProfileName": inst.profiel["name"], "activeDirectory": inst.root, "tags": []}
            if kind == "sonarr" and server_id == 1:
                server.update(activeAnimeProfileId=inst.profiel["id"], activeAnimeProfileName=inst.profiel["name"],
                              activeAnimeDirectory=inst.root, animeTags=[])
            out.append(server)
        return out

    @r.post("/settings/{kind}/test")
    async def test_server(kind: Literal["sonarr", "radarr"], body: Json, request: Request) -> Json:
        velden = ("hostname", "port", "useSsl", "baseUrl", "apiKey")
        for server in await servers(kind, request):
            if all(body.get(k) == server[k] for k in velden):
                result: Json = {}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://arr.invalid") as client:
                    for veld, pad in (("profiles", "qualityprofile"), ("rootFolders", "rootfolder"), ("tags", "tag")):
                        reply = await client.get(f"/{server['name']}/api/v3/{pad}",
                                                 headers={"X-Api-Key": body["apiKey"]})
                        try:
                            waarde = reply.json()
                        except ValueError:
                            waarde = None
                        if reply.status_code != 200 or not isinstance(waarde, list):
                            raise HTTPException(500, "De doelinstantie kon niet worden uitgelezen")
                        result[veld] = waarde
                if kind == "sonarr":
                    result["languageProfiles"] = None
                return result
        raise HTTPException(500, "Verbinding met testinstantie mislukt")

    @r.get("/request")
    async def lijst(take: int = 10, skip: int = 0, filter: Literal["all"] = "all",
                    sort: Literal["added"] = "added") -> Json:
        if take < 1 or skip < 0:
            raise HTTPException(400, "Ongeldige pagina")
        items = list(reversed(aanvragen.values()))
        return {"pageInfo": {"pages": (len(items) + take - 1) // take, "page": (skip + take - 1) // take + 1,
                             "results": len(items), "pageSize": take}, "results": items[skip:skip + take]}

    @r.get("/request/{nummer}")
    async def aanvraag(nummer: int) -> Json:
        if nummer not in aanvragen:
            raise HTTPException(404, "Aanvraag ontbreekt")
        return aanvragen[nummer]

    @r.post("/request")
    async def plaats(body: Aanvraag) -> JSONResponse:
        titel = CATALOGUS.get(body.mediaId)
        serie = body.mediaType == "tv"
        if (not titel or ("tvdbId" in titel) != serie
                or (body.tvdbId is not None and titel.get("tvdbId") != body.tvdbId)
                or (serie and body.seasons not in ([1], "all")) or (not serie and body.seasons is not None)):
            raise HTTPException(400, "Onbekende testtitel of seizoen")
        server_id = body.serverId if body.serverId is not None else 0
        if server_id not in (0, 1):
            raise HTTPException(400, "Onbekende testserver")
        naam = INSTANTIES[server_id + (0 if serie else 2)]
        async with aanvraag_lock:
            if any(a["media"]["tmdbId"] == body.mediaId for a in aanvragen.values()):
                return JSONResponse({"message": "No seasons available" if serie else "Request already exists"},
                                    status_code=202 if serie else 409)
            inst = instanties[naam]
            resource = {**titel, "rootFolderPath": inst.root, "qualityProfileId": inst.profiel["id"]}
            if serie:
                resource["seasons"] = [{"seasonNumber": 1, "monitored": True}]
            # Een echte ASGI-aanroep door dezelfde authenticatie, foutinjectie en verzoekregistratie.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://arr.invalid") as c:
                reply = await c.post(f"/{naam}/api/v3/{'series' if serie else 'movie'}",
                                     headers={"X-Api-Key": inst.sleutel}, json=resource)
            try:
                result = reply.json()
            except ValueError:
                result = None
            if (reply.status_code != 201 or not isinstance(result, dict) or type(result.get("id")) is not int
                    or instanties[naam] is not inst):
                raise HTTPException(502, "Doelinstantie heeft de aanvraag niet bevestigd")
            nummer = len(aanvragen) + 1
            media = {"mediaType": body.mediaType, "tmdbId": body.mediaId, "tvdbId": titel.get("tvdbId"),
                     "serviceId": server_id, "externalServiceId": result["id"], "status": 3}
            row = {"id": nummer, "type": body.mediaType, "status": 2, "is4k": False, "serverId": server_id,
                   "media": media, "seasons": [{"seasonNumber": 1, "status": 2}] if serie else []}
            aanvragen[nummer] = row
            return JSONResponse(row, status_code=201)

    app.include_router(r)
    return app
