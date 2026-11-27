"""De centrale flowproef telt alleen de inhoudelijke beleidsweigering."""
from unittest.mock import Mock

import httpx
import pytest

from e2e.integral.scenarios import EX45_flowprivilege as scenario


@pytest.mark.parametrize("reden", ["parameters_ongeldig", "actie_verboden_destructief"])
def test_ex45_controleert_het_actiedetail(monkeypatch, reden):
    kern = Mock()
    bron = Mock(actor={"id": "flow:proef"})
    bron.event.return_value = {"trace_id": "spoor",
                               "payload": {"action_id": "pool.dataset.delete", "target": {}, "parameters": {}}}
    bron.lever.return_value = "accepted"
    bron.acties.return_value = [{"action_id": "pool.dataset.delete", "risk_class": "R3", "status": "geweigerd",
                                "gestart_op": None, "request_id": "proef"}]
    bron.audit.return_value = [{"actor_type": "flow", "actor_id": "flow:proef",
                               "doel_id": "proef", "trace_id": "spoor"}]
    kern.hub.get.return_value.raise_for_status.return_value.json.return_value = {"blokkades": [{"code": reden}]}
    kern.hub.post.return_value = httpx.Response(409, json={"code": "status_past_niet"})
    monkeypatch.setattr(scenario, "FlowBron", lambda _: bron)
    monkeypatch.setattr(scenario, "schrijfacties", lambda _: [])
    productie = Mock(return_value={"http": 403, "reden": "destructief_verboden",
                                  "audit": 1, "geweigerd": 1, "aangevraagd": 0})
    monkeypatch.setattr(scenario, "productieweigering", productie, raising=False)
    meting = Mock()
    monkeypatch.setattr(scenario, "meet", meting)
    if reden == "parameters_ongeldig":
        with pytest.raises(AssertionError):
            scenario.test_verboden_flowactie(kern)
        meting.assert_not_called()
    else:
        scenario.test_verboden_flowactie(kern)
        kern.hub.get.assert_called_once_with("/api/v1/actions/proef")
        meting.assert_called_once()
        productie.assert_called_once_with(kern, bron.event.return_value["payload"])


@pytest.mark.parametrize(("veld", "waarde"), [("http", 200), ("reden", "buiten_scope"), ("audit", 0),
                                          ("geweigerd", 2), ("aangevraagd", 1)])
def test_productiemeting_weigert_afwijking(veld, waarde):
    uitkomst = {"http": 403, "reden": "destructief_verboden", "audit": 1, "geweigerd": 1, "aangevraagd": 0}
    uitkomst[veld] = waarde
    with pytest.raises(AssertionError):
        scenario.toets_productieweigering(uitkomst)
