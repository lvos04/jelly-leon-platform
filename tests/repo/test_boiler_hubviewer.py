"""De normale Hub-sessie leest en kan geen opdracht voor de proefstekker aanmaken."""

import json
from unittest.mock import Mock

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from e2e.integral.scenarios import EX27_boiler as proef


@pytest.mark.parametrize(("status", "reden", "verschil", "geldig"), [
    (403, "forbidden", 0, True), (200, "ok", 0, False), (403, "andere_reden", 0, False), (403, "forbidden", 1, False),
])
def test_gewone_hub_gebruikt_eigen_sessie_en_blijft_lezen(status, reden, verschil, geldig):
    paden = []

    def antwoord(r):
        paden.append((r.method, r.url.path))
        if r.method == "GET":
            return httpx.Response(200, json={"actions": ["socket.relay.propose"]})
        aanvraag = json.loads(r.content)
        assert aanvraag["signature"] and aanvraag["proposal"]["target"] == {"device_id": "proef"}
        return httpx.Response(status, json={"code": reden})

    huis = Mock()
    huis.wachtrij.side_effect = [7, 7 + verschil]
    with httpx.Client(base_url="http://huis.invalid", transport=httpx.MockTransport(antwoord)) as viewer:
        args = (huis, viewer, Ed25519PrivateKey.generate(), {"device_id": "proef"}, "voor_binding")
        if geldig:
            proef.meet_hubviewer(*args)
            huis.meet.assert_called_once_with("voor_binding",
                                              {"ontdekking_http": 200, "voorstel_http": 403, "nieuwe_opdrachten": 0})
        else:
            with pytest.raises(AssertionError):
                proef.meet_hubviewer(*args)
    huis.api.post.assert_not_called()
    assert paden == [("GET", "/connector/v1/discover"), ("POST", "/connector/v1/propose")]
