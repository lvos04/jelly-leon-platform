"""``python -m gree_sim --mac f4911e000001 --mod 4`` (alleen tests): één Gree-unit op UDP met HTTP-testbediening.

``--fake-bron`` is de map met ``fake_gree.py`` uit de gepinde my_house-commit; ``shc_integrations`` moet importeerbaar
zijn (in de API-image van my_house is dat zo).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import uvicorn
from simkit import basis_args

from . import GreeUnit, create_app


async def draai(unit: GreeUnit, host: str, udp_poort: int, poort: int) -> None:
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(lambda: unit, local_addr=(host, udp_poort))
    print(f"Gree-simulator {unit.naam} ({unit.mac}) op udp/{udp_poort}, bediening op tcp/{poort}", flush=True)
    await uvicorn.Server(uvicorn.Config(create_app(unit), host=host, port=poort, log_level="warning")).serve()


def main() -> None:
    parser = basis_args("Gree-simulator", 30220, alleen_testnet=True)
    parser.add_argument("--udp-poort", type=int, default=7000)
    parser.add_argument("--fake-bron", type=Path, default=Path(os.environ.get("GREE_FAKE_BRON", "/e2e")))
    parser.add_argument("--mac", default="f4911e000001")
    parser.add_argument("--naam", default="unit1")
    parser.add_argument("--versie", choices=("v1", "v2"), default="v1")
    for kolom, standaard in (("pow", 1), ("mod", 4), ("settem", 21), ("temsen", 60)):
        parser.add_argument(f"--{kolom}", type=int, default=standaard)
    args = parser.parse_args()
    toestand = {"Pow": args.pow, "Mod": args.mod, "SetTem": args.settem, "TemSen": args.temsen}
    unit = GreeUnit(args.fake_bron, args.mac, args.naam, toestand, args.versie)
    asyncio.run(draai(unit, args.host, args.udp_poort, args.port))


if __name__ == "__main__":
    main()
