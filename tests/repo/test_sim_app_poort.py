"""Ongeldige berichten krijgen een vast HTTP-antwoord voordat de lokale proefpoort iets doorstuurt."""

import io
from email.message import Message
from unittest.mock import Mock

import pytest

from e2e.integral.scenarios import sim_app_poort


@pytest.mark.parametrize(("lengte", "body", "status"), [
    (None, b"", 400), ("ongeldige-lengte", b"", 400), ("0", b"", 413), ("65537", b"", 413),
    ("2", b"{}", 400), ("1", b"{", 400), ("2", b"[]", 400),
    ("16", b'{"proposal": []}', 400),
])
def test_weiger_onvolledig_bericht(monkeypatch, lengte, body, status):
    verbinding = Mock()
    monkeypatch.setattr(sim_app_poort, "HTTPConnection", verbinding)
    handler = object.__new__(sim_app_poort.AppPoort)
    handler.path = "/api/v1/execute"
    handler.headers = Message()
    if lengte is not None:
        handler.headers["Content-Length"] = lengte
    handler.rfile = io.BytesIO(body)
    handler.send_error = Mock()
    handler.do_POST()
    handler.send_error.assert_called_once_with(status)
    verbinding.assert_not_called()
