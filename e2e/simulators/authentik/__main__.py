"""``python -m authentik_sim --port 30140 [--versie 2026.8.5]`` (alleen tests)."""

from __future__ import annotations

from simkit import basis_args, serve

from . import VERSIE, create_app


def main() -> None:
    parser = basis_args("Authentik-simulator", 30140)
    parser.add_argument("--versie", default=VERSIE, help="gesimuleerde Authentik-versie (grant_types, filters)")
    args = parser.parse_args()
    serve(create_app(versie=args.versie), args.host, args.port, args.ssl_certfile, args.ssl_keyfile)


if __name__ == "__main__":
    main()
