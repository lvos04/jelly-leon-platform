"""KLEIN-36: een onverwachte databasefout zet de DETAIL-regel van PostgreSQL niet in de app-log.

PostgreSQL noemt bij een constraint-schending in de fouttekst de sleutelwaarde ("Key (kolom)=(waarde) already
exists.") of de hele rij ("Failing row contains (...)"). ``hide_parameters=True`` (V-094) haalt die regel niet weg.
De algemene foutafhandeling van API en worker logt bij een DBAPIError daarom alleen soort, sqlstate, constraint en
tabel; het logfilter (``DbDetailFilter``) is het vangnet op de handlers. Het HTTP-antwoord blijft de vaste 500.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from lifecycle.db import make_engine
from lifecycle.errors import TRACE_HEADER, TraceIdMiddleware, install_error_handlers

# Herkenbare testwaarden (geen echte geheimen).
UNIEK_WAARDE = "jlp-klein36-uniek-0123456789"
RIJ_WAARDE = "jlp-klein36-rij-9876543210"
TABEL = "klein36_proef"  # ook letterlijk in de SQL hieronder (geen samengestelde SQL)
UNIEK_CONSTRAINT = "klein36_proef_sleutel_uniek"
CHECK_CONSTRAINT = "klein36_proef_aantal_positief"


def _verboden(tekst: str) -> None:
    for woord in (UNIEK_WAARDE, RIJ_WAARDE, "DETAIL", "Failing row", "Key ("):
        assert woord not in tekst, f"{woord!r} staat in de log"


@pytest.fixture
def engine(pg_url: str) -> Iterator[Engine]:
    eng = make_engine(pg_url)
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS klein36_proef"))
        conn.execute(text(
            "CREATE TABLE klein36_proef (sleutel text CONSTRAINT klein36_proef_sleutel_uniek UNIQUE, geheim text, "
            "aantal int CONSTRAINT klein36_proef_aantal_positief CHECK (aantal > 0))"
        ))
        conn.execute(text("INSERT INTO klein36_proef (sleutel, geheim, aantal) VALUES (:s, :g, 1)"),
                     {"s": UNIEK_WAARDE, "g": RIJ_WAARDE})
    yield eng
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS klein36_proef"))
    eng.dispose()


def _schending(engine: Engine, soort: str) -> None:
    with engine.begin() as conn:
        if soort == "uniek":
            conn.execute(text("INSERT INTO klein36_proef (sleutel, geheim, aantal) VALUES (:s, 'x', 2)"),
                         {"s": UNIEK_WAARDE})
        else:
            conn.execute(text("INSERT INTO klein36_proef (sleutel, geheim, aantal) VALUES ('ander', :g, -1)"),
                         {"g": RIJ_WAARDE})


VERWACHT = {"uniek": ("23505", UNIEK_CONSTRAINT), "check": ("23514", CHECK_CONSTRAINT)}


@pytest.mark.parametrize("soort", ["uniek", "check"])
def test_schending_toont_detail_in_str(engine: Engine, soort: str) -> None:
    """Uitgangspunt: zonder maatregel staat de DETAIL-regel wel in str(fout) (anders bewijst de rest niets)."""
    with pytest.raises(DBAPIError) as info:
        _schending(engine, soort)
    assert "DETAIL" in str(info.value) or "Failing row" in str(info.value)


@pytest.mark.parametrize("soort", ["uniek", "check"])
def test_api_algemene_handler_zonder_detail(engine: Engine, soort: str, caplog: pytest.LogCaptureFixture) -> None:
    app = FastAPI()
    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)

    @app.post("/proef")
    def proef() -> dict[str, Any]:
        _schending(engine, soort)
        return {}

    trace = "6a1f0c3e-2b4d-4e5f-8a9b-0c1d2e3f4a5b"
    with caplog.at_level(logging.DEBUG), TestClient(app, raise_server_exceptions=False) as client:
        antwoord = client.post("/proef", headers={TRACE_HEADER: trace})
    # Het HTTP-antwoord is ongewijzigd: vaste 500 internal_error zonder fouttekst.
    assert antwoord.status_code == 500
    assert antwoord.json() == {
        "code": "internal_error",
        "message": "Interne fout; er is niets bevestigd. Zie de app-log met dit trace-id.",
        "details": {},
        "trace_id": trace,
    }
    assert antwoord.headers[TRACE_HEADER] == trace
    records = [r for r in caplog.records if r.name == "lifecycle"]
    assert records, "de fout is niet gelogd"
    log_tekst = caplog.text
    _verboden(log_tekst)
    sqlstate, constraint = VERWACHT[soort]
    assert sqlstate in log_tekst
    assert constraint in log_tekst
    assert TABEL in log_tekst
    assert trace in log_tekst


@pytest.mark.parametrize("soort", ["uniek", "check"])
def test_worker_algemene_handler_zonder_detail(
    engine: Engine, soort: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """De algemene handler van de worker (Worker.run: één ronde mislukt, de volgende gaat door)."""
    import signal

    from lifecycle.worker import Worker

    monkeypatch.setattr(signal, "signal", lambda *_: None)
    worker = Worker.__new__(Worker)
    stop = threading.Event()
    rondes: list[int] = []

    def tick() -> dict[str, Any]:
        rondes.append(1)
        stop.set()
        _schending(engine, soort)
        return {}

    worker.stop = stop
    worker.settings = SimpleNamespace(worker_interval_seconds=0)
    worker.engine = SimpleNamespace(dispose=lambda: None)
    worker.tick = tick
    with caplog.at_level(logging.DEBUG):
        worker.run()
    assert rondes == [1]
    log_tekst = caplog.text
    assert "worker-ronde mislukt" in log_tekst
    _verboden(log_tekst)
    sqlstate, constraint = VERWACHT[soort]
    assert sqlstate in log_tekst
    assert constraint in log_tekst
    assert TABEL in log_tekst


def _record_met_fout(engine: Engine) -> logging.LogRecord:
    try:
        _schending(engine, "check")
    except DBAPIError as fout:
        import sys

        return logging.LogRecord("elders", logging.ERROR, __file__, 1, "mislukt: %s", (str(fout),), sys.exc_info())
    raise AssertionError("geen schending")


def test_filter_schoont_bericht_en_traceback(engine: Engine) -> None:
    from lifecycle.logfilter import DETAIL_WEGGELATEN, DbDetailFilter

    record = _record_met_fout(engine)
    handler = logging.StreamHandler()
    assert DbDetailFilter().filter(record) is True
    tekst = handler.format(record)
    _verboden(tekst.replace(DETAIL_WEGGELATEN, ""))
    assert DETAIL_WEGGELATEN in tekst
    assert "CheckViolation" in tekst


def test_filter_aangehaakt_en_access_log_ongeschonden() -> None:
    from lifecycle.logfilter import DbDetailFilter, install_log_filter, uvicorn_log_config

    logger = logging.getLogger("klein36-proef")
    handler = logging.StreamHandler()
    logger.addHandler(handler)
    try:
        install_log_filter(logger)
        install_log_filter(logger)  # tweemaal aanhaken mag
        assert sum(isinstance(f, DbDetailFilter) for f in handler.filters) == 1
    finally:
        logger.removeHandler(handler)
    config = uvicorn_log_config()
    assert config["handlers"], "uvicorn heeft geen handlers"
    for naam, h in config["handlers"].items():
        assert "db_detail" in h["filters"], naam
    # Een access-regel zonder DETAIL blijft ongewijzigd (uvicorn leest record.args zelf).
    args = ("127.0.0.1:1", "GET", "/healthz", "1.1", 200)
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None)
    assert DbDetailFilter().filter(record) is True
    assert record.args == args


def test_cli_logging_haakt_filter_aan(monkeypatch: pytest.MonkeyPatch) -> None:
    from lifecycle import cli
    from lifecycle.logfilter import DbDetailFilter

    root = logging.getLogger()
    handler = logging.StreamHandler()
    monkeypatch.setattr(root, "handlers", [handler])
    cli._setup_logging()
    assert any(isinstance(f, DbDetailFilter) for f in handler.filters)
