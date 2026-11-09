"""``python -m beszel_sim --port 30333`` (alleen tests)."""

from __future__ import annotations

from simkit import basis_args, serve

from . import create_app


def main() -> None:
    args = basis_args("Beszel-hub-simulator", 30333).parse_args()
    serve(create_app(), args.host, args.port, args.ssl_certfile, args.ssl_keyfile)


if __name__ == "__main__":
    main()
