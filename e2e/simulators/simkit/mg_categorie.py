"""Alleen-lezen MediaGuard-fixture voor de categoriediagnose door MSO."""

import secrets
from importlib import import_module

from simkit import basis_args, serve
from simkit.msomock import met_testbediening


def leessleutel() -> dict[str, str]:
    sleutel = "mgk_" + secrets.token_urlsafe(32)
    vars(import_module("msomocks.mediaguard"))["API_KEYS"] = {sleutel: ("categorieproef", "viewer")}
    return {"apikey": sleutel}


if __name__ == "__main__":
    args = basis_args("MediaGuard-categoriesimulator", 8080, alleen_testnet=True).parse_args()
    serve(met_testbediening("mediaguard", leessleutel), args.host, args.port, args.ssl_certfile, args.ssl_keyfile)
