"""``python -m qbittorrent_sim --port 30218`` (alleen tests)."""

from __future__ import annotations

from simkit import basis_args, serve

from . import create_app


def main() -> None:
    parser = basis_args("qBittorrent-simulator", 30218, alleen_testnet=True)
    parser.add_argument("--leeg", action="store_true", help="Begin zonder torrents voor de publieke controle")
    args = parser.parse_args()
    serve(create_app(leeg=args.leeg), args.host, args.port, args.ssl_certfile, args.ssl_keyfile)


if __name__ == "__main__":
    main()
