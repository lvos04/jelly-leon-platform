"""Toestandstabel Zigbee-simulator: MQTT 3.1.1 tegen een proefbroker in de test, één test per rij.

| Rij | Toestand | Verwacht |
|---|---|---|
| protocol | pakketvorm | bytes zoals MQTT 3.1.1 (lengtecodering, CONNECT, PUBLISH) |
| protocol | na verbinden | brug, apparatenlijst en beschikbaarheid retained, daarna meetberichten |
| my_house | berichten | de gepinde Zigbee-adapter van my_house maakt er metingen van |
| EX29 | stop meetberichten | daarna komt er aantoonbaar niets meer (alleen PINGREQ) |
| EX29 | hervatten | meetberichten komen weer |
| negatief | broker weigert, ongeldige bediening | fout zichtbaar, niets verbonden; 422 |
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from zigbee_sim import (
    DISCONNECT,
    PINGREQ,
    SENSOR_IEEE,
    SENSOR_NAAM,
    Publisher,
    State,
    _lengte,
    connect_pakket,
    create_app,
    lees_pakket,
    lees_publish,
    publish_pakket,
)


@dataclass
class Broker:
    """Proefbroker: accepteert één client, antwoordt CONNACK met ``rc`` en bewaart elk ontvangen pakket."""

    rc: int = 0
    connect: bytes = b""
    publish: list[tuple[str, bytes, bool]] = field(default_factory=list)
    pings: int = 0
    poort: int = 0

    async def client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            kop, self.connect = await lees_pakket(reader)
            assert kop == 0x10
            writer.write(bytes([0x20, 2, 0, self.rc]))
            await writer.drain()
            while self.rc == 0:
                kop, body = await lees_pakket(reader)
                if kop == PINGREQ[0]:
                    self.pings += 1
                    writer.write(b"\xd0\x00")
                else:
                    self.publish.append(lees_publish(kop, body))
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()

    def onderwerpen(self) -> list[str]:
        return [o for o, _, _ in self.publish]


@asynccontextmanager
async def opstelling(rc: int = 0, interval: float = 0.02) -> AsyncIterator[tuple[Broker, httpx.AsyncClient, Any]]:
    broker = Broker(rc)
    server = await asyncio.start_server(broker.client, "127.0.0.1", 0)
    broker.poort = server.sockets[0].getsockname()[1]
    st = State()
    app = create_app(st)
    pub = Publisher(st, "127.0.0.1", broker.poort, "zigbee2mqtt", "testwachtwoord", interval, keepalive=1)
    taak = asyncio.create_task(pub.sessie())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim") as c:
        try:
            yield broker, c, taak
        finally:
            taak.cancel()
            server.close()


async def wacht_op(voorwaarde: Any, timeout: float = 2.0) -> None:
    async def lus() -> None:
        while not voorwaarde():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(lus(), timeout)


def test_lengtecodering_zoals_de_specificatie() -> None:
    assert [_lengte(n) for n in (0, 127, 128, 321, 16383, 16384)] == [
        b"\x00", b"\x7f", b"\x80\x01", b"\xc1\x02", b"\xff\x7f", b"\x80\x80\x01"]


def test_connect_en_publish_pakketten() -> None:
    assert connect_pakket("c", "u", "p", 30) == b"\x10\x13\x00\x04MQTT\x04\xc2\x00\x1e\x00\x01c\x00\x01u\x00\x01p"
    assert connect_pakket("c", None, None, 30) == b"\x10\x0d\x00\x04MQTT\x04\x02\x00\x1e\x00\x01c"
    assert publish_pakket("a/b", b"x") == b"\x30\x06\x00\x03a/bx"
    assert publish_pakket("a/b", b"x", retain=True)[0] == 0x31
    assert (PINGREQ, DISCONNECT) == (b"\xc0\x00", b"\xe0\x00")


def test_qos1_publish_wordt_niet_als_qos0_gelezen() -> None:
    with pytest.raises(ValueError):
        lees_publish(0x32, b"\x00\x01a\x00\x01x")


def test_na_verbinden_eerst_brug_en_apparaat_retained_daarna_metingen() -> None:
    async def main() -> None:
        async with opstelling() as (broker, c, _):
            await wacht_op(lambda: broker.onderwerpen().count(f"zigbee2mqtt/{SENSOR_NAAM}") >= 2)
            assert b"zigbee2mqtt" in broker.connect and b"testwachtwoord" in broker.connect
            assert broker.publish[:3] == [
                ("zigbee2mqtt/bridge/state", b'{"state": "online"}', True),
                ("zigbee2mqtt/bridge/devices", broker.publish[1][1], True),
                (f"zigbee2mqtt/{SENSOR_NAAM}/availability", b'{"state": "online"}', True)]
            assert SENSOR_IEEE in json.loads(broker.publish[1][1])[1]["ieee_address"]
            meting = json.loads(broker.publish[3][1])
            assert meting["temperature"] == 20.5 and meting["last_seen"].endswith("Z")
            assert broker.publish[3][2] is False
            t = (await c.get("/_sim/toestand")).json()
            assert t["verbonden"] is True and t["gepubliceerd"][0]["onderwerp"] == "zigbee2mqtt/bridge/state"

    asyncio.run(main())


def test_my_house_leest_de_berichten_als_metingen(my_house_bron: Path) -> None:
    from shc_integrations.base import Reading
    from shc_integrations.zigbee import ZigbeeAdapter

    async def main() -> None:
        async with opstelling() as (broker, c, _):
            await c.post("/_sim/meting", json={"waarden": {"temperature": 18.25}})
            await wacht_op(lambda: any(b"18.25" in payload for _, payload, _ in broker.publish))
            adapter = ZigbeeAdapter({"host": "mqtt"})
            events = [e for o, payload, _ in broker.publish for e in adapter.handle_message(o, payload)]
            metingen = {(r.device_identity, r.metric): r.value for r in events if isinstance(r, Reading)}  # laatste
            assert metingen[(SENSOR_IEEE, "temperature")] == 18.25
            assert metingen[(SENSOR_IEEE, "humidity")] == 50.0

    asyncio.run(main())


def test_ex29_na_stop_komt_er_aantoonbaar_niets_meer() -> None:
    async def main() -> None:
        async with opstelling() as (broker, c, taak):
            await wacht_op(lambda: len(broker.publish) >= 4)
            assert (await c.post("/_sim/meetberichten", json={"aan": False})).json() == {"aan": False}
            await asyncio.sleep(0.05)  # een bericht dat al onderweg was
            na_stop = len(broker.publish)
            await asyncio.sleep(1.2)  # ruim 50 intervallen en meer dan de helft van de keepalive
            assert len(broker.publish) == na_stop
            assert broker.pings >= 1 and not taak.done()  # verbinding blijft open, alleen PINGREQ

    asyncio.run(main())


def test_ex29_hervatten_na_stop() -> None:
    async def main() -> None:
        async with opstelling() as (broker, c, _):
            await c.post("/_sim/meetberichten", json={"aan": False})
            await asyncio.sleep(0.1)
            voor = len(broker.publish)
            await c.post("/_sim/meetberichten", json={"aan": True})
            await wacht_op(lambda: len(broker.publish) > voor)
            assert broker.onderwerpen()[-1] == f"zigbee2mqtt/{SENSOR_NAAM}"

    asyncio.run(main())


def test_broker_weigert_verbinding() -> None:
    async def main() -> None:
        async with opstelling(rc=5) as (broker, c, taak):
            with pytest.raises(ConnectionError, match="weigert"):
                await asyncio.wait_for(taak, 2)
            assert broker.publish == [] and (await c.get("/_sim/toestand")).json()["verbonden"] is False

    asyncio.run(main())


@pytest.mark.parametrize("pad,body", [("/_sim/meetberichten", {"aan": "nee"}),
                                      ("/_sim/meetberichten", {"aan": 0}),
                                      ("/_sim/meting", {"waarden": {"co2": 400}})])
def test_ongeldige_bediening_wordt_geweigerd(pad: str, body: dict[str, Any]) -> None:
    async def main() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(State())),
                                     base_url="http://sim") as c:
            assert (await c.post(pad, json=body)).status_code == 422
            assert (await c.get("/_sim/toestand")).json()["meetberichten"] is True

    asyncio.run(main())


def test_reset_hervat_meetberichten_en_herstelt_meting() -> None:
    async def main() -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(State())),
                                     base_url="http://sim") as c:
            await c.post("/_sim/meetberichten", json={"aan": False})
            await c.post("/_sim/meting", json={"waarden": {"temperature": 30}})
            await c.post("/_sim/reset")
            t = (await c.get("/_sim/toestand")).json()
            assert t["meetberichten"] is True and t["meting"]["temperature"] == 20.5

    asyncio.run(main())
