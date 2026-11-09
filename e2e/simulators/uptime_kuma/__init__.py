"""Uptime Kuma-simulator (alleen tests). Protocol en bronnen: ``e2e/simulators/uptime-kuma/README.md``.

Volgt ``louislam/uptime-kuma`` **2.5.9** (``df601c5``) voor de HTTP-delen die de Hub gebruikt (B09-002):
``GET /metrics`` (Prometheus-tekst met ``app_version`` voor versiedetectie; Basic-aanmelding met een API-sleutel
``uk<id>_<sleutel>`` als wachtwoord, of met gebruikersnaam/wachtwoord als API-sleutels uit staan),
``GET /api/status-page/<slug>``, ``GET /api/status-page/heartbeat/<slug>``, ``GET /api/entry-page`` en
``GET|POST /api/push/<pushToken>`` (push-monitor als dead-man's switch). De Socket.IO-beheer-API (monitors aanmaken,
instellingen) wordt **niet** nagebootst: de Hub gebruikt die niet.
"""

from __future__ import annotations

import base64
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from simkit import ALGEMENE_FOUTEN, Fouten, Verzoeklog, install

VERSIE = "2.5.9"
UP, DOWN, PENDING, MAINTENANCE = 1, 0, 2, 3
MAX_PING_MS = 100_000_000_000  # server/routers/api-router.js: past in BIGINT en FLOAT(20,2)
_GETAL = re.compile(r"^\s*[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
# De push-token ís het geheim (wie hem kent, kan de dead-man's switch op 'up' houden): nooit in het verzoeklog.
GEHEIME_PADEN = ((re.compile(r"^/api/push/[^/]+"), "/api/push/<push-token>"),)

FOUTEN = {
    **ALGEMENE_FOUTEN,
    "sqlite_busy": "Status-page-routes antwoorden 503 {status: 'fail', msg: 'SQLITE_BUSY: database is locked'}.",
    "metrics_weigeren": "/metrics weigert elke aanmelding (401), zoals na het intrekken van de API-sleutel.",
    "oude_versie": "Versie 1.23.16 (geen API-sleutels: /metrics alleen met gebruikersnaam/wachtwoord).",
}


def _nu() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


@dataclass
class Monitor:
    id: int
    name: str
    type: str
    url: str | None
    hostname: str | None
    port: int | None
    status: int = UP
    response_ms: int = 42
    publiek: bool = True
    push_token: str | None = None
    hartslagen: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class State:
    """Begintoestand: één statuspagina ``platform`` met monitors zoals I08-08-03 ze voorschrijft (adressen zijn
    simulatorwaarden onder ``.invalid``, nooit echte NAS-adressen) en één push-monitor voor de Hub-hartslag."""

    api_key_id: int = 1
    api_key: str = ""
    gebruiker: tuple[str, str] = ("beheer", "")
    api_keys_aan: bool = True
    monitors: dict[int, Monitor] = field(default_factory=dict)
    slug: str = "platform"
    lock: threading.Lock = field(default_factory=threading.Lock)

    def seed(self) -> None:
        self.api_key = secrets.token_urlsafe(24)
        self.gebruiker = ("beheer", secrets.token_urlsafe(12))
        self.api_keys_aan = True
        self.monitors = {}
        defs = [
            ("Hub", "http", "http://hub.sim.invalid:30400/healthz", None, None),
            ("TrueNAS-webinterface", "http", "https://truenas.sim.invalid/", None, None),
            ("MediaGuard", "http", "http://mediaguard.sim.invalid:30080/", None, None),
            ("MSO", "http", "http://mso.sim.invalid:38081/", None, None),
            ("my_house", "http", "http://myhouse.sim.invalid:8088/", None, None),
            ("Jelly Leon", "http", "http://jellyleon.sim.invalid:30460/health", None, None),
            ("TrueNAS-HTTPS-poort", "port", None, "truenas.sim.invalid", 443),
        ]
        for i, (naam, soort, url, host, port) in enumerate(defs, start=1):
            self.monitors[i] = Monitor(i, naam, soort, url, host, port)
        push = Monitor(len(defs) + 1, "Hub-hartslag", "push", None, None, None, push_token=secrets.token_urlsafe(16))
        self.monitors[push.id] = push
        for m in self.monitors.values():
            m.hartslagen = [{"status": m.status, "time": _nu(), "msg": "", "ping": m.response_ms}]


def _fout(status: int, msg: str) -> dict[str, Any]:
    return {"status": "fail", "msg": msg}


def _ping(waarde: str | None) -> float | None:
    """Zoals ``parseFloat(request.query.ping) || null``: numeriek begin van de tekst; NaN en 0 worden null."""
    m = _GETAL.match(waarde or "")
    return (float(m.group(0)) or None) if m else None


def _label(waarde: Any) -> str:
    return "" if waarde is None else str(waarde).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def create_app(state: State | None = None) -> FastAPI:
    st = state or State()
    st.seed()
    fouten = Fouten(dict(FOUTEN))
    log = Verzoeklog(geheime_paden=GEHEIME_PADEN)
    app = FastAPI(title="Uptime Kuma-simulator", version=VERSIE, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.sim = st
    app.state.fouten = fouten
    app.state.log = log

    def versie() -> str:
        return "1.23.16" if fouten.is_aan("oude_versie") else VERSIE

    def metrics_toegang(request: Request) -> bool:
        if fouten.is_aan("metrics_weigeren"):
            return False
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return False
        if st.api_keys_aan and not fouten.is_aan("oude_versie"):
            # server/auth.js verifyAPIKey: "uk" + id + "_" + sleutel; de gebruikersnaam telt niet
            verwacht = f"uk{st.api_key_id}_{st.api_key}"
            return secrets.compare_digest(pw, verwacht)
        return user == st.gebruiker[0] and secrets.compare_digest(pw, st.gebruiker[1])

    @app.get("/metrics")
    def metrics(request: Request) -> Response:
        if not metrics_toegang(request):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Authorization Required"'})
        v = versie()
        major, minor, patch = v.split(".")
        regels = [
            "# HELP app_version The service version by package.json",
            "# TYPE app_version gauge",
            f'app_version{{version="{v}",major="{major}",minor="{minor}",patch="{patch}"}} 1',
            "# HELP monitor_status Monitor Status (1 = UP, 0= DOWN, 2= PENDING, 3= MAINTENANCE)",
            "# TYPE monitor_status gauge",
        ]
        with st.lock:
            monitors = list(st.monitors.values())

        def labels(m: Monitor) -> str:
            return (f'monitor_id="{m.id}",monitor_name="{_label(m.name)}",monitor_type="{m.type}",'
                    f'monitor_url="{_label(m.url or "https://")}",monitor_hostname="{_label(m.hostname or "null")}",'
                    f'monitor_port="{_label(m.port or "null")}"')

        regels += [f"monitor_status{{{labels(m)}}} {m.status}" for m in monitors]
        regels += ["# HELP monitor_response_time Monitor Response Time (ms)", "# TYPE monitor_response_time gauge"]
        regels += [f"monitor_response_time{{{labels(m)}}} {m.response_ms if m.status == UP else -1}" for m in monitors]
        regels += ["# HELP monitor_cert_days_remaining The number of days remaining until the certificate expires",
                   "# TYPE monitor_cert_days_remaining gauge"]
        regels += [f"monitor_cert_days_remaining{{{labels(m)}}} 60" for m in monitors if (m.url or "").startswith(
            "https://")]
        return PlainTextResponse("\n".join(regels) + "\n", media_type="text/plain; version=0.0.4; charset=utf-8")

    def status_page_monitors() -> list[Monitor]:
        return [m for m in st.monitors.values() if m.publiek]

    @app.get("/api/entry-page")
    def entry_page() -> dict[str, str | None]:
        # server.js: de instelling 'entryPage' bestaat pas als de beheerder hem kiest; tot dan null (gemeten met de
        # echte image 2.5.9, ook na een herstart; tools/upstream_rooktest.py)
        return {"type": "entryPage", "entryPage": None}

    @app.get("/api/status-page/heartbeat/{slug}")
    def heartbeat(slug: str) -> JSONResponse:
        if fouten.is_aan("sqlite_busy"):
            return JSONResponse(_fout(503, "SQLITE_BUSY: database is locked"), status_code=503)
        if slug.lower() != st.slug:
            # status-page-router.js: slugToID → onbekend → 'not found' → 404
            return JSONResponse(_fout(404, "Status Page Not Found"), status_code=404)
        with st.lock:
            ms = status_page_monitors()
            return JSONResponse({
                "heartbeatList": {str(m.id): m.hartslagen[-100:] for m in ms},
                "uptimeList": {f"{m.id}_24": (1.0 if m.status == UP else 0.0) for m in ms},
            })

    @app.get("/api/status-page/{slug}")
    def status_page(slug: str) -> JSONResponse:
        if fouten.is_aan("sqlite_busy"):
            return JSONResponse(_fout(503, "SQLITE_BUSY: database is locked"), status_code=503)
        if slug.lower() != st.slug:
            return JSONResponse(_fout(404, "Status Page Not Found"), status_code=404)
        with st.lock:
            lijst = [{"id": m.id, "name": m.name, "sendUrl": 0, "type": m.type} for m in status_page_monitors()]
        return JSONResponse({
            "config": {"slug": st.slug, "title": "Platform", "description": None, "icon": "/icon.svg",
                       "autoRefreshInterval": 300, "theme": "auto", "published": True, "showTags": False,
                       "customCSS": "", "footerText": None, "showPoweredBy": True, "googleAnalyticsId": None,
                       "showCertificateExpiry": False},
            "incident": None,
            "publicGroupList": [{"id": 1, "name": "Diensten", "weight": 1, "monitorList": lijst}],
            "maintenanceList": [],
        })

    @app.api_route("/api/push/{push_token}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def push(push_token: str, status: str = "up", msg: str = "OK", ping: str | None = None) -> JSONResponse:
        # api-router.js: elke fout (ongeldige ping, onbekende of inactieve token) → 404 {ok:false, msg}
        waarde = _ping(ping)
        if waarde is not None and not 0 <= waarde <= MAX_PING_MS:
            return JSONResponse({"ok": False, "msg": f"Invalid ping value. Must be between 0 and {MAX_PING_MS} ms."},
                                status_code=404)
        with st.lock:
            m = next((x for x in st.monitors.values() if x.push_token and secrets.compare_digest(x.push_token,
                                                                                               push_token)), None)
            if m is None:
                return JSONResponse({"ok": False, "msg": "Monitor not found or not active."}, status_code=404)
            m.status = UP if status == "up" else DOWN
            # msg staat in de echte heartbeat, maar de publieke statuspagina toont hem nooit (toPublicJSON: msg "")
            m.hartslagen.append({"status": m.status, "time": _nu(), "msg": "",
                                 "ping": None if waarde is None else (int(waarde) if waarde.is_integer() else waarde)})
        return JSONResponse({"ok": True})

    @app.post("/_sim/monitor/{monitor_id}", include_in_schema=False)
    async def zet_monitor(monitor_id: int, request: Request) -> JSONResponse:
        """Testbediening: monitorstatus (1 up, 0 down, 2 pending, 3 onderhoud) en responstijd."""
        body = await request.json()
        with st.lock:
            m = st.monitors.get(monitor_id)
            if m is None:
                return JSONResponse({"fout": "onbekende monitor"}, status_code=404)
            m.status = int(body.get("status", m.status))
            m.response_ms = int(body.get("response_ms", m.response_ms))
            m.hartslagen.append({"status": m.status, "time": _nu(), "msg": "", "ping": m.response_ms})
        return JSONResponse({"id": m.id, "status": m.status})

    def sleutels() -> dict[str, Any]:
        push_tokens = {m.name: m.push_token for m in st.monitors.values() if m.push_token}
        return {"api_key": f"uk{st.api_key_id}_{st.api_key}", "gebruiker": list(st.gebruiker), "slug": st.slug,
                "push_tokens": push_tokens}

    install(app, fouten=fouten, log=log, reset=st.seed, fout_body=_fout, sleutels=sleutels)
    return app
