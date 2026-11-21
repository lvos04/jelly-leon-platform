"""Gree-opstelling (alleen tests): de nagebootste unit van my_house met testbediening voor de scenario's.

Het protocol wordt NIET opnieuw geschreven. Elke unit is een ``FakeGreeUnit`` uit
``my_house:integrations/tests/fake_gree.py`` (gepinde commit in ``my-house.commit``), die zelf
``shc_integrations.gree.protocol`` gebruikt. Deze module voegt alleen toe wat de scenario's nodig hebben: een eigen
MAC per unit, foutinjectie (onbereikbaar, bind mislukt), een van buitenaf gewijzigde toestand en een datagramlog.
Zie ``e2e/simulators/gree/README.md``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, StrictInt
from simkit import Fouten, Verzoeklog, install

FOUTEN = {
    "onbereikbaar": "De unit beantwoordt geen datagram (geen stroom of wifi). 'pad' = regex op de soort "
                    "(scan, bind, status, cmd), bijv. '^cmd$' laat alleen opdrachten wegvallen.",
    "bind_mislukt": "De unit beantwoordt de scan maar negeert elke bind, zoals bij onbekende firmware.",
}
MAC_PATROON = re.compile(r"^[0-9a-f]{12}$")
MAX_LOG = 5000


def laad_fake(bron: Path, mac: str) -> Any:
    """Eigen kopie van ``fake_gree.py`` per unit: de MAC is daar een modulewaarde, dus zo krijgt elke unit de zijne."""
    if not MAC_PATROON.match(mac):
        raise ValueError("mac moet 12 kleine hexadecimale tekens zijn")
    spec = importlib.util.spec_from_file_location(f"fake_gree_{mac}", bron / "fake_gree.py")
    if spec is None or spec.loader is None:
        raise FileNotFoundError(bron / "fake_gree.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    setattr(mod, "MAC", mac)  # noqa: B010 - modulewaarde van de geladen fake
    return mod


class GreeUnit(asyncio.DatagramProtocol):
    """Eén binnenunit op UDP. Stuurt elk datagram door naar de ``FakeGreeUnit`` tenzij een fout actief is."""

    def __init__(self, bron: Path, mac: str = "f4911e000001", naam: str = "unit1",
                 toestand: dict[str, int] | None = None, versie: str = "v1") -> None:
        self.mod = laad_fake(bron, mac)
        self.mac, self.naam, self.versie = mac, naam, versie
        self.begin = dict(toestand or {})
        self.fouten = Fouten(dict(FOUTEN))
        self.log: list[dict[str, Any]] = []
        self.transport: asyncio.BaseTransport | None = None
        self.reset()

    def reset(self) -> None:
        info = {"t": "dev", "cid": self.mac, "mac": self.mac, "mid": "10001", "model": "gree", "name": self.naam,
                "ver": "V1.2.1", "brand": "gree", "series": "gree"}
        self.unit = self.mod.FakeGreeUnit(version=self.versie, state=dict(self.begin), scan_info=info)
        self.binds = set(self.unit.bind_versions)
        if self.transport is not None:
            self.unit.connection_made(self.transport)
        self.log.clear()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport
        self.unit.connection_made(transport)

    def soort(self, data: bytes) -> str:
        try:
            msg = json.loads(data)
        except ValueError:
            return "onleesbaar"
        if not isinstance(msg, dict):
            return "onleesbaar"
        if msg.get("t") == "scan":
            return "scan"
        if msg.get("i") == 1:
            return "bind"
        try:
            return str(self.mod.p.open_packet(msg, self.mod.DEVICE_KEY.encode()).get("t", "onbekend"))
        except Exception:  # verkeerde sleutel of kapot pakket: de fake zou erop vastlopen
            return "onbekend"

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        soort = self.soort(data)
        item: dict[str, Any] = {"t": time.time(), "soort": soort, "fouten": []}
        self.log.append(item)
        del self.log[: max(0, len(self.log) - MAX_LOG)]
        if self.fouten.raak("onbereikbaar", soort):
            item["fouten"].append("onbereikbaar")
            return
        bind_fout = soort == "bind" and self.fouten.raak("bind_mislukt", soort) is not None
        if bind_fout:
            item["fouten"].append("bind_mislukt")
        # De fake negeert een bind in een versie die niet in bind_versions staat: dat is precies 'bind mislukt'.
        self.unit.bind_versions = set() if bind_fout else self.binds
        if soort in ("onleesbaar", "onbekend"):
            return
        voor = len(self.unit.commands)
        self.unit.datagram_received(data, addr)
        if len(self.unit.commands) > voor:
            opt, waarden = self.unit.commands[-1]
            item["opdracht"] = dict(zip(opt, waarden, strict=True))

    def toestand(self) -> dict[str, Any]:
        return {"mac": self.mac, "naam": self.naam, "gebonden": self.unit.bound, "versie": self.unit.version,
                "toestand": dict(self.unit.state), "bindpogingen": list(self.unit.bind_requests),
                "opdrachten": [dict(zip(o, w, strict=True)) for o, w in self.unit.commands]}


class Toestand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    waarden: dict[str, StrictInt]


def create_app(unit: GreeUnit) -> FastAPI:
    app = FastAPI(title=f"Gree-testbediening {unit.naam}", docs_url=None, redoc_url=None, openapi_url=None)
    install(app, fouten=unit.fouten, log=Verzoeklog(), reset=unit.reset,
            fout_body=lambda status, melding: {"fout": melding})

    @app.get("/_sim/toestand", include_in_schema=False)
    def lees() -> dict[str, Any]:
        return unit.toestand()

    @app.post("/_sim/toestand", include_in_schema=False)
    def zet(body: Toestand) -> dict[str, Any]:
        """Van buitenaf gewijzigd, zoals met de afstandsbediening (bijv. ``{"waarden": {"Pow": 0}}``)."""
        if onbekend := sorted(set(body.waarden) - set(unit.unit.state)):
            raise HTTPException(422, f"Onbekende Gree-kolom: {', '.join(onbekend)}")
        unit.unit.state.update(body.waarden)
        unit.log.append({"t": time.time(), "soort": "extern", "fouten": [], "opdracht": dict(body.waarden)})
        return unit.toestand()

    @app.get("/_sim/udp", include_in_schema=False)
    def udp() -> list[dict[str, Any]]:
        return list(unit.log)

    return app
