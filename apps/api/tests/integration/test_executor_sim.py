"""De executor-stand-in van de ketentest controleert een ondertekend ``ExecutionRequest`` zelf opnieuw (contract 1.3
§8), met de codes van ``docs/EXECUTION_REQUEST.md`` van de Hub, en voert alleen de vaste acties van de allowlist uit.

Een stand-in die alles doorlaat maakt de ketentest waardeloos; daarom hier elke weigering apart, telkens met de
controle dat er **niets** op de NAS is gewijzigd. Waar een verzoek alleen door een foute of gekaapte Hub kan ontstaan
(te lage risicoklasse, ontbrekende goedkeuring), ondertekent de test het zelf met de policy-sleutel: de handtekening
klopt dan, en de uitvoerder moet het op eigen herberekening toch weigeren. Sim-getest.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import contract8 as c8
import ketenhulp as k
import planhulp as h
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from executor_sim import ExecutorSim, Weigering

NIEUW_TAG = f"3.1.1@{h.GOTIFY_DIGEST}"
BIJWERKEN = {"image": {"repository": h.GOTIFY_REPO, "tag": NIEUW_TAG}}


@dataclass
class Opstelling:
    hub: Any
    rpc: k.Rpc
    nas: Any
    executor: ExecutorSim
    tijd: dict[str, float]

    @property
    def sim(self) -> Any:
        return self.hub.state.sim

    def voorstel(self, action_id: str, target: dict[str, Any], parameters: dict[str, Any] | None = None
                 ) -> dict[str, Any]:
        return dict(self.sim.p2.vul_expected_version({
            "idempotency_key": str(uuid.uuid4()), "action_id": action_id, "target": target,
            "parameters": parameters or {}, "actor": k.EIGENAAR, "expected_version": None}))

    def verzoek(self, action_id: str, target: dict[str, Any], parameters: dict[str, Any] | None = None, *,
                herstelpunt: str | None = None, **kw: Any) -> dict[str, Any]:
        """Langs de gewone weg: voorstel → (goedkeuring) → ondertekend verzoek van de Hub."""
        v = self.sim.p2.propose(self.voorstel(action_id, target, parameters))
        if v.status == "wacht_op_goedkeuring":
            self.sim.p2.approve(v.request_id, backup_checkpoint_id=herstelpunt)
        request: dict[str, Any] = self.sim.execution_request(v.request_id, **kw)
        return request

    def onderteken(self, body: dict[str, Any]) -> dict[str, Any]:
        """Zelf ondertekend met de policy-sleutel van de Hub (een foute of gekaapte Hub)."""
        return c8.onderteken({k_: v for k_, v in body.items() if k_ != "signature"}, self.sim.policy_keys[-1][1])

    def geweigerd(self, request: Any, status: int, code: str) -> Weigering:
        voor, zelf = self.schrijvend(), list(self.executor.aanroepen)
        with pytest.raises(Weigering) as fout:
            self.executor.execute(request)
        assert (fout.value.status, fout.value.code) == (status, code), (fout.value.code, fout.value.details)
        assert self.schrijvend() == voor and self.executor.aanroepen == zelf  # er is niets uitgevoerd
        return fout.value

    def schrijvend(self) -> list[str]:
        return [c["method"] for c in self.nas.get("/_sim/calls").json()
                if c["write"] and not c["method"].startswith(("auth.", "core."))]

    def image(self, naam: str = "gotify") -> str:
        return str(self.rpc.call("app.query", [["id", "=", naam]])[0]["active_workloads"]["images"][0])

    def snapshot(self, naam: str = "voor-update") -> str:
        r = self.executor.execute(self.verzoek("zfs.snapshot.create", {"dataset": k.DATASET}, {"naam": naam}))
        assert r["status"] == "voltooid", r
        return naam


def opstellen(hub: Any, rpc: k.Rpc, nas: Any) -> Opstelling:
    sim, tijd = hub.state.sim, {"vooruit": 0.0}
    executor = ExecutorSim(nas=rpc, keys=[key.public_key() for _kid, key in sim.policy_keys],
                           clock=lambda: float(sim.p2.clock()) + tijd["vooruit"])
    sim.p2.etag_lezer = executor.etag
    nas.post("/_sim/fixtures", json={"datasets": [k.DATASET, k.SANDBOX], "apps": [
        {"app_name": "gotify", "version": "1.4.8", "values": k.formulier(f"3.0.0@{h.OUDE_DIGEST}")}]})
    return Opstelling(hub, rpc, nas, executor, tijd)


@pytest.fixture
def op(hub: Any) -> Iterator[Opstelling]:
    with k.nas_simulator() as (rpc, nas):
        yield opstellen(hub, rpc, nas)


def test_r1_wordt_uitgevoerd_en_teruggelezen(op: Opstelling) -> None:
    r = op.executor.execute(op.verzoek("app.stop", {"app_id": "gotify"}))
    assert (r["status"], r["risk_class"], r["readback"]["staat"]) == ("voltooid", "R1", "STOPPED")
    assert r["readback"]["etag"] == op.executor.etag({"app_id": "gotify"}) and op.schrijvend() == ["app.stop"]
    start = op.executor.execute(op.verzoek("app.restart", {"app_id": "gotify"}))
    assert start["readback"]["staat"] == "RUNNING" and op.schrijvend() == ["app.stop", "app.stop", "app.start"]


def test_geknoeid_of_door_een_vreemde_sleutel_ondertekend(op: Opstelling) -> None:
    request = op.verzoek("app.stop", {"app_id": "gotify"})
    geknoeid = copy.deepcopy(request)
    geknoeid["proposal"]["action_id"] = "app.uninstall"
    op.geweigerd(geknoeid, 403, "handtekening_ongeldig")
    vreemd = c8.onderteken({k_: v for k_, v in request.items() if k_ != "signature"}, Ed25519PrivateKey.generate())
    op.geweigerd(vreemd, 403, "handtekening_ongeldig")
    zonder = {k_: v for k_, v in request.items() if k_ != "signature"}
    op.geweigerd(zonder, 422, "execution_request_ongeldig")
    op.geweigerd({**request, "extra": 1}, 422, "execution_request_ongeldig")
    assert op.executor.execute(request)["status"] == "voltooid"  # het echte verzoek is hierdoor niet verbruikt


def test_herhaald_verzoek_en_herhaald_voorstel_voeren_niet_nog_eens_uit(op: Opstelling) -> None:
    request = op.verzoek("app.stop", {"app_id": "gotify"})
    eerste = op.executor.execute(request)
    op.geweigerd(request, 409, "nonce_al_gebruikt")
    # de Hub geeft hetzelfde voorstel opnieuw uit (nieuwe nonce): geen tweede uitvoering, wel hetzelfde resultaat
    v = op.sim.p2.propose(request["proposal"])
    opnieuw = op.executor.execute(op.sim.execution_request(v.request_id))
    assert opnieuw["herhaling"] is True and opnieuw["execution_id"] == eerste["execution_id"]
    assert op.schrijvend() == ["app.stop"]


def test_tijden(op: Opstelling) -> None:
    request = op.verzoek("app.stop", {"app_id": "gotify"})
    op.tijd["vooruit"] = 301.0
    op.geweigerd(request, 403, "verzoek_verlopen")
    op.tijd["vooruit"] = -120.0
    op.geweigerd(op.verzoek("app.stop", {"app_id": "gotify"}), 403, "verzoek_nog_niet_geldig")
    op.tijd["vooruit"] = 0.0
    op.geweigerd(op.verzoek("app.stop", {"app_id": "gotify"}, ttl_s=3600), 403, "verzoek_geldigheid_ongeldig")


def test_ander_beleid_dan_de_hub(op: Opstelling) -> None:
    op.executor.policy_hash = "sha256:" + "0" * 64
    fout = op.geweigerd(op.verzoek("app.stop", {"app_id": "gotify"}), 409, "policy_hash_verschilt")
    assert fout.details == {"eigen_policy_hash": op.executor.policy_hash}


def test_buiten_de_allowlist_of_buiten_het_schema(op: Opstelling) -> None:
    basis = op.verzoek("app.stop", {"app_id": "gotify"})
    for action_id, target, parameters, status, code in (
            ("app.exec", {"app_id": "gotify"}, {"command": "sh -c id"}, 403, "actie_niet_toegestaan"),
            ("zfs.dataset.destroy", {"dataset": k.DATASET}, {}, 403, "actie_niet_toegestaan"),
            ("zfs.snapshot.rollback", {"snapshot": f"{k.DATASET}@x"}, {}, 403, "actie_niet_toegestaan"),
            ("notification.send", {}, {}, 403, "actie_niet_toegestaan"),
            ("app.stop", {"app_id": "gotify"}, {"grace_seconds": 30}, 422, "parameters_ongeldig"),
            ("zfs.snapshot.clone_to_sandbox", {"snapshot": f"{k.DATASET}@x"}, {"doel_dataset": "simpool/media/x"},
             422, "parameters_ongeldig"),
            ("app.update", {"app_id": "gotify"}, {"image": {"repository": h.GOTIFY_REPO, "tag": "latest"}},
             422, "parameters_ongeldig")):
        body = copy.deepcopy(basis)
        body["proposal"].update(action_id=action_id, target=target, parameters=parameters,
                                idempotency_key=str(uuid.uuid4()))
        body["nonce"] = str(uuid.uuid4())
        op.geweigerd(op.onderteken(body), status, code)


def test_eigen_herberekening_van_de_risicoklasse(op: Opstelling) -> None:
    """Een Hub die een R2-actie als R1 zonder goedkeuring ondertekent, krijgt die niet uitgevoerd."""
    body = copy.deepcopy(op.verzoek("app.stop", {"app_id": "gotify"}))
    body["proposal"].update(action_id="app.uninstall", idempotency_key=str(uuid.uuid4()))
    body["nonce"] = str(uuid.uuid4())
    fout = op.geweigerd(op.onderteken(body), 403, "risicoklasse_te_laag")
    assert fout.details == {"eigen_risk_class": "R2"}
    body["policy_decision"]["risk_class"], body["nonce"] = "R2", str(uuid.uuid4())
    op.geweigerd(op.onderteken(body), 403, "goedkeuring_ontbreekt")
    assert op.image() == f"{h.GOTIFY_REPO}:3.0.0@{h.OUDE_DIGEST}"


def test_herstart_van_een_kritieke_app_vraagt_goedkeuring(op: Opstelling) -> None:
    """R-klasse volgt de contracttabel: ``app.restart`` is R1, op een kritieke app R2 (I10-10-04)."""
    op.nas.post("/_sim/fixtures", json={"apps": [{"app_name": "authentik", "version": "1.4.8", "values": {
        "image": {"repository": "ghcr.io/voorbeeld/app", "tag": "1.0.0"}}}]})
    v = op.sim.p2.propose(op.voorstel("app.restart", {"app_id": "authentik"}))
    assert (v.risk_class, v.status) == ("R2", "wacht_op_goedkeuring")
    # zonder goedkeuring, door een foute Hub toch als R1 ondertekend: de executor rekent zelf R2 uit
    body = copy.deepcopy(op.verzoek("app.restart", {"app_id": "gotify"}))
    body["proposal"]["target"] = {"app_id": "authentik"}
    body["proposal"]["expected_version"] = op.executor.etag({"app_id": "authentik"})
    op.geweigerd(op.onderteken(body), 403, "risicoklasse_te_laag")
    # met goedkeuring wordt precies deze herstart uitgevoerd
    op.sim.p2.approve(v.request_id)
    r = op.executor.execute(op.sim.execution_request(v.request_id))
    assert (r["status"], r["risk_class"]) == ("voltooid", "R2") and op.schrijvend() == ["app.stop", "app.start"]


def test_goedkeuring_is_gebonden_eenmalig_en_beperkt_geldig(op: Opstelling) -> None:
    herstelpunt = op.snapshot()
    goed = op.verzoek("app.update", {"app_id": "gotify"}, BIJWERKEN, herstelpunt=herstelpunt)
    # dezelfde goedkeuring onder een ander voorstel (andere image): gebonden aan de diff
    ander = copy.deepcopy(goed)
    ander["proposal"]["parameters"] = {"image": {"repository": h.GOTIFY_REPO, "tag": "9.9.9@" + "sha256:" + "f" * 64}}
    ander["proposal"]["idempotency_key"], ander["nonce"] = str(uuid.uuid4()), str(uuid.uuid4())
    fout = op.geweigerd(op.onderteken(ander), 409, "goedkeuring_past_niet")
    assert fout.details["gewijzigd"] == ["action_scope", "preview_hash"]
    # een goedkeuring die langer dan 15 minuten geldig zou zijn
    lang = copy.deepcopy(goed)
    lang["approval"]["expires_at"] = c8.iso(c8.parse_ts(lang["approval"]["approved_at"]) + timedelta(hours=2))
    lang["nonce"] = str(uuid.uuid4())
    op.geweigerd(op.onderteken(lang), 403, "goedkeuring_ongeldig")
    # het echte verzoek: uitgevoerd; daarna is de goedkeuring verbruikt
    r = op.executor.execute(goed)
    assert r["status"] == "voltooid" and op.image() == f"{h.GOTIFY_REPO}:{NIEUW_TAG}"
    nogmaals = {**copy.deepcopy(goed), "nonce": str(uuid.uuid4())}
    op.geweigerd(op.onderteken(nogmaals), 409, "goedkeuring_al_gebruikt")


def test_verlopen_goedkeuring(op: Opstelling) -> None:
    herstelpunt = op.snapshot()
    v = op.sim.p2.propose(op.voorstel("app.update", {"app_id": "gotify"}, BIJWERKEN))
    op.sim.p2.approve(v.request_id, backup_checkpoint_id=herstelpunt, ttl_s=60)
    op.tijd["vooruit"] = 61.0
    op.geweigerd(op.sim.execution_request(v.request_id), 403, "goedkeuring_verlopen")


def test_update_alleen_met_een_bestaand_herstelpunt(op: Opstelling) -> None:
    op.geweigerd(op.verzoek("app.update", {"app_id": "gotify"}, BIJWERKEN), 409, "backup_checkpoint_ontbreekt")
    fout = op.geweigerd(op.verzoek("app.update", {"app_id": "gotify"}, BIJWERKEN, herstelpunt="bestaat-niet"),
                        409, "backup_checkpoint_ontbreekt")
    assert fout.details == {"herstelpunt": "bestaat-niet"}
    assert op.image() == f"{h.GOTIFY_REPO}:3.0.0@{h.OUDE_DIGEST}"


def test_precondities_uit_eigen_readback(op: Opstelling) -> None:
    herstelpunt = op.snapshot()
    request = op.verzoek("app.update", {"app_id": "gotify"}, BIJWERKEN, herstelpunt=herstelpunt)
    op.rpc.job("app.stop", "gotify")  # het doel verandert ná de goedkeuring (buiten de uitvoerder om)
    op.geweigerd(request, 409, "doel_gewijzigd")
    assert op.image() == f"{h.GOTIFY_REPO}:3.0.0@{h.OUDE_DIGEST}"
    # de goedkeuring is hiermee verbruikt: een nieuw verzoek met dezelfde goedkeuring wordt niet alsnog uitgevoerd
    op.geweigerd(op.onderteken({**copy.deepcopy(request), "nonce": str(uuid.uuid4())}), 409, "goedkeuring_al_gebruikt")
    # snapshot of kloon van iets dat er niet is
    op.geweigerd(op.verzoek("zfs.snapshot.create", {"dataset": "simpool/apps/bestaat-niet"}, {"naam": "x"}),
                 409, "doel_gewijzigd")
    op.geweigerd(op.verzoek("zfs.snapshot.clone_to_sandbox", {"snapshot": f"{k.DATASET}@bestaat-niet"},
                            {"doel_dataset": f"{k.SANDBOX}/x"}), 409, "doel_gewijzigd")


def test_geen_wijziging_op_een_zieke_pool(op: Opstelling) -> None:
    assert op.nas.post("/_sim/faults/pool_degraded", json={"pool": "simpool"}).status_code == 200
    fout = op.geweigerd(op.verzoek("zfs.snapshot.create", {"dataset": k.DATASET}, {"naam": "x"}),
                        409, "pool_niet_gezond")
    assert fout.details == {"pools": ["simpool"]}
    # stoppen mag wel: dat schrijft niets naar de pool
    assert op.executor.execute(op.verzoek("app.stop", {"app_id": "gotify"}))["status"] == "voltooid"


def test_terugzetten_is_app_update_met_de_bewaarde_waarden(op: Opstelling) -> None:
    herstelpunt = op.snapshot()
    op.geweigerd(op.verzoek("app.rollback", {"app_id": "gotify"}, {"herstel_van": str(uuid.uuid4())},
                            herstelpunt=herstelpunt), 409, "herstelbron_onbekend")
    bij = op.executor.execute(op.verzoek("app.update", {"app_id": "gotify"}, BIJWERKEN, herstelpunt=herstelpunt))
    assert bij["status"] == "voltooid" and f"{h.GOTIFY_REPO}@{h.GOTIFY_DIGEST}" in bij["readback"]["digests"]
    terug = op.executor.execute(op.verzoek("app.rollback", {"app_id": "gotify"},
                                           {"herstel_van": bij["execution_id"]}, herstelpunt=herstelpunt))
    assert terug["status"] == "voltooid" and op.image() == f"{h.GOTIFY_REPO}:3.0.0@{h.OUDE_DIGEST}"
    assert f"{h.GOTIFY_REPO}@{h.OUDE_DIGEST}" in terug["readback"]["digests"]
    # nooit app.rollback (alleen catalogus) en nooit een snapshot-rollback: de data blijft zoals hij is
    assert op.schrijvend() == ["pool.snapshot.create", "app.update", "app.update"]
    assert op.nas.get("/_sim/violations").json() == []


def test_verwijderen_laat_de_data_staan_en_een_nas_fout_is_geen_succes(op: Opstelling) -> None:
    r = op.executor.execute(op.verzoek("app.uninstall", {"app_id": "gotify"}))
    assert (r["status"], r["readback"]["staat"], r["readback"]["etag"]) == ("voltooid", "ABSENT", "absent")
    assert k.DATASET in op.nas.get("/_sim/state").json()["datasets"] and op.nas.get("/_sim/violations").json() == []
    # twee keer dezelfde snapshotnaam: de NAS weigert; dat wordt eerlijk 'mislukt', nooit 'voltooid'
    op.snapshot("dubbel")
    r = op.executor.execute(op.verzoek("zfs.snapshot.create", {"dataset": k.DATASET}, {"naam": "dubbel"}))
    assert r["status"] == "mislukt" and r["reden"].startswith("nas_fout:")


def test_snapshotfamilie_volgt_de_truenas_versie(hub: Any) -> None:
    """25.04 kent ``zfs.snapshot.*``, 25.10 ``pool.snapshot.*``; de uitvoerder kiest op de gemelde versie."""
    with k.nas_simulator("25.04.2.6") as (rpc, nas):
        op = opstellen(hub, rpc, nas)
        naam = op.snapshot()
        kloon = op.executor.execute(op.verzoek("zfs.snapshot.clone_to_sandbox", {"snapshot": f"{k.DATASET}@{naam}"},
                                               {"doel_dataset": f"{k.SANDBOX}/gotify-proef"}))
        assert kloon["status"] == "voltooid" and kloon["readback"]["staat"] == "AANWEZIG"
        assert op.schrijvend() == ["zfs.snapshot.create", "zfs.snapshot.clone"]
