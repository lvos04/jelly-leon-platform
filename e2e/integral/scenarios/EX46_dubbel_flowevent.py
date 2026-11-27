"""Dezelfde vijf bezorgingen en vijf afzonderlijke triggers met identieke actie-inhoud."""

from __future__ import annotations

from e2e.integral.bewijs import schrijf
from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import schrijfacties
from e2e.integral.scenarios.meldingen import FlowBron, Gotify

SCENARIO = {
    "eigenaar": "Node-RED en Hub", "groepen": ("meldingen",), "diensten": ("node-red", "gotify-sim"),
    "verwachting": "Vijf leveringen van één event geven één uitgevoerde melding, vijf unieke events geven er vijf.",
}


def meet_leveringen(kern: Kern, bron: FlowBron, gotify: Gotify, uniek: int) -> dict[str, int]:
    eerder = {a["request_id"] for a in bron.acties()}
    ontvangen = {r["id"] for r in gotify.berichten("hub")}
    events = [bron.event("melding") for _ in range(uniek)]
    assert len({e["source_event_id"] for e in events}) == uniek
    statussen = [bron.lever(e) for e in (events * (5 // uniek))]
    assert statussen == ["accepted"] * uniek + ["duplicate"] * (5 - uniek)
    nieuw = [a for a in bron.acties() if a["request_id"] not in eerder]
    assert len(nieuw) == uniek
    audit = {r["doel_id"] for r in bron.audit("action.proposal.created")}
    assert audit == eerder | {a["request_id"] for a in nieuw}
    for a in nieuw:
        assert a["action_id"] == "notification.send" and a["risk_class"] == "R0" and a["status"] == "klaar"
        res = kern.wijzig("POST", f"/api/v1/actions/{a['request_id']}/execute").json()
        assert res["status"] == "uitgevoerd" and len(res["resultaat"]["meldingen"]) == 1

    def afgeleverd() -> set[int]:
        return {r["id"] for r in gotify.berichten("hub") if r["title"] == "Flowproef"
                and r["message"] == "Bericht van de proefflow"} - ontvangen

    wacht("aflevering aan Gotify", lambda: len(afgeleverd()) >= uniek)
    aantal = len(afgeleverd())
    assert aantal == uniek
    return {"leveringen": len(statussen), "unieke_events": uniek, "duplicaten": statussen.count("duplicate"),
            "voorstellen": len(nieuw), "uitgevoerde_acties": len(nieuw), "ontvangen_meldingen": aantal}


def test_herlevering_en_vijf_afzonderlijke_events(kern: Kern) -> None:
    bron, gotify = FlowBron(kern), Gotify(kern)
    kern.wijzig("POST", "/api/v1/notifications/channels", json={
        "naam": "Proefkanaal", "basis_url": str(gotify.api.base_url), "token": gotify.applicaties["hub"],
    })
    assert bron.acties() == []
    herhaling = meet_leveringen(kern, bron, gotify, 1)
    controle = meet_leveringen(kern, bron, gotify, 5)
    mutaties = schrijfacties(kern)
    assert mutaties == []
    schrijf(kern.net.dossier / "metingen.json",
            {"herlevering": herhaling, "afzonderlijk": controle, "schrijfacties": len(mutaties)})
