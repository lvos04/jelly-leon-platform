"""Testbediening rond het oorspronkelijke protocol uit de gepinde MSO-mockimage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from importlib import import_module

from fastapi import FastAPI, Request
from starlette.types import Receive, Scope, Send

from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install


def met_testbediening(
    naam: str,
    sleutels: Callable[[], dict[str, str]],
    routes: Callable[[FastAPI], None] | None = None,
    beschrijf: Callable[[Request], Awaitable[dict[str, str]]] | None = None,
) -> FastAPI:
    factory = import_module(f"msomocks.{naam}").create_app
    app = FastAPI(title=f"Testbediening {naam}", docs_url=None, redoc_url=None, openapi_url=None)

    def reset() -> None:
        waarden = sleutels()
        app.state.bron = factory(**{("api_key" if k == "apikey" else k): v for k, v in waarden.items()})
        app.state.sleutels = waarden

    reset()
    install(app, fouten=Fouten(dict(ALGEMENE_FOUTEN)), log=Verzoeklog(), reset=reset,
            sleutels=lambda: app.state.sleutels, fout_body=lambda status, melding: {"error": melding},
            beschrijf=beschrijf)
    if routes:
        routes(app)

    async def doorgeven(scope: Scope, receive: Receive, send: Send) -> None:
        await app.state.bron(scope, receive, send)

    app.mount("/", doorgeven)
    return app
