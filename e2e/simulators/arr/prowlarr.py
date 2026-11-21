"""Prowlarr uit MSO met telbare zoekopdrachten en terugoproepen voor EX42."""

import secrets

from fastapi import FastAPI
from simkit.msomock import met_testbediening


def zoekstatistiek(app: FastAPI) -> None:
    @app.get("/_sim/zoekstatistiek")
    async def statistiek() -> dict[str, int]:
        state = app.state.bron.state
        return {"zoekopdrachten": state.search_calls, "max_diepte": state.max_active,
                "terugoproepen": len(state.callbacks), "mislukte_terugoproepen": len(state.callback_errors)}


def create_app() -> FastAPI:
    return met_testbediening("prowlarr", lambda: {"apikey": secrets.token_hex(16)}, zoekstatistiek)
