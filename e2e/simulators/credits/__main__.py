"""Start het lokale eindpunt voor inbegrepen testtegoed."""

from simkit import basis_args, serve

from . import create_app


def main() -> None:
    parser = basis_args("Credits-simulator", 30224, alleen_testnet=True)
    parser.add_argument("--eenheden", type=int, default=0)
    args = parser.parse_args()
    serve(create_app(args.eenheden), args.host, args.port, args.ssl_certfile, args.ssl_keyfile)


if __name__ == "__main__":
    main()
