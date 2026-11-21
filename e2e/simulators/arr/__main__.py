"""Start de arr-instanties met Seerr, of de Prowlarr-mock van MSO."""

import os

from simkit import basis_args, serve

from . import create_app, prowlarr


def main() -> None:
    parser = basis_args("Seerr en arr-testsimulator", 30216, alleen_testnet=True)
    parser.set_defaults(port=None)
    parser.add_argument("--prowlarr", action="store_true", help="Gebruik de Prowlarr-mock van MSO")
    parser.add_argument("--sab-url", help="Loopback of de SAB-dienst in het interne testnet")
    args = parser.parse_args()
    app = prowlarr.create_app() if args.prowlarr else create_app(sab_url=args.sab_url,
                                                               sab_key=os.environ.get("SIM_SAB_APIKEY"))
    port = args.port if args.port is not None else (30219 if args.prowlarr else 30216)
    serve(app, args.host, port, args.ssl_certfile, args.ssl_keyfile)


if __name__ == "__main__":
    main()
