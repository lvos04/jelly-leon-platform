"""``python -m sabnzbd_sim --port 30217`` (alleen tests)."""

from __future__ import annotations

from simkit import basis_args, serve

from . import create_app


def main() -> None:
    args = basis_args("SABnzbd-simulator", 30217, alleen_testnet=True).parse_args()
    serve(create_app(), args.host, args.port, args.ssl_certfile, args.ssl_keyfile)


if __name__ == "__main__":
    main()
