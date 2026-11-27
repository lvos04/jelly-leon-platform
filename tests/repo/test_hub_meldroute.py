"""De lokale meldroute stuurt niets door als de berichtlengte ontbreekt of ongeldig is."""

from unittest.mock import Mock

import pytest

from e2e.fixtures.meldingen import hub_meldroute


@pytest.mark.parametrize(("lengte", "status"), [(None, 413), ("", 400), ("ongeldig", 400), ("0", 413), ("16385", 413)])
def test_ongeldige_lengte_bereikt_ontvanger_niet(monkeypatch, lengte, status):
    verbinding = Mock()
    monkeypatch.setattr(hub_meldroute, "HTTPConnection", verbinding)
    handler = object.__new__(hub_meldroute.Meldroute)
    handler.path = "/message"
    handler.headers = {} if lengte is None else {"Content-Length": lengte}
    handler.send_error = Mock()
    handler.do_POST()
    handler.send_error.assert_called_once_with(status)
    verbinding.assert_not_called()
