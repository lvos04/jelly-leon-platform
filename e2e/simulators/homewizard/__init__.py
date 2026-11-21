"""HomeWizard-simulator (alleen tests): P1-meter of Energy Socket, lokale API v1, alleen lezen.

Protocol en bronnen: ``e2e/simulators/homewizard/README.md``. Eén proces is één apparaat, net als thuis (elk apparaat
heeft een eigen adres). Elk schrijvend verzoek (ook een schakelopdracht aan de socket) wordt vastgelegd in
``/_sim/verzoeken`` en geweigerd met 403: zo kan een scenario bewijzen dat geen route het boilerstopcontact schakelt.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install

TZ = ZoneInfo("Europe/Amsterdam")
APPARATEN = ("p1", "socket")

# Testwaarden: geen apparaat-ID's, serienummers of netwerknamen uit een huishouden.
INFO = {
    "p1": {"product_name": "P1 meter", "product_type": "HWE-P1", "serial": "5e0000000001", "firmware_version": "4.19",
           "api_version": "v1"},
    "socket": {"product_name": "Energy Socket", "product_type": "HWE-SKT", "serial": "5e0000000002",
               "firmware_version": "3.03", "api_version": "v1"},
}
BEGIN = {
    "p1": {"wifi_ssid": "sim-wifi", "wifi_strength": 100, "smr_version": 50, "meter_model": "SIM-METER",
           "unique_id": "53494d00000000000000000000000001", "active_tariff": 2,
           "total_power_import_t1_kwh": 1000.0, "total_power_import_t2_kwh": 500.0,
           "total_power_export_t1_kwh": 0.0, "total_power_export_t2_kwh": 0.0,
           "active_power_w": 450.0, "active_power_l1_w": 450.0, "total_gas_m3": 800.0, "gas_unique_id": "53494d47"},
    "socket": {"wifi_ssid": "sim-wifi", "wifi_strength": 94, "total_power_import_t1_kwh": 30.0,
               "total_power_export_t1_kwh": 0.0, "active_power_w": 1987.2, "active_power_l1_w": 1987.2},
}
TELLERS = ("total_power_import_t1_kwh", "total_power_import_t2_kwh", "total_power_export_t1_kwh",
           "total_power_export_t2_kwh")
# Velden die /_sim/meting mag zetten, per apparaat.
METINGEN = {
    "p1": {"active_power_w", "active_power_l1_w", "total_gas_m3", "active_tariff", *TELLERS},
    "socket": {"active_power_w", "active_power_l1_w", "total_power_import_t1_kwh", "total_power_export_t1_kwh"},
}


def zomertijdwissel(jaar: int, richting: str) -> datetime:
    """UTC-moment van de wissel in de EU: laatste zondag van maart/oktober om 01:00 UTC."""
    maand = 3 if richting == "naar_zomertijd" else 10
    laatste = datetime(jaar, maand, 31, 1, tzinfo=UTC)
    return laatste - timedelta(days=(laatste.weekday() + 1) % 7)


@dataclass
class State:
    apparaat: str
    data: dict[str, Any] = field(default_factory=dict)
    aan: bool = True
    klok: datetime | None = None  # None = echte tijd; anders een vaste simulatortijd (UTC)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def seed(self) -> None:
        self.data = dict(BEGIN[self.apparaat])
        self.aan = True
        self.klok = None

    def nu(self) -> datetime:
        return self.klok or datetime.now(UTC)

    def v1_data(self) -> dict[str, Any]:
        d = dict(self.data)
        d["total_power_import_kwh"] = round(sum(d.get(k, 0.0) for k in TELLERS if "import" in k), 3)
        d["total_power_export_kwh"] = round(sum(d.get(k, 0.0) for k in TELLERS if "export" in k), 3)
        if self.apparaat == "p1":
            # v1 gas_timestamp: YYMMDDhhmmss in lokale tijd zonder zone (daardoor dubbelzinnig bij de wintertijdwissel).
            d["gas_timestamp"] = int(self.nu().astimezone(TZ).strftime("%y%m%d%H%M%S"))
        return d


class Meting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    waarden: dict[str, float]


class Meterreset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stand: float = Field(default=0.0, ge=0)


class Klok(BaseModel):
    model_config = ConfigDict(extra="forbid")
    utc: datetime | None = None
    verder_s: float | None = None
    echt: bool = False


class Zomertijd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    richting: str = Field(pattern="^(naar_zomertijd|naar_wintertijd)$")
    jaar: int | None = Field(default=None, ge=2000, le=2099)
    voor_s: float = Field(default=1800, ge=0)


def _fout(status: int, melding: str) -> dict[str, Any]:
    # De vorm volgt de v1-foutbody; "id" is een simulatorwaarde, geen code van HomeWizard.
    return {"error": {"id": f"sim_{status}", "description": melding}}


def create_app(apparaat: str = "p1") -> FastAPI:
    if apparaat not in APPARATEN:
        raise ValueError(f"apparaat moet een van {APPARATEN} zijn")
    st = State(apparaat)
    st.seed()
    log = Verzoeklog()
    app = FastAPI(title=f"HomeWizard-simulator ({apparaat})", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sim = st

    async def beschrijf(request: Request) -> dict[str, str]:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return {}
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError:
            return {"body": "geen json"}
        # Alleen de gevraagde schakelstand, geen andere invoer in het log.
        if isinstance(body, dict) and isinstance(body.get("power_on"), bool):
            return {"power_on": str(body["power_on"]).lower()}
        return {}

    install(app, fouten=Fouten(dict(ALGEMENE_FOUTEN)), log=log, reset=st.seed, fout_body=_fout, beschrijf=beschrijf)

    @app.get("/api")
    def api() -> dict[str, Any]:
        return INFO[apparaat]

    @app.get("/api/v1/data")
    def data() -> dict[str, Any]:
        with st.lock:
            return st.v1_data()

    @app.get("/api/v1/state")
    def state() -> JSONResponse:
        if apparaat != "socket":
            return JSONResponse(_fout(404, "Geen schakelstand op dit apparaat"), status_code=404)
        return JSONResponse({"power_on": st.aan, "switch_lock": False, "brightness": 255})

    @app.get("/_sim/toestand", include_in_schema=False)
    def toestand() -> dict[str, Any]:
        with st.lock:
            return {"apparaat": apparaat, "data": st.v1_data(), "power_on": st.aan, "klok": st.nu().isoformat(),
                    "klok_vast": st.klok is not None,
                    "schakelverzoeken": [v.__dict__ for v in log.schrijvend("/api")]}

    @app.post("/_sim/meting", include_in_schema=False)
    def meting(body: Meting) -> dict[str, Any]:
        if onbekend := sorted(set(body.waarden) - METINGEN[apparaat]):
            raise HTTPException(422, f"Onbekend veld voor dit apparaat: {', '.join(onbekend)}")
        with st.lock:
            st.data.update(body.waarden)
            return st.v1_data()

    @app.post("/_sim/meterreset", include_in_schema=False)
    def meterreset(body: Meterreset) -> dict[str, Any]:
        # Tellerstand springt terug, zoals na vervanging of reset van de meter.
        with st.lock:
            for k in TELLERS:
                if k in st.data:
                    st.data[k] = 0.0
            st.data["total_power_import_t1_kwh"] = body.stand
            return st.v1_data()

    @app.post("/_sim/klok", include_in_schema=False)
    def klok(body: Klok) -> dict[str, Any]:
        with st.lock:
            if body.echt:
                st.klok = None
            if body.utc is not None:
                if body.utc.tzinfo is None:
                    raise HTTPException(422, "utc heeft een tijdzone nodig")
                st.klok = body.utc.astimezone(UTC)
            if body.verder_s is not None:
                st.klok = st.nu() + timedelta(seconds=body.verder_s)
            return {"klok": st.nu().isoformat(), "lokaal": st.nu().astimezone(TZ).isoformat(),
                    "klok_vast": st.klok is not None}

    @app.post("/_sim/zomertijd", include_in_schema=False)
    def zomertijd(body: Zomertijd) -> dict[str, Any]:
        with st.lock:
            wissel = zomertijdwissel(body.jaar or st.nu().year, body.richting)
            st.klok = wissel - timedelta(seconds=body.voor_s)
            return {"klok": st.klok.isoformat(), "wissel_utc": wissel.isoformat()}

    # Als laatste: de /_sim/-routes hierboven gaan voor.
    @app.api_route("/{pad:path}", methods=["PUT", "POST", "PATCH", "DELETE"])
    def schrijven(pad: str) -> JSONResponse:
        # Alleen lezen: elk schrijvend verzoek staat al in /_sim/verzoeken en verandert niets.
        return JSONResponse(_fout(403, "Simulator is alleen-lezen: schakelen en instellen worden geweigerd"),
                            status_code=403)

    return app
