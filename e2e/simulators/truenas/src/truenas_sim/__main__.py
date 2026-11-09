"""Start de simulator: ``python -m truenas_sim --host 127.0.0.1 --port 8080 --version 25.10.8``."""

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
    args = parser.parse_args()
    uvicorn.run(create_app(SimServer(args.version)), host=args.host, port=args.port, log_level="warning",
                ws="websockets-sansio")


if __name__ == "__main__":
    main()
