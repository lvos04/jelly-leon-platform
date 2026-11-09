"""Kleine Nederlandse statuspagina (``GET /``) in het Peil-sjabloon, met koppelformulier (contract 1.2 §3.7).

Alle schermen van het platform staan in de Hub; deze pagina toont alleen status en maakt handmatig koppelen mogelijk.
De HTML/CSS is het framework-vrije Peil-sjabloon (``lifecycle/peil/statuspagina.html``, versie-gepinde kopie van
``ai-server-manager/design/ontwerptaal``; ONTWERPTAAL §13.2): geen JavaScript, geen externe bron, CSP
``default-src 'none'; style-src 'unsafe-inline'``.

- Precies één koppeltoestand: ``koppelformulier`` (alleen zolang de dienst **nog nooit** gekoppeld is,
  ``first_linked_at`` leeg), ``gekoppeld``, ``ontkoppeld`` of ``ingetrokken``. Een onleesbaar koppelbestand toont geen
  formulier (fail-closed, ADR-0011) maar een foutmelding. Geen ontkoppelen, geen lokale ontkoppelcode en geen
  herkoppelen na intrekking op deze pagina; "Gekoppeld" is een neutrale pil, nooit groen.
- Velden (namen uit het sjabloon): ``hub_adres`` (zelfde regels als ``HUB_URL``), ``koppelcode`` (12 tekens
  Crockford) en ``hub_vingerafdruk`` (verplicht, ``XXXX-XXXX-XXXX-XXXX``). Invoer wordt nooit teruggezet of
  weerspiegeld; meldingen en veldfouten komen uit vaste lijsten (``MELDINGEN``).
- Foutweergave: titel + vaste tekst + code + trace-ID (selecteerbaar, zonder JS) van het verzoek; de app-log
  noteert dezelfde trace-ID.
- Eerste signaal (totaal) = slechtste van gezondheid en gereedheid; nooit groen als de dienst niet gereed is
  (gereed = ``/readyz`` in orde **én** gekoppeld).
- CSRF (dubbel ingediend token in een cookie ``SameSite=Strict``, HttpOnly) én ``Origin``-controle. De pagina zet
  ``Referrer-Policy: same-origin`` zodat de browser bij het versturen de echte ``Origin`` meestuurt.
- Max. 5 pogingen per 15 min per bron-IP (adres van de TCP-verbinding; ``X-Forwarded-For`` wordt nooit gebruikt) én
  max. 20 per 15 min voor de hele dienst; telling in het geheugen (één API-proces). Overschrijding → vaste melding,
  geen verzoek naar de Hub.
- Het Hub-adres van een **mislukte** poging wordt nergens bewaard.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from lifecycle import __version__
from lifecycle.config import ConfigError, normalize_hub_url
from lifecycle.enrollment import (
    EnrollmentError,
    HubSignals,
    PairingRefused,
    confirm,
    enroll_once,
    mark_failed,
    sha256_hex,
)
from lifecycle.errors import trace_id_of
from lifecycle.health import collect_health, readiness
from lifecycle.hublink import ERROR_MESSAGES_NL, HubLinkCorrupt, Link
from lifecycle.keys import normalize_fingerprint, normalize_pairing_code
from lifecycle.peil import render, statuspagina_template
from lifecycle.timeutil import iso_z, utcnow

log = logging.getLogger("lifecycle")
router = APIRouter(include_in_schema=False)

CSRF_COOKIE = "jlp_csrf"
CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
DIENST_NAAM = "Jelly Leon Platform"
DIENST_OMSCHRIJVING = (
    "Lifecycle-dienst van het TrueNAS Home Platform: legt installeren, bijwerken, terugzetten, verwijderen en "
    "verhuizen van apps vast en levert het connectorcontract. Alle schermen staan in de Hub; dit is alleen een "
    "statuspagina."
)
KOPPELCODE_GELDIG = "60 minuten"
MAX_LEN = {"hub_adres": 512, "koppelcode": 32, "hub_vingerafdruk": 32, "csrf": 128}
_CSRF_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_URL_RE = re.compile(r"^https?://[A-Za-z0-9.:\[\]-]+$")

TOON = {"healthy": "gezond", "degraded": "beperkt", "down": "storing", "unknown": "onbekend"}
LABEL = {"gezond": "Gezond", "beperkt": "Beperkt", "storing": "Storing", "onbekend": "Onbekend"}
RANG = {"gezond": 0, "onbekend": 1, "beperkt": 2, "storing": 3}  # Storing > Beperkt > Onbekend > Gezond (§4.3)
CHECK_NL = {
    "database": "Eigen database (ingebouwd)",
    "migrations": "Databaseschema",
    "secrets": "Geheimen (/appdata/secrets)",
    "worker": "Achtergrondproces",
    "hub_link": "Koppeling met de Hub",
}
MAANDEN = ("jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec")

# Veld → (id van het invoerveld in het sjabloon, naam in de tekst)
VELDEN = {
    "hub_adres": ("hub", "Adres van de Hub"),
    "koppelcode": ("koppelcode", "Koppelcode"),
    "vingerafdruk": ("vingerafdruk", "Vingerafdruk van de Hub"),
}


@dataclass(frozen=True)
class Melding:
    """Vaste melding (nooit weerspiegelde invoer). ``veld`` + ``veldfout`` = foutmelding bij het invoerveld."""

    toon: str  # fout | info
    titel: str
    tekst: str
    veld: str | None = None
    veldfout: str | None = None


_MISLUKT = "Koppelen is mislukt"
MELDINGEN: dict[str, Melding] = {
    "csrf_ongeldig": Melding("fout", "Formulier verlopen", "Het formulier is verlopen of komt van een andere "
                             "pagina. Laad de pagina opnieuw en probeer het nog eens. Er is niets naar de Hub gestuurd."),
    "al_gekoppeld": Melding("fout", "Koppelen via deze pagina kan niet meer", "Deze dienst is al eens gekoppeld. "
                            "Ontkoppelen of opnieuw koppelen gaat via de Hub."),
    "koppelen_bezig": Melding("info", "Koppelen is bezig", "Er loopt al een koppelpoging (eenmalig token of "
                              "koppelcode). Laad de pagina over een minuut opnieuw."),
    "te_veel_pogingen": Melding("fout", "Te veel koppelpogingen", "Wacht 15 minuten en probeer het dan opnieuw. Er "
                                "is niets naar de Hub gestuurd."),
    "invoer_ongeldig": Melding("fout", "Controleer de invoer", "Niet alle velden kloppen; zie de melding bij het "
                               "veld. Er is niets naar de Hub gestuurd."),
    "geheimen_ontbreken": Melding("fout", "Geheimen ontbreken", "De eigen sleutels van deze dienst ontbreken. "
                                  "Herstart de app; bij de eerste start worden ze aangemaakt."),
    "hub_link_onleesbaar": Melding("fout", "Koppelbestand onleesbaar", "Het koppelbestand is onleesbaar; de dienst "
                                   "vertrouwt daarom geen enkele Hub-aanmelding. Opnieuw koppelen gaat via de Hub "
                                   "(nieuw token in het TrueNAS-formulier)."),
    # Fouten van de Hub-aanroep (vaste codes §3.5, tekst uit ERROR_MESSAGES_NL); veldfout bij het waarschijnlijke veld
    "hub_onbereikbaar": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["hub_onbereikbaar"], "hub_adres",
                                "Geen antwoord van een Hub op dit adres. Controleer het adres en of de Hub draait."),
    "hub_weigerde": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["hub_weigerde"], "koppelcode",
                            "De Hub weigerde deze koppelcode: verlopen, al gebruikt of ongeldig. Maak een nieuwe in "
                            "de Hub."),
    "hub_vingerafdruk_onjuist": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["hub_vingerafdruk_onjuist"],
                                        "vingerafdruk", "De sleutels van de Hub op dit adres hebben een andere "
                                        "vingerafdruk. Vergelijk adres en vingerafdruk met de Hub."),
    "hub_antwoord_ongeldig": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["hub_antwoord_ongeldig"], "hub_adres",
                                     "Het antwoord op dit adres is geen geldig antwoord van een Hub."),
    "enroll_endpoint_ontbreekt": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["enroll_endpoint_ontbreekt"],
                                         "hub_adres", "Op dit adres draait geen Hub die koppelen kent."),
    "rate_limited": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["rate_limited"]),
    "ingetrokken_door_hub": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["ingetrokken_door_hub"]),
}
VELDFOUT = {
    "hub_adres_ontbreekt": "Vul het adres van de Hub in.",
    "koppelcode_ongeldig": "De koppelcode moet 12 tekens zijn (XXXX-XXXX-XXXX), zoals de Hub hem toont.",
    "vingerafdruk_ongeldig": "De vingerafdruk moet 16 tekens zijn (XXXX-XXXX-XXXX-XXXX), zoals de Hub hem toont.",
    "te_lang": "Te lang; kopieer de waarde uit de Hub.",
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


# ----------------------------------------------------------------------------------------------- weergave-hulpjes
def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def nl_datum(dt: datetime | None) -> str:
    """``9 nov 2026, 11:56 UTC`` (24 uur; de dienst draait in UTC, dus de tijdzone staat erbij)."""
    if dt is None:
        return "onbekend"
    dt = dt.astimezone(UTC)
    return f"{dt.day} {MAANDEN[dt.month - 1]} {dt.year}, {dt:%H:%M} UTC"


def _zin(text: str) -> str:
    """Eerste letter hoofdletter en een punt aan het eind (de details van de controles zijn zinsdelen)."""
    if not text:
        return text
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


@dataclass(frozen=True)
class Koppeling:
    """Koppeltoestand voor de pagina (precies één blok van het sjabloon, of geen bij een onleesbaar bestand)."""

    blok: str | None  # koppelformulier | gekoppeld | ontkoppeld | ingetrokken | None
    woord: str  # voor de titel
    rij: dict[str, str] | None  # uitzonderingsrij in de controles (None = gekoppeld en bevestigd: neutraal blok)


def _koppeling(link: Link, link_error: str | None) -> Koppeling:
    if link_error is not None:
        return Koppeling(None, "koppelstatus onbekend",
                         {"toon": "onbekend", "label": "Onbekend", "reden": _zin(link_error)})
    if link.corrupt:
        return Koppeling(None, "koppelbestand onleesbaar", {
            "toon": "storing", "label": "Onleesbaar",
            "reden": "Het koppelbestand is onleesbaar; de API vertrouwt geen enkele Hub-aanmelding."})
    if link.state == "gekoppeld":
        if link.doc.get("confirmed_at"):
            return Koppeling("gekoppeld", "gekoppeld", None)
        return Koppeling("gekoppeld", "gekoppeld", {
            "toon": "beperkt", "label": "Nog niet bevestigd",
            "reden": "Gekoppeld; de bevestiging bij de Hub (services/self) volgt."})
    if link.state == "ingetrokken":
        return Koppeling("ingetrokken", "ingetrokken", {
            "toon": "storing", "label": "Ingetrokken",
            "reden": "De Hub heeft de koppeling ingetrokken; de API weigert elke aanvraag."})
    if link.state == "ontkoppeld":
        return Koppeling("ontkoppeld", "ontkoppeld", {
            "toon": "onbekend", "label": "Ontkoppeld",
            "reden": "De Hub heeft deze dienst ontkoppeld; de API weigert elke aanvraag."})
    if link.pending is not None:
        return Koppeling("koppelformulier", "koppelen bezig", {
            "toon": "onbekend", "label": "Koppelen bezig", "reden": "Er loopt een koppelpoging met de Hub."})
    return Koppeling("koppelformulier", "niet gekoppeld", {
        "toon": "onbekend", "label": "Niet gekoppeld",
        "reden": "Zonder koppeling weigert de API elke aanvraag; alleen deze pagina, /healthz en /readyz werken."})


def _totaal(worst: str, ready: bool, link: Link, koppeling: Koppeling) -> tuple[str, str, str, str]:
    """(toon, label, toelichting, gereed). Nooit groen als de dienst niet gereed is (ONTWERPTAAL §13.2)."""
    if not ready:
        toon = worst if worst != "gezond" else "onbekend"
        woord = "Storing" if toon == "storing" else "Deels onbekend" if toon == "onbekend" else "Beperkt"
        return (toon, "Niet gereed", f"{woord} — de eigen database, het schema of de geheimen zijn niet in orde.",
                "Nee — eigen afhankelijkheden niet in orde (/readyz: not_ready)")
    if not link.linked:
        toon = worst if RANG[worst] >= RANG["onbekend"] else "onbekend"
        if koppeling.blok == "ingetrokken" or koppeling.blok is None:
            reden = ("de Hub heeft de koppeling ingetrokken" if koppeling.blok == "ingetrokken"
                     else "de koppelstatus is niet bruikbaar")
            return ("storing" if koppeling.rij and koppeling.rij["toon"] == "storing" else toon, "Niet gereed",
                    f"Storing — {reden}; opnieuw koppelen gaat via de Hub.", f"Nee — {reden}")
        woord = {"storing": "Storing", "beperkt": "Beperkt"}.get(toon, "Deels onbekend")
        wacht = "wacht op koppeling met de Hub" if koppeling.blok == "koppelformulier" else (
            "ontkoppeld door de Hub; opnieuw koppelen gaat via de Hub")
        return (toon, "Niet gereed", f"{woord} — {wacht}.",
                f"Nee — {wacht} (de eigen afhankelijkheden zijn in orde)")
    if worst == "gezond":
        return "gezond", "Alles gezond", "Gereed; alle controles zijn gezond.", "Ja"
    label = {"beperkt": "Beperkt", "storing": "Storing", "onbekend": "Deels onbekend"}[worst]
    uitleg = {"beperkt": "één of meer controles zijn beperkt", "storing": "een controle heeft een storing",
              "onbekend": "één of meer controles zijn onbekend"}[worst]
    return worst, label, f"Gereed; {uitleg}.", "Ja"


def _melding_data(code: str | None, melding: Melding | None, trace: str | None, extra: str = "") -> dict[str, Any]:
    if melding is None:
        return {"melding": False}
    data: dict[str, Any] = {
        "melding": True, "melding_toon": melding.toon, "melding_icoon": "storing" if melding.toon == "fout" else "info",
        "melding_titel": melding.titel, "melding_tekst": melding.tekst + extra, "melding_link": False,
        "trace_id": trace or "", "melding_code": code or "",
    }
    if melding.veld:
        veld_id, naam = VELDEN[melding.veld]
        data.update(melding_link=True, melding_link_doel=veld_id, melding_link_tekst=f"Naar het veld {naam}")
    return data


def render_page(
    request: Request,
    *,
    code: str | None = None,
    melding: Melding | None = None,
    veldfouten: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Vul het Peil-sjabloon in. ``code``/``melding`` = resultaat van dit verzoek (met trace-ID); zonder melding toont
    de pagina zelf de opgeslagen toestand (bijv. de laatste mislukte koppelpoging, zonder trace-ID)."""
    app = request.app
    now = utcnow()
    health = collect_health(app)
    ready, _ = readiness(app)
    link_error: str | None = None
    try:
        link: Link = app.state.hub_link.read()
    except Exception as exc:  # fail-closed: geen formulier, geen koppelgegevens
        link, link_error = Link(corrupt=True), f"koppelstatus niet leesbaar ({type(exc).__name__})"
    hub_url = link.hub_url if link.linked else None
    if link.linked and not (hub_url and _URL_RE.fullmatch(hub_url)):
        link, link_error = Link(corrupt=True), "opgeslagen Hub-adres is ongeldig"
    koppeling = _koppeling(link, link_error)

    rijen: list[dict[str, str]] = []
    for c in health["checks"]:
        if c["name"] == "hub_link":
            if koppeling.rij is None:
                continue  # gekoppeld en bevestigd: staat neutraal in "Koppeling met de Hub" (nooit groen)
            rij = dict(koppeling.rij)
        else:
            toon = TOON.get(c["status"], "onbekend")
            rij = {"toon": toon, "label": LABEL[toon], "reden": "" if toon == "gezond" else _zin(c["detail"])}
        at = _parse(c["observed_at"]) or now
        rijen.append({"naam": CHECK_NL.get(c["name"], c["name"]), "iso": iso_z(at) or "", "tijd": f"{at:%H:%M} UTC",
                      **rij})
    worst = max((r["toon"] for r in rijen), key=RANG.__getitem__, default="onbekend")
    totaal_toon, totaal_label, toelichting, gereed = _totaal(worst, ready, link, koppeling)

    # Melding: resultaat van dit verzoek, anders de opgeslagen toestand (vaste teksten, geen trace-ID).
    trace = trace_id_of(request)
    data_melding: dict[str, Any]
    if melding is not None:
        data_melding = _melding_data(code, melding, trace)
    elif koppeling.blok is None:  # blijvende toestand: code in de tekst (de trace-ID van dit verzoek zegt niets)
        data_melding = _melding_data(None, MELDINGEN["hub_link_onleesbaar"], None, " Code hub_link_onleesbaar.")
    elif request.query_params.get("gekoppeld") == "1" and link.linked and link.doc.get("confirmed_at"):
        data_melding = _melding_data(None, Melding("info", "Gekoppeld met de Hub", "De Hub heeft de koppeling "
                                     "bevestigd. Vergelijk de vingerafdruk hieronder met die in de Hub."), None)
    elif koppeling.blok == "koppelformulier" and link.pending is not None:
        data_melding = _melding_data(None, MELDINGEN["koppelen_bezig"], None)
    elif (err := link.public()["last_error"]) and not (koppeling.blok == "ingetrokken"
                                                       and err["code"] == "ingetrokken_door_hub"):
        titel = "Laatste koppelpoging is mislukt" if koppeling.blok == "koppelformulier" else "Melding van de koppeling"
        stamp = nl_datum(_parse(err["at"]))
        data_melding = _melding_data(None, Melding("fout", titel, err["message_nl"]), None,
                                     f" Code {err['code']} · {stamp}.")
    else:
        data_melding = {"melding": False}

    fouten = veldfouten or {}
    eerste = next((v for v in VELDEN if v in fouten), None)
    spring = (VELDEN[eerste][0], f"Naar het veld {VELDEN[eerste][1]} (fout)") if eerste else ("inhoud", "Naar inhoud")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    token = cookie if _CSRF_RE.fullmatch(cookie) else secrets.token_urlsafe(24)
    titel_melding = "koppelen mislukt" if data_melding.get("melding_toon") == "fout" and melding else koppeling.woord

    data: dict[str, Any] = {
        "voorbeeld": False,
        "dienst_naam": DIENST_NAAM, "dienst_omschrijving": DIENST_OMSCHRIJVING,
        "titel_toestand": f"{totaal_label.lower()}, {titel_melding}",
        "spring_doel": spring[0], "spring_tekst": spring[1],
        **data_melding,
        "totaal_toon": totaal_toon, "totaal_label": totaal_label, "totaal_toelichting": toelichting,
        "checks": rijen, "gereed": gereed, "versie": __version__,
        "bijgewerkt": nl_datum(now), "bijgewerkt_iso": iso_z(now) or "",
        "gekoppeld": koppeling.blok == "gekoppeld", "ontkoppeld": koppeling.blok == "ontkoppeld",
        "ingetrokken": koppeling.blok == "ingetrokken", "koppelformulier": koppeling.blok == "koppelformulier",
        "gekoppeld_sinds": nl_datum(_parse(link.doc.get("linked_at"))) if link.linked else "",
        "hub_adres": hub_url or "", "hub_vingerafdruk": link.keyset_fingerprint or "",
        "csrf_token": token, "koppelcode_geldig": KOPPELCODE_GELDIG,
        "fout_hub_adres": fouten.get("hub_adres", ""), "fout_koppelcode": fouten.get("koppelcode", ""),
        "fout_vingerafdruk": fouten.get("vingerafdruk", ""),
        "schrijfsleutel_formulier": False, "executor_beheer": False,  # alleen de executor (§3.9)
        "dienst_blok_titel": "Wat deze versie wel en niet doet",
        "dienst_blok": [
            {"naam": "Installeren, bijwerken, terugzetten, verwijderen en verhuizen", "toon": "gate",
             "label": "Nog niet beschikbaar",
             "reden": "Volgt in fase P2. Tot dan antwoordt de API eerlijk capability_unavailable en wordt er niets "
                      "op de NAS uitgevoerd. Dit is geen storing; er wacht een voorwaarde."},
            {"naam": "Wijzigingen op TrueNAS", "toon": "gate", "label": "Alleen via de executor",
             "reden": "Deze dienst heeft geen TrueNAS-schrijfsleutel. Uitvoering loopt vanaf P2 uitsluitend via de "
                      "executor van de Hub, met goedkeuring volgens het risicobeleid."},
        ],
    }
    body = render(statuspagina_template(), data)
    response = HTMLResponse(body, status_code=status_code, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "same-origin", "Content-Security-Policy": CSP})
    response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return response


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host or origin == "null":
        return False
    return hmac.compare_digest(origin.rstrip("/"), f"{request.url.scheme}://{host}")


def _weiger(request: Request, code: str, status: int, veldfouten: dict[str, str] | None = None) -> HTMLResponse:
    log.warning("koppelen via de statuspagina geweigerd: %s (trace %s)", code, trace_id_of(request))
    return render_page(request, code=code, melding=MELDINGEN[code], veldfouten=veldfouten, status_code=status)


@router.get("/", response_class=HTMLResponse)
def status_page(request: Request) -> Response:
    return render_page(request)


@router.post("/koppelen", response_class=HTMLResponse)
def pair(
    request: Request,
    hub_adres: str = Form(""),
    koppelcode: str = Form(""),
    hub_vingerafdruk: str = Form(""),
    csrf: str = Form(""),
) -> Response:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if (not cookie or len(csrf) > MAX_LEN["csrf"] or not hmac.compare_digest(cookie.encode(), csrf.encode())
            or not _same_origin(request)):
        return _weiger(request, "csrf_ongeldig", 403)
    state = request.app.state
    link: Link = state.hub_link.read()
    if link.ever_linked:
        return _weiger(request, "hub_link_onleesbaar" if link.corrupt else "al_gekoppeld", 409)
    if link.pending is not None:
        return _weiger(request, "koppelen_bezig", 409)
    source = request.client.host if request.client else "onbekend"  # TCP-verbinding; nooit X-Forwarded-For
    if not state.pairing_limiter.allow(source):
        return _weiger(request, "te_veel_pogingen", 429)

    fouten: dict[str, str] = {}
    url: str | None = None
    if len(hub_adres) > MAX_LEN["hub_adres"]:
        fouten["hub_adres"] = VELDFOUT["te_lang"]
    else:
        try:
            url = normalize_hub_url(hub_adres, allow_loopback=state.settings.allow_loopback_hub, name="Hub-adres")
        except ConfigError as exc:  # vaste teksten uit config.py, nooit de invoer zelf
            fouten["hub_adres"] = f"{exc}."
        else:
            if not url:
                fouten["hub_adres"] = VELDFOUT["hub_adres_ontbreekt"]
    code = normalize_pairing_code(koppelcode) if len(koppelcode) <= MAX_LEN["koppelcode"] else None
    if code is None:
        fouten["koppelcode"] = VELDFOUT["koppelcode_ongeldig"]
    fingerprint = (normalize_fingerprint(hub_vingerafdruk) if len(hub_vingerafdruk) <= MAX_LEN["hub_vingerafdruk"]
                   else None)
    if fingerprint is None:
        fouten["vingerafdruk"] = VELDFOUT["vingerafdruk_ongeldig"]
    if fouten or url is None or code is None or fingerprint is None:
        return _weiger(request, "invoer_ongeldig", 400, fouten)
    if state.identity is None:
        return _weiger(request, "geheimen_ontbreken", 503)
    try:
        with state.http_client_factory() as client:
            enroll_once(state.hub_link, client, state.identity, state.settings, kind="koppelcode", secret=code,
                        hub_url=url, expected_fingerprint=fingerprint)
            confirmed = confirm(state.hub_link, client, HubSignals())
    except EnrollmentError as exc:
        if not exc.final:  # de gebruiker wacht: niet op de achtergrond doorproberen, adres niet bewaren
            mark_failed(state.hub_link, exc.code, env_token_sha=None, secret_sha=sha256_hex(code))
        melding = MELDINGEN.get(exc.code) or Melding("fout", _MISLUKT, ERROR_MESSAGES_NL.get(exc.code, "Onbekende fout."))
        veld = {melding.veld: melding.veldfout} if melding.veld and melding.veldfout else None
        log.warning("koppelen via de statuspagina mislukt: %s (trace %s)", exc.code, trace_id_of(request))
        return render_page(request, code=exc.code, melding=melding, veldfouten=veld, status_code=400)
    except PairingRefused as exc:  # onder de vergrendeling vastgesteld: intussen gekoppeld of een andere poging
        return _weiger(request, "al_gekoppeld" if exc.reason == "ever_linked" else "koppelen_bezig", 409)
    except HubLinkCorrupt:
        return _weiger(request, "hub_link_onleesbaar", 409)
    if confirmed == "ingetrokken":  # de Hub kent het nieuwe token al niet meer (bijv. een herhaald verzoek)
        log.warning("koppelen via de statuspagina: direct ingetrokken (trace %s)", trace_id_of(request))
        return render_page(request, code="ingetrokken_door_hub", melding=MELDINGEN["ingetrokken_door_hub"],
                           status_code=400)
    return RedirectResponse("/?gekoppeld=1", status_code=303)
