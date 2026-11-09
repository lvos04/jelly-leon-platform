"""Kleine Nederlandse statuspagina (``GET /``) met koppelformulier (contract 1.2 §3.7).

Alle schermen van het platform staan in de Hub; deze pagina toont alleen status en maakt handmatig koppelen mogelijk.

- Het koppelformulier bestaat **alleen zolang de dienst nog nooit gekoppeld is** (``first_linked_at`` leeg). Daarna
  toont de pagina alleen de status (witte lijst §3.5) en "ontkoppelen of opnieuw koppelen gaat via de Hub". Geen
  ontkoppelen, geen lokale ontkoppelcode en geen herkoppelen na intrekking op deze pagina.
- Velden: Hub-adres (zelfde regels als ``HUB_URL``), koppelcode (12 tekens Crockford) en **Hub-vingerafdruk**
  (verplicht, ``XXXX-XXXX-XXXX-XXXX``).
- CSRF (dubbel ingediend token in een cookie ``SameSite=Strict``, HttpOnly) én ``Origin``-controle. De pagina zet
  ``Referrer-Policy: same-origin`` zodat de browser bij het versturen de echte ``Origin`` meestuurt.
- Max. 5 pogingen per 15 min per bron-IP (adres van de TCP-verbinding; ``X-Forwarded-For`` wordt nooit gebruikt) én
  max. 20 per 15 min voor de hele dienst; telling in het geheugen (één API-proces). Overschrijding → vaste melding,
  geen verzoek naar de Hub.
- Het Hub-adres van een **mislukte** poging wordt nergens bewaard.
"""

from __future__ import annotations

import hmac
import html
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from lifecycle import APP_ID, __version__
from lifecycle.config import ConfigError, normalize_hub_url
from lifecycle.enrollment import EnrollmentError, HubSignals, confirm, enroll_once, mark_failed
from lifecycle.health import collect_health
from lifecycle.hublink import ERROR_MESSAGES_NL, HubLinkCorrupt, Link
from lifecycle.keys import normalize_fingerprint, normalize_pairing_code
from lifecycle.timeutil import iso_z, utcnow

router = APIRouter(include_in_schema=False)

CSRF_COOKIE = "jlp_csrf"
KOPPELCODE_MAX = 32
STATUS_NL = {"healthy": "In orde", "degraded": "Beperkt", "down": "Storing", "unknown": "Onbekend"}
CHECK_NL = {
    "database": "Database (ingebouwd)",
    "migrations": "Databaseschema",
    "secrets": "Geheimen (/appdata/secrets)",
    "worker": "Achtergrondproces",
    "hub_link": "Koppeling met de Hub",
}
VIA_NL = {"token": "eenmalig token", "koppelcode": "koppelcode"}
MSG = {
    "csrf": "Formulier verlopen of van een andere pagina; laad de pagina opnieuw en probeer het nog eens.",
    "ever_linked": "Deze dienst is al eens gekoppeld. Ontkoppelen of opnieuw koppelen gaat via de Hub.",
    "pending": "Er loopt al een koppelpoging; wacht tot die klaar is.",
    "rate": "Te veel koppelpogingen; wacht 15 minuten en probeer het dan opnieuw.",
    "url_missing": "Vul het adres van de Hub in.",
    "code": "De koppelcode moet 12 tekens zijn (XXXX-XXXX-XXXX), zoals de Hub-UI hem toont.",
    "fingerprint": "De vingerafdruk moet 16 tekens zijn (XXXX-XXXX-XXXX-XXXX), zoals de Hub-UI hem toont.",
    "secrets": "Geheimen ontbreken; herstart de app (eerste start).",
    "corrupt": "Het koppelbestand is onleesbaar. Opnieuw koppelen gaat via de Hub (nieuw token in het formulier).",
}


class PairingLimiter:
    """Max. ``per_source`` pogingen per bron-IP en ``total`` voor de hele dienst per ``window_s`` (§3.7)."""

    def __init__(self, per_source: int = 5, total: int = 20, window_s: float = 900.0) -> None:
        self.per_source, self.total, self.window_s = per_source, total, window_s
        self._by_source: defaultdict[str, deque[float]] = defaultdict(deque)
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, hits: deque[float], now: float) -> None:
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()

    def allow(self, source: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._by_source[source]
            self._trim(hits, now)
            self._trim(self._all, now)
            if len(hits) >= self.per_source or len(self._all) >= self.total:
                return False
            hits.append(now)
            self._all.append(now)
            for key in [k for k, v in self._by_source.items() if not v]:
                del self._by_source[key]
            return True


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
border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);min-height:44px}}
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


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _status_rows(link: Link) -> str:
    view = link.public()
    rows = [
        ("Toestand", view["state_label_nl"]),
        ("Hub-adres", view["hub_url"]),
        ("Gekoppeld via", VIA_NL.get(str(view["via"]), view["via"])),
        ("Eerst gekoppeld", view["first_linked_at"]),
        ("Laatst gekoppeld", view["linked_at"]),
        ("Service-ID", view["service_id"]),
        ("Vingerafdruk Hub-sleutels", view["keyset_fingerprint"]),
        ("Service-token eindigt op", f"…{view['service_token_last4']}" if view["service_token_last4"] else None),
    ]
    body = "".join(f"<tr><th scope=\"row\">{_e(k)}</th><td><code>{_e(v)}</code></td></tr>" for k, v in rows
                   if v is not None)
    err = view["last_error"]
    error = (f'<p class="err">Laatste melding ({_e(err["at"])}): {_e(err["message_nl"])}</p>' if err else "")
    return f"<table><tbody>{body}</tbody></table>{error}"


def _link_section(link: Link, token: str) -> str:
    if link.corrupt:
        return f'<p class="err">{_e(MSG["corrupt"])}</p>'
    if link.ever_linked:
        lead = ('<p class="ok">Gekoppeld met de Hub.</p>' if link.linked else
                f'<p class="err">{_e(link.public()["state_label_nl"])}.</p>')
        return (lead + _status_rows(link) + '<p class="muted">Vergelijk de vingerafdruk met die in de Hub-UI. '
                "Ontkoppelen of opnieuw koppelen gaat via de Hub (alleen beheerders); deze pagina kan dat niet.</p>")
    if link.pending is not None:
        return ('<p>Koppelen met de Hub is <strong>bezig</strong> (eenmalig token of koppelcode). Ververs deze pagina '
                "over een minuut.</p>")
    err = link.public()["last_error"]
    error = f'<p class="err">Laatste poging: {_e(err["message_nl"])}</p>' if err else ""
    return (
        "<p>Nog <strong>nooit gekoppeld</strong>. Zonder koppeling weigert de API elke aanvraag (alleen deze "
        "statuspagina, <code>/healthz</code> en <code>/readyz</code> werken). Aanbevolen: laat de Hub deze app "
        "installeren of vul in het TrueNAS-formulier <code>HUB_URL</code>, <code>HUB_ENROLLMENT_TOKEN</code> en "
        "<code>HUB_KEY_FINGERPRINT</code> in. Anders: maak in de Hub een koppelcode voor "
        f"<code>{APP_ID}</code> en vul hieronder het Hub-adres, de koppelcode en de vingerafdruk in die de Hub-UI "
        f"erbij toont.</p>{error}"
        '<form method="post" action="/koppelen" autocomplete="off">'
        f'<input type="hidden" name="csrf" value="{_e(token)}">'
        '<label for="hub_url">Adres van de Hub</label>'
        '<input id="hub_url" name="hub_url" type="url" required maxlength="512" '
        'placeholder="http://&lt;NAS-adres&gt;:30400">'
        '<label for="koppelcode">Koppelcode uit de Hub (XXXX-XXXX-XXXX)</label>'
        f'<input id="koppelcode" name="koppelcode" required minlength="12" maxlength="{KOPPELCODE_MAX}" '
        'inputmode="text" autocapitalize="characters" spellcheck="false">'
        '<label for="vingerafdruk">Vingerafdruk van de Hub (XXXX-XXXX-XXXX-XXXX)</label>'
        '<input id="vingerafdruk" name="vingerafdruk" required minlength="16" maxlength="32" inputmode="text" '
        'autocapitalize="characters" spellcheck="false">'
        '<button type="submit">Koppelen</button></form>'
    )


def _render(request: Request, message: str = "", status_code: int = 200) -> HTMLResponse:
    health = collect_health(request.app)
    rows = "".join(
        f'<tr><td>{_e(CHECK_NL.get(c["name"], c["name"]))}</td>'
        f'<td class="s-{c["status"]}">{STATUS_NL[c["status"]]}</td><td>{_e(c["detail"])}</td>'
        f'<td><time datetime="{c["observed_at"]}">{c["observed_at"]}</time></td></tr>'
        for c in health["checks"]
    )
    link: Link = request.app.state.hub_link.read()
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(24)
    status = health["status"]
    body = PAGE.format(app_id=APP_ID, version=__version__, now=iso_z(utcnow()), message=message, status=status,
                       status_nl=STATUS_NL[status], rows=rows, link=_link_section(link, token))
    response = HTMLResponse(body, status_code=status_code,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "same-origin"})
    response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return response


def _alert(text: str) -> str:
    return f'<p class="err card" role="alert">{_e(text)}</p>'


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host or origin == "null":
        return False
    return hmac.compare_digest(origin.rstrip("/"), f"{request.url.scheme}://{host}")


@router.get("/", response_class=HTMLResponse)
def status_page(request: Request) -> Response:
    msg = '<p class="ok card" role="status">Koppelen gelukt.</p>' if request.query_params.get("gekoppeld") == "1" else ""
    return _render(request, msg)


@router.post("/koppelen", response_class=HTMLResponse)
def pair(
    request: Request,
    hub_url: str = Form(..., max_length=512),
    koppelcode: str = Form(..., max_length=KOPPELCODE_MAX),
    vingerafdruk: str = Form(..., max_length=32),
    csrf: str = Form(..., max_length=128),
) -> Response:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not cookie or not hmac.compare_digest(cookie, csrf) or not _same_origin(request):
        return _render(request, _alert(MSG["csrf"]), 403)
    state = request.app.state
    link: Link = state.hub_link.read()
    if link.ever_linked:
        return _render(request, _alert(MSG["corrupt"] if link.corrupt else MSG["ever_linked"]), 409)
    if link.pending is not None:
        return _render(request, _alert(MSG["pending"]), 409)
    source = request.client.host if request.client else "onbekend"  # TCP-verbinding; nooit X-Forwarded-For
    if not state.pairing_limiter.allow(source):
        return _render(request, _alert(MSG["rate"]), 429)
    try:
        url = normalize_hub_url(hub_url, allow_loopback=state.settings.allow_loopback_hub, name="Hub-adres")
    except ConfigError as exc:
        return _render(request, _alert(str(exc)), 400)
    if not url:
        return _render(request, _alert(MSG["url_missing"]), 400)
    code = normalize_pairing_code(koppelcode)
    if code is None:
        return _render(request, _alert(MSG["code"]), 400)
    fingerprint = normalize_fingerprint(vingerafdruk)
    if fingerprint is None:
        return _render(request, _alert(MSG["fingerprint"]), 400)
    if state.identity is None:
        return _render(request, _alert(MSG["secrets"]), 503)
    try:
        with state.http_client_factory() as client:
            enroll_once(state.hub_link, client, state.identity, state.settings, kind="koppelcode", secret=code,
                        hub_url=url, expected_fingerprint=fingerprint)
            confirm(state.hub_link, client, HubSignals())
    except EnrollmentError as exc:
        if not exc.final:  # de gebruiker wacht: niet op de achtergrond doorproberen, adres niet bewaren
            mark_failed(state.hub_link, exc.code, env_token_sha=None)
        return _render(request, _alert(ERROR_MESSAGES_NL[exc.code]), 400)
    except HubLinkCorrupt:
        return _render(request, _alert(MSG["corrupt"]), 409)
    return RedirectResponse("/?gekoppeld=1", status_code=303)
