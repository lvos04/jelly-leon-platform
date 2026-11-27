"""De behoudcontrole moet verlies bij gelijke sleutels en tellingen onderscheiden van echte voortgang."""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from e2e.integral import media


@pytest.fixture
def proef(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[sqlite3.Connection, Mock, httpx.Client]]:
    db = sqlite3.connect(":memory:")
    db.executescript("""
        CREATE TABLE jobs(id INTEGER PRIMARY KEY, job_type TEXT, status TEXT, progress REAL, created_at TEXT);
        INSERT INTO jobs VALUES (1,'library.sync','running',0.625,CURRENT_TIMESTAMP);
        CREATE TABLE job_logs(id INTEGER PRIMARY KEY, job_id INTEGER, ts TEXT, level TEXT, message TEXT);
        CREATE TABLE audit_events(id INTEGER PRIMARY KEY);
        INSERT INTO audit_events VALUES (1);
        CREATE TABLE mediaserver_watch(path TEXT PRIMARY KEY, title TEXT, user_name TEXT, last_seen TEXT);
    """)

    def lokaal_sql(_kern: object, *stappen: media.Stap, **opties: bool) -> list[list[Any]]:
        rijen = [db.execute(query, args).fetchall() for query, args in stappen]
        if opties.get("schrijven"):
            db.commit()
        return rijen

    monkeypatch.setattr(media, "sql", lokaal_sql)
    kern = Mock(net=SimpleNamespace(dossier=tmp_path))
    health = httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "ok"}))
    try:
        with httpx.Client(base_url="http://mediaguard", transport=health) as mg:
            yield db, kern, mg
    finally:
        db.close()


@pytest.mark.parametrize(("tabel", "wijziging"), [
    ("jobs", "UPDATE jobs SET id=10 WHERE id=1"),
    ("job_logs", "UPDATE job_logs SET id=10 WHERE id=1"),
    ("audit_events", "UPDATE audit_events SET id=2"),
    ("mediaserver_watch", "UPDATE mediaserver_watch SET path='/data/andere-proef.mkv'"),
    ("mediaserver_watch", "UPDATE mediaserver_watch SET user_name=NULL"),
], ids=("job", "historie", "audit", "kijkpad", "kijker"))
def test_behoud_weigert_verlies_bij_gelijke_telling(tabel: str, wijziging: str, tmp_path: Path,
                                                     proef: tuple[sqlite3.Connection, Mock, httpx.Client]) -> None:
    db, kern, mg = proef
    zelfde_sleutel = "user_name=NULL" in wijziging
    fout = "Bestaande kijkgegevens gewijzigd" if zelfde_sleutel else "MediaGuard-voortgang verdwenen"
    with pytest.raises(AssertionError, match=fout), media.behoud(kern, mg, 1):
        db.execute(wijziging)
    meting = json.loads((tmp_path / "voortgang.json").read_text())[tabel]
    assert meting["voor"] == meting["na"] == (2 if tabel in {"jobs", "job_logs"} else 1)
    assert meting["verdwenen_na"] == (0 if zelfde_sleutel else 1)
    if zelfde_sleutel:
        assert meting["gewijzigd_na"] == 1


@pytest.mark.parametrize("moment", ["tijdens", "na"])
@pytest.mark.parametrize(("tabel", "wijziging", "teller", "fout"), [
    ("jobs", "UPDATE jobs SET progress=0 WHERE id=1", "teruggevallen", "Bestaande jobvoortgang teruggevallen"),
    ("job_logs", "UPDATE job_logs SET message='' WHERE id=1", "gewijzigd", "Bestaande jobhistorie gewijzigd"),
], ids=("voortgang", "logtekst"))
def test_behoud_weigert_inhoudsverlies(tabel: str, wijziging: str, teller: str, fout: str, moment: str,
                                       proef: tuple[sqlite3.Connection, Mock, httpx.Client], tmp_path: Path) -> None:
    db, kern, mg = proef
    with pytest.raises(AssertionError, match=fout), media.behoud(kern, mg, 1) as meet:
        if moment == "tijdens":
            # Isoleer het tijdelijke verlies: bij de nameting is de oorspronkelijke waarde terug.
            db.execute("SAVEPOINT storing")
            db.execute(wijziging)
            try:
                meet("tijdens", False)
            finally:
                db.execute("ROLLBACK TO storing")
        else:
            meet("tijdens", False)
            db.execute(wijziging)
    meting = json.loads((tmp_path / "voortgang.json").read_text())[tabel]
    assert meting["voor"] == meting[moment] and meting["verdwenen_" + moment] == 0
    assert meting[teller + "_" + moment] == 1
    if tabel == "job_logs":
        assert meting["afdrukken_" + moment]["1"] != meting["afdrukken_voor"]["1"]


def test_behoud_weigert_terugval_na_eerdere_vooruitgang(proef: tuple[sqlite3.Connection, Mock, httpx.Client]) -> None:
    db, kern, mg = proef
    with pytest.raises(AssertionError, match="Bestaande jobvoortgang teruggevallen"), media.behoud(kern, mg, 1) as meet:
        db.execute("UPDATE jobs SET progress=0.875 WHERE id=1")
        meet("tijdens", False)
        db.execute("UPDATE jobs SET progress=0.75 WHERE id=1")


def test_behoud_bij_afronding_en_nieuwe_historie(proef: tuple[sqlite3.Connection, Mock, httpx.Client],
                                                 tmp_path: Path) -> None:
    db, kern, mg = proef
    with media.behoud(kern, mg, 1) as meet:
        db.execute("UPDATE jobs SET progress=0.875 WHERE id=1")
        meet("tijdens", False)
        db.execute("UPDATE jobs SET status='succeeded', progress=1 WHERE id=1")
        db.execute("INSERT INTO job_logs(job_id,ts,level,message) VALUES (1,CURRENT_TIMESTAMP,'info','Afgerond')")
    meting = json.loads((tmp_path / "voortgang.json").read_text())
    for moment, verwacht in (("voor", 0.625), ("tijdens", 0.875), ("na", 1.0)):
        assert meting["jobs"]["voortgang_" + moment]["1"] == verwacht
        assert meting["jobs"]["voortgang_" + moment][str(meting["jobs"]["proefjob"])] == 0.625
        assert meting["job_logs"]["proefregel_intact_" + moment] is True
        assert meting["job_logs"]["afdrukken_" + moment] == meting["job_logs"]["afdrukken_voor"]
    assert meting["job_logs"]["na"] == meting["job_logs"]["voor"] + 1
    assert meting["job_logs"]["gewijzigd_na"] == meting["jobs"]["teruggevallen_na"] == 0
