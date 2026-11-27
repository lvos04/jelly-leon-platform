"""Lokale Emby-/Jellyfin-testdienst voor sessies en kijkgegevens, alleen op het interne scenarionetwerk."""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, StrictBool, StrictInt
from simkit import basis_args, serve  # type: ignore[import-not-found]


class Kijkgegevens(BaseModel):
    Played: StrictBool
    PlaybackPositionTicks: StrictInt
    IsFavorite: StrictBool
    PlayCount: StrictInt | None = None
    LastPlayedDate: str | None = None
    Rating: float | None = None
    AudioStreamIndex: StrictInt | None = None
    SubtitleStreamIndex: StrictInt | None = None


def create_app(*, emby: bool = False) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    sleutel = secrets.token_hex(16)
    items: dict[str, dict[str, Kijkgegevens]] = {}
    gebruikers = ("proefkijker-a", "proefkijker-b")

    def toegang(request: Request) -> None:
        kop = request.headers.get("Authorization", "")
        token = kop.partition('Token="')[2]
        if not kop.startswith("MediaBrowser ") or not secrets.compare_digest(token, sleutel + '"'):
            raise HTTPException(401)

    @app.post("/_sim/bibliotheek")
    def begin(bibliotheek: dict[str, dict[str, Kijkgegevens]]) -> dict[str, str]:
        if items or len(bibliotheek) != 2 or any(set(r) != set(gebruikers) for r in bibliotheek.values()):
            raise HTTPException(409)
        items.update(bibliotheek)
        return {"api_key": sleutel}

    @app.get("/System/Info/Public")
    @app.get("/System/Info", dependencies=[Depends(toegang)])
    def info() -> dict[str, str]:
        return {"Id": "kijkproef", "ServerName": "Kijkproef", "ProductName": "Emby Server" if emby else "Jellyfin",
                "Version": "12.4"}

    @app.get("/Library/VirtualFolders", dependencies=[Depends(toegang)])
    def mappen() -> list[dict[str, list[str]]]:
        return [{"Locations": ["/media"]}]

    @app.get("/Sessions", dependencies=[Depends(toegang)])
    def sessies() -> list[dict[str, Any]]:
        return [{"UserName": u, "NowPlayingItem": {"Path": p, "Name": Path(p).name}, "PlayState": {"IsPaused": True}}
                for p, r in items.items() for u, data in r.items() if data.Played]

    @app.get("/Users", dependencies=[Depends(toegang)])
    def kijkers() -> list[dict[str, Any]]:
        if emby:
            return [{"Id": u, "Name": u, "Policy": {"IsAdministrator": False, "EnableMediaPlayback": True,
                                                     "EnableContentDownloading": n == 0}}
                    for n, u in enumerate(gebruikers)]
        return [{"Id": u, "Name": u} for u in gebruikers]

    @app.get("/Items", dependencies=[Depends(toegang)])
    def bibliotheek(UserId: str) -> dict[str, Any]:
        if UserId not in gebruikers:
            raise HTTPException(404)
        return {"Items": [{"Id": str(n), "Path": p, "UserData": r[UserId].model_dump(exclude_unset=True)}
                          for n, (p, r) in enumerate(items.items(), 1) if UserId in r]}

    @app.post("/Users/{uid}/Items/{item_id}/UserData", dependencies=[Depends(toegang)])
    def bekijk(uid: str, item_id: int, data: Kijkgegevens) -> None:
        if uid not in gebruikers or not 1 <= item_id <= len(items):
            raise HTTPException(404)
        list(items.values())[item_id - 1][uid] = data

    return app


if __name__ == "__main__":
    parser = basis_args("Kijkserver voor de inhoudsproef", 8096, alleen_testnet=True)
    parser.add_argument("--emby", action="store_true")
    args = parser.parse_args()
    serve(create_app(emby=args.emby), args.host, args.port)
