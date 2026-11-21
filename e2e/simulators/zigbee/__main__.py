"""``python -m zigbee_sim --mqtt-host mosquitto`` (alleen tests): publiceert als Zigbee2MQTT, bediening op HTTP."""

from __future__ import annotations

import asyncio
from pathlib import Path

import uvicorn
from simkit import basis_args

from . import Publisher, State, create_app


async def draai(publisher: Publisher, app_host: str, poort: int, st: State) -> None:
    taak = asyncio.create_task(publisher.altijd())
    try:
        await uvicorn.Server(uvicorn.Config(create_app(st), host=app_host, port=poort, log_level="warning")).serve()
    finally:
        taak.cancel()


def main() -> None:
    parser = basis_args("Zigbee-simulator", 30222, alleen_testnet=True)
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-poort", type=int, default=1883)
    parser.add_argument("--mqtt-gebruiker", default="zigbee2mqtt")
    parser.add_argument("--mqtt-wachtwoordbestand", type=Path, default=None,
                        help="bestand met het testwachtwoord (nooit op de opdrachtregel)")
    parser.add_argument("--base-topic", default="zigbee2mqtt")
    parser.add_argument("--interval", type=float, default=30.0, help="seconden tussen meetberichten")
    args = parser.parse_args()
    wachtwoord = args.mqtt_wachtwoordbestand.read_text().strip() if args.mqtt_wachtwoordbestand else None
    st = State(base=args.base_topic.rstrip("/"))
    publisher = Publisher(st, args.mqtt_host, args.mqtt_poort, args.mqtt_gebruiker, wachtwoord, args.interval)
    asyncio.run(draai(publisher, args.host, args.port, st))


if __name__ == "__main__":
    main()
