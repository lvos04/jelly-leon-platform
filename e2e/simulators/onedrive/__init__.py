"""OneDrive-simulator (alleen tests): offsite-doel van een geplande cloud-sync-back-up van TrueNAS, met herstel.

Protocol en bronnen: ``e2e/simulators/onedrive/README.md``. De Hub praat nooit met OneDrive: hij leest de back-upstand
uit ``cloudsync.query`` van TrueNAS. Deze simulator levert alleen wat de TrueNAS-simulator niet heeft: een doel waar de
geplande taak naartoe schrijft, dat op commando faalt of veroudert, en de stand van die taak in de vorm van
``cloudsync.query`` (voor ``POST /_sim/fixtures {"cloudsync_tasks": …}`` van de TrueNAS-simulator).
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install

FOUTEN = {
    **ALGEMENE_FOUTEN,
    "doel_vol": "De volgende geplande back-up mislukt: het doel is vol ('waarde' = eigen melding).",
    "token_verlopen": "De volgende geplande back-up mislukt: de koppeling met het doel is verlopen.",
}
MELDINGEN = {
    "doel_vol": "Quota exceeded (sim): het offsite-doel is vol",
    "token_verlopen": "Token expired (sim): de koppeling met het offsite-doel moet opnieuw worden gemaakt",
}
STANDAARD_PAD = "/mnt/simpool/appdata"


def _datum(t: datetime) -> dict[str, int]:
    # Tijdvorm van de TrueNAS-middleware (ms sinds epoch).
    return {"$date": int(t.timestamp() * 1000)}


@dataclass
class State:
    pad: str = STANDAARD_PAD
    bestanden: dict[str, str] = field(default_factory=dict)
    job: dict[str, Any] | None = None  # None = de taak heeft nog nooit gedraaid
    runs: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def seed(self) -> None:
        self.pad, self.bestanden, self.job, self.runs = STANDAARD_PAD, {}, None, 0

    def taak(self) -> dict[str, Any]:
        # Vorm van cloudsync.query; credentials zonder attributen (geen token of geheim, ook geen testwaarde).
        return {"id": 1, "description": "Offsite-back-up naar OneDrive (sim)", "path": self.pad,
                "direction": "PUSH", "transfer_mode": "SYNC", "enabled": True, "encryption": False,
                "schedule": {"minute": "0", "hour": "3", "dom": "*", "month": "*", "dow": "*"},
                "credentials": {"id": 1, "name": "onedrive-sim", "provider": {"type": "ONEDRIVE"}},
                "job": dict(self.job) if self.job else None}


class Backup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bestanden: dict[str, str] = Field(default_factory=dict)
    pad: str | None = Field(default=None, pattern="^/mnt/[^\\s]+$")


class Verouder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    uren: float = Field(gt=0, le=24 * 365)


def _fout(status: int, melding: str) -> dict[str, Any]:
    # Vorm van een Graph-foutbody; de code is een simulatorwaarde.
    return {"error": {"code": f"sim_{status}", "message": melding}}


def create_app() -> FastAPI:
    st = State()
    fouten = Fouten(dict(FOUTEN))
    app = FastAPI(title="OneDrive-simulator", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sim = st
    install(app, fouten=fouten, log=Verzoeklog(), reset=st.seed, fout_body=_fout)

    @app.post("/_sim/backup", include_in_schema=False)
    def backup(body: Backup) -> dict[str, Any]:
        """De geplande taak draait nu. Bij een storing blijft de vorige stand op het doel staan."""
        with st.lock:
            if body.pad:
                st.pad = body.pad
            nu = datetime.now(UTC)
            st.runs += 1
            fout = next((f for n in MELDINGEN if (f := fouten.raak(n)) is not None), None)
            job: dict[str, Any] = {"id": st.runs, "method": "cloudsync.sync", "time_started": _datum(nu),
                                   "time_finished": _datum(nu)}
            if fout is not None:
                job |= {"state": "FAILED", "error": str(fout.waarde or MELDINGEN[fout.naam])}
            else:
                st.bestanden = dict(body.bestanden)  # SYNC: het doel wordt gelijk aan de bron
                job |= {"state": "SUCCESS", "error": None}
            st.job = job
            return st.taak()

    @app.post("/_sim/verouder", include_in_schema=False)
    def verouder(body: Verouder) -> dict[str, Any]:
        """De laatste run ligt ``uren`` verder in het verleden (er draait sindsdien geen geplande back-up meer)."""
        with st.lock:
            if st.job is None:
                raise HTTPException(409, "De taak heeft nog nooit gedraaid; er is niets om te laten verouderen")
            for k in ("time_started", "time_finished"):
                st.job[k] = {"$date": st.job[k]["$date"] - int(body.uren * 3600 * 1000)}
            return st.taak()

    @app.get("/_sim/cloudsync_taken", include_in_schema=False)
    def cloudsync_taken() -> list[dict[str, Any]]:
        with st.lock:
            return [st.taak()]

    @app.get("/_sim/toestand", include_in_schema=False)
    def toestand() -> dict[str, Any]:
        with st.lock:
            return {"taak": st.taak(), "runs": st.runs, "bestanden": sorted(st.bestanden)}

    @app.get("/herstel")
    def herstel_lijst() -> dict[str, Any]:
        with st.lock:
            return {"bestanden": {p: hashlib.sha256(i.encode()).hexdigest() for p, i in sorted(st.bestanden.items())}}

    @app.get("/herstel/{pad:path}")
    def herstel(pad: str) -> dict[str, str]:
        with st.lock:
            if pad not in st.bestanden:
                raise HTTPException(404, "Bestand staat niet op het doel")
            return {"pad": pad, "inhoud": st.bestanden[pad]}

    return app

