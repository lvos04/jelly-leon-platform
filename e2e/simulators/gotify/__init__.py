"""Gotify-simulator (alleen tests). Protocol en bronnen: ``e2e/simulators/gotify/README.md``.

Volgt ``gotify/server`` **v3.1.1** (``e6361ca``, ``router/router.go``, ``api/message.go``, ``auth/authentication.go``)
voor wat het platform gebruikt: ``GET /health``, ``GET /version``, ``POST /message`` (applicatietoken via
``X-Gotify-Key``, ``?token=`` of ``Authorization: Bearer``), ``GET /application`` en ``GET /message`` (clienttoken of
Basic-aanmelding) en ``GET /current/user`` (om te controleren dat het standaardaccount admin/admin weg is).
"""

from __future__ import annotations

import base64
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install

VERSIE = "3.1.1"
COMMIT = "e6361caa512547052b1013dbf4544e6d7ff32604"

FOUTEN = {
    **ALGEMENE_FOUTEN,
    "db_storing": "Database onbereikbaar: /health → 500 (orange/red), /message → 500.",
    "tokens_ingetrokken": "Alle applicatie- en clienttokens zijn ongeldig (401), zoals na intrekken in Gotify.",
}


def _nu() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass
class App:
    id: int
    token: str
    name: str
    description: str = ""
    default_priority: int = 0
    user_id: int = 1

    def view(self) -> dict[str, Any]:
        return {"id": self.id, "token": self.token, "name": self.name, "description": self.description,
                "internal": False, "image": "static/defaultapp.png", "defaultPriority": self.default_priority,
                "lastUsed": None}


@dataclass
class State:
    """Begintoestand = net geïnstalleerde Gotify: admin/admin bestaat nog (zoals de echte image zonder
    GOTIFY_DEFAULTUSER_PASS), plus de applicaties die het platform per bron aanmaakt (I13-13-02)."""

    admin_wachtwoord: str = "admin"
    apps: dict[str, App] = field(default_factory=dict)
    client_tokens: dict[str, int] = field(default_factory=dict)
    berichten: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    next_msg: int = 1

    def seed(self) -> None:
        self.admin_wachtwoord = "admin"
        self.apps = {}
        for i, naam in enumerate(("hub", "uptime-kuma", "backup"), start=1):
            tok = "A" + secrets.token_urlsafe(10)[:14]
            self.apps[tok] = App(i, tok, naam, f"Meldingen van {naam} (simulator)")
        self.client_tokens = {"C" + secrets.token_urlsafe(10)[:14]: 1}
        self.berichten = []
        self.next_msg = 1


def _fout(status: int, description: str) -> dict[str, Any]:
    names = {400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error",
             502: "Bad Gateway", 503: "Service Unavailable"}
    return {"error": names.get(status, "Error"), "errorCode": status, "errorDescription": description}


def create_app(state: State | None = None) -> FastAPI:
    st = state or State()
    st.seed()
    fouten = Fouten(dict(FOUTEN))
    log = Verzoeklog()
    app = FastAPI(title="Gotify-simulator", version=VERSIE, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sim = st
    app.state.fouten = fouten
    app.state.log = log

    def token_van(request: Request) -> str | None:
        if tok := request.headers.get("x-gotify-key"):
            return tok
        if tok := request.query_params.get("token"):
            return tok
        auth = request.headers.get("authorization", "")
        if auth.startswith("Bearer "):
            return auth[len("Bearer "):]
        return None

    def basic_ok(request: Request) -> bool:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        return user == "admin" and secrets.compare_digest(pw, st.admin_wachtwoord)

    def client_ok(request: Request) -> bool:
        if fouten.is_aan("tokens_ingetrokken"):
            return False
        tok = token_van(request)
        return (tok is not None and tok in st.client_tokens) or basic_ok(request)

    def geweigerd() -> JSONResponse:
        return JSONResponse(_fout(401, "you need to provide a valid access token or user credentials to access this "
                                       "api"), status_code=401)

    @app.get("/health")
    def health() -> JSONResponse:
        if fouten.is_aan("db_storing"):
            return JSONResponse({"health": "orange", "database": "red"}, status_code=500)
        return JSONResponse({"health": "green", "database": "green"})

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": VERSIE, "commit": COMMIT, "buildDate": "2026-10-01T00:00:00Z"}

    @app.post("/message")
    async def create_message(request: Request) -> JSONResponse:
        tok = token_van(request)
        app_obj = None if fouten.is_aan("tokens_ingetrokken") else st.apps.get(tok or "")
        is_client = client_ok(request)
        if app_obj is None and not is_client:
            return geweigerd()
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(_fout(400, "invalid JSON"), status_code=400)
        if not isinstance(body, dict) or not str(body.get("message") or "").strip():
            return JSONResponse(_fout(400, "Field 'message' is required"), status_code=400)
        if app_obj is None:
            appid = body.get("appid")
            if not appid:
                return JSONResponse(_fout(400, "appid is required when not authenticating with an application "
                                               "token"), status_code=400)
            app_obj = next((a for a in st.apps.values() if a.id == appid), None)
            if app_obj is None:
                return JSONResponse(_fout(400, "appid not found"), status_code=400)
        if fouten.is_aan("db_storing"):
            return JSONResponse(_fout(500, "database is locked"), status_code=500)
        prio = body.get("priority")
        if prio is not None and not isinstance(prio, int):
            return JSONResponse(_fout(400, "Field 'priority' must be an integer"), status_code=400)
        with st.lock:
            msg = {"id": st.next_msg, "appid": app_obj.id, "message": str(body["message"]),
                   "title": str(body.get("title") or "").strip() or app_obj.name,
                   "priority": prio if prio is not None else app_obj.default_priority,
                   "extras": body.get("extras") if isinstance(body.get("extras"), dict) else None, "date": _nu()}
            if msg["extras"] is None:
                del msg["extras"]
            st.next_msg += 1
            st.berichten.append(msg)
        return JSONResponse(msg)

    @app.get("/application")
    def applications(request: Request) -> JSONResponse:
        if not client_ok(request):
            return geweigerd()
        return JSONResponse([a.view() for a in st.apps.values()])

    @app.get("/message")
    def messages(request: Request, limit: int = 100) -> JSONResponse:
        if not client_ok(request):
            return geweigerd()
        items = list(reversed(st.berichten))[: max(1, min(limit, 200))]
        return JSONResponse({"messages": items, "paging": {"size": len(items), "limit": limit, "since": 0}})

    @app.get("/current/user")
    def current_user(request: Request) -> JSONResponse:
        if not client_ok(request):
            return geweigerd()
        return JSONResponse({"id": 1, "name": "admin", "admin": True})

    def sleutels() -> dict[str, Any]:
        return {"applicaties": {a.name: a.token for a in st.apps.values()}, "client": next(iter(st.client_tokens)),
                "admin_wachtwoord_is_standaard": st.admin_wachtwoord == "admin"}

    @app.post("/_sim/admin-wachtwoord", include_in_schema=False)
    async def wachtwoord(request: Request) -> dict[str, bool]:
        """Zoals de gebruiker na installatie in de Gotify-UI doet (I13-13-01): standaardwachtwoord vervangen."""
        body = await request.json()
        nieuw = str(body.get("wachtwoord") or "")
        if len(nieuw) < 12:
            return {"gewijzigd": False}
        st.admin_wachtwoord = nieuw
        return {"gewijzigd": True}

    install(app, fouten=fouten, log=log, reset=st.seed, fout_body=_fout, sleutels=sleutels)
    return app
