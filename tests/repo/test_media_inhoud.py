"""Bewaking van vooraf bestaande kijkgegevens en taaklogregels in de vijf inhoudsproeven."""

import ast
import json
import re
import secrets
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from e2e.integral.media_inhoud import FIXTURES, Inhoud

PADEN = ("/media/kwaliteitsproef/controle.mkv", "/media/bestaand/bewaard.mkv")
KIJKERS = ("proefkijker-a", "proefkijker-b")


@pytest.fixture
def diensten(tmp_path):
    kijk = {u: {p: {"Played": i == j, "PlaybackPositionTicks": 0 if i == j else (j + 1) * 1_200_000,
                    "IsFavorite": i == 1}
                for j, p in enumerate(PADEN)} for i, u in enumerate(KIJKERS)}
    werklog = {str(i): {"job_id": 1, "ts": "2026-01-01", "level": "info", "message": tekst}
               for i, tekst in enumerate(("Bestaande proefreeks ingepland", "Bestaande voortgang vastgelegd"), 1)}
    registraties = {p: {"user_name": u, "title": p.split("/")[-1], "last_seen": "2026-01-01"}
                   for p, u in zip(PADEN, KIJKERS, strict=True)}

    stand = {"voortgang": 0.625, "sessies": [{"user": u, "title": p.split("/")[-1], "paused": True}
                                             for p, u in zip(PADEN, KIJKERS, strict=True)], "log": True}

    def antwoord(req):
        pad = req.url.path
        if pad == "/Users":
            data = [{"Id": u, "Name": u} for u in kijk]
        elif pad == "/Items":
            data = {"Items": [{"Id": str(PADEN.index(p)), "Path": p, "UserData": waarden}
                              for p, waarden in kijk[req.url.params["UserId"]].items()]}
        elif pad == "/api/v1/jobs":
            data = [{"id": 1, "progress": 0.625}]
        elif pad == "/api/v1/jobs/1":
            data = {"id": 1, "progress": stand["voortgang"], "logs_truncated": False,
                    "logs": [{k: r[k] for k in ("ts", "level", "message")} for r in werklog.values()] if stand["log"]
                    else []}
        elif pad == "/api/v1/mediaserver/status":
            data = {"sessions": {"watching": stand["sessies"]}}
        elif pad == "/api/v1/health":
            data = {"ok": True}
        elif pad == "/api/v1/auth/setup":
            data = {"csrf_token": secrets.token_hex(16)}
        elif pad == "/_sim/bibliotheek":
            data = {"api_key": secrets.token_hex(16)}
        elif pad == "/api/v1/mediaserver/config":
            data = {"test": {"ok": True, "path_map": [["/media", "/media"]]}}
        elif pad == "/api/v1/library/sync":
            data = {"queued": {"sonarr-series": 1}}
        else:
            pytest.fail(f"Onverwachte meetroute {pad}")
        return httpx.Response(200, json=data)

    with httpx.Client(base_url="http://proef", transport=httpx.MockTransport(antwoord)) as client:
        kern = SimpleNamespace(client=lambda *_: client, net=SimpleNamespace(dossier=tmp_path, project=Mock()))
        proef = Inhoud(kern)

        def fixture(actie, *_):
            if actie == "inventaris":
                # De oude inventaris leverde alleen deze twee afdrukken. Ze veranderen in de verliesproeven niet.
                return json.dumps({"sha256": dict.fromkeys(PADEN, "a" * 64), "registraties": registraties,
                                   "logregels": werklog,
                                   "historie": {"kijkregistratie": "a" * 64, "taaklog": "b" * 64}})
            return ""

        proef.fixture = fixture
        yield proef, kijk, werklog, registraties, stand


@pytest.fixture
def meting(diensten):
    proef, kijk, werklog, registraties, _ = diensten
    proef.meet("voor")
    return proef, kijk, werklog, registraties


@pytest.mark.parametrize("afwijking", ["voortgang", "sessies", "log", "hervatpunt"])
def test_begin_verwerpt_afwijkend_dienstantwoord(diensten, afwijking):
    proef, kijk, _, _, stand = diensten
    if afwijking == "hervatpunt":
        kijk[KIJKERS[1]][PADEN[0]]["PlaybackPositionTicks"] = 0
    else:
        stand[afwijking] = {"voortgang": 0, "sessies": [], "log": False}[afwijking]
    with pytest.raises(AssertionError, match="Beginstand"):
        proef.begin("kwaliteit")


def test_begin_bevestigt_de_waarden_via_beide_diensten(diensten):
    diensten[0].begin("kwaliteit")


@pytest.mark.parametrize("pad", PADEN)
@pytest.mark.parametrize("gebruiker", KIJKERS)
def test_ontbrekende_kijkrij(meting, pad, gebruiker):
    proef, kijk, _, _ = meting
    del kijk[gebruiker][pad]
    with pytest.raises(AssertionError, match="kijkgegevens"):
        proef.meet("tijdens")


@pytest.mark.parametrize(("veld", "waarde"), [("Played", False), ("PlaybackPositionTicks", 500000)])
def test_gewijzigde_kijkwaarde(meting, veld, waarde):
    proef, kijk, _, _ = meting
    kijk[KIJKERS[0]][PADEN[0]][veld] = waarde
    with pytest.raises(AssertionError, match="kijkgegevens"):
        proef.meet("na")


@pytest.mark.parametrize("pad", PADEN)
def test_ontbrekende_registratie(meting, pad):
    proef, _, _, registraties = meting
    del registraties[pad]
    with pytest.raises(AssertionError, match="kijkregistratie"):
        proef.meet("na")


@pytest.mark.parametrize("nummer", ("1", "2"))
@pytest.mark.parametrize("vervanging", [False, True], ids=["verlies", "gelijke-telling"])
def test_ontbrekende_taaklogregel(meting, nummer, vervanging):
    proef, _, werklog, _ = meting
    oud = werklog.pop(nummer)
    if vervanging:
        werklog["3"] = {**oud, "message": "Nieuwe regel"}
    with pytest.raises(AssertionError, match="taaklog"):
        proef.meet("tijdens")


def test_nieuwe_taaklogregel_mag(meting):
    proef, _, werklog, _ = meting
    werklog["3"] = deepcopy(werklog["2"])
    proef.meet("na")


@pytest.mark.parametrize("bestand", ["bibliotheek.py", "encode.py"])
def test_fixtures_schrijven_uitsluitend_via_de_dienstcode(bestand):
    boom = ast.parse((FIXTURES / bestand).read_text())
    for node in ast.walk(boom):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert not re.search(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\s", node.value, re.I), (
                f"Rechtstreekse SQL-schrijfactie op regel {node.lineno}")
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "sqlite3.connect":
            assert "mode=ro" in ast.literal_eval(node.args[0])
            assert any(k.arg == "uri" and ast.literal_eval(k.value) is True for k in node.keywords)
