"""Toestandstabel Gree-opstelling: echt UDP met het gepinde protocol van my_house, één test per rij.

| Rij | Toestand | Verwacht |
|---|---|---|
| protocol | scan, bind, status, cmd | antwoorden van de fake van my_house, opdracht teruggelezen |
| EX24 | onbereikbaar | geen antwoord; met pad '^cmd$' geen nieuwe opdracht, status blijft |
| EX25 | twee binnenunits, tegengestelde modus | elk een eigen MAC en modus |
| EX26 | van buitenaf uit | status leest Pow 0; onbekende kolom of geen geheel getal geweigerd |
| EX30 | bind mislukt | scan antwoordt, bind blijft onbeantwoord, unit niet gebonden |
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from gree_sim import GreeUnit, create_app

Unit = tuple[GreeUnit, int, httpx.AsyncClient]


@asynccontextmanager
async def unit_op_udp(bron: Path, **kw: Any) -> AsyncIterator[Unit]:
    unit = GreeUnit(bron, **kw)
    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: unit,
                                                                             local_addr=("127.0.0.1", 0))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(unit)), base_url="http://sim") as c:
        try:
            yield unit, transport.get_extra_info("sockname")[1], c
        finally:
            transport.close()


def draai(bron: Path, proef: Callable[[Unit], Awaitable[None]], **kw: Any) -> None:
    async def main() -> None:
        async with unit_op_udp(bron, **kw) as u:
            await proef(u)

    asyncio.run(main())


def p() -> Any:  # het gepinde protocol van my_house (pas na de fixture importeerbaar)
    from shc_integrations.gree import protocol

    return protocol


async def scan(poort: int, timeout: float = 2.0) -> dict[str, Any]:
    reply = await p().request("127.0.0.1", {"t": "scan"}, port=poort, timeout=timeout)
    return p().open_packet(reply, p().GENERIC_KEY_V1)


async def bind(poort: int, mac: str, timeout: float = 2.0) -> bytes:
    pkt = p().build_packet({"mac": mac, "t": "bind", "uid": 0}, p().GENERIC_KEY_V1, "v1", i=1)
    inner = p().open_packet(await p().request("127.0.0.1", pkt, port=poort, timeout=timeout), p().GENERIC_KEY_V1)
    assert inner["t"] == "bindok"
    return str(inner["key"]).encode()


async def vraag(poort: int, mac: str, key: bytes, inner: dict[str, Any], timeout: float = 2.0) -> dict[str, Any]:
    pkt = p().build_packet(inner, key, "v1", tcid=mac)
    return p().open_packet(await p().request("127.0.0.1", pkt, port=poort, timeout=timeout), key)


async def status(poort: int, mac: str, key: bytes, timeout: float = 2.0) -> dict[str, int]:
    res = await vraag(poort, mac, key, {"cols": ["Pow", "Mod", "SetTem"], "mac": mac, "t": "status"}, timeout)
    assert res["mac"] == mac
    return dict(zip(res["cols"], res["dat"], strict=True))


def test_protocol_scan_bind_status_en_opdracht(my_house_bron: Path) -> None:
    async def proef(u: Unit) -> None:
        _, poort, c = u
        info = await scan(poort)
        assert (info["mac"], info["name"]) == ("f4911e000001", "woonkamer")
        key = await bind(poort, info["mac"])
        assert await status(poort, info["mac"], key) == {"Pow": 1, "Mod": 4, "SetTem": 21}
        res = await vraag(poort, info["mac"], key, {"opt": ["SetTem"], "p": [23], "t": "cmd"})
        assert res["t"] == "res" and (await status(poort, info["mac"], key))["SetTem"] == 23
        t = (await c.get("/_sim/toestand")).json()
        assert t["gebonden"] is True and t["opdrachten"] == [{"SetTem": 23}]
        assert [i["soort"] for i in (await c.get("/_sim/udp")).json()] == ["scan", "bind", "status", "cmd", "status"]

    draai(my_house_bron, proef, naam="woonkamer", toestand={"Pow": 1, "Mod": 4, "SetTem": 21})


def test_ex24_onbereikbaar_geeft_geen_antwoord(my_house_bron: Path) -> None:
    async def proef(u: Unit) -> None:
        _, poort, c = u
        assert (await c.post("/_sim/fouten", json={"naam": "onbereikbaar"})).status_code == 200
        with pytest.raises(TimeoutError):
            await scan(poort, timeout=0.3)
        assert (await c.get("/_sim/udp")).json()[-1]["fouten"] == ["onbereikbaar"]
        await c.delete("/_sim/fouten")
        assert (await scan(poort))["mac"] == "f4911e000001"

    draai(my_house_bron, proef)


def test_ex24_onbereikbaar_voor_opdrachten_voert_niets_uit(my_house_bron: Path) -> None:
    async def proef(u: Unit) -> None:
        _, poort, c = u
        key = await bind(poort, "f4911e000001")
        await c.post("/_sim/fouten", json={"naam": "onbereikbaar", "pad": "^cmd$"})
        with pytest.raises(TimeoutError):
            await vraag(poort, "f4911e000001", key, {"opt": ["Pow"], "p": [0], "t": "cmd"}, timeout=0.3)
        assert await status(poort, "f4911e000001", key) == {"Pow": 1, "Mod": 4, "SetTem": 21}
        assert (await c.get("/_sim/toestand")).json()["opdrachten"] == []

    draai(my_house_bron, proef, toestand={"Pow": 1, "Mod": 4, "SetTem": 21})


def test_ex25_twee_binnenunits_met_tegengestelde_modus(my_house_bron: Path) -> None:
    async def main() -> None:
        async with (unit_op_udp(my_house_bron, mac="f4911e00000a", naam="a", toestand={"Mod": 4}) as (_, pa, _ca),
                    unit_op_udp(my_house_bron, mac="f4911e00000b", naam="b", toestand={"Mod": 1}) as (_, pb, _cb)):
            gezien = {}
            for poort in (pa, pb):
                mac = (await scan(poort))["mac"]
                gezien[mac] = (await status(poort, mac, await bind(poort, mac)))["Mod"]
            assert gezien == {"f4911e00000a": 4, "f4911e00000b": 1}  # 4 = verwarmen, 1 = koelen

    asyncio.run(main())


def test_ex26_van_buitenaf_uitgezet(my_house_bron: Path) -> None:
    async def proef(u: Unit) -> None:
        _, poort, c = u
        key = await bind(poort, "f4911e000001")
        assert (await c.post("/_sim/toestand", json={"waarden": {"Pow": 0}})).status_code == 200
        assert (await status(poort, "f4911e000001", key))["Pow"] == 0
        extern = [i["opdracht"] for i in (await c.get("/_sim/udp")).json() if i["soort"] == "extern"]
        assert extern == [{"Pow": 0}]
        assert (await c.get("/_sim/toestand")).json()["opdrachten"] == []  # geen opdracht via het netwerk

    draai(my_house_bron, proef, toestand={"Pow": 1})


@pytest.mark.parametrize("waarden", [{"Vermogen": 0}, {"Pow": "0"}, {"Pow": 0.5}])
def test_ex26_ongeldige_externe_toestand_wordt_geweigerd(my_house_bron: Path, waarden: dict[str, Any]) -> None:
    async def proef(u: Unit) -> None:
        _, _, c = u
        assert (await c.post("/_sim/toestand", json={"waarden": waarden})).status_code == 422
        assert (await c.get("/_sim/toestand")).json()["toestand"]["Pow"] == 1

    draai(my_house_bron, proef, toestand={"Pow": 1})


def test_ex30_bind_mislukt_zoals_bij_onbekende_firmware(my_house_bron: Path) -> None:
    async def proef(u: Unit) -> None:
        _, poort, c = u
        await c.post("/_sim/fouten", json={"naam": "bind_mislukt"})
        assert (await scan(poort))["mac"] == "f4911e000001"
        with pytest.raises(TimeoutError):
            await bind(poort, "f4911e000001", timeout=0.3)
        t = (await c.get("/_sim/toestand")).json()
        assert t["gebonden"] is False and t["bindpogingen"] == ["v1"]
        laatste = (await c.get("/_sim/udp")).json()[-1]
        assert (laatste["soort"], laatste["fouten"]) == ("bind", ["bind_mislukt"])

    draai(my_house_bron, proef)


def test_onleesbaar_datagram_en_reset(my_house_bron: Path) -> None:
    async def proef(u: Unit) -> None:
        unit, poort, c = u
        loop = asyncio.get_running_loop()
        t, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=("127.0.0.1", poort))
        t.sendto(b"geen json")
        t.close()
        await asyncio.sleep(0.05)
        assert (await c.get("/_sim/udp")).json()[-1]["soort"] == "onleesbaar"
        await bind(poort, "f4911e000001")
        await c.post("/_sim/fouten", json={"naam": "onbereikbaar"})
        assert (await c.post("/_sim/reset")).status_code == 200
        assert (await c.get("/_sim/udp")).json() == [] and unit.toestand()["gebonden"] is False
        assert (await scan(poort))["mac"] == "f4911e000001"  # fout gewist, transport blijft werken

    draai(my_house_bron, proef)


@pytest.mark.parametrize("mac", ["F4911E000001", "f4911e00001", "f4911e00000g"])
def test_ongeldige_mac_wordt_geweigerd(my_house_bron: Path, mac: str) -> None:
    with pytest.raises(ValueError):
        GreeUnit(my_house_bron, mac=mac)
