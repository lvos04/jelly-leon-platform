"""Alleen de door de beheerder bekeken en goedgekeurde digest mag worden uitgevoerd."""

from __future__ import annotations

from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import (
    APP_A,
    akkoord,
    bezorgingen,
    meet,
    richt_schrijfpad_in,
    schrijfacties,
    stel_voor,
    uitvoeren,
)

SCENARIO = {
    "groepen": ("hub-veiligheid",),
    "eigenaar": "jelly-leon-platform",
    "verwachting": "Digestdrift vereist nieuw akkoord. Bij een stabiele digest volgt één uitvoering.",
}


def test_drift_en_stabiele_tegenproef(kern: Kern) -> None:
    richt_schrijfpad_in(kern)
    voor = stel_voor(kern, APP_A)
    akkoord(kern, voor)
    kern.sim.post("/_sim/faults/digest_mismatch", json={}).raise_for_status()
    fout = uitvoeren(kern, voor, geweigerd=True)
    assert fout["code"] == "goedkeuring_vervallen"
    assert {"resource_etag", "preview_hash"} <= set(fout["details"]["gewijzigd"])
    na = kern.hub.get(f"/api/v1/actions/{voor['request_id']}").raise_for_status().json()
    assert na["status"] == "wacht_op_goedkeuring" and na["goedkeuring"]["vervallen_op"]
    assert na["resource_etag"] != voor["resource_etag"] and na["preview_hash"] != voor["preview_hash"]
    oud, nieuw = voor["preview"]["huidig"], na["preview"]["huidig"]
    assert oud["digests"] and nieuw["digests"] and oud["digests"] != nieuw["digests"]
    assert {k for k in oud if oud[k] != nieuw[k]} == {"digests"}
    assert schrijfacties(kern) == [] and bezorgingen(kern) == []
    akkoord(kern, na)
    uitvoeren(kern, na)
    mutaties = schrijfacties(kern)
    assert mutaties == ["app.redeploy"] and bezorgingen(kern) == [[200]]
    meet(kern, geweigerde_drift_http=409, verschillen=len(fout["details"]["gewijzigd"]),
         mutaties_bij_drift=0, mutaties_bij_stabiele_digest=len(mutaties))
