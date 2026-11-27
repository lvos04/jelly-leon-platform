"""Een vervangen rij mag bij gelijkblijvende telling niet doorgaan voor behouden voortgang."""

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest

from e2e.integral import media


@pytest.mark.parametrize(("tabel", "wijziging"), [
    ("jobs", "UPDATE jobs SET id=2"),
    ("job_logs", "UPDATE job_logs SET id=2"),
    ("audit_events", "UPDATE audit_events SET id=2"),
    ("mediaserver_watch", "UPDATE mediaserver_watch SET path='/data/andere-proef.mkv'"),
    ("mediaserver_watch", "UPDATE mediaserver_watch SET user_name=NULL"),
], ids=("job", "historie", "audit", "kijkpad", "kijker"))
def test_behoud_weigert_verlies_bij_gelijke_telling(tabel: str, wijziging: str, tmp_path: Path,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    with sqlite3.connect(":memory:") as db:
        db.executescript("""
            CREATE TABLE jobs(id INTEGER PRIMARY KEY);
            INSERT INTO jobs VALUES (1);
            CREATE TABLE job_logs(id INTEGER PRIMARY KEY, job_id INTEGER, ts TEXT, level TEXT, message TEXT);
            CREATE TABLE audit_events(id INTEGER PRIMARY KEY);
            INSERT INTO audit_events VALUES (1);
            CREATE TABLE mediaserver_watch(path TEXT PRIMARY KEY, title TEXT, user_name TEXT, last_seen TEXT);
        """)

        def lokaal_sql(_kern: object, *stappen: media.Stap, **_opties: bool) -> list[list[Any]]:
            with db:
                return [db.execute(query, args).fetchall() for query, args in stappen]

        monkeypatch.setattr(media, "sql", lokaal_sql)
        kern = Mock(net=SimpleNamespace(dossier=tmp_path))
        health = httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "ok"}))
        zelfde_sleutel = "user_name=NULL" in wijziging
        fout = "Bestaande kijkgegevens gewijzigd" if zelfde_sleutel else "MediaGuard-voortgang verdwenen"
        with (
            httpx.Client(base_url="http://mediaguard", transport=health) as mg,
            pytest.raises(AssertionError, match=fout),
            media.behoud(kern, mg, 1),
        ):
            db.execute(wijziging)
        meting = json.loads((tmp_path / "voortgang.json").read_text())[tabel]
        assert meting["voor"] == meting["na"] == 1
        assert meting["verdwenen_na"] == (0 if zelfde_sleutel else 1)
        if zelfde_sleutel:
            assert meting["gewijzigd_na"] == 1
