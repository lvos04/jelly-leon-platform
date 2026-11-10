"""Kleine Nederlandse statuspagina (``GET /``) in het Peil-sjabloon, met koppelformulier (contract 1.3 §3.5, §3.7).

Alle schermen van het platform staan in de Hub; deze pagina toont alleen status en maakt handmatig koppelen mogelijk.
De HTML/CSS is het framework-vrije Peil-sjabloon 1.1.1 (``lifecycle/peil/statuspagina.html``, versie-gepinde kopie van
``ai-server-manager/design/ontwerptaal``; ONTWERPTAAL §13.2): geen JavaScript, geen externe bron, CSP
``default-src 'none'; style-src 'unsafe-inline'``.

- Precies één koppeltoestand: ``koppelformulier`` (alleen zolang de dienst **nog nooit** gekoppeld is en het
  koppelbestand leesbaar is), ``gekoppeld``, ``ontkoppeld``, ``ingetrokken`` of ``koppelstatus_onleesbaar``
  (fail-closed, geen formulier). Daarnaast de vlag ``koppelen_bezig`` (``pending`` ≠ ``null``): het formulier
  verdwijnt, de stabiele toestand blijft staan. Geen ontkoppelen en geen ontkoppelcode op deze pagina.
- **Herkomst** van elke formulier-POST (§3.7): ``Referrer-Policy: same-origin``; ``Origin`` exact
  ``<schema>://<Host>`` (ontbreekt of ``null`` → 403); ``Sec-Fetch-Site`` alleen ``same-origin`` of ``none``;
  ``Host``-beperking tegen DNS-rebinding (IP-adres, naam zonder punt of ``.local``/``.lan``/``.internal``/``.home.arpa``,
  anders 403 ``host_niet_toegestaan``); daarnaast de double-submit-cookie (``SameSite=Strict``, HttpOnly). Een geweigerd
  verzoek gaat niet naar de Hub en verandert niets.
- **Pogingen:** elke POST die de herkomstcontrole doorstaat telt mee (5 per 15 min per bron-IP van de TCP-verbinding,
  nooit ``X-Forwarded-For``; 20 per 15 min voor de dienst), ook bij ongeldige invoer, een lopende poging (409
  ``koppelen_bezig``), een onleesbaar koppelbestand (409 ``koppelstatus_onleesbaar``) of een dienst die al eens
  gekoppeld is (409 ``al_gekoppeld``). Teller in het geheugen: de dienst heeft één API-proces.
- **Invoer** als geheel controleren, nooit afkappen: koppelcode ≤ 32, vingerafdruk ≤ 64, Hub-adres ≤ 512 tekens; langer
  → veldfout. Invoer wordt nooit teruggezet of weerspiegeld; meldingen en veldfouten komen uit vaste lijsten.
- **Meldingen** horen bij een echte poging van deze browser: de fout van dít verzoek (``role="alert"``, code en
  trace-ID) of, na een geslaagde koppeling, een sleutel in het geheugen (15 min, gebonden aan de CSRF-cookie van deze
  browser; ``role="status"``). Een query-parameter alleen toont nooit iets. Een toestand die al bestond (onleesbaar,
  laatste mislukte poging) heeft geen live-rol en geen trace-ID, wel een code.
- **Log:** precies één regel per formulier-POST in de app-log, met code en trace-ID; zonder ingevuld Hub-adres en zonder
  geheimen.
- **Gereed** = ``/readyz`` in orde én ``state: gekoppeld`` met ``confirmed_at``; het totaal is nooit groen zonder
  Gereed. Het Hub-adres van een **mislukte** poging wordt nergens bewaard.
"""

from __future__ import annotations

import hmac
import logging
import math
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
from lifecycle.config import ConfigError, host_header_allowed, normalize_hub_url
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
from lifecycle.hublink import ERROR_MESSAGES_NL, FILE_NAME, HubLinkCorrupt, Link
from lifecycle.keys import normalize_fingerprint, normalize_pairing_code
from lifecycle.peil import render, statuspagina_template
from lifecycle.timeutil import iso_z, utcnow

log = logging.getLogger("lifecycle")
router = APIRouter(include_in_schema=False)

CSRF_COOKIE = "jlp_csrf"
CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
DIENST_NAAM = "Jelly Leon Platform"
# Kort (status eerst, ONTWERPTAAL §1.2): op 320 px staat de status zo binnen het eerste scherm. "Onderdeel van TrueNAS
# Home Platform" staat al in de kop; vaktermen (lifecycle, connectorcontract) horen in de Hub, niet hier.
DIENST_OMSCHRIJVING = (
    "Houdt de levenscyclus van apps bij: installeren, bijwerken, terugzetten, verwijderen en verhuizen. Deze pagina "
    "toont alleen de status; alle schermen staan in de Hub."
)
BRON = "Eigen controles (/readyz), de hartslag van het achtergrondproces en het koppelbestand van deze dienst."
KOPPELCODE_GELDIG = "60 minuten"
MAX_LEN = {"hub_adres": 512, "koppelcode": 32, "hub_vingerafdruk": 64, "csrf": 128}  # §3.7
_CSRF_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_URL_RE = re.compile(r"^https?://[A-Za-z0-9.:\[\]-]+$")
MELDING_GELDIG_S = 900.0  # §3.7: hooguit 15 min
SEC_FETCH_SITE_OK = frozenset({"same-origin", "none"})

TOON = {"healthy": "gezond", "degraded": "beperkt", "down": "storing", "unknown": "onbekend"}
LABEL = {"gezond": "Gezond", "beperkt": "Beperkt", "storing": "Storing", "onbekend": "Onbekend"}
# Gezondheidsklasse van een rij voor het totaal (ONTWERPTAAL §13.2 1.1.1, E03/E10): niet-gekoppeld, niet-beschikbaar en
# verouderd tellen als Onbekend; nog-niet, gate, beleid en feit tellen niet mee.
KLASSE = {"gezond": "gezond", "beperkt": "beperkt", "storing": "storing", "onbekend": "onbekend",
          "niet-gekoppeld": "onbekend", "niet-beschikbaar": "onbekend", "verouderd": "onbekend"}
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
_NIETS = " Er is niets naar de Hub gestuurd."
MELDINGEN: dict[str, Melding] = {
    "herkomst_ongeldig": Melding("fout", "Formulier niet van deze pagina", "Het formulier kwam niet van deze "
                                 "statuspagina, of de browser stuurde niet mee waar het vandaan kwam. Laad de pagina "
                                 "opnieuw en vul het formulier hier in." + _NIETS),
    "host_niet_toegestaan": Melding("fout", "Koppelen via dit adres kan niet", "Open deze pagina via het IP-adres van "
                                    "de NAS, of koppel via de Hub. Via een eigen domeinnaam kan een andere website "
                                    "zich als deze pagina voordoen." + _NIETS),
    "csrf_ongeldig": Melding("fout", "Formulier verlopen", "Het formulier is verlopen of komt van een andere "
                             "pagina. Laad de pagina opnieuw en probeer het nog eens." + _NIETS),
    "al_gekoppeld": Melding("fout", "Koppelen via deze pagina kan niet meer", "Deze dienst is al eens gekoppeld. "
                            "Ontkoppelen of opnieuw koppelen gaat via de Hub." + _NIETS),
    "koppelen_bezig": Melding("info", "Koppelen is bezig", "Er loopt al een koppelpoging (eenmalig token of "
                              "koppelcode). Laad de pagina over een minuut opnieuw." + _NIETS),
    "te_veel_pogingen": Melding("fout", "Te veel koppelpogingen", "Ter bescherming tegen raden neemt deze pagina "
                                "tijdelijk geen pogingen aan. Wacht 15 minuten en probeer het dan opnieuw." + _NIETS),
    "invoer_ongeldig": Melding("fout", "Controleer de invoer", "Niet alle velden kloppen; bij elk veld met een fout "
                               "staat wat er mis is." + _NIETS),
    "geheimen_ontbreken": Melding("fout", "Geheimen ontbreken", "De eigen sleutels van deze dienst ontbreken. "
                                  "Herstart de app; bij de eerste start worden ze aangemaakt."),
    "koppelstatus_onleesbaar": Melding("fout", "De koppelstatus is onleesbaar", "Deze dienst kon zijn koppelbestand "
                                       "niet lezen en voert daarom niets uit; hier staat bewust geen formulier. "
                                       "Herstel gaat alleen met een nieuw koppeltoken via de Hub. Het bestand:"),
    # Fouten van de Hub-aanroep (vaste codes §3.5, tekst uit ERROR_MESSAGES_NL); veldfout bij het waarschijnlijke veld
    "hub_onbereikbaar": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["hub_onbereikbaar"], "hub_adres",
                                "Geen antwoord van een Hub op dit adres. Controleer het adres en of de Hub draait."),
    "hub_adres_ongeldig": Melding("fout", _MISLUKT, "Dit adres wijst naar een adres dat geen Hub kan zijn (loopback, "
                                  "link-local of 0.0.0.0).", "hub_adres",
                                  "Dit adres wijst naar loopback, link-local of 0.0.0.0. Gebruik het adres van de NAS."),
    "hub_weigerde": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["hub_weigerde"], "koppelcode",
                            "De Hub weigerde deze koppelcode: verlopen, al gebruikt of ongeldig. Maak een nieuwe in "
                            "de Hub."),
    "hub_vingerafdruk_onjuist": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["hub_vingerafdruk_onjuist"],
                                        "vingerafdruk", "De sleutels van de Hub op dit adres hebben een andere "
                                        "vingerafdruk. Vergelijk adres en vingerafdruk met de Hub."),
    "hub_antwoord_ongeldig": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["hub_antwoord_ongeldig"], "hub_adres",
                                     "Het antwoord op dit adres is geen geldig antwoord van een Hub."),
    "hub_incompatibel": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["hub_incompatibel"]),
    "enroll_endpoint_ontbreekt": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["enroll_endpoint_ontbreekt"],
                                         "hub_adres", "Op dit adres draait geen Hub die koppelen kent."),
    "rate_limited": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["rate_limited"]),
    "ingetrokken_door_hub": Melding("fout", _MISLUKT, ERROR_MESSAGES_NL["ingetrokken_door_hub"]),
}
GEKOPPELD = Melding("info", "Gekoppeld met de Hub", "De Hub heeft de koppeling bevestigd. Vergelijk de vingerafdruk "
                    "hieronder met die in de Hub.")
HOST_HINT = Melding("info", "Koppelen via dit adres kan niet", "Je opent deze pagina via een domeinnaam. Koppelen via "
                    "dit formulier kan alleen via het IP-adres van de NAS of een lokale naam (zoals een naam die "
                    "eindigt op .local). Koppel via de Hub (aanbevolen: een eenmalig koppeltoken in het "
                    "TrueNAS-formulier van deze app) of open deze pagina via het IP-adres.")
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

    def retry_after(self, source: str) -> int:
        """Seconden tot deze bron weer een poging mag doen (header ``Retry-After`` bij 429, §5); minstens 1."""
        now = time.monotonic()
        waits: list[float] = []
        with self._lock:
            hits = self._by_source.get(source)
            if hits and len(hits) >= self.per_source:
                waits.append(self.window_s - (now - hits[0]))
            if len(self._all) >= self.total:
                waits.append(self.window_s - (now - self._all[0]))
        return max(1, math.ceil(max(waits, default=self.window_s)))


class MeldingGeheugen:
    """§3.7: een melding na een geslaagde poging hoort bij déze browser en vervalt na 15 minuten. De sleutel staat in
    het geheugen (één API-proces), gebonden aan de CSRF-cookie van de browser die de poging deed."""

    def __init__(self, geldig_s: float = MELDING_GELDIG_S, maximum: int = 200) -> None:
        self.geldig_s, self.maximum = geldig_s, maximum
        self._items: dict[str, tuple[str, str, float]] = {}
        self._lock = threading.Lock()

    def bewaar(self, code: str, browser: str) -> str:
        sleutel = secrets.token_urlsafe(18)
        with self._lock:
            now = time.monotonic()
            self._items = {k: v for k, v in self._items.items() if now - v[2] < self.geldig_s}
            while len(self._items) >= self.maximum:
                self._items.pop(next(iter(self._items)))
            self._items[sleutel] = (code, browser, now)
        return sleutel

    def lees(self, sleutel: str | None, browser: str) -> str | None:
        if not sleutel or not browser:
            return None
        with self._lock:
            item = self._items.get(sleutel)
        if item is None or time.monotonic() - item[2] >= self.geldig_s:
            return None
        code, eigenaar, _ = item
        return code if hmac.compare_digest(eigenaar.encode(), browser.encode()) else None


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
    """Koppeltoestand voor de pagina: precies één blok van het sjabloon plus de vlag ``bezig``."""

    blok: str  # koppelformulier | gekoppeld | ontkoppeld | ingetrokken | koppelstatus_onleesbaar
    woord: str  # voor de titel
    rij: dict[str, str] | None  # rij in de controles (None = gekoppeld en bevestigd: alleen het neutrale blok)
    bezig: bool = False
    bezig_sinds: datetime | None = None


def _rij(toon: str, label: str, reden: str, reden_code: str = "") -> dict[str, str]:
    """Controlerij (ONTWERPTAAL §13.2): toon uit de vaste lijst, het woord, de reden en eventueel een code. Een woord
    heeft op de hele pagina één uiterlijk (§1.8, §7): "Niet gekoppeld"/"Ontkoppeld" met link-2-off, "Onleesbaar" als
    Storing, net als in het koppelblok."""
    return {"toon": toon, "label": label, "reden": reden, "reden_code": reden_code}


def _koppeling(link: Link) -> Koppeling:
    pending = link.pending if not link.corrupt else None
    bezig = pending is not None
    sinds = _parse(pending.get("started_at")) if pending else None
    if link.onleesbaar:
        return Koppeling("koppelstatus_onleesbaar", "koppelstatus onleesbaar", _rij(
            "storing", "Onleesbaar", "Het koppelbestand is geen geldig bestand van deze dienst; de API vertrouwt geen "
            "enkele aanmelding van de Hub. Herstel staat hieronder bij Koppeling met de Hub.", "koppelstatus_onleesbaar"),
            bezig, sinds)
    if link.state == "gekoppeld":
        if link.doc.get("confirmed_at"):
            return Koppeling("gekoppeld", "gekoppeld", None, bezig, sinds)
        return Koppeling("gekoppeld", "gekoppeld", _rij(
            "beperkt", "Beperkt", "Gekoppeld, maar nog niet bevestigd door de Hub (services/self); de dienst probeert "
            "het opnieuw."), bezig, sinds)
    if link.state == "ingetrokken":
        return Koppeling("ingetrokken", "ingetrokken", _rij(
            "storing", "Storing", "Ingetrokken door de Hub: de API weigert elke aanvraag."), bezig, sinds)
    if link.state == "ontkoppeld":
        return Koppeling("ontkoppeld", "ontkoppeld", _rij(
            "niet-gekoppeld", "Ontkoppeld", "Ontkoppeld door de Hub: de API weigert elke aanvraag."), bezig, sinds)
    if bezig:
        tijd = f" sinds {sinds:%H:%M} UTC" if sinds else ""
        return Koppeling("koppelformulier", "koppelen bezig", _rij(
            "niet-gekoppeld", "Niet gekoppeld", f"Er loopt een koppelpoging{tijd}."), True, sinds)
    return Koppeling("koppelformulier", "niet gekoppeld", _rij(
        "niet-gekoppeld", "Niet gekoppeld", "Nog niet gekoppeld: zonder koppeling weigert de API elke aanvraag; alleen "
        "deze pagina, /healthz en /readyz werken."))


def _gereed(ready: bool, link: Link, koppeling: Koppeling) -> str | None:
    """§3.7: ``None`` = Gereed; anders de reden waarom niet."""
    if not ready:
        return "/readyz meldt not_ready (eigen database, schema of geheimen)"
    if koppeling.blok == "koppelstatus_onleesbaar":
        return "koppelstatus onleesbaar"
    if koppeling.blok == "ingetrokken":
        return "ingetrokken door de Hub"
    if koppeling.blok == "ontkoppeld":
        return "ontkoppeld door de Hub"
    if not link.linked:
        return "wacht op koppeling met de Hub"
    if not link.doc.get("confirmed_at"):
        return "wacht op bevestiging door de Hub"
    return None


def _totaal(worst: str, ready: bool, niet_gereed: str | None, koppeling: Koppeling) -> tuple[str, str, str, str]:
    """(toon, label, toelichting, gereed). Nooit groen zonder Gereed (contract 1.3 §3.7/§13, ONTWERPTAAL §13.2)."""
    if not ready:
        toon = worst if worst != "gezond" else "onbekend"
        woord = {"storing": "Storing", "onbekend": "Deels onbekend"}.get(toon, "Beperkt")
        uitleg = {"storing": "de eigen database, het schema of de geheimen werken niet",
                  "onbekend": "niet alle eigen afhankelijkheden zijn te controleren",
                  "beperkt": "niet alle eigen afhankelijkheden werken volledig"}[toon]
        return toon, "Niet gereed", f"{woord} — {uitleg}; zie de controles hieronder.", f"Nee — {niet_gereed}"
    if niet_gereed is not None:
        # de koppelrij staat dan altijd in de controles, dus 'worst' telt haar al mee (minstens Onbekend)
        toon = worst if RANG[worst] >= RANG["onbekend"] else "onbekend"
        woord = {"storing": "Storing", "beperkt": "Beperkt"}.get(toon, "Deels onbekend")
        uitleg = {
            "koppelstatus_onleesbaar": "de koppelstatus is onleesbaar; herstel met een nieuw koppeltoken via de Hub",
            "ingetrokken": "de Hub heeft de koppeling ingetrokken; opnieuw koppelen gaat via de Hub",
            "ontkoppeld": "ontkoppeld door de Hub; opnieuw koppelen gaat via de Hub",
            "gekoppeld": "gekoppeld, maar nog niet bevestigd door de Hub",
        }.get(koppeling.blok, "er loopt een koppelpoging" if koppeling.bezig else "wacht op koppeling met de Hub")
        return toon, "Niet gereed", f"{woord} — {uitleg}.", f"Nee — {niet_gereed}"
    if worst == "gezond":
        return "gezond", "Alles gezond", "Gereed; alle controles zijn gezond.", "Ja"
    label = {"beperkt": "Beperkt", "storing": "Storing", "onbekend": "Deels onbekend"}[worst]
    uitleg = {"beperkt": "één of meer controles zijn beperkt", "storing": "een controle heeft een storing",
              "onbekend": "één of meer controles zijn onbekend"}[worst]
    return worst, label, f"Gereed; {uitleg}.", "Ja"


def _melding_data(melding: Melding | None, *, rol: str, code: str = "", trace: str = "", extra: str = "",
                  pad: str = "", eerste_fout: str | None = None) -> dict[str, Any]:
    """Peil 1.1.1 (E05/E06): ``melding_rol`` alert (fout van dít verzoek) · status (info na dít verzoek) · leeg
    (toestand die al bestond); ``melding_code`` los van ``trace_id``; ``melding_pad`` in mono."""
    if melding is None:
        return {"melding": False}
    data: dict[str, Any] = {
        "melding": True, "melding_toon": melding.toon, "melding_icoon": "storing" if melding.toon == "fout" else "info",
        "melding_rol": rol, "melding_titel": melding.titel, "melding_tekst": melding.tekst + extra,
        "melding_link": False, "melding_code": code, "melding_pad": pad, "trace_id": trace,
    }
    # link naar het veld (COMPONENTEN §25): het veld van de melding, anders het eerste veld met een fout
    if veld := melding.veld or eerste_fout:
        veld_id, naam = VELDEN[veld]
        data.update(melding_link=True, melding_link_doel=veld_id, melding_link_tekst=f"Naar het veld {naam}")
    return data


def _lees_link(request: Request) -> Link:
    try:
        link: Link = request.app.state.hub_link.read()
    except Exception:  # fail-closed: een onverwachte leesfout telt als onleesbaar (geen formulier, geen vertrouwen)
        return Link(corrupt=True)
    if link.linked and not (link.hub_url and _URL_RE.fullmatch(link.hub_url)):
        return Link(corrupt=True)  # opgeslagen Hub-adres ongeldig: nooit als link op de pagina zetten
    return link


def render_page(
    request: Request,
    *,
    code: str | None = None,
    melding: Melding | None = None,
    veldfouten: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Vul het Peil-sjabloon in. ``code``/``melding`` = resultaat van dít verzoek (met trace-ID); zonder melding toont
    de pagina zelf de opgeslagen toestand (onleesbaar, laatste mislukte poging) zonder live-rol en zonder trace-ID."""
    app = request.app
    now = utcnow()
    health = collect_health(app)
    ready, _ = readiness(app)
    link = _lees_link(request)
    koppeling = _koppeling(link)

    rijen: list[dict[str, str]] = []
    for c in health["checks"]:
        at = _parse(c["observed_at"]) or now
        if c["name"] == "hub_link":
            if koppeling.rij is None:
                continue  # gekoppeld en bevestigd: staat neutraal in "Koppeling met de Hub" (nooit groen)
            rij = dict(koppeling.rij)
        elif c["status"] == "unknown" and c["detail"].startswith("meting verouderd"):
            rij = _rij("verouderd", f"Verouderd · laatst bekend {at:%H:%M} UTC", _zin(c["detail"]), "meting_verouderd")
        else:
            toon = TOON.get(c["status"], "onbekend")
            rij = _rij(toon, LABEL[toon], "" if toon == "gezond" else _zin(c["detail"]))
        rijen.append({"naam": CHECK_NL.get(c["name"], c["name"]), "iso": iso_z(at) or "", "tijd": f"{at:%H:%M} UTC",
                      **rij})
    worst = max((KLASSE[r["toon"]] for r in rijen if r["toon"] in KLASSE), key=RANG.__getitem__, default="onbekend")
    niet_gereed = _gereed(ready, link, koppeling)
    totaal_toon, totaal_label, toelichting, gereed = _totaal(worst, ready, niet_gereed, koppeling)

    fouten = veldfouten or {}
    eerste = next((v for v in VELDEN if v in fouten), None)
    cookie = request.cookies.get(CSRF_COOKIE, "")
    browser = cookie if _CSRF_RE.fullmatch(cookie) else ""
    formulier = koppeling.blok == "koppelformulier" and not koppeling.bezig
    data_melding: dict[str, Any]
    if melding is not None:  # resultaat van dít verzoek
        data_melding = _melding_data(melding, rol="alert" if melding.toon == "fout" else "status", code=code or "",
                                     trace=trace_id_of(request), eerste_fout=eerste)
    elif koppeling.blok == "koppelstatus_onleesbaar":  # blijvende toestand: code en pad, geen trace-ID, geen live-rol
        data_melding = _melding_data(MELDINGEN["koppelstatus_onleesbaar"], rol="", code="koppelstatus_onleesbaar",
                                     pad=str(app.state.settings.secrets_dir / FILE_NAME))
    elif (app.state.melding_geheugen.lees(request.query_params.get("melding"), browser) == "gekoppeld"
          and link.linked and link.doc.get("confirmed_at")):  # na dít verzoek; spreekt de koppelstatus nooit tegen
        data_melding = _melding_data(GEKOPPELD, rol="status")
    elif formulier and not host_header_allowed(request.headers.get("host")):
        data_melding = _melding_data(HOST_HINT, rol="", code="host_niet_toegestaan")
    elif not koppeling.bezig and (err := link.public()["last_error"]) and not (
            koppeling.blok == "ingetrokken" and err["code"] == "ingetrokken_door_hub"):
        titel = "Laatste koppelpoging is mislukt" if koppeling.blok == "koppelformulier" else "Melding van de koppeling"
        data_melding = _melding_data(Melding("fout", titel, err["message_nl"]), rol="", code=err["code"],
                                     extra=f" Sinds {nl_datum(_parse(err['at']))}.")
    else:
        data_melding = {"melding": False}

    # Korte, vaste tekst: een skiplink die op 320 px over twee regels loopt, verbergt het sjabloon (1.1.1) met transform.
    spring = (VELDEN[eerste][0], "Naar het veld met de fout") if eerste else ("inhoud", "Naar inhoud")
    token = browser or secrets.token_urlsafe(24)
    titel_melding = "koppelen mislukt" if melding is not None and melding.toon == "fout" else koppeling.woord
    hub_url = link.hub_url if link.linked else None

    data: dict[str, Any] = {
        "voorbeeld": False,
        "dienst_naam": DIENST_NAAM, "dienst_omschrijving": DIENST_OMSCHRIJVING,
        "titel_toestand": f"{totaal_label.lower()}, {titel_melding}",
        "spring_doel": spring[0], "spring_tekst": spring[1],
        **data_melding,
        "totaal_toon": totaal_toon, "totaal_label": totaal_label, "totaal_toelichting": toelichting,
        "checks": rijen, "gereed": gereed, "versie": __version__, "bron": BRON,
        "bijgewerkt": nl_datum(now), "bijgewerkt_iso": iso_z(now) or "",
        "gekoppeld": koppeling.blok == "gekoppeld", "ontkoppeld": koppeling.blok == "ontkoppeld",
        "ingetrokken": koppeling.blok == "ingetrokken", "koppelformulier": koppeling.blok == "koppelformulier",
        "koppelstatus_onleesbaar": koppeling.blok == "koppelstatus_onleesbaar",
        "koppelen_bezig": koppeling.bezig,
        "koppelen_bezig_sinds": f"{koppeling.bezig_sinds:%H:%M} UTC" if koppeling.bezig_sinds else "",
        "koppelen_bezig_iso": iso_z(koppeling.bezig_sinds) or "",
        "gekoppeld_sinds": nl_datum(_parse(link.doc.get("linked_at"))) if link.linked else "",
        "hub_adres": hub_url or "", "hub_vingerafdruk": link.keyset_fingerprint or "",
        "csrf_token": token, "koppelcode_geldig": KOPPELCODE_GELDIG,
        "fout_hub_adres": fouten.get("hub_adres", ""), "fout_koppelcode": fouten.get("koppelcode", ""),
        "fout_vingerafdruk": fouten.get("vingerafdruk", ""),
        "schrijfsleutel_formulier": False, "executor_beheer": False,  # alleen de executor (§3.9)
        "dienst_blok_titel": "Wat deze versie wel en niet doet",
        "dienst_blok": [
            {"naam": "Installeren, bijwerken, terugzetten, verwijderen en verhuizen", "toon": "nog-niet",
             "label": "Nog niet beschikbaar",
             "reden": "Volgt in fase P2. Tot dan antwoordt de API met capability_unavailable en voert deze dienst "
                      "niets uit op de NAS. Dit is geen storing.", "reden_code": "capability_unavailable"},
            {"naam": "Wijzigingen op TrueNAS", "toon": "beleid", "label": "Alleen via de executor",
             "reden": "Deze dienst heeft geen TrueNAS-schrijfsleutel. Vanaf fase P2 loopt uitvoering uitsluitend via "
                      "de executor van de Hub, met goedkeuring volgens het risicobeleid.", "reden_code": ""},
        ],
    }
    body = render(statuspagina_template(), data)
    response = HTMLResponse(body, status_code=status_code, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "same-origin", "Content-Security-Policy": CSP})
    response.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return response


def herkomst_probleem(request: Request) -> str | None:
    """§3.7 punten 1–4: ``herkomst_ongeldig`` (Origin ontbreekt, ``null`` of ≠ ``<schema>://<Host>``; of
    ``Sec-Fetch-Site`` anders dan ``same-origin``/``none``) of ``host_niet_toegestaan`` (DNS-rebinding)."""
    origin = request.headers.get("origin")
    host = request.headers.get("host")
    if not origin or not host or origin == "null":
        return "herkomst_ongeldig"
    if not hmac.compare_digest(origin.encode(), f"{request.url.scheme}://{host}".encode()):
        return "herkomst_ongeldig"
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in SEC_FETCH_SITE_OK:
        return "herkomst_ongeldig"
    if not host_header_allowed(host):
        return "host_niet_toegestaan"
    return None


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
    """Eén formulier-POST = één logregel met code en trace-ID (§3.7), nooit met het ingevulde adres of een geheim."""
    code, response = _pair(request, hub_adres, koppelcode, hub_vingerafdruk, csrf)
    log.log(logging.INFO if code == "gekoppeld" else logging.WARNING,
            "koppelpoging via de statuspagina: %s (HTTP %s, trace %s)", code, response.status_code,
            trace_id_of(request))
    return response


def _weiger(request: Request, code: str, status: int, veldfouten: dict[str, str] | None = None) -> tuple[str, Response]:
    return code, render_page(request, code=code, melding=MELDINGEN[code], veldfouten=veldfouten, status_code=status)


def _pair(request: Request, hub_adres: str, koppelcode: str, hub_vingerafdruk: str,
          csrf: str) -> tuple[str, Response]:
    # 1. herkomst (telt niet als poging: het verzoek kwam niet van deze pagina)
    if (probleem := herkomst_probleem(request)) is not None:
        return _weiger(request, probleem, 403)
    cookie = request.cookies.get(CSRF_COOKIE, "")
    if not cookie or len(csrf) > MAX_LEN["csrf"] or not hmac.compare_digest(cookie.encode(), csrf.encode()):
        return _weiger(request, "csrf_ongeldig", 403)
    # 2. elke POST die de herkomstcontrole doorstaat, telt als poging (ook al gekoppeld, onleesbaar, bezig, ongeldig)
    state = request.app.state
    source = request.client.host if request.client else "onbekend"  # TCP-verbinding; nooit X-Forwarded-For
    if not state.pairing_limiter.allow(source):
        refused, response = _weiger(request, "te_veel_pogingen", 429)
        response.headers["Retry-After"] = str(state.pairing_limiter.retry_after(source))  # §5: 429 + Retry-After
        return refused, response
    # 3. toestand: onleesbaar en al eens gekoppeld gaan nooit naar de Hub; een lopende poging ook niet
    link = _lees_link(request)
    if link.onleesbaar:
        return _weiger(request, "koppelstatus_onleesbaar", 409)
    if link.ever_linked:
        return _weiger(request, "al_gekoppeld", 409)
    if link.pending is not None:
        return _weiger(request, "koppelen_bezig", 409)
    # 4. invoer als geheel controleren, nooit afkappen
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
    code: str | None = None
    if len(koppelcode) > MAX_LEN["koppelcode"]:
        fouten["koppelcode"] = VELDFOUT["te_lang"]
    elif (code := normalize_pairing_code(koppelcode)) is None:
        fouten["koppelcode"] = VELDFOUT["koppelcode_ongeldig"]
    fingerprint: str | None = None
    if len(hub_vingerafdruk) > MAX_LEN["hub_vingerafdruk"]:
        fouten["vingerafdruk"] = VELDFOUT["te_lang"]
    elif (fingerprint := normalize_fingerprint(hub_vingerafdruk)) is None:
        fouten["vingerafdruk"] = VELDFOUT["vingerafdruk_ongeldig"]
    if fouten or url is None or code is None or fingerprint is None:
        return _weiger(request, "invoer_ongeldig", 422, fouten)
    if state.identity is None:
        return _weiger(request, "geheimen_ontbreken", 503)
    # 5. de poging zelf
    try:
        with state.http_client_factory() as client:
            enroll_once(state.hub_link, client, state.identity, state.settings, kind="koppelcode", secret=code,
                        hub_url=url, expected_fingerprint=fingerprint, logregel=False)
            confirmed = confirm(state.hub_link, client, HubSignals(), logregel=False)
    except EnrollmentError as exc:
        if not exc.final:  # de gebruiker wacht: niet op de achtergrond doorproberen, adres niet bewaren
            mark_failed(state.hub_link, exc.code, env_token_sha=None, secret_sha=sha256_hex(code))
        melding = MELDINGEN.get(exc.code) or Melding("fout", _MISLUKT, ERROR_MESSAGES_NL.get(exc.code, "Onbekende fout."))
        veld = {melding.veld: melding.veldfout} if melding.veld and melding.veldfout else None
        return exc.code, render_page(request, code=exc.code, melding=melding, veldfouten=veld, status_code=400)
    except PairingRefused as exc:  # onder de vergrendeling vastgesteld: intussen gekoppeld of een andere poging
        refused = {"ever_linked": "al_gekoppeld", "onleesbaar": "koppelstatus_onleesbaar"}.get(exc.reason,
                                                                                               "koppelen_bezig")
        return _weiger(request, refused, 409)
    except HubLinkCorrupt:
        return _weiger(request, "koppelstatus_onleesbaar", 409)
    if confirmed == "ingetrokken":  # de Hub kent het nieuwe token al niet meer (bijv. een herhaald verzoek)
        return "ingetrokken_door_hub", render_page(request, code="ingetrokken_door_hub",
                                                   melding=MELDINGEN["ingetrokken_door_hub"], status_code=400)
    sleutel = state.melding_geheugen.bewaar("gekoppeld", request.cookies.get(CSRF_COOKIE, ""))
    return "gekoppeld", RedirectResponse(f"/?melding={sleutel}", status_code=303)
