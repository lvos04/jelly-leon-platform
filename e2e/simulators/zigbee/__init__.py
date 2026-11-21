"""Zigbee-simulator (alleen tests): staat in voor Zigbee2MQTT en publiceert meetberichten op de MQTT-broker.

my_house ontvangt Zigbee-metingen via MQTT: ``shc_integrations.zigbee`` abonneert op ``<base>/#`` van Zigbee2MQTT
(``zigbee2mqtt/bridge/state``, ``bridge/devices``, ``<naam>/availability`` en ``<naam>``). Deze simulator publiceert
precies die onderwerpen (MQTT 3.1.1, QoS 0) en kan stoppen met meetberichten (EX29). Zie ``zigbee/README.md``.
"""

from __future__ import annotations

import asyncio
import json
import struct
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool
from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install

# Testwaarden: geen IEEE-adressen of namen uit een huishouden.
COORDINATOR = {"ieee_address": "0x00124b0000000000", "type": "Coordinator", "friendly_name": "Coordinator",
               "supported": True, "interview_completed": True}
SENSOR_IEEE = "0x00124b00000000a1"
SENSOR_NAAM = "testkamer_sensor"
BEGINMETING = {"temperature": 20.5, "humidity": 50.0, "battery": 90.0, "linkquality": 120.0, "voltage": 3000.0}
MAX_LOG = 5000


# --- MQTT 3.1.1 (alleen wat een publicerende client nodig heeft; QoS 0) ---------------------------------------------

def _lengte(n: int) -> bytes:
    out = bytearray()
    while True:
        n, byte = divmod(n, 128)
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _tekst(s: str) -> bytes:
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def connect_pakket(client_id: str, gebruiker: str | None, wachtwoord: str | None, keepalive: int) -> bytes:
    vlaggen = 0x02 | (0x80 if gebruiker else 0) | (0x40 if gebruiker and wachtwoord else 0)  # schone sessie
    body = _tekst("MQTT") + bytes([4, vlaggen]) + struct.pack("!H", keepalive) + _tekst(client_id)
    if gebruiker:
        body += _tekst(gebruiker)
        if wachtwoord:
            body += _tekst(wachtwoord)
    return b"\x10" + _lengte(len(body)) + body


def publish_pakket(onderwerp: str, payload: bytes, retain: bool = False) -> bytes:
    body = _tekst(onderwerp) + payload
    return bytes([0x30 | int(retain)]) + _lengte(len(body)) + body


PINGREQ = b"\xc0\x00"
DISCONNECT = b"\xe0\x00"


async def lees_pakket(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    """(eerste byte, body) van één MQTT-pakket."""
    kop = (await reader.readexactly(1))[0]
    n, factor = 0, 1
    for _ in range(4):
        byte = (await reader.readexactly(1))[0]
        n += (byte & 0x7F) * factor
        if not byte & 0x80:
            return kop, await reader.readexactly(n)
        factor *= 128
    raise ValueError("ongeldige MQTT-lengte")


def lees_publish(kop: int, body: bytes) -> tuple[str, bytes, bool]:
    """Onderwerp, payload en retain van een PUBLISH met QoS 0."""
    if kop >> 4 != 3 or kop & 0x06:
        raise ValueError("geen PUBLISH met QoS 0")
    n = struct.unpack("!H", body[:2])[0]
    return body[2:2 + n].decode(), body[2 + n:], bool(kop & 1)


# --- Simulator -------------------------------------------------------------------------------------------------------

@dataclass
class State:
    base: str = "zigbee2mqtt"
    meetberichten: bool = True
    meting: dict[str, float] = field(default_factory=dict)
    gepubliceerd: list[dict[str, Any]] = field(default_factory=list)
    verbonden: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def seed(self) -> None:
        with self.lock:
            self.meetberichten = True
            self.meting = dict(BEGINMETING)
            self.gepubliceerd.clear()

    def apparaten(self) -> list[dict[str, Any]]:
        sensor = {"ieee_address": SENSOR_IEEE, "type": "EndDevice", "friendly_name": SENSOR_NAAM,
                  "power_source": "Battery", "supported": True, "interview_completed": True, "model_id": "lumi.weather",
                  "definition": {"model": "WSDCGQ11LM", "vendor": "Aqara",
                                 "description": "Temperature, humidity and pressure sensor",
                                 "exposes": [{"type": "numeric", "property": k} for k in BEGINMETING]}}
        return [COORDINATOR, sensor]

    def bericht(self) -> dict[str, Any]:
        with self.lock:
            return {**self.meting, "last_seen": datetime.now(UTC).isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z")}


class Publisher:
    """Verbindt met de broker, publiceert de brug en het apparaat (retained) en daarna elke ``interval`` een meting."""

    def __init__(self, st: State, host: str, poort: int, gebruiker: str | None, wachtwoord: str | None,
                 interval: float, keepalive: int = 30) -> None:
        self.st, self.host, self.poort = st, host, poort
        self.gebruiker, self.wachtwoord, self.interval, self.keepalive = gebruiker, wachtwoord, interval, keepalive

    async def _stuur(self, writer: asyncio.StreamWriter, onderwerp: str, payload: Any, retain: bool = False) -> None:
        writer.write(publish_pakket(f"{self.st.base}/{onderwerp}", json.dumps(payload).encode(), retain))
        await writer.drain()
        with self.st.lock:
            self.st.gepubliceerd.append({"t": time.time(), "onderwerp": f"{self.st.base}/{onderwerp}",
                                         "retain": retain})
            del self.st.gepubliceerd[: max(0, len(self.st.gepubliceerd) - MAX_LOG)]

    async def sessie(self) -> None:
        reader, writer = await asyncio.open_connection(self.host, self.poort)
        try:
            writer.write(connect_pakket("zigbee-sim", self.gebruiker, self.wachtwoord, self.keepalive))
            await writer.drain()
            kop, body = await asyncio.wait_for(lees_pakket(reader), 10)
            if kop != 0x20 or len(body) != 2 or body[1] != 0:
                raise ConnectionError(f"broker weigert de verbinding (CONNACK {body.hex()})")
            self.st.verbonden = True
            await self._stuur(writer, "bridge/state", {"state": "online"}, retain=True)
            await self._stuur(writer, "bridge/devices", self.st.apparaten(), retain=True)
            await self._stuur(writer, f"{SENSOR_NAAM}/availability", {"state": "online"}, retain=True)
            lezer = asyncio.create_task(self._leeg(reader))
            laatst = time.monotonic()
            try:
                while not lezer.done():
                    if self.st.meetberichten:
                        await self._stuur(writer, SENSOR_NAAM, self.st.bericht())
                        laatst = time.monotonic()
                    elif time.monotonic() - laatst >= self.keepalive / 2:
                        writer.write(PINGREQ)  # verbinding open houden zonder meetbericht
                        await writer.drain()
                        laatst = time.monotonic()
                    await asyncio.sleep(self.interval)
            finally:
                lezer.cancel()
            raise ConnectionError("broker heeft de verbinding gesloten")
        finally:
            self.st.verbonden = False
            writer.close()

    @staticmethod
    async def _leeg(reader: asyncio.StreamReader) -> None:
        while True:  # PINGRESP en andere pakketten van de broker
            await lees_pakket(reader)

    async def altijd(self) -> None:
        """Opnieuw verbinden na een storing van de broker (zoals Zigbee2MQTT)."""
        while True:
            try:
                await self.sessie()
            except (OSError, ConnectionError, asyncio.IncompleteReadError, TimeoutError) as exc:
                print(f"zigbee-sim: {exc}; opnieuw over 2 s", flush=True)
            await asyncio.sleep(2)


class Meetberichten(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aan: StrictBool


class Meting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    waarden: dict[str, float]


def create_app(st: State) -> FastAPI:
    st.seed()
    app = FastAPI(title="Zigbee-testbediening", docs_url=None, redoc_url=None, openapi_url=None)
    install(app, fouten=Fouten(dict(ALGEMENE_FOUTEN)), log=Verzoeklog(), reset=st.seed,
            fout_body=lambda status, melding: {"fout": melding})

    @app.get("/_sim/toestand", include_in_schema=False)
    def toestand() -> dict[str, Any]:
        with st.lock:
            return {"verbonden": st.verbonden, "meetberichten": st.meetberichten, "meting": dict(st.meting),
                    "gepubliceerd": list(st.gepubliceerd)}

    @app.post("/_sim/meetberichten", include_in_schema=False)
    def meetberichten(body: Meetberichten) -> dict[str, bool]:
        """``{"aan": false}``: er komt geen meetbericht meer (EX29); ``true`` hervat."""
        with st.lock:
            st.meetberichten = body.aan
        return {"aan": body.aan}

    @app.post("/_sim/meting", include_in_schema=False)
    def meting(body: Meting) -> dict[str, float]:
        if onbekend := sorted(set(body.waarden) - set(BEGINMETING)):
            raise HTTPException(422, f"Onbekende eigenschap: {', '.join(onbekend)}")
        with st.lock:
            st.meting.update(body.waarden)
            return dict(st.meting)

    return app
