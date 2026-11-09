"""``python -m gotify_sim --port 30215`` (alleen tests)."""

from __future__ import annotations

from simkit import basis_args, serve

from . import create_app


def main() -> None:
    args = basis_args("Gotify-simulator", 30215).parse_args()
    serve(create_app(), args.host, args.port, args.ssl_certfile, args.ssl_keyfile)


if __name__ == "__main__":
    main()
