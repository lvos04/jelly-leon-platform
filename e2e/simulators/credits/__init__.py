"""Inbegrepen tegoed op het diagnose-eindpunt van de lokale proef-Hub."""

from __future__ import annotations

from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install


def create_app(eenheden: int = 0) -> FastAPI:
    if eenheden < 0:
        raise ValueError("Het inbegrepen proeftegoed moet nul of hoger zijn.")
    app = FastAPI(title="Kredietproef")
    lock = Lock()
    stand = {"beschikbaar": eenheden, "adviezen": 0, "uitgeput": 0}
    fouten = Fouten({**ALGEMENE_FOUTEN, "tegoed_op": "Verbruik het resterende inbegrepen proeftegoed."})

    def reset() -> None:
        with lock:
            stand.update(beschikbaar=eenheden, adviezen=0, uitgeput=0)

    install(app, fouten=fouten, log=Verzoeklog(), reset=reset, fout_body=lambda _, uitleg: {"detail": uitleg})

    @app.get("/_sim/stand")
    def lees() -> dict[str, int]:
        with lock:
            return stand.copy()

    @app.post("/diagnose")
    async def diagnose(request: Request) -> dict[str, str]:
        try:
            taak = await request.json()
        except (ValueError, RecursionError):
            raise HTTPException(422, "Ongeldige aanvraag") from None
        kosten = taak.get("max_eenheden") if isinstance(taak, dict) else None
        if type(kosten) is not int or kosten < 1 or taak.get("alleen_inbegrepen") is not True:
            raise HTTPException(422, "Alleen inbegrepen eenheden zijn toegestaan")
        with lock:
            if fouten.raak("tegoed_op", "/diagnose") or kosten > stand["beschikbaar"]:
                stand["beschikbaar"] = 0
                stand["uitgeput"] += 1
                raise HTTPException(402, "Het inbegrepen tegoed is op")
            stand["beschikbaar"] -= kosten
            stand["adviezen"] += 1
        return {"analyse": "Controleer de bronmeting."}

    return app
