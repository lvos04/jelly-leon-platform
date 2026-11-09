"""Start de simulator: ``python -m truenas_sim --host 127.0.0.1 --port 8080 --version 25.10.8``.

Met ``--ssl-certfile``/``--ssl-keyfile`` luistert hij op HTTPS/WSS (``wss://…/api/current``), zoals de Hub en de
executor eisen (een echte TrueNAS trekt een API-key in die over een onversleutelde verbinding gaat). Het certificaat
maakt de test zelf (zelfondertekend); de Hub vertrouwt het pas na bevestiging van de vingerafdruk.
"""

from __future__ import annotations

import argparse


def main() -> None:
    import uvicorn

    from truenas_sim.server import SimServer, create_app
    from truenas_sim.state import PROFILES

    parser = argparse.ArgumentParser(description="TrueNAS-simulator (JSON-RPC 2.0 over WebSocket) — alleen voor tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--version", default=None, choices=sorted(PROFILES), help="gesimuleerde TrueNAS-versie")
    parser.add_argument("--ssl-certfile", default=None, help="PEM-certificaat: luister op HTTPS/WSS")
    parser.add_argument("--ssl-keyfile", default=None, help="PEM-sleutel bij --ssl-certfile")
    args = parser.parse_args()
    if bool(args.ssl_certfile) != bool(args.ssl_keyfile):
        parser.error("--ssl-certfile en --ssl-keyfile horen samen")
    uvicorn.run(create_app(SimServer(args.version)), host=args.host, port=args.port, log_level="warning",
                ws="websockets-sansio", ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)


if __name__ == "__main__":
    main()
