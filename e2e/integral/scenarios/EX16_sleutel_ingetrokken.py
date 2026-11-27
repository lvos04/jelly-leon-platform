"""Een eerder werkende leessleutel vervalt op de verbinding, beide aanmeldroutes en de inventaris."""

from __future__ import annotations

from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import meet, oproepen, schrijfacties, vernieuw

SCENARIO = {
    "blokkade": "NIET GEVALIDEERD (echte NAS)",
    "toelichting": "De praktijkproef voor beleid en executor volgt in N-P6-03.",
    "groepen": ("hub-veiligheid",),
    "eigenaar": "jelly-leon-platform",
    "verwachting": "Na intrekking weigeren beide aanmeldroutes en de bestaande sessie. De Hub markeert oude data.",
}

# Deze aanvullende protocolproef draait uitsluitend binnen de simulator met diens eigen TLS-certificaat.
SESSIES = """
import json, ssl
from contextlib import closing
from http.client import HTTPSConnection
from websockets.sync.client import connect

ca = ssl.create_default_context(cafile="/certificaat/sim.crt")
def beheer(methode, pad, body=None):
    with closing(HTTPSConnection("truenas", 8080, context=ca, timeout=5)) as c:
        c.request(methode, pad, body, {"Content-Type": "application/json"})
        r = c.getresponse()
        assert r.status == 200
        return json.loads(r.read())

def sessie():
    return connect("wss://truenas:8080/api/current", ssl=ca, proxy=None, open_timeout=5)

def rpc(ws, methode, params):
    ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": methode, "params": params}))
    return json.loads(ws.recv(timeout=5))

sleutel = beheer("GET", "/_sim/keys")["readonly"]
modern = [{"mechanism": "API_KEY_PLAIN", "username": "hub-readonly", "api_key": sleutel}]
with sessie() as oud, sessie() as tweede:
    assert rpc(oud, "auth.login_with_api_key", [sleutel])["result"] is True
    assert rpc(tweede, "auth.login_ex", modern)["result"]["response_type"] == "SUCCESS"
    assert rpc(oud, "pool.query", [])["result"]
    beheer("POST", "/_sim/faults/key_revoked", json.dumps({"key_id": int(sleutel.split("-", 1)[0])}))
    for ws in (oud, tweede):
        assert rpc(ws, "pool.query", [])["error"]["data"]["errname"] == "ENOTAUTHENTICATED"
with sessie() as nieuw:
    assert rpc(nieuw, "auth.login_with_api_key", [sleutel])["result"] is False
    assert rpc(nieuw, "auth.login_ex", modern)["result"]["response_type"] == "AUTH_ERR"
    assert rpc(nieuw, "pool.query", [])["error"]["data"]["errname"] == "ENOTAUTHENTICATED"
print("twee routes en twee bestaande sessies geweigerd")
"""


def test_sleutel_ingetrokken(kern: Kern) -> None:
    vernieuw(kern)
    pad = "/api/v1/inventory/pools"
    voor = kern.hub.get(pad).raise_for_status().json()
    assert voor["status"] == "ok" and voor["items"] and not voor["verouderd"]
    resultaat = kern.net.project.compose("exec", "-T", "truenas", "python", "-", invoer=SESSIES)
    assert resultaat.strip() == "twee routes en twee bestaande sessies geweigerd"
    begin = len(oproepen(kern))
    test = kern.wijzig("POST", "/api/v1/truenas/test").json()
    assert test["verbonden"] is False and test["status"] == "unknown" and test["reason_code"] == "auth_failed"
    assert test["melding"] and test["system"] is None
    status = kern.hub.get("/api/v1/truenas").raise_for_status().json()["laatste_test"]
    assert status["reason_code"] == "auth_failed" and status["melding"] == test["melding"]
    vernieuw(kern)
    na = kern.hub.get(pad).raise_for_status().json()
    assert na["status"] == "unknown" and na["reason_code"] == "auth_failed" and na["verouderd"] is True
    assert na["items"] == voor["items"] and na["observed_at"] == voor["observed_at"] and na["geldig_tot"] is None
    pogingen = oproepen(kern)[begin:]
    logins = [r for r in pogingen if r["method"] == "auth.login_with_api_key"]
    assert len(logins) >= 2 and not any(r["user"] == "hub-readonly" for r in pogingen)
    assert schrijfacties(kern) == []
    meet(kern, aanmeldpogingen_hub=len(logins), verouderd=na["verouderd"], schrijfacties=0)
