"""De hele keten van contract 1.3 §8 voor B08-003 en B08-004, met echte onderdelen waar die er zijn:

plan bij deze dienst (echte API + PostgreSQL) → Hub-stand-in (meten, beleid, goedkeuring, Ed25519-ondertekening) →
executor-stand-in (controleert het verzoek opnieuw, voert uit, leest terug) → TrueNAS-simulator (JSON-RPC).

Acceptaties:

- B08-003: een poortconflict of een ontbrekende afhankelijkheid blokkeert de deployment — er bereikt niets de NAS;
- B08-004: een mislukte databasemigratie laat de oude app met zijn data intact.

De echte Hub heeft nog geen voorstel-/goedkeuringsroutes en de echte executor weigert nog elke actie; daarom is dit
**Sim-getest**. Tegen de echte Hub/executor en een echte NAS: NIET GEVALIDEERD.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import contract8 as c8
import ketenhulp as k
import planhulp as h
import pytest
from executor_sim import Weigering
from hub_p2 import P2Error

NIEUW = f"{h.GOTIFY_REPO}:3.1.1@{h.GOTIFY_DIGEST}"
OUD = f"{h.GOTIFY_REPO}:3.0.0@{h.OUDE_DIGEST}"
DATA_VOOR = {"schema": "3.0.0", "berichten": 42, "beschadigd": False}


@pytest.fixture
def keten(client: Any, auth: Any, hub: Any) -> Iterator[k.Keten]:
    with k.nas_simulator() as (rpc, nas):
        yield k.bouw_keten(client, auth, hub, rpc, nas)


def muterend(keten: k.Keten) -> list[str]:
    """Schrijvende NAS-aanroepen (zonder inloggen en ``core.*``), in volgorde."""
    lezen = (".query", ".config", "system.", "core.", "auth.", "catalog.get_app_details")
    return [m for m in keten.aanroepen() if not any(deel in m for deel in lezen)]


def test_installatie_door_de_hele_keten(keten: k.Keten) -> None:
    view = keten.plan("install", keten.body())
    assert (view["status"], view["vrijgegeven_stappen"]) == ("klaar_voor_voorstel", ["installeren"]), view["plan"]
    assert view["plan"]["bronnen"]["catalogus"]["beschikbaar"] == "ja"

    # R2: zonder goedkeuring geeft de Hub geen ondertekend verzoek uit en gebeurt er niets op de NAS
    verzoek = keten.dien_in(view, "installeren")
    assert (verzoek.risk_class, verzoek.status) == ("R2", "wacht_op_goedkeuring")
    with pytest.raises(P2Error) as fout:
        keten.hub.state.sim.execution_request(verzoek.request_id)
    assert fout.value.code == "goedkeuring_ontbreekt"
    assert keten.app("gotify") is None and muterend(keten) == []

    uit = keten.voer_uit(view, "installeren")  # zelfde idempotency_key → hetzelfde verzoek, nu met goedkeuring
    assert uit.verzoek.request_id == verzoek.request_id
    assert uit.request["approval"]["resource_etag"] == "absent"
    assert uit.resultaat["status"] == "voltooid", uit.resultaat
    assert uit.plan["status"] == "voltooid"

    app = keten.app("gotify")
    assert app is not None and app["state"] == "RUNNING" and app["custom_app"] is False
    assert app["metadata"]["name"] == "ix-app"  # standaard Custom App-formulier, nooit compose/YAML
    assert app["active_workloads"]["images"] == [NIEUW]
    assert f"{h.GOTIFY_REPO}@{h.GOTIFY_DIGEST}" in uit.resultaat["readback"]["digests"]
    assert muterend(keten) == ["app.create"] and keten.overtredingen() == []

    # hetzelfde ondertekende verzoek nog eens aanbieden voert niets uit
    with pytest.raises(Weigering) as herhaald:
        keten.executor.execute(uit.request)
    assert herhaald.value.code == "nonce_al_gebruikt" and muterend(keten) == ["app.create"]


def test_poortconflict_uit_de_gemeten_inventaris_blokkeert_de_deployment(keten: k.Keten) -> None:
    """Acceptatie B08-003. De bezette poort komt uit de NAS (via de inventaris van de Hub), niet uit testgegevens."""
    keten.rpc.job("app.create", {"app_name": "andere-app", "catalog_app": "ix-app", "train": "stable",
                                 "version": keten.doel()["ix_app_version"],
                                 "values": {"image": {"repository": "ghcr.io/voorbeeld/app", "tag": "1.0.0"},
                                            "ports": [{"bind_mode": "published", "port_number": 30215,
                                                       "container_port": 80, "protocol": "tcp", "host_ips": []}]}})
    voor = muterend(keten)
    view = keten.plan("install", keten.body())
    assert view["status"] == "geblokkeerd" and view["vrijgegeven_stappen"] == []
    assert "port_in_use" in h.codes(view["plan"])
    melden = keten.meld(view["id"], "installeren", {"status": "voltooid", "execution_id": str(uuid.uuid4()),
                                                    "readback": {"staat": "RUNNING", "versie": None, "digests": [],
                                                                 "images": [], "etag": None}, "reden": None})
    assert (melden.status_code, melden.json()["code"]) == (409, "plan_geblokkeerd")
    assert keten.app("gotify") is None and muterend(keten) == voor  # er is niets naar de NAS gegaan


def test_ontbrekende_afhankelijkheid_blokkeert_de_deployment(keten: k.Keten) -> None:
    """Acceptatie B08-003: de agent van Beszel vraagt een geïnstalleerde Beszel-hub; die staat niet op de NAS."""
    parameters = {"host_port": 45876, "state_path": "/mnt/simpool/apps/beszel-agent",
                  "beszel_hub_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItestsleutel",
                  "timezone": "Europe/Amsterdam"}
    view = keten.plan("install", keten.body("beszel-agent", parameters))
    assert view["status"] == "geblokkeerd" and view["vrijgegeven_stappen"] == []
    assert "dependency_missing" in h.codes(view["plan"])
    assert keten.app("beszel-agent") is None and muterend(keten) == []


def test_update_met_schaduwtest_en_daarna_terugzetten(keten: k.Keten) -> None:
    k.gotify_draait(keten)
    view = keten.plan("update", keten.body(schaduw={"parameters": h.SCHADUW}))
    assert (view["status"], view["vrijgegeven_stappen"]) == ("klaar_voor_voorstel", ["stoppen"]), view["plan"]
    snapshot = view["plan"]["vastgepind"]["snapshot_naam"]
    kloon = view["plan"]["vastgepind"]["sandbox_klonen"][0]
    assert kloon.startswith(f"{k.SANDBOX}/")

    # tot en met de schaduwtest: productie draait nog op de oude image
    view = keten.loop(view, tot="schaduw-installeren")
    status = h.status(view["voortgang"])
    assert [status[s] for s in ("stoppen", "snapshot-data", "starten", "kloon-data", "schaduw-installeren")] \
        == ["voltooid"] * 5, view["voortgang"]
    productie, schaduw = keten.app("gotify"), keten.app("gotify-test")
    assert productie is not None and productie["active_workloads"]["images"] == [OUD]
    assert schaduw is not None and schaduw["state"] == "RUNNING" and schaduw["active_workloads"]["images"] == [NIEUW]
    assert schaduw["active_workloads"]["volumes"][0]["source"] == f"/mnt/{kloon}"  # de testkopie draait op de kloon
    assert keten.inhoud()["snapshots"][f"{k.DATASET}@{snapshot}"] == DATA_VOOR
    assert view["vrijgegeven_stappen"][0] == "bijwerken"

    # de update zelf: R2, de goedkeuring draagt het herstelpunt; de executor kijkt of dat herstelpunt bestaat
    bij = keten.voer_uit(view, "bijwerken")
    assert bij.verzoek.risk_class == "R2" and bij.request["approval"]["backup_checkpoint_id"] == snapshot
    assert bij.resultaat["status"] == "voltooid", bij.resultaat
    assert f"{h.GOTIFY_REPO}@{h.GOTIFY_DIGEST}" in bij.resultaat["readback"]["digests"]
    productie = keten.app("gotify")
    assert productie is not None and productie["state"] == "RUNNING"
    assert productie["active_workloads"]["images"] == [NIEUW]

    view = keten.loop({**view, **bij.plan})
    assert keten.app("gotify-test") is None  # de testkopie is opgeruimd (app weg, data blijft)
    status = h.status(view["voortgang"])
    assert status["schaduw-verwijderen"] == "voltooid" and status["kloon-opruimen"] != "voltooid"
    # de kloon verwijderen is een eigenaarshandeling: het platform verwijdert nooit zelf een dataset
    assert kloon in keten.inhoud()["datasets"] and view["vrijgegeven_stappen"] == ["kloon-opruimen"]
    assert h.stap(view["plan"], "kloon-opruimen")["voorstel"] is None

    # terugzetten: app.update met de bewaarde vorige waarden — geen app.rollback, geen snapshot-rollback
    terug = keten.plan("rollback", {"update_plan_id": view["id"]})
    assert (terug["soort"], terug["status"]) == ("rollback", "klaar_voor_voorstel"), terug["plan"]
    stap_id = next(s["id"] for s in terug["plan"]["stappen"]
                   if s["voorstel"] and s["voorstel"]["action_id"] == "app.rollback")
    assert h.stap(terug["plan"], stap_id)["voorstel"]["parameters"] == {"herstel_van": bij.resultaat["execution_id"]}
    voor = muterend(keten)
    terug = keten.loop(terug)
    assert h.status(terug["voortgang"])[stap_id] == "voltooid", terug["voortgang"]
    productie = keten.app("gotify")
    assert productie is not None and productie["state"] == "RUNNING"
    assert productie["active_workloads"]["images"] == [OUD]
    na = muterend(keten)[len(voor):]
    assert "app.update" in na and "app.rollback" not in na and not any(m.endswith(".rollback") for m in na)
    assert not any(m.endswith((".delete", ".destroy")) and not m.startswith("app.") for m in muterend(keten))
    assert keten.inhoud()["snapshots"][f"{k.DATASET}@{snapshot}"] == DATA_VOOR  # het herstelpunt staat er nog
    assert keten.overtredingen() == []


def test_mislukte_migratie_laat_de_oude_app_met_data_intact(keten: k.Keten) -> None:
    """Acceptatie B08-004: "DB migratiefout laat oude app met data intact"."""
    k.gotify_draait(keten)
    assert keten.nas.post("/_sim/faults/migratie_mislukt", json={"image": f"{h.GOTIFY_REPO}:3.1.1"}).status_code == 200
    view = keten.plan("update", keten.body(schaduw={"parameters": h.SCHADUW}))
    kloon = view["plan"]["vastgepind"]["sandbox_klonen"][0]

    view = keten.loop(view, tot="schaduw-installeren")
    status = h.status(view["voortgang"])
    assert status["schaduw-installeren"] == "mislukt", view["voortgang"]
    reden = next(s["reden"] for s in view["voortgang"] if s["id"] == "schaduw-installeren")
    assert "CRASHED" in reden
    # de migratie liep stuk op de KLOON; de productiedata en de oude app zijn niet aangeraakt
    inhoud = keten.inhoud()["datasets"]
    assert inhoud[kloon]["beschadigd"] is True and inhoud[kloon]["schema"].startswith("half-gemigreerd")
    assert inhoud[k.DATASET] == DATA_VOOR
    productie = keten.app("gotify")
    assert productie is not None and productie["state"] == "RUNNING"
    assert productie["active_workloads"]["images"] == [OUD]

    # de productie-update komt nooit vrij: het plan weigert de melding en de Hub krijgt geen voorstel
    assert status["bijwerken"] != "vrijgegeven" and "bijwerken" not in view["vrijgegeven_stappen"]
    geweigerd = keten.meld(view["id"], "bijwerken", {"status": "voltooid", "execution_id": str(uuid.uuid4()),
                                                     "readback": {"staat": "RUNNING", "versie": None, "digests": [],
                                                                  "images": [], "etag": None}, "reden": None})
    assert (geweigerd.status_code, geweigerd.json()["code"]) == (409, "stap_niet_vrijgegeven")

    # opruimen van de testkopie blijft mogelijk; productie draait ongewijzigd door
    view = keten.loop(view)
    assert keten.app("gotify-test") is None
    status = h.status(view["voortgang"])
    assert (status["bijwerken"], status["schaduw-verwijderen"]) == ("geblokkeerd", "voltooid"), view["voortgang"]
    assert view["vrijgegeven_stappen"] == ["kloon-opruimen"] and "app.update" not in muterend(keten)
    # de kloon blijft staan tot de eigenaar hem zelf weghaalt en dat meldt; pas dan is het plan afgesloten (mislukt)
    assert kloon in keten.inhoud()["datasets"]
    eigenaar = keten.client.post(f"/lifecycle/v1/plans/{view['id']}/steps/kloon-opruimen/result",
                                 headers=keten.auth("operator", idem=True), json={"status": "voltooid"})
    assert eigenaar.status_code == 200, eigenaar.text
    assert keten.lees(view["id"])["status"] == "mislukt"
    productie = keten.app("gotify")
    assert productie is not None and productie["active_workloads"]["images"] == [OUD]
    assert keten.inhoud()["datasets"][k.DATASET] == DATA_VOOR and keten.overtredingen() == []


def test_gewijzigde_app_na_goedkeuring_wordt_niet_bijgewerkt(keten: k.Keten) -> None:
    """De goedkeuring is gebonden aan de ETag van de preview: verandert de app daarna, dan voert de executor niets
    uit (``doel_gewijzigd``) en moet er een nieuw voorstel komen."""
    k.gotify_draait(keten)
    view = keten.loop(keten.plan("update", keten.body(schaduw={"parameters": h.SCHADUW})), tot="schaduw-installeren")
    verzoek = keten.dien_in(view, "bijwerken")
    keten.keur_goed(view, "bijwerken", verzoek)
    keten.rpc.job("app.stop", "gotify")  # iemand stopt de app ná de goedkeuring
    request = keten.hub.state.sim.execution_request(verzoek.request_id)
    with pytest.raises(Weigering) as fout:
        keten.executor.execute(request)
    assert (fout.value.status, fout.value.code) == (409, "doel_gewijzigd")
    productie = keten.app("gotify")
    assert productie is not None and productie["active_workloads"]["images"] == [OUD]
    assert c8.app_etag(productie, keten.rpc.call("app.image.query")) != verzoek.proposal["expected_version"]
