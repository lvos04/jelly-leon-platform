"""SABnzbd uit de MSO-mockimage met afzonderlijke, omkeerbare providerstoringen."""

from __future__ import annotations

import secrets
from typing import Literal
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool
from simkit.msomock import met_testbediening

MODI = {"version", "status", "fullstatus", "warnings", "queue", "history", "get_config", "get_cats", "server_stats",
        "config", "set_config", "del_config", "set_config_default", "retry", "get_files", "addurl", "addfile"}


class Serveruitval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server: str
    uitgevallen: StrictBool
    reden: Literal["aanmelding", "verlopen"] = "aanmelding"


async def handeling(request: Request) -> dict[str, str]:
    if request.url.path != "/api":
        return {}
    params = dict(request.query_params)
    if request.method == "POST" and request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded"):
        params.update(parse_qsl((await request.body()).decode(errors="replace")))
    # Alleen vaste protocolwaarden mogen in het log (SAB gebruikt GET ook voor schrijven).
    mode = params.get("mode", "")
    details = {"mode": mode if mode in MODI else "onbekend"}
    if "section" in params:
        section = params["section"]
        details["section"] = section if section in {"servers", "categories", "misc"} else "onbekend"
    if mode in {"history", "queue", "config"} and "name" in params:
        name = params["name"]
        details["name"] = name if name in {"delete", "purge", "test_server"} else "onbekend"
    return details


def serverbediening(app: FastAPI) -> None:
    @app.post("/_sim/serveruitval")
    async def uitval(body: Serveruitval) -> dict[str, bool]:
        sab = app.state.bron.state.sab
        with sab.lock:
            if not any(s["name"] == body.server for s in sab.servers):
                raise HTTPException(404, "Onbekende testserver")
            if body.uitgevallen:
                melding = "482 Account expired" if body.reden == "verlopen" else "502 Authentication Failed"
                sab.set_server_error(body.server, f"Server {body.server} reported: {melding}")
                sab.server_inactive.add(body.server)
            else:
                sab.fix_account(body.server)
                sab.server_inactive.discard(body.server)
        return {"uitgevallen": body.uitgevallen}


def create_app() -> FastAPI:
    return met_testbediening("sabnzbd", lambda: {"apikey": secrets.token_hex(16)}, serverbediening, handeling)
