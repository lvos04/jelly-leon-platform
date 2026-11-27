"""Kleine Jellyfin-testdienst voor sessies en kijkgegevens, alleen op het interne scenarionetwerk."""

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


def create_app() -> FastAPI:
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
        return {"Id": "kijkproef", "ServerName": "Kijkproef", "ProductName": "Jellyfin", "Version": "12.4"}

    @app.get("/Library/VirtualFolders", dependencies=[Depends(toegang)])
    def mappen() -> list[dict[str, list[str]]]:
        return [{"Locations": ["/media"]}]

    @app.get("/Sessions", dependencies=[Depends(toegang)])
    def sessies() -> list[dict[str, Any]]:
        return [{"UserName": u, "NowPlayingItem": {"Path": p, "Name": Path(p).name}, "PlayState": {"IsPaused": True}}
                for p, r in items.items() for u, data in r.items() if data.Played]

    @app.get("/Users", dependencies=[Depends(toegang)])
    def kijkers() -> list[dict[str, str]]:
        return [{"Id": u, "Name": u} for u in gebruikers]

    @app.get("/Items", dependencies=[Depends(toegang)])
    def bibliotheek(UserId: str) -> dict[str, Any]:
        if UserId not in gebruikers:
            raise HTTPException(404)
        return {"Items": [{"Id": str(n), "Path": p, "UserData": r[UserId].model_dump()}
                          for n, (p, r) in enumerate(items.items(), 1) if UserId in r]}

    return app


if __name__ == "__main__":
    args = basis_args("Kijkserver voor de inhoudsproef", 8096, alleen_testnet=True).parse_args()
    serve(create_app(), args.host, args.port)
