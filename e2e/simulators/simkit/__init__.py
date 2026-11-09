"""Gedeelde bouwstenen voor de simulators van apps van derden (Beszel, Uptime Kuma, Gotify, Authentik).

ALLEEN voor tests en de integrale E2E-omgeving — geen productiecode, geen echte app. Elke simulator volgt het
gedocumenteerde deel van de echte API (zie de README per simulator) en heeft dezelfde testbediening:

- ``GET /_sim/fouten`` — actieve fouten + de lijst met bekende fouten (naam → uitleg).
- ``POST /_sim/fouten`` ``{"naam": …, "aantal": n|null, "pad": "<regex>"|null, "waarde": …}`` — fout aanzetten
  (``aantal``: alleen de eerstvolgende n keer; ``pad``: alleen verzoeken waarvan het pad matcht).
- ``DELETE /_sim/fouten`` (alle) of ``DELETE /_sim/fouten?naam=…`` (één).
- ``GET /_sim/verzoeken`` — wat de simulator heeft ontvangen (methode, pad, of er aanmelding bij zat; nooit de
  aanmeldgegevens zelf), om bijvoorbeeld aan te tonen dat een client nooit schrijft.
- ``POST /_sim/reset`` — begintoestand.

Algemene fouten (in elke simulator, vóór de routering; ``/_sim/…`` is uitgezonderd):

| naam | effect |
|---|---|
| ``http_500`` / ``http_502`` / ``http_503`` | antwoord met die status (JSON-foutbody in het formaat van de app) |
| ``traag`` | ``waarde`` seconden wachten vóór het antwoord (standaard 20 s; om time-outs te testen) |
| ``ongeldige_json`` | 200 met een body die geen JSON is |
| ``te_groot`` | 200 met ``waarde`` MiB (standaard 6) aan JSON-achtige data, zonder ``Content-Length`` |
| ``redirect`` | 302 naar ``http://elders.invalid/`` (een client hoort dat nooit te volgen) |
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

ALGEMENE_FOUTEN: dict[str, str] = {
    "http_500": "Antwoord 500 (interne fout).",
    "http_502": "Antwoord 502 (proxy/gateway-fout).",
    "http_503": "Antwoord 503 (tijdelijk niet beschikbaar).",
    "traag": "Wacht 'waarde' seconden (standaard 20) vóór het antwoord.",
    "ongeldige_json": "Antwoord 200 met een body die geen geldige JSON is.",
    "te_groot": "Antwoord 200 met 'waarde' MiB (standaard 6) zonder Content-Length.",
    "redirect": "Antwoord 302 naar een andere host (nooit volgen).",
}


@dataclass
class Fout:
    naam: str
    aantal: int | None = None
    pad: re.Pattern[str] | None = None
    waarde: Any = None

    def view(self) -> dict[str, Any]:
        return {"naam": self.naam, "aantal": self.aantal, "pad": self.pad.pattern if self.pad else None,
                "waarde": self.waarde}


@dataclass
class Fouten:
    bekend: dict[str, str]
    actief: dict[str, Fout] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def zet(self, naam: str, aantal: int | None = None, pad: str | None = None, waarde: Any = None) -> Fout:
        if naam not in self.bekend:
            raise KeyError(naam)
        if aantal is not None and aantal < 1:
            raise ValueError("aantal moet ≥ 1 zijn")
        fout = Fout(naam, aantal, re.compile(pad) if pad else None, waarde)
        with self.lock:
            self.actief[naam] = fout
        return fout

    def wis(self, naam: str | None = None) -> None:
        with self.lock:
            if naam is None:
                self.actief.clear()
            else:
                self.actief.pop(naam, None)

    def raak(self, naam: str, pad: str = "") -> Fout | None:
        """Is fout ``naam`` actief voor dit pad? Telt een beperkt ``aantal`` af (en verwijdert de fout bij 0)."""
        with self.lock:
            fout = self.actief.get(naam)
            if fout is None or (fout.pad is not None and not fout.pad.search(pad)):
                return None
            if fout.aantal is not None:
                fout.aantal -= 1
                if fout.aantal <= 0:
                    self.actief.pop(naam, None)
            return fout

    def is_aan(self, naam: str, pad: str = "") -> bool:
        """Zoals :meth:`raak`, maar telt niet af (voor toestanden zoals 'agent_down')."""
        with self.lock:
            fout = self.actief.get(naam)
            return fout is not None and (fout.pad is None or bool(fout.pad.search(pad)))


@dataclass
class Verzoek:
    t: float
    methode: str
    pad: str
    query: list[str]
    aanmelding: bool


@dataclass
class Verzoeklog:
    """Ontvangen verzoeken zonder aanmeldgegevens. ``geheime_paden``: (regex, vervanging) voor routes waarvan een
    padsegment zelf het geheim is (bijv. de push-token van Uptime Kuma in ``/api/push/<token>``); zo'n pad wordt
    geredigeerd opgeslagen en telt als verzoek mét aanmelding."""

    items: list[Verzoek] = field(default_factory=list)
    max_items: int = 5000
    geheime_paden: tuple[tuple[re.Pattern[str], str], ...] = ()

    def add(self, request: Request) -> None:
        auth = bool(request.headers.get("authorization") or request.headers.get("x-gotify-key")
                    or "token" in request.query_params)
        pad = request.url.path
        for patroon, vervanging in self.geheime_paden:
            geredigeerd = patroon.sub(vervanging, pad)
            if geredigeerd != pad:
                pad, auth = geredigeerd, True
        self.items.append(Verzoek(time.time(), request.method, pad,
                                  sorted(k for k in request.query_params if k != "token"), auth))
        del self.items[: max(0, len(self.items) - self.max_items)]

    def schrijvend(self, prefix: str = "") -> list[Verzoek]:
        return [v for v in self.items if v.methode not in ("GET", "HEAD", "OPTIONS") and v.pad.startswith(prefix)]


FoutBody = Callable[[int, str], dict[str, Any]]


def install(
    app: FastAPI,
    *,
    fouten: Fouten,
    log: Verzoeklog,
    reset: Callable[[], None],
    fout_body: FoutBody,
    sleutels: Callable[[], dict[str, Any]] | None = None,
) -> None:
    """Testbediening (``/_sim/…``) en de algemene foutinjectie als middleware."""

    @app.middleware("http")
    async def algemene_fouten(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        pad = request.url.path
        if pad.startswith("/_sim/"):
            return await call_next(request)
        log.add(request)
        if f := fouten.raak("traag", pad):
            await asyncio.sleep(float(f.waarde if f.waarde is not None else 20))
        for status in (500, 502, 503):
            if fouten.raak(f"http_{status}", pad):
                return JSONResponse(fout_body(status, "Gesimuleerde storing"), status_code=status)
        if fouten.raak("redirect", pad):
            return Response(status_code=302, headers={"Location": "http://elders.invalid/"})
        if fouten.raak("ongeldige_json", pad):
            return Response(b"{dit is geen json", status_code=200, media_type="application/json")
        if f := fouten.raak("te_groot", pad):
            mib = int(f.waarde or 6)

            async def stroom() -> AsyncIterator[bytes]:
                yield b'{"vulling":"'
                blok = b"x" * 65536
                for _ in range(mib * 16):
                    yield blok
                yield b'"}'

            return StreamingResponse(stroom(), media_type="application/json")
        return await call_next(request)

    @app.get("/_sim/fouten", include_in_schema=False)
    def lijst() -> dict[str, Any]:
        return {"actief": [f.view() for f in fouten.actief.values()], "bekend": fouten.bekend}

    @app.post("/_sim/fouten", include_in_schema=False)
    async def zet(request: Request) -> JSONResponse:
        body = await request.json()
        try:
            fout = fouten.zet(str(body["naam"]), body.get("aantal"), body.get("pad"), body.get("waarde"))
        except KeyError:
            return JSONResponse({"fout": "onbekende fout", "bekend": sorted(fouten.bekend)}, status_code=422)
        except (ValueError, re.error) as exc:
            return JSONResponse({"fout": str(exc)}, status_code=422)
        return JSONResponse(fout.view())

    @app.delete("/_sim/fouten", include_in_schema=False)
    def wis(naam: str | None = None) -> dict[str, Any]:
        fouten.wis(naam)
        return {"actief": [f.view() for f in fouten.actief.values()]}

    @app.get("/_sim/verzoeken", include_in_schema=False)
    def verzoeken() -> list[dict[str, Any]]:
        return [v.__dict__ for v in log.items]

    @app.post("/_sim/reset", include_in_schema=False)
    def opnieuw() -> dict[str, str]:
        fouten.wis()
        log.items.clear()
        reset()
        return {"status": "begintoestand"}

    if sleutels is not None:
        @app.get("/_sim/sleutels", include_in_schema=False)
        def sim_sleutels() -> dict[str, Any]:
            return sleutels()


def serve(app: FastAPI, host: str, port: int, certfile: str | None = None, keyfile: str | None = None) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning", ssl_certfile=certfile, ssl_keyfile=keyfile)


def basis_args(beschrijving: str, standaard_poort: int) -> Any:
    import argparse

    parser = argparse.ArgumentParser(description=beschrijving + " — alleen voor tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=standaard_poort)
    parser.add_argument("--ssl-certfile", default=None)
    parser.add_argument("--ssl-keyfile", default=None)
    return parser
