"""Beszel-hub-simulator (alleen tests). Protocol en bronnen: ``e2e/simulators/beszel/README.md``.

Volgt ``henrygd/beszel`` **v0.21.1** (``e2ed147``; PocketBase v0.40.5) voor de alleen-lezen-delen die de Hub gebruikt
(B09-002, I07-07-06): ``GET /api/health``, ``POST /api/collections/users/auth-with-password``,
``POST /api/collections/users/auth-refresh``, ``GET /api/beszel/first-run``, ``POST /api/beszel/create-user``,
``GET /api/beszel/info`` (publieke sleutel + versie), en de PocketBase-lijsten ``systems``, ``system_stats``,
``alerts`` en ``containers`` (``GET /api/collections/<naam>/records`` met ``page``/``perPage``/``filter``/``sort``).
De agentverbinding (SSH/WebSocket) wordt **niet** nagebootst; de toestand van de agent is testbediening.

Bewust nagebootst: een lijst zonder (geldig) token geeft **200 met een lege lijst** (PocketBase-listRule), geen 401 —
een client mag "leeg" dus nooit als "geen systemen" lezen zonder eerst aangemeld te zijn.
"""

from __future__ import annotations

import base64
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install

VERSIE = "0.21.1"
TOKEN_TTL_S = 7 * 24 * 3600  # PocketBase: auth-token standaard 7 dagen (hier ingekort met fout token_verlopen)

FOUTEN = {
    **ALGEMENE_FOUTEN,
    "agent_down": "Het systeem staat op 'down' en er komen geen nieuwe metingen (laatste meting veroudert).",
    "verouderd": "Laatste meting is 'waarde' minuten oud (standaard 30) terwijl het systeem 'up' blijft.",
    "geen_arc": "De agent levert geen ZFS-ARC (mz/mem[3] ontbreken), zoals als arcstats niet zichtbaar is.",
    "token_verlopen": "Elk eerder uitgegeven token is ongeldig (401 bij auth-refresh, lege lijsten).",
    "dubbele_host": "Een tweede systeemrecord met dezelfde naam/host (agent zonder vaste vingerafdruk, I07-07-06).",
}

_ID = re.compile(r"^[a-z0-9]{15}$")
_FILTER_SYSTEM = re.compile(r"system\s*=\s*['\"]([a-z0-9]{15})['\"]")
_FILTER_TYPE = re.compile(r"type\s*=\s*['\"](1m|10m|20m|120m|480m)['\"]")


def _pbtijd(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def _pbid() -> str:
    return "".join(secrets.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(15))


def _fout(status: int, message: str) -> dict[str, Any]:
    return {"data": {}, "message": message, "status": status}


@dataclass
class State:
    """Begintoestand: verse installatie (nog géén gebruiker → first-run) met één systeem ``truenas`` waarvan de
    agent elke minuut een meting levert. Waarden zijn simulatorwaarden, geen metingen van een echte NAS."""

    gebruikers: dict[str, dict[str, Any]] = field(default_factory=dict)
    tokens: dict[str, tuple[str, float]] = field(default_factory=dict)
    systemen: dict[str, dict[str, Any]] = field(default_factory=dict)
    publieke_sleutel: str = ""
    begin: datetime = field(default_factory=lambda: datetime.now(UTC))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def seed(self) -> None:
        self.gebruikers = {}
        self.tokens = {}
        self.begin = datetime.now(UTC).replace(microsecond=0)
        sleutel = base64.b64encode(b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + secrets.token_bytes(32)).decode()
        self.publieke_sleutel = f"ssh-ed25519 {sleutel}"
        sid = _pbid()
        self.systemen = {sid: self._systeem(sid, "truenas")}

    def _systeem(self, sid: str, naam: str) -> dict[str, Any]:
        t = _pbtijd(self.begin)
        return {"collectionId": "2hz5ncl8tizk5nx", "collectionName": "systems", "id": sid, "name": naam,
                "status": "up", "host": "truenas.sim.invalid", "port": "45876",
                "info": {"u": 86400, "cpu": 7.5, "mp": 41.2, "dp": 12.0, "v": VERSIE, "la": [0.4, 0.5, 0.6],
                         "t": 8, "ct": 1, "bb": 1024},
                "users": [], "created": t, "updated": t}


def create_app(state: State | None = None) -> FastAPI:
    st = state or State()
    st.seed()
    fouten = Fouten(dict(FOUTEN))
    log = Verzoeklog()
    app = FastAPI(title="Beszel-simulator", version=VERSIE, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sim = st
    app.state.fouten = fouten
    app.state.log = log

    def gebruiker_van(request: Request) -> dict[str, Any] | None:
        auth = request.headers.get("authorization", "")
        tok = auth[len("Bearer "):] if auth.startswith("Bearer ") else auth
        if not tok or fouten.is_aan("token_verlopen"):
            return None
        with st.lock:
            item = st.tokens.get(tok)
            if item is None or item[1] < time.time():
                return None
            return st.gebruikers.get(item[0])

    def nieuw_token(email: str) -> str:
        tok = "pb_sim_" + secrets.token_urlsafe(32)
        st.tokens[tok] = (email, time.time() + TOKEN_TTL_S)
        return tok

    def record(g: dict[str, Any]) -> dict[str, Any]:
        return {"collectionId": "_pb_users_auth_", "collectionName": "users", "id": g["id"], "email": g["email"],
                "emailVisibility": False, "verified": True, "role": g["role"], "username": g["email"].split("@")[0],
                "created": g["created"], "updated": g["created"]}

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"message": "API is healthy.", "code": 200, "data": {}}

    @app.get("/api/beszel/first-run")
    def first_run() -> dict[str, bool]:
        return {"firstRun": not st.gebruikers}

    @app.post("/api/beszel/create-user")
    async def create_user(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"err": "Bad request"}, status_code=400)
        email, wachtwoord = str(body.get("email") or ""), str(body.get("password") or "")
        if not email or not wachtwoord:
            return JSONResponse({"err": "Bad request"}, status_code=400)
        with st.lock:
            if st.gebruikers:  # internal/users/users.go: bootstrap alleen zonder gebruikers
                return JSONResponse({"err": "Forbidden"}, status_code=403)
            st.gebruikers[email] = {"id": _pbid(), "email": email, "wachtwoord": wachtwoord, "role": "admin",
                                    "created": _pbtijd(datetime.now(UTC))}
            for s in st.systemen.values():
                s["users"] = [st.gebruikers[email]["id"]]
        return JSONResponse({"msg": "User created"})

    @app.post("/api/collections/users/auth-with-password")
    async def auth_with_password(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse(_fout(400, "Failed to authenticate."), status_code=400)
        with st.lock:
            g = st.gebruikers.get(str(body.get("identity") or ""))
            if g is None or not secrets.compare_digest(str(body.get("password") or ""), g["wachtwoord"]):
                return JSONResponse(_fout(400, "Failed to authenticate."), status_code=400)
            return JSONResponse({"token": nieuw_token(g["email"]), "record": record(g)})

    @app.post("/api/collections/users/auth-refresh")
    def auth_refresh(request: Request) -> JSONResponse:
        g = gebruiker_van(request)
        if g is None:
            return JSONResponse(_fout(401, "The request requires valid record authorization token."),
                                status_code=401)
        with st.lock:
            return JSONResponse({"token": nieuw_token(g["email"]), "record": record(g)})

    @app.get("/api/beszel/info")
    @app.get("/api/beszel/getkey")
    def info(request: Request) -> JSONResponse:
        if gebruiker_van(request) is None:
            return JSONResponse(_fout(401, "The request requires valid record authorization token."),
                                status_code=401)
        return JSONResponse({"key": st.publieke_sleutel, "v": VERSIE, "cu": False})

    def stats_voor(sid: str, soort: str, nu: datetime) -> list[dict[str, Any]]:
        stap = {"1m": 1, "10m": 10, "20m": 20, "120m": 120, "480m": 480}[soort]
        laatste = nu
        if fouten.is_aan("agent_down"):
            laatste = nu - timedelta(minutes=45)
        elif f := fouten.actief.get("verouderd"):
            laatste = nu - timedelta(minutes=float(f.waarde or 30))
        uit: list[dict[str, Any]] = []
        for i in range(60):
            t = (laatste - timedelta(minutes=stap * i)).replace(second=0, microsecond=0)
            if t < st.begin - timedelta(days=1):
                break
            s: dict[str, Any] = {"cpu": round(5 + (i % 7) * 1.5, 2), "m": 64.0, "mu": 26.3, "mp": 41.1, "mb": 18.2,
                                 "d": 32.0, "du": 3.8, "dp": 11.9, "la": [0.4, 0.5, 0.6],
                                 "t": {"cpu_package": 47.0, "nvme0": 38.0},
                                 "mem": [68719476736, 28239986278, 19541180006, 21474836480]}
            if not fouten.is_aan("geen_arc"):
                s["mz"] = 20.0
            else:
                s["mem"] = [*s["mem"][:3], 0]
            ts = _pbtijd(t)
            uit.append({"collectionId": "ej9oowivz8b2mht", "collectionName": "system_stats",
                        "id": f"{sid[:9]}{i:06d}", "system": sid, "type": soort, "stats": s, "created": ts,
                        "updated": ts})
        return uit

    def lijst(request: Request, items: list[dict[str, Any]]) -> JSONResponse:
        q = request.query_params
        try:
            page = max(1, int(q.get("page", "1")))
            per_page = max(1, min(int(q.get("perPage", "30")), 1000))  # PocketBase: max 1000
        except ValueError:
            return JSONResponse(_fout(400, "Something went wrong while processing your request."), status_code=400)
        sort = q.get("sort", "")
        if sort.lstrip("-") == "created":
            items = sorted(items, key=lambda r: r["created"], reverse=sort.startswith("-"))
        totaal = len(items)
        start = (page - 1) * per_page
        return JSONResponse({"page": page, "perPage": per_page, "totalItems": totaal,
                             "totalPages": (totaal + per_page - 1) // per_page,
                             "items": items[start: start + per_page]})

    @app.get("/api/collections/{collectie}/records")
    def records(collectie: str, request: Request) -> JSONResponse:
        if collectie not in ("systems", "system_stats", "alerts", "containers", "container_stats"):
            return JSONResponse(_fout(404, "Missing collection context."), status_code=404)
        g = gebruiker_van(request)
        if g is None:  # listRule niet voldaan → lege lijst (geen 401)
            return lijst(request, [])
        nu = datetime.now(UTC)
        filt = request.query_params.get("filter", "")
        with st.lock:
            systemen = [s for s in st.systemen.values() if g["id"] in s["users"]]
            if fouten.is_aan("dubbele_host") and systemen:
                kopie = dict(systemen[0])
                kopie["id"] = systemen[0]["id"][::-1]
                kopie["status"] = "down"
                systemen = [*systemen, kopie]
            if fouten.is_aan("agent_down"):
                systemen = [{**s, "status": "down"} for s in systemen]
            if collectie == "systems":
                return lijst(request, [{**s, "updated": _pbtijd(nu)} for s in systemen])
            if collectie in ("alerts", "containers", "container_stats"):
                return lijst(request, [])  # geen alerts ingesteld; containers vereisen de Docker-socket (verboden)
            m_sys, m_type = _FILTER_SYSTEM.search(filt), _FILTER_TYPE.search(filt)
            ids = [s["id"] for s in systemen if not m_sys or s["id"] == m_sys.group(1)]
        items = [r for sid in ids for r in stats_voor(sid, m_type.group(1) if m_type else "1m", nu)]
        return lijst(request, items)

    @app.get("/api/collections/{collectie}/records/{record_id}")
    def een_record(collectie: str, record_id: str, request: Request) -> JSONResponse:
        g = gebruiker_van(request)
        with st.lock:
            s = st.systemen.get(record_id)
            if collectie != "systems" or g is None or s is None or g["id"] not in s["users"] or not _ID.match(
                    record_id):
                return JSONResponse(_fout(404, "The requested resource wasn't found."), status_code=404)
            return JSONResponse(s)

    def sleutels() -> dict[str, Any]:
        return {"publieke_sleutel": st.publieke_sleutel, "systemen": list(st.systemen)}

    @app.post("/_sim/systeem", include_in_schema=False)
    async def systeem(request: Request) -> JSONResponse:
        """Testbediening: extra systeem toevoegen (naam)."""
        body = await request.json()
        sid = _pbid()
        with st.lock:
            s = st._systeem(sid, str(body.get("naam") or "extra"))
            s["users"] = [g["id"] for g in st.gebruikers.values()]
            st.systemen[sid] = s
        return JSONResponse({"id": sid})

    install(app, fouten=fouten, log=log, reset=st.seed, fout_body=_fout, sleutels=sleutels)
    return app
