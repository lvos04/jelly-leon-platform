"""Kleine Nederlandse statuspagina (``GET /``) met koppelformulier (koppelcode uit de Hub-UI, contract §3).

Alle schermen van het platform staan in de Hub; deze pagina toont alleen status en maakt handmatig koppelen mogelijk.
Beveiliging van het formulier: CSRF (dubbel ingediend token, cookie ``SameSite=Strict``), alleen zolang de dienst niet
gekoppeld is, eenvoudige snelheidsbegrenzing en nooit het tonen van geheimen (alleen de laatste 4 tekens).
"""

from __future__ import annotations

import hmac
import html
import secrets
import threading
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from lifecycle import APP_ID, __version__
from lifecycle.config import ConfigError, normalize_hub_url
from lifecycle.enrollment import EnrollmentError, enroll_and_store
from lifecycle.health import collect_health
from lifecycle.timeutil import iso_z, utcnow

router = APIRouter(include_in_schema=False)

CSRF_COOKIE = "jlp_csrf"
STATUS_NL = {"healthy": "In orde", "degraded": "Beperkt", "down": "Storing", "unknown": "Onbekend"}
CHECK_NL = {
    "database": "Database (ingebouwd)",
    "migrations": "Databaseschema",
    "secrets": "Geheimen (/appdata/secrets)",
    "worker": "Achtergrondproces",
    "hub_link": "Koppeling met de Hub",
}


class AttemptLimiter:
    def __init__(self, limit: int = 5, window_s: float = 900.0) -> None:
        self.limit, self.window_s = limit, window_s
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > self.window_s:
                self._hits.popleft()
            if len(self._hits) >= self.limit:
                return False
            self._hits.append(now)
            return True


limiter = AttemptLimiter()

PAGE = """<!doctype html>
<html lang="nl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jelly Leon Platform — lifecycle-dienst</title>
<style>
:root{{color-scheme:light dark;--bg:#f7f7f8;--fg:#1b1b1f;--muted:#5d5d66;--card:#fff;--line:#d9d9de;--ok:#1d7a3a;--warn:#9a6700;
--bad:#b42318;--unk:#5d5d66}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141417;--fg:#ececf0;--muted:#a3a3ad;--card:#1e1e23;--line:#33333a;
--ok:#4cc27a;--warn:#e3b341;--bad:#ff7b72;--unk:#a3a3ad}}}}
body{{margin:0;font:16px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg)}}
main{{max-width:760px;margin:0 auto;padding:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}}
h1{{font-size:1.4rem;margin:.2rem 0}} h2{{font-size:1.1rem;margin:0 0 .5rem}}
.muted{{color:var(--muted)}} table{{width:100%;border-collapse:collapse}} td,th{{text-align:left;padding:6px 4px;
border-bottom:1px solid var(--line);vertical-align:top}}
.s-healthy{{color:var(--ok)}} .s-degraded{{color:var(--warn)}} .s-down{{color:var(--bad)}} .s-unknown{{color:var(--unk)}}
label{{display:block;margin:.6rem 0 .2rem}} input{{width:100%;box-sizing:border-box;padding:10px;font:inherit;
border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg)}}
button{{margin-top:12px;padding:10px 16px;font:inherit;border-radius:8px;border:1px solid var(--line);cursor:pointer;
min-height:44px}} .err{{color:var(--bad)}} .ok{{color:var(--ok)}} code{{word-break:break-all}}
</style></head><body><main>
<h1>Jelly Leon Platform — lifecycle-dienst</h1>
<p class="muted">App <code>{app_id}</code> · versie {version} · alle schermen staan in de Hub; dit is alleen een
statuspagina. Laatst bijgewerkt: <time datetime="{now}">{now}</time> (UTC).</p>
{message}
<section class="card" aria-labelledby="h-status"><h2 id="h-status">Status: <span class="s-{status}">{status_nl}</span>
</h2><table><thead><tr><th scope="col">Onderdeel</th><th scope="col">Status</th><th scope="col">Toelichting</th>
<th scope="col">Gemeten</th></tr></thead><tbody>{rows}</tbody></table>
<p class="muted">Onbekend betekent: niet gemeten of niet bereikbaar — nooit "in orde".</p></section>
<section class="card" aria-labelledby="h-link"><h2 id="h-link">Koppeling met de Hub</h2>{link}</section>
<section class="card" aria-labelledby="h-fase"><h2 id="h-fase">Wat deze versie wel en niet doet</h2>
<p>Deze versie (fase P0) legt levenscyclus-runs vast en biedt de API en het connectorcontract. Installeren, bijwerken,
terugzetten, verwijderen en verhuizen worden in fase P2 gebouwd; tot dan antwoordt de API eerlijk
<code>capability_unavailable</code> en wordt er <strong>niets</strong> op de NAS uitgevoerd.</p></section>
</main></body></html>"""


def _render(request: Request, message: str = "", status_code: int = 200) -> HTMLResponse:
    health = collect_health(request.app)
    rows = "".join(
        f'<tr><td>{html.escape(CHECK_NL.get(c["name"], c["name"]))}</td>'
        f'<td class="s-{c["status"]}">{STATUS_NL[c["status"]]}</td><td>{html.escape(c["detail"])}</td>'
        f'<td><time datetime="{c["observed_at"]}">{c["observed_at"]}</time></td></tr>'
        for c in health["checks"]
    )
    snap: Any = None
    try:
        request.app.state.hub_link.invalidate()
        snap = request.app.state.hub_link.current()
    except Exception:
        snap = None
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    if snap is not None and snap.state == "gekoppeld":
        link = (
            f'<p class="ok">Gekoppeld met <code>{html.escape(snap.hub_url or "")}</code> '
            f"(via {html.escape(snap.linked_via or '?')}, sinds {html.escape(iso_z(snap.enrolled_at) or '?')}). "
            f"Service-token eindigt op <code>…{html.escape(snap.service_token_last4 or '????')}</code>.</p>"
            "<p class=\"muted\">Ontkoppelen of opnieuw koppelen gaat via de Hub (alleen beheerders).</p>"
        )
    else:
        error = ""
        if snap is not None and snap.last_error:
            error = f'<p class="err">Laatste poging: {html.escape(snap.last_error)}</p>'
        default_url = html.escape((snap.hub_url if snap is not None else None) or "")
        link = (
            "<p>Nog <strong>niet gekoppeld</strong>. Zonder koppeling weigert de API elke aanvraag (alleen deze "
            "statuspagina, <code>/healthz</code> en <code>/readyz</code> werken). Maak in de Hub een koppelcode voor "
            f"<code>{APP_ID}</code> en vul die hieronder in.</p>{error}"
            '<form method="post" action="/koppelen" autocomplete="off">'
            f'<input type="hidden" name="csrf" value="{html.escape(token)}">'
            '<label for="hub_url">Adres van de Hub</label>'
            f'<input id="hub_url" name="hub_url" type="url" required placeholder="http://&lt;NAS-adres&gt;:30400" '
            f'value="{default_url}">'
            '<label for="koppelcode">Koppelcode uit de Hub</label>'
            '<input id="koppelcode" name="koppelcode" required minlength="6" maxlength="128" inputmode="text">'
            "<button type=\"submit\">Koppelen</button></form>"
        )
    status = health["status"]
    body = PAGE.format(
        app_id=APP_ID,
        version=__version__,
        now=iso_z(utcnow()),
        message=message,
        status=status,
        status_nl=STATUS_NL[status],
        rows=rows,
        link=link,
    )
    response = HTMLResponse(body, status_code=status_code, headers={"Cache-Control": "no-store"})
    response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return response


@router.get("/", response_class=HTMLResponse)
def status_page(request: Request) -> Response:
    msg = ""
    if request.query_params.get("gekoppeld") == "1":
        msg = '<p class="ok card" role="status">Koppelen gelukt.</p>'
    return _render(request, msg)


@router.post("/koppelen", response_class=HTMLResponse)
def pair(
    request: Request,
    hub_url: str = Form(..., max_length=512),
    koppelcode: str = Form(..., min_length=6, max_length=128),
    csrf: str = Form(..., max_length=128),
) -> Response:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not cookie or not hmac.compare_digest(cookie, csrf):
        return _render(request, '<p class="err card" role="alert">Formulier verlopen; probeer het opnieuw.</p>', 403)
    request.app.state.hub_link.invalidate()
    if request.app.state.hub_link.current().state == "gekoppeld":
        return _render(
            request, '<p class="err card" role="alert">Al gekoppeld; ontkoppel eerst via de Hub.</p>', 409
        )
    if not limiter.allow():
        return _render(request, '<p class="err card" role="alert">Te veel pogingen; wacht 15 minuten.</p>', 429)
    try:
        url = normalize_hub_url(hub_url)
    except ConfigError as exc:
        return _render(request, f'<p class="err card" role="alert">{html.escape(str(exc))}</p>', 400)
    if not url:
        return _render(request, '<p class="err card" role="alert">Vul het adres van de Hub in.</p>', 400)
    state = request.app.state
    if state.vault is None or state.identity is None:
        return _render(
            request, '<p class="err card" role="alert">Geheimen ontbreken; herstart de app (eerste start).</p>', 503
        )
    try:
        with state.http_client_factory() as client:
            enroll_and_store(
                state.session_factory,
                state.vault,
                state.identity,
                state.settings,
                client,
                hub_url=url,
                kind="koppelcode",
                secret=koppelcode.strip(),
                jwks_cache=state.jwks_cache,
            )
    except EnrollmentError as exc:
        state.hub_link.invalidate()
        return _render(request, f'<p class="err card" role="alert">{html.escape(exc.message)}</p>', 400)
    state.hub_link.invalidate()
    return RedirectResponse("/?gekoppeld=1", status_code=303)
