"""``python -m homewizard_sim --apparaat p1|socket --port 30221`` (alleen tests)."""

from __future__ import annotations

from simkit import basis_args, serve

from . import APPARATEN, create_app


def main() -> None:
    parser = basis_args("HomeWizard-simulator", 30221, alleen_testnet=True)
    parser.add_argument("--apparaat", choices=APPARATEN, default="p1")
    args = parser.parse_args()
    serve(create_app(args.apparaat), args.host, args.port, args.ssl_certfile, args.ssl_keyfile)


if __name__ == "__main__":
    main()
