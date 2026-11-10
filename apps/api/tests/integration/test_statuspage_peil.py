"""Statuspagina in het Peil-sjabloon 1.1.1 (JP11; ONTWERPTAAL §13.2, contract 1.3 §3.5/§3.7): rendering per toestand.

Toestanden: niet gekoppeld · koppelen mislukt · gekoppeld · ingetrokken · niet gereed (plus ontkoppeld, koppelen
bezig, koppelstatus onleesbaar, beperkt, verouderde hartslag en een niet-toegestaan adres). Elke pagina: geen
JavaScript, CSP ``default-src 'none'``, precies één koppeltoestand (plus eventueel de vlag "koppelen bezig"), geen
ontkoppelen of ontkoppelcode, eerste signaal nooit groen zonder Gereed (``/readyz`` + gekoppeld + bevestigd), de fout
van dít verzoek met ``role="alert"``, code en selecteerbare trace-ID; een toestand die al bestond zonder live-rol.

Met ``STATUSPAGINA_SNAPSHOTS=<map>`` schrijft elke test zijn pagina (HTML + antwoordkoppen) naar die map; daar draait
``tests/ui/controle.mjs`` (axe-core, overloop op 320 px, aanraakdoelen, CSP, schermafdrukken) op.
"""

from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from lifecycle.app import create_app
from lifecycle.config import Settings
from lifecycle.hublink import HubLinkStore

APP = "jelly-leon-platform"
BASE = "http://nas.local"  # §3.7: Host-beperking (IP-adres of lokale naam)
ORIGIN = {"Origin": BASE}
CSP = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
SNAPSHOTS = os.environ.get("STATUSPAGINA_SNAPSHOTS")


class Structuur(HTMLParser):
    """Telt wat de Peil-controles (A-02, A-11) vragen: h1, id's, aria-verwijzingen, scripts en event-attributen."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.refs: list[str] = []
        self.h1 = 0
        self.scripts = 0
        self.handlers: list[str] = []
        self.inputs: list[dict[str, str | None]] = []
        self.forms: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("id"):
            self.ids.append(str(a["id"]))
        for key in ("aria-describedby", "aria-labelledby"):
            self.refs += (a.get(key) or "").split()
        if tag == "a" and (a.get("href") or "").startswith("#"):
            self.refs.append(str(a["href"])[1:])
        if tag == "use" and (a.get("href") or "").startswith("#"):
            self.refs.append(str(a["href"])[1:])
        self.h1 += tag == "h1"
        self.scripts += tag == "script"
        self.handlers += [k for k in a if k.startswith("on")]
        if tag == "input":
            self.inputs.append(a)
        if tag == "form":
            self.forms.append(a.get("action"))


def check_page(html: str, headers: Any) -> Structuur:
    """Regels die voor elke toestand gelden."""
    assert html.startswith('<!doctype html>\n<html lang="nl">'), html[:80]
    assert "/*! Peil statuspagina.css 1.1.1 · bron ai-server-manager/design/ontwerptaal" in html
    assert "HERKOMST" not in html and "{{" not in html
    assert not re.search(r"<!--/?(ALS|NIET|ELK):", html), "onverwerkte plek"
    assert "javascript:" not in html.lower() and "<link" not in html and "<img" not in html
    s = Structuur()
    s.feed(html)
    assert s.scripts == 0 and s.handlers == [], "geen JavaScript op de statuspagina"
    assert s.h1 == 1 and len(s.ids) == len(set(s.ids)), "één h1 en unieke id's"
    assert set(s.refs) <= set(s.ids), f"kapotte verwijzingen: {set(s.refs) - set(s.ids)}"
    assert headers["Content-Security-Policy"] == CSP and headers["X-Frame-Options"] == "DENY"
    assert headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Type"].startswith("text/html")
    # alleen de executor heeft een schrijfsleutel- of beheerblok; nergens ontkoppelen of een ontkoppelcode (§3.7)
    assert set(s.forms) <= {"/koppelen"}
    assert "ontkoppelcode" not in html.lower() and "Ontkoppelen van de Hub" not in html
    assert len(koppeltoestanden(html)) == 1, koppeltoestanden(html)  # precies één koppeltoestand (§3.7)
    if 'class="pil pil-gezond"><svg aria-hidden="true"><use href="#i-gezond"/></svg>Alles gezond' in html:
        assert "<dt>Gereed</dt><dd>Ja</dd>" in html
    if "Gekoppeld met de Hub" not in html and "role=" in html:  # alleen de fout of info van dít verzoek is live
        assert html.count('role="') == 1
    assert '<form method="post" action="/koppelen" autocomplete="off" novalidate>' in html or "<form" not in html
    return s


def koppeltoestanden(html: str) -> set[str]:
    sectie = html.split('id="h-koppeling"', 1)[1].split("</section>", 1)[0]
    found = set()
    for naam, label in (("koppelformulier", "Niet gekoppeld"), ("gekoppeld", "Gekoppeld"),
                        ("ontkoppeld", "Ontkoppeld"), ("ingetrokken", "Ingetrokken"),
                        ("koppelstatus_onleesbaar", "Onleesbaar")):
        if f"</svg>{label}</span></p>" in sectie:
            found.add(naam)
    return found


def bezig(html: str) -> bool:
    return "</svg>Koppelen bezig</span></p>" in html.split('id="h-koppeling"', 1)[1].split("</section>", 1)[0]


def formulier(html: str) -> bool:
    return 'action="/koppelen"' in html


def totaal(html: str) -> tuple[str, str]:
    m = re.search(r'<p class="totaal"><span class="pil pil-(\w+)">.*?</svg>([^<]+)</span>'
                  r'(?:<span class="sr">: </span>)?<span>([^<]+)</span>', html)
    assert m, "geen totaalstatus"
    return m.group(1), m.group(2) + " | " + m.group(3)


def snapshot(naam: str, response: Any) -> None:
    if not SNAPSHOTS:
        return
    out = Path(SNAPSHOTS)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{naam}.html").write_text(response.text, encoding="utf-8")
    keep = ("Content-Security-Policy", "Content-Type", "X-Frame-Options", "X-Content-Type-Options",
            "Referrer-Policy", "Cache-Control")
    (out / f"{naam}.headers.json").write_text(json.dumps({k: response.headers[k] for k in keep}, indent=1))


def unlinked(make_app: Any, ip: str = "192.0.2.10") -> TestClient:
    return TestClient(make_app(hub_url=None, linked=False), base_url=BASE, client=(ip, 50000), headers=ORIGIN)


def csrf(page: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', page)
    assert m, "geen koppelformulier"
    return m.group(1)


def form(code: str, fingerprint: str, token: str, hub_url: str = "http://hub.test") -> dict[str, str]:
    return {"hub_adres": hub_url, "koppelcode": code, "hub_vingerafdruk": fingerprint, "csrf": token}


def heartbeat(app: Any) -> None:
    from lifecycle.models import WorkerHeartbeat
    from lifecycle.timeutil import utcnow

    with app.state.session_factory() as session:
        session.merge(WorkerHeartbeat(name="worker", beat_at=utcnow(), detail={}))
        session.commit()


# --------------------------------------------------------------------------------------------------- toestanden
def test_niet_gekoppeld_shows_the_form_and_is_not_ready(make_app: Any) -> None:
    with unlinked(make_app) as c:
        heartbeat(c.app)  # zoals in de image: de worker draait
        r = c.get("/")
        assert r.status_code == 200
        s = check_page(r.text, r.headers)
        snapshot("niet-gekoppeld", r)
        assert koppeltoestanden(r.text) == {"koppelformulier"} and formulier(r.text) and not bezig(r.text)
        velden = {str(i.get("name")): i for i in s.inputs}
        assert set(velden) == {"csrf", "hub_adres", "koppelcode", "hub_vingerafdruk"}
        for naam in ("hub_adres", "koppelcode", "hub_vingerafdruk"):  # alle drie verplicht, vingerafdruk ook
            assert "required" in velden[naam] and velden[naam].get("value") is None, naam
        assert r.text.count('<span class="verplicht">(verplicht)</span>') == 3  # Peil 1.1.1 (E09)
        assert velden["csrf"]["type"] == "hidden" and velden["csrf"]["value"] == c.cookies.get("jlp_csrf")
        assert "XXXX-XXXX-XXXX-XXXX" in r.text and "Geldig 60 minuten" in r.text
        # één woord, één uiterlijk (ONTWERPTAAL §1.8, §7; 1.1.1 E04): "Niet gekoppeld" met link-2-off, in de controles
        # én in het koppelblok
        pil = 'pil-niet-gekoppeld"><svg aria-hidden="true"><use href="#i-niet-gekoppeld"/></svg>Niet gekoppeld'
        assert r.text.count(pil) == 2
        assert ('<span class="naam">Koppeling met de Hub<span class="sr">:</span></span><span class="pil '
                'pil-niet-gekoppeld">') in r.text and "Nog niet gekoppeld: zonder" in r.text
        # §11.1: "In orde" is nooit een statuswoord (de vaste uitleg van Gereed mag het als gewone zin gebruiken)
        assert not re.search(r'class="pil[^"]*">(?:<svg[^>]*>.*?</svg>)?[^<]*in orde', r.text, re.I)
        toon, tekst = totaal(r.text)
        assert toon == "onbekend" and tekst == "Niet gereed | Deels onbekend — wacht op koppeling met de Hub."
        assert "<dt>Gereed</dt><dd>Nee — wacht op koppeling met de Hub</dd>" in r.text
        assert "Gereed betekent: de eigen controles zijn in orde" in r.text  # uitleg Gereed ↔ /readyz (1.1.1)
        assert r.text.count('class="melding') == 0  # nog niets gebeurd: geen melding
        assert "<title>Jelly Leon Platform — niet gereed, niet gekoppeld</title>" in r.text
        # ?gekoppeld=1 of een verzonnen sleutel kan iedereen intypen: nooit "gekoppeld" melden (§3.7)
        for q in ("?gekoppeld=1", "?melding=abc"):
            assert "De Hub heeft de koppeling bevestigd" not in c.get("/" + q).text


def test_koppelen_mislukt_shows_code_and_copyable_trace_id(make_app: Any, hub: Any, data_dir: Path) -> None:
    sim = hub.state.sim
    code = sim.add_grant("koppelcode", APP)
    with unlinked(make_app) as c:
        heartbeat(c.app)
        r = c.post("/koppelen", data=form(code, "MEGG-8GFG-2E4Y-K2RQ", csrf(c.get("/").text)))
        assert r.status_code == 400
        check_page(r.text, r.headers)
        snapshot("koppelen-mislukt", r)
        trace = r.headers["X-Trace-Id"]
        assert '<div class="melding melding-fout" role="alert" aria-labelledby="melding-titel"' in r.text
        assert "<strong>Koppelen is mislukt</strong>" in r.text
        assert '<p class="meta">Code <code>hub_vingerafdruk_onjuist</code></p>' in r.text
        assert f'Trace-ID (selecteer en kopieer)</p><code class="selecteer">{trace}</code>' in r.text
        assert 'aria-describedby="vinger-hulp vinger-fout" aria-invalid="true"' in r.text
        assert '<a class="spring" href="#vingerafdruk">' in r.text and 'href="#vingerafdruk">Naar het veld' in r.text
        assert koppeltoestanden(r.text) == {"koppelformulier"} and formulier(r.text)  # nog nooit gekoppeld
        assert "hub.test" not in r.text and "MEGG-8GFG-2E4Y-K2RQ" not in r.text  # invoer nooit weerspiegeld
        assert totaal(r.text)[0] != "gezond"
        # daarna (GET): de opgeslagen fout met code, zonder trace-ID en zonder live-rol (bestond al bij het laden)
        page = c.get("/").text
        assert "Laatste koppelpoging is mislukt" in page and "Code <code>hub_vingerafdruk_onjuist</code>" in page
        assert 'class="selecteer"' not in page and 'role="alert"' not in page
    assert json.loads((data_dir / "secrets" / "hub-link.json").read_text())["hub_url"] is None


def test_koppelen_invoer_ongeldig_marks_every_field_without_hub_call(make_app: Any, hub: Any) -> None:
    sim = hub.state.sim
    with unlinked(make_app) as c:
        r = c.post("/koppelen", data=form("ABCD", "MEGG", csrf(c.get("/").text), hub_url="http://127.0.0.1:30400"))
        assert r.status_code == 422
        check_page(r.text, r.headers)
        snapshot("koppelen-invoer-ongeldig", r)
        assert r.text.count('aria-invalid="true"') == 3 and r.text.count('class="veldfout"') == 3
        assert "loopback" in r.text and "12 tekens" in r.text and "16 tekens" in r.text
        assert "Code <code>invoer_ongeldig</code>" in r.text and '<a class="spring" href="#hub">' in r.text
        # de melding bovenaan linkt naar het eerste foute veld (COMPONENTEN §25), ook zonder Hub-fout
        assert '<a href="#hub">Naar het veld Adres van de Hub</a>' in r.text
        assert '<a class="spring" href="#hub">Naar het veld met de fout</a>' in r.text  # kort: past op 320 px
        r = c.post("/koppelen", data=form("x" * 33, "y" * 65, csrf(c.get("/").text), hub_url="h" * 513))
        assert r.status_code == 422 and r.text.count('aria-invalid="true"') == 3  # te lang: HTML, geen JSON
        assert r.text.count("Te lang; kopieer de waarde uit de Hub.") == 3
        r = c.post("/koppelen", data={"csrf": csrf(c.get("/").text)})  # velden ontbreken: zelfde HTML-fout
        assert r.status_code == 422 and r.text.count('aria-invalid="true"') == 3
    assert sim.calls["enroll"] == 0


def test_csrf_failure_is_an_html_page_with_trace(make_app: Any) -> None:
    with unlinked(make_app) as c:
        c.get("/")
        r = c.post("/koppelen", data=form("ABCD-EFGH-JKMN", "MEGG-8GFG-2E4Y-K2RQ", "fout"))
        assert r.status_code == 403
        check_page(r.text, r.headers)
        assert "Code <code>csrf_ongeldig</code>" in r.text and r.headers["X-Trace-Id"] in r.text


def test_gekoppeld_is_neutral_without_form_and_all_healthy_only_when_measured(make_app: Any, hub: Any) -> None:
    sim = hub.state.sim
    app = make_app()
    with TestClient(app, base_url=BASE, headers=ORIGIN) as c:
        r = c.get("/")
        check_page(r.text, r.headers)
        assert totaal(r.text) == ("onbekend", "Deels onbekend | Gereed; één of meer controles zijn onbekend.")
        assert "Nog geen hartslag van de worker" in r.text  # onbekend met reden, nooit groen
        heartbeat(app)
        sleutel = app.state.melding_geheugen.bewaar("gekoppeld", c.cookies.get("jlp_csrf") or "")
        r = c.get(f"/?melding={sleutel}")  # zoals na een geslaagde poging van deze browser
        s = check_page(r.text, r.headers)
        snapshot("gekoppeld", r)
        assert koppeltoestanden(r.text) == {"gekoppeld"} and "csrf" not in {i.get("name") for i in s.inputs}
        assert totaal(r.text) == ("gezond", "Alles gezond | Gereed; alle controles zijn gezond.")
        assert '<span class="pil pil-neutraal"><svg aria-hidden="true"><use href="#i-gekoppeld"/></svg>Gekoppeld' \
            in r.text  # verbonden is geen gezondheid
        assert '<span class="naam">Koppeling met de Hub' not in r.text  # geen groene koppelrij
        assert f"<code>{sim.keyset_fingerprint()}</code>" in r.text
        assert '<a class="knop knop-secundair" href="http://hub.test">Hub openen</a>' in r.text  # 1.1.1: knop 44 px
        assert "Ontkoppelen of opnieuw koppelen gaat via de Hub" in r.text
        assert '<div class="melding melding-info" role="status"' in r.text and "De Hub heeft de koppeling" in r.text
        assert "svc_" not in r.text  # nooit het service-token
        assert '<dt>Bron</dt><dd>Eigen controles (/readyz), de hartslag' in r.text
        # dienstblok (1.1.1): nog-niet en beleid tellen niet mee in het totaal
        assert 'pil-nog-niet"><svg aria-hidden="true"><use href="#i-nog-niet"/></svg>Nog niet beschikbaar' in r.text
        assert 'pil-beleid"><svg aria-hidden="true"><use href="#i-beleid"/></svg>Alleen via de executor' in r.text


def test_gekoppeld_unconfirmed_is_not_ready(clean_db: str, data_dir: Path, hub: Any) -> None:
    """§3.7: Gereed = /readyz in orde én gekoppeld én bevestigd (``confirmed_at``)."""
    from conftest import link_with_hub

    link_with_hub(data_dir, hub.state.sim, confirmed=False)
    app = create_app(Settings(data_dir=data_dir, database_url=clean_db),
                     http_client_factory=lambda: TestClient(hub, base_url="http://hub.test"))
    with TestClient(app, base_url=BASE) as c:
        heartbeat(app)
        r = c.get("/")
        check_page(r.text, r.headers)
        snapshot("beperkt", r)
        assert totaal(r.text) == ("beperkt", "Niet gereed | Beperkt — gekoppeld, maar nog niet bevestigd door de Hub.")
        assert "<dt>Gereed</dt><dd>Nee — wacht op bevestiging door de Hub</dd>" in r.text
        assert '<span class="pil pil-beperkt"><svg aria-hidden="true"><use href="#i-beperkt"/></svg>Beperkt</span>' \
            in r.text  # vast gezondheidswoord, de koppeltoestand staat in het koppelblok
        assert "De Hub heeft de koppeling bevestigd" not in r.text  # niet bevestigd = geen succesmelding


def test_ingetrokken_shows_no_form_and_storing(make_app: Any, hub: Any) -> None:
    sim = hub.state.sim
    code = sim.add_grant("koppelcode", APP)
    sim.faults.add("self_401")  # de Hub trekt het token direct in (bevestiging geeft 401)
    with unlinked(make_app) as c:
        heartbeat(c.app)
        r = c.post("/koppelen", data=form(code, sim.keyset_fingerprint(), csrf(c.get("/").text)))
        assert r.status_code == 400 and "Code <code>ingetrokken_door_hub</code>" in r.text
        r = c.get("/")
        check_page(r.text, r.headers)
        snapshot("ingetrokken", r)
        assert koppeltoestanden(r.text) == {"ingetrokken"} and not formulier(r.text)
        assert 'pil-niet-gekoppeld"><svg aria-hidden="true"><use href="#i-niet-gekoppeld"/></svg>Ingetrokken' in r.text
        assert "Opnieuw koppelen gaat via de Hub" in r.text
        assert totaal(r.text) == ("storing", "Niet gereed | Storing — de Hub heeft de koppeling ingetrokken; "
                                             "opnieuw koppelen gaat via de Hub.")
        assert '<span class="naam">Koppeling met de Hub<span class="sr">:</span></span><span class="pil pil-storing">' \
            in r.text


def test_ontkoppeld_and_post_refused(make_app: Any, auth: Any) -> None:
    with TestClient(make_app(), base_url=BASE, headers=ORIGIN) as c:
        assert c.post("/connector/v1/hub-link/unlink", headers=auth("admin", idem=True)).status_code == 200
        r = c.get("/")
        check_page(r.text, r.headers)
        snapshot("ontkoppeld", r)
        assert koppeltoestanden(r.text) == {"ontkoppeld"} and "Opnieuw koppelen gaat via de Hub" in r.text
        assert r.text.count('pil-niet-gekoppeld"><svg aria-hidden="true"><use href="#i-niet-gekoppeld"/></svg>'
                            'Ontkoppeld') == 2  # controles en koppelblok: één uiterlijk
        assert totaal(r.text)[1] == ("Niet gereed | Deels onbekend — ontkoppeld door de Hub; opnieuw koppelen gaat "
                                     "via de Hub.")
        r = c.post("/koppelen", data=form("ABCD-EFGH-JKMN", "MEGG-8GFG-2E4Y-K2RQ", c.cookies.get("jlp_csrf") or ""))
        assert r.status_code == 409 and "Code <code>al_gekoppeld</code>" in r.text and koppeltoestanden(r.text) == {
            "ontkoppeld"}


def test_koppelen_bezig_hides_the_form_and_keeps_the_state(make_app: Any, data_dir: Path) -> None:
    """Peil 1.1.1 (E02) + contract 1.3 §3.7: tijdens een poging geen formulier en geen stappen; de stabiele toestand
    blijft erboven staan; een POST uit een oud tabblad krijgt 409 ``koppelen_bezig``."""
    from lifecycle.enrollment import mark_pending

    mark_pending(HubLinkStore(data_dir / "secrets"), kind="token", secret_sha="a" * 64, hub_url="http://hub.test")
    with unlinked(make_app) as c:
        r = c.get("/")
        check_page(r.text, r.headers)
        snapshot("koppelen-bezig", r)
        assert koppeltoestanden(r.text) == {"koppelformulier"} and bezig(r.text) and not formulier(r.text)
        assert '<ol class="stappen">' not in r.text and "(gestart om <time" in r.text
        assert 'pil-nog-niet"><svg aria-hidden="true"><use href="#i-nog-niet"/></svg>Koppelen bezig' in r.text
        assert totaal(r.text) == ("onbekend", "Niet gereed | Deels onbekend — er loopt een koppelpoging.")
        assert 'class="melding' not in r.text  # het blok zegt het al; geen extra melding
        r = c.post("/koppelen", data=form("ABCD-EFGH-JKMN", "MEGG-8GFG-2E4Y-K2RQ", c.cookies.get("jlp_csrf") or ""))
        assert r.status_code == 409 and "Code <code>koppelen_bezig</code>" in r.text


def test_koppelstatus_onleesbaar_fails_closed(make_app: Any, data_dir: Path) -> None:
    """Contract 1.3 §3.5 + Peil 1.1.1 (E01): geen formulier, pil Storing "Onleesbaar", vaste herstelweg; de melding
    bestond al bij het laden: geen live-rol en geen trace-ID, wel de code en het pad."""
    import os

    path = data_dir / "secrets" / "hub-link.json"
    path.write_text("{kapot")
    os.chmod(path, 0o600)
    with TestClient(make_app(hub_url=None, linked=False), base_url=BASE, headers=ORIGIN) as c:
        heartbeat(c.app)
        r = c.get("/")
        check_page(r.text, r.headers)
        snapshot("onleesbaar", r)
        assert koppeltoestanden(r.text) == {"koppelstatus_onleesbaar"} and not formulier(r.text)
        assert "Herstel gaat alleen met een nieuw koppeltoken" in r.text
        assert '<div class="melding melding-fout" style=' in r.text and 'role="' not in r.text
        assert "Code <code>koppelstatus_onleesbaar</code>" in r.text and 'class="selecteer"' not in r.text
        assert f"<p><code>{path}</code></p>" in r.text  # melding_pad in mono
        assert r.text.count('pil-storing"><svg aria-hidden="true"><use href="#i-storing"/></svg>Onleesbaar') == 2
        assert totaal(r.text) == ("storing", "Niet gereed | Storing — de koppelstatus is onleesbaar; herstel met een "
                                             "nieuw koppeltoken via de Hub.")
        assert "<dt>Gereed</dt><dd>Nee — koppelstatus onleesbaar</dd>" in r.text
        r = c.post("/koppelen", data=form("ABCD-EFGH-JKMN", "MEGG-8GFG-2E4Y-K2RQ", c.cookies.get("jlp_csrf") or ""))
        assert r.status_code == 409 and "Code <code>koppelstatus_onleesbaar</code>" in r.text


def test_onleesbaar_after_replacement_with_pending_env_token(make_app: Any, data_dir: Path) -> None:
    """§3.5: na vervanging (nieuw env-token, ``unreadable_replaced_at``) blijft de toestand "onleesbaar" tot de eerste
    geslaagde koppeling; de lopende poging staat erbij."""
    import os

    from lifecycle.enrollment import mark_pending

    store = HubLinkStore(data_dir / "secrets")
    store.path.write_text("{kapot")
    os.chmod(store.path, 0o600)
    mark_pending(store, kind="token", secret_sha="c" * 64, hub_url="http://hub.test")
    with TestClient(make_app(hub_url=None, linked=False), base_url=BASE, headers=ORIGIN) as c:
        r = c.get("/")
        check_page(r.text, r.headers)
        assert koppeltoestanden(r.text) == {"koppelstatus_onleesbaar"} and bezig(r.text) and not formulier(r.text)


def test_stale_worker_heartbeat_is_verouderd_not_green(make_app: Any) -> None:
    """Peil 1.1.1 (E03/E15) + contract 1.3 §6.2: een verouderde meting heet Verouderd met de tijd van de laatste
    meting, telt als Onbekend en wordt nooit als actueel getoond."""
    from datetime import timedelta

    from lifecycle.models import WorkerHeartbeat
    from lifecycle.timeutil import utcnow

    app = make_app()
    with TestClient(app, base_url=BASE) as c:
        with app.state.session_factory() as session:
            session.merge(WorkerHeartbeat(name="worker", beat_at=utcnow() - timedelta(minutes=20), detail={}))
            session.commit()
        r = c.get("/")
        check_page(r.text, r.headers)
        snapshot("verouderd", r)
        assert 'pil-verouderd"><svg aria-hidden="true"><use href="#i-verouderd"/></svg>Verouderd · laatst bekend' \
            in r.text and "(code <code>meting_verouderd</code>)" in r.text
        assert totaal(r.text)[0] == "onbekend"


def test_niet_gereed_when_the_database_is_down(data_dir: Path, hub: Any) -> None:
    settings = Settings(data_dir=data_dir, database_url="postgresql+psycopg://postgres@127.0.0.1:1/jlp_bestaat_niet")
    with TestClient(create_app(settings), base_url=BASE) as c:
        r = c.get("/")
        assert r.status_code == 200  # de pagina werkt ook zonder database
        check_page(r.text, r.headers)
        snapshot("niet-gereed", r)
        assert totaal(r.text) == ("storing", "Niet gereed | Storing — de eigen database, het schema of de geheimen "
                                             "werken niet; zie de controles hieronder.")
        assert "<dt>Gereed</dt><dd>Nee — /readyz meldt not_ready (eigen database, schema of geheimen)</dd>" \
            in r.text
        assert ('<span class="naam">Eigen database (ingebouwd)<span class="sr">:</span></span><span class="pil '
                'pil-storing">') in r.text
        assert "pil-gezond" not in r.text.split('id="h-koppeling"')[0].split('class="totaal"')[1].split("</p>")[0]


def test_niet_gereed_without_secrets(tmp_path: Path, clean_db: str) -> None:
    settings = Settings(data_dir=tmp_path, database_url=clean_db)  # geen eerste-start-initialisatie
    with TestClient(create_app(settings), base_url=BASE, headers=ORIGIN) as c:
        r = c.get("/")
        check_page(r.text, r.headers)
        assert totaal(r.text)[0] == "storing" and "Geheimen (/appdata/secrets)" in r.text
        token = csrf(r.text)
        r = c.post("/koppelen", data=form("ABCD-EFGH-JKMN", "MEGG-8GFG-2E4Y-K2RQ", token))
        assert r.status_code == 503 and "Code <code>geheimen_ontbreken</code>" in r.text


def test_unreadable_link_status_fails_closed_as_onleesbaar(make_app: Any) -> None:
    """Een onverwachte leesfout (bijv. rechten) is fail-closed: geen formulier, koppelstatus onleesbaar, nooit groen."""
    app = make_app()
    with TestClient(app, base_url=BASE) as c:
        heartbeat(app)

        def kapot() -> Any:
            raise PermissionError("geen toegang")

        app.state.hub_link.read = kapot
        r = c.get("/")
        check_page(r.text, r.headers)
        assert koppeltoestanden(r.text) == {"koppelstatus_onleesbaar"} and not formulier(r.text)
        assert totaal(r.text)[0] == "storing"


def test_host_not_allowed_hint_without_live_role(make_app: Any) -> None:
    """§3.7: wie de pagina via een eigen domeinnaam opent, ziet vooraf dat koppelen via dit formulier niet kan."""
    with TestClient(make_app(hub_url=None, linked=False), base_url="http://jlp.voorbeeld.nl",
                    headers={"Origin": "http://jlp.voorbeeld.nl"}) as c:
        heartbeat(c.app)
        r = c.get("/")
        check_page(r.text, r.headers)
        snapshot("adres-niet-toegestaan", r)
        assert '<div class="melding melding-info" style=' in r.text and "Code <code>host_niet_toegestaan</code>" \
            in r.text and "IP-adres van de NAS" in r.text
