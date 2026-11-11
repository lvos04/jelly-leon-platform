"""``/lifecycle/v1/plans`` tegen echte PostgreSQL en de Hub-simulator (B08-003, B08-004, C1.3-JLP-14).

De inventaris en de catalogusbeschikbaarheid komen hier écht over HTTP van de (gesimuleerde) Hub. Sim-getest.
"""

from __future__ import annotations

import uuid
from typing import Any

import planhulp as h
import pytest
from sqlalchemy import create_engine, text

PREVIEW = "/lifecycle/v1/plans/{}/preview"
EXEC = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def body(naam: str = "gotify", parameters: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {"manifest_yaml": h.manifest(naam), "parameters": h.GOTIFY if parameters is None else parameters,
            "doel": h.DOEL_JSON, **extra}


def meetbaar(hub: Any, *records: dict[str, Any], ix_app: str = "ja") -> Any:
    p2 = hub.state.sim.p2
    p2.zet_apps(*records)
    p2.zet_beschikbaar("stable", "ix-app", "1.4.8", ix_app)
    return p2


def blokkades(view: dict[str, Any]) -> set[str]:
    return {b["code"] for b in view["plan"]["blokkades"]}


def rijen(pg_url: str, sql: str) -> list[Any]:
    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            return list(conn.execute(text(sql)).all())
    finally:
        engine.dispose()


def test_installatieplan_diff_met_vereiste_goedkeuring(client: Any, auth: Any, hub: Any, clean_db: str) -> None:
    meetbaar(hub)
    kop = auth("operator", idem=True)
    r = client.post(PREVIEW.format("install"), json=body(), headers=kop)
    assert r.status_code == 201, r.text
    view = r.json()
    assert r.headers["ETag"] == view["etag"] and r.headers["Location"] == f"/lifecycle/v1/plans/{view['id']}"
    assert (view["soort"], view["app_name"], view["status"]) == ("install", "gotify", "klaar_voor_voorstel")
    assert view["vrijgegeven_stappen"] == ["installeren"] and blokkades(view) == set()
    plan = view["plan"]
    assert plan["bronnen"]["inventaris"]["status"] == "ok" and plan["bronnen"]["inventaris"]["bron"] == "hub_snapshot"
    assert plan["bronnen"]["catalogus"]["beschikbaar"] == "ja"
    assert plan["goedkeuring"] == {**plan["goedkeuring"], "vereist": True, "hoogste_risicoklasse_verwacht": "R2",
                                   "stappen": ["installeren"]}
    assert plan["diff"]["image"]["naar"]["digest"] == h.GOTIFY_DIGEST
    assert plan["diff"]["poorten"] == [{"host_poort": 30215, "container_poort": 8080, "protocol": "tcp"}]
    assert plan["diff"]["privileges"]["privileged"] is False and plan["diff"]["privileges"]["docker_socket"] is False
    assert plan["diff"]["netwerk"]["uitgaand"]["afdwingbaar"] is False  # eerlijk: het formulier kent geen egressregels
    assert plan["uitvoering"]["deze_dienst_voert_uit"] is False
    assert hub.state.sim.calls["apps"] == 1 and hub.state.sim.calls["catalog_availability"] == 1

    # zelfde Idempotency-Key + zelfde inhoud → hetzelfde plan, geen tweede meting of rij
    opnieuw = client.post(PREVIEW.format("install"), json=body(), headers=kop)
    assert opnieuw.status_code == 201 and opnieuw.json()["id"] == view["id"]
    assert opnieuw.headers["Idempotent-Replay"] == "true" and hub.state.sim.calls["apps"] == 1
    assert rijen(clean_db, "SELECT count(*) FROM lifecycle_plans")[0][0] == 1

    gelezen = client.get(f"/lifecycle/v1/plans/{view['id']}", headers=auth("viewer"))
    assert gelezen.status_code == 200 and gelezen.json()["plan"] == plan and gelezen.headers["ETag"] == view["etag"]

    events = rijen(clean_db, "SELECT event_type, entity_id, payload FROM outbox_events")
    assert [(e[0], e[1]) for e in events] == [("lifecycle.plan.created", view["id"])]
    assert events[0][2]["status"] == "klaar_voor_voorstel" and events[0][2]["stappen"] == ["installeren"]
    audit = rijen(clean_db, "SELECT action, outcome, target_id FROM audit_log WHERE action LIKE 'lifecycle.plan.%'")
    assert audit == [("lifecycle.plan.create", "ok", view["id"])]


def test_poortconflict_blokkeert_en_er_komt_geen_stap_vrij(client: Any, auth: Any, hub: Any, clean_db: str) -> None:
    """Acceptatie B08-003: een poortconflict blokkeert de deployment."""
    from hub_p2 import app_record

    meetbaar(hub, app_record("uptime-kuma", repository="docker.io/louislam/uptime-kuma", tag="2.0.2",
                             poorten=[(3001, 30215, "tcp")]))
    r = client.post(PREVIEW.format("install"), json=body(), headers=auth("operator", idem=True))
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["status"] == "geblokkeerd" and view["vrijgegeven_stappen"] == []
    assert "port_in_use" in blokkades(view)
    assert all(s["status"] != "vrijgegeven" for s in view["voortgang"])
    melden = client.post(f"/lifecycle/v1/plans/{view['id']}/steps/installeren/result",
                         headers=auth("operator", idem=True),
                         json={"status": "voltooid", "execution_id": EXEC, "readback": {"staat": "RUNNING"}})
    assert (melden.status_code, melden.json()["code"]) == (409, "plan_geblokkeerd")
    audit = rijen(clean_db, "SELECT outcome, detail FROM audit_log WHERE action = 'lifecycle.plan.create'")
    assert audit[0][0] == "geblokkeerd" and "port_in_use" in audit[0][1]["blokkades"]


def test_ontbrekende_afhankelijkheid_blokkeert(client: Any, auth: Any, hub: Any) -> None:
    """Acceptatie B08-003: een ontbrekende vereiste afhankelijkheid blokkeert de deployment."""
    meetbaar(hub)
    parameters = {"host_port": 45876, "state_path": "/mnt/pool-x/apps/beszel-agent",
                  "beszel_hub_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItestsleutel",
                  "timezone": "Europe/Amsterdam"}
    r = client.post(PREVIEW.format("install"), json=body("beszel-agent", parameters), headers=auth("admin", idem=True))
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["status"] == "geblokkeerd" and "dependency_missing" in blokkades(view)
    assert view["vrijgegeven_stappen"] == []


@pytest.mark.parametrize(("instelling", "code", "reden"), [
    ("nee", "version_unavailable", None),
    ("onbekend", "version_availability_unknown", "niet_gemeten"),
    ("geen_route", "capability_unavailable", "route_ontbreekt"),
    ("geen_scope", "capability_unavailable", "scope_ontbreekt"),
])
def test_catalogusbeschikbaarheid_wordt_gemeten_bij_de_hub(client: Any, auth: Any, hub: Any, instelling: str,
                                                           code: str, reden: str | None) -> None:
    """C1.3-JLP-14 / contract §2.2: ``version_unavailable`` alleen op een meting; onbekend is nooit goed."""
    p2 = meetbaar(hub, ix_app=instelling if instelling in ("nee", "onbekend") else "ja")
    if instelling == "geen_route":
        p2.catalogus_route = False
    if instelling == "geen_scope":
        p2.scopes["jelly-leon-platform"] = {"inventaris.lezen"}
    r = client.post(PREVIEW.format("install"), json=body(), headers=auth("operator", idem=True))
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["status"] == "geblokkeerd" and code in blokkades(view) and view["vrijgegeven_stappen"] == []
    catalogus = view["plan"]["bronnen"]["catalogus"]
    assert catalogus["beschikbaar"] == ("nee" if instelling == "nee" else "onbekend")
    assert catalogus["reden"] == reden


@pytest.mark.parametrize(("storing", "reden"), [
    ("geen_scope", "scope_ontbreekt"), ("verouderd", "inventaris_verouderd"), ("hub_stuk", "hub_antwoord_500"),
])
def test_zonder_gemeten_inventaris_is_het_plan_geblokkeerd(client: Any, auth: Any, hub: Any, storing: str,
                                                          reden: str) -> None:
    p2 = meetbaar(hub)
    if storing == "geen_scope":
        p2.scopes["jelly-leon-platform"] = {"catalogus.lezen"}
    elif storing == "verouderd":
        p2.verouderd = True
    else:
        p2.inventaris_storing = (500, "internal_error", None)
    r = client.post(PREVIEW.format("install"), json=body(), headers=auth("operator", idem=True))
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["status"] == "geblokkeerd" and view["vrijgegeven_stappen"] == []
    assert blokkades(view) & {"inventaris_onbekend", "capability_unavailable"}
    assert view["plan"]["bronnen"]["inventaris"]["reden"] == reden


def test_oude_meting_die_de_hub_niet_verouderd_noemt_geeft_niets_vrij(client: Any, auth: Any, hub: Any) -> None:
    """Contract §6.2: een meting ouder dan 900 s is onbekend, ook als de bron haar zelf niet als verouderd markeert en
    ook voor een update of terugzetplan (die doen geen conflictanalyse). Het tijdstip van de meting blijft zichtbaar."""
    p2 = meetbaar(hub, h.gotify_record(tag="3.1.1", digest=h.GOTIFY_DIGEST))
    p2.gemeten_op = p2.clock() - 1200  # twintig minuten oud; 'verouderd' staat uit
    for soort, verzoek in (("update", body(schaduw={"parameters": h.SCHADUW})),
                           ("rollback", {"app_name": "gotify", "herstel_van": EXEC})):
        r = client.post(PREVIEW.format(soort), json=verzoek, headers=auth("operator", idem=True))
        assert r.status_code == 201, r.text
        view = r.json()
        assert view["status"] == "geblokkeerd" and view["vrijgegeven_stappen"] == [], soort
        assert "inventaris_onbekend" in blokkades(view), soort
        bron = view["plan"]["bronnen"]["inventaris"]
        assert (bron["status"], bron["reden"], bron["aantal_apps"]) == ("unknown", "meting_verouderd", None), soort
        assert bron["gemeten_op"] == p2._iso(p2.gemeten_op), soort
    # dezelfde verzoeken op een verse meting: de blokkade door de leeftijd verdwijnt (alleen herstel_van kent nog steeds
    # geen herstelpunt van vóór de update; dat blijft geblokkeerd, R-HARDE-EIS-1)
    p2.gemeten_op = None
    vers = client.post(PREVIEW.format("rollback"), json={"app_name": "gotify", "herstel_van": EXEC},
                       headers=auth("operator", idem=True)).json()
    assert vers["status"] == "geblokkeerd" and blokkades(vers) == {"herstelpunt_onbekend"}
    assert vers["plan"]["bronnen"]["inventaris"]["status"] == "ok"
    p2.zet_apps(h.gotify_record())
    vers = client.post(PREVIEW.format("update"), json=body(schaduw={"parameters": h.SCHADUW}),
                       headers=auth("operator", idem=True)).json()
    assert vers["status"] == "klaar_voor_voorstel" and vers["vrijgegeven_stappen"] == ["stoppen"]

    p2.zet_apps()
    p2.catalogus_gemeten_op = p2.clock() - 1200  # de Hub zegt 'ja', maar op een meting van twintig minuten oud
    r = client.post(PREVIEW.format("install"), json=body(), headers=auth("operator", idem=True))
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["status"] == "geblokkeerd" and view["vrijgegeven_stappen"] == []
    assert blokkades(view) == {"version_availability_unknown"}
    catalogus = view["plan"]["bronnen"]["catalogus"]
    assert (catalogus["beschikbaar"], catalogus["reden"]) == ("onbekend", "meting_verouderd")
    assert catalogus["gemeten_op"] == p2._iso(p2.catalogus_gemeten_op)


def test_rollen_idempotency_en_invoergrenzen(client: Any, auth: Any, hub: Any) -> None:
    meetbaar(hub)
    assert client.post(PREVIEW.format("install"), json=body()).status_code == 401
    assert client.post(PREVIEW.format("install"), json=body(), headers=auth("viewer", idem=True)).status_code == 403
    assert client.post(PREVIEW.format("install"), json=body(), headers=auth("kind", idem=True)).status_code == 403
    zonder = client.post(PREVIEW.format("install"), json=body(), headers=auth("operator"))
    assert (zonder.status_code, zonder.json()["code"]) == (400, "idempotency_key_required")
    for fout in ({**body(), "compose": "services: {}"}, {**body(), "app_name": "Niet_Goed"},
                 {"parameters": {}}, {**body(), "doel": {"truenas_version": "25.04.2.4", "latest": True}}):
        r = client.post(PREVIEW.format("install"), json=fout, headers=auth("operator", idem=True))
        assert r.status_code == 422, r.text
    assert client.get(f"/lifecycle/v1/plans/{uuid.uuid4()}", headers=auth("viewer")).status_code == 404
    assert client.get("/lifecycle/v1/plans/geen-uuid", headers=auth("viewer")).status_code == 404
    assert client.get(f"/lifecycle/v1/plans/{uuid.uuid4()}").status_code == 401
    assert hub.state.sim.calls["apps"] == 0  # geweigerde verzoeken meten niets bij de Hub


def test_ongeldig_manifest_geeft_een_geblokkeerd_plan_zonder_stappen(client: Any, auth: Any, hub: Any) -> None:
    meetbaar(hub)
    r = client.post(PREVIEW.format("install"), json={**body(), "manifest_yaml": "app_id: [kapot"},
                    headers=auth("operator", idem=True))
    assert r.status_code == 201, r.text
    view = r.json()
    assert view["status"] == "geblokkeerd" and "yaml_invalid" in blokkades(view) and view["plan"]["stappen"] == []


def _update(client: Any, auth: Any, **extra: Any) -> dict[str, Any]:
    r = client.post(PREVIEW.format("update"), json=body(schaduw={"parameters": h.SCHADUW}, **extra),
                    headers=auth("operator", idem=True))
    assert r.status_code == 201, r.text
    view: dict[str, Any] = r.json()
    return view


def _meld(client: Any, auth: Any, plan_id: str, stap: str, **resultaat: Any) -> Any:
    return client.post(f"/lifecycle/v1/plans/{plan_id}/steps/{stap}/result", headers=auth("operator", idem=True),
                       json={"status": "voltooid", "execution_id": str(uuid.uuid4()), **resultaat})


def test_updateplan_geeft_stap_voor_stap_vrij(client: Any, auth: Any, hub: Any, clean_db: str) -> None:
    meetbaar(hub, h.gotify_record())
    view = _update(client, auth)
    assert (view["soort"], view["status"]) == ("update", "klaar_voor_voorstel")
    assert view["vrijgegeven_stappen"] == ["stoppen"]
    volgorde = [s["id"] for s in view["plan"]["stappen"]]
    assert volgorde == ["stoppen", "snapshot-data", "starten", "kloon-data", "schaduw-installeren", "bijwerken",
                        "schaduw-verwijderen", "kloon-opruimen"]
    pid = view["id"]

    te_vroeg = _meld(client, auth, pid, "bijwerken", readback={"staat": "RUNNING"})
    assert (te_vroeg.status_code, te_vroeg.json()["code"]) == (409, "stap_niet_vrijgegeven")
    assert _meld(client, auth, pid, "bestaat-niet", readback={"staat": "RUNNING"}).status_code == 404
    zonder = client.post(f"/lifecycle/v1/plans/{pid}/steps/stoppen/result", headers=auth("operator", idem=True),
                         json={"status": "voltooid", "readback": {"staat": "STOPPED"}})
    assert (zonder.status_code, zonder.json()["code"]) == (422, "execution_id_ontbreekt")
    assert client.post(f"/lifecycle/v1/plans/{pid}/steps/stoppen/result", headers=auth("viewer", idem=True),
                       json={"status": "voltooid", "execution_id": EXEC}).status_code == 403

    gestopt = _meld(client, auth, pid, "stoppen", readback={"staat": "STOPPED"})
    assert gestopt.status_code == 200, gestopt.text
    assert gestopt.json()["status"] == "in_uitvoering" and gestopt.json()["vrijgegeven_stappen"] == ["snapshot-data"]
    assert gestopt.headers["ETag"] == gestopt.json()["etag"] != view["etag"]
    for stap, readback in (("snapshot-data", {"staat": "AANWEZIG"}), ("starten", {"staat": "RUNNING"}),
                           ("kloon-data", {"staat": "AANWEZIG"})):
        r = _meld(client, auth, pid, stap, readback=readback)
        assert r.status_code == 200, r.text
    assert r.json()["vrijgegeven_stappen"] == ["schaduw-installeren"]

    # Acceptatie B08-004: de migratie mislukt in de testkopie → de productie-update komt nooit vrij
    mislukt = _meld(client, auth, pid, "schaduw-installeren", status="mislukt", readback={"staat": "CRASHED"},
                    reden="migratie mislukt in de testkopie")
    assert mislukt.status_code == 200, mislukt.text
    na = mislukt.json()
    voortgang = {s["id"]: s["status"] for s in na["voortgang"]}
    assert voortgang["schaduw-installeren"] == "mislukt" and voortgang["bijwerken"] != "vrijgegeven"
    assert "bijwerken" not in na["vrijgegeven_stappen"]
    geweigerd = _meld(client, auth, pid, "bijwerken", readback={"staat": "RUNNING"})
    assert (geweigerd.status_code, geweigerd.json()["code"]) == (409, "stap_niet_vrijgegeven")
    events = [e[0] for e in rijen(clean_db, "SELECT event_type FROM outbox_events ORDER BY id")]
    assert events[0] == "lifecycle.plan.created" and events.count("lifecycle.plan.step_reported") == 5
    uitkomsten = [a[0] for a in rijen(clean_db, "SELECT outcome FROM audit_log WHERE action = "
                                              "'lifecycle.plan.step_result' ORDER BY at")]
    assert uitkomsten[-1] == "mislukt"


def test_update_en_terugzetten_via_het_updateplan(client: Any, auth: Any, hub: Any) -> None:
    """Terugzetten via het updateplan, voor de testvariant waarvoor alleen de image terugzetten veilig is (geen
    migraties, ``tag_only_rollback_safe``; ADR-0025). Voor het echte Gotify-manifest: zie
    ``test_terugzetten_na_update_met_migraties_is_geblokkeerd``."""
    p2 = meetbaar(hub, h.gotify_record())
    r = client.post(PREVIEW.format("update"), json={**body(), "manifest_yaml": h.gotify_alleen_image_veilig()},
                    headers=auth("operator", idem=True))
    assert r.status_code == 201, r.text
    view = r.json()
    assert [s["id"] for s in view["plan"]["stappen"]] == ["stoppen", "snapshot-data", "starten", "bijwerken"]
    pid = view["id"]
    goed = {"staat": "RUNNING", "digests": [f"{h.GOTIFY_REPO}@{h.GOTIFY_DIGEST}"]}
    update_exec = str(uuid.uuid4())
    for stap, readback in (("stoppen", {"staat": "STOPPED"}), ("snapshot-data", {"staat": "AANWEZIG"}),
                           ("starten", {"staat": "RUNNING"})):
        assert _meld(client, auth, pid, stap, readback=readback).status_code == 200
    # de update zelf start, maar de readback laat een andere digest zien → mislukt (nooit 'gelukt' op vertrouwen)
    fout = client.post(f"/lifecycle/v1/plans/{pid}/steps/bijwerken/result", headers=auth("operator", idem=True),
                       json={"status": "voltooid", "execution_id": update_exec,
                             "readback": {"staat": "RUNNING", "digests": [f"{h.GOTIFY_REPO}@{h.OUDE_DIGEST}"]}})
    assert fout.status_code == 200, fout.text
    stap = next(s for s in fout.json()["voortgang"] if s["id"] == "bijwerken")
    assert stap["status"] == "mislukt" and stap["reden"].startswith("readback_afwijking")

    p2.zet_apps(h.gotify_record(tag="3.1.1", digest=h.GOTIFY_DIGEST))
    terug = client.post(PREVIEW.format("rollback"), json={"update_plan_id": pid}, headers=auth("operator", idem=True))
    assert terug.status_code == 201, terug.text
    tv = terug.json()
    assert (tv["soort"], tv["app_name"], tv["status"]) == ("rollback", "gotify", "klaar_voor_voorstel"), tv["plan"]
    stappen = {s["id"]: s for s in tv["plan"]["stappen"]}
    voorstel = next(s["voorstel"] for s in stappen.values() if s["voorstel"] and
                    s["voorstel"]["action_id"] == "app.rollback")
    assert voorstel["parameters"] == {"herstel_van": update_exec} and voorstel["target"] == {"app_id": "gotify"}
    # terugzetten is pas bewezen als de digest van vóór de update terug is — 'de app draait' is niet genoeg
    nog_nieuw = _meld(client, auth, tv["id"], "terugzetten", readback=goed)
    assert nog_nieuw.status_code == 200, nog_nieuw.text
    assert nog_nieuw.json()["status"] == "mislukt"
    assert nog_nieuw.json()["voortgang"][0]["reden"].startswith("readback_afwijking")
    opnieuw = client.post(PREVIEW.format("rollback"), json={"update_plan_id": pid},
                          headers=auth("operator", idem=True)).json()
    terug_gelezen = _meld(client, auth, opnieuw["id"], "terugzetten",
                          readback={"staat": "RUNNING", "digests": [f"{h.GOTIFY_REPO}@{h.OUDE_DIGEST}"]})
    assert terug_gelezen.status_code == 200 and terug_gelezen.json()["status"] == "voltooid", terug_gelezen.text

    assert opnieuw["plan"]["stappen"][0]["herstelpunt"] == view["plan"]["vastgepind"]["snapshot_naam"]

    onbekend = client.post(PREVIEW.format("rollback"), json={"update_plan_id": str(uuid.uuid4())},
                           headers=auth("operator", idem=True))
    assert onbekend.status_code == 404
    for fout_body in ({}, {"herstel_van": EXEC}, {"update_plan_id": pid, "herstel_van": EXEC},
                      {"app_name": "gotify", "catalog": {"train": "stable", "app": "gotify", "version": "latest"}}):
        r = client.post(PREVIEW.format("rollback"), json=fout_body, headers=auth("operator", idem=True))
        assert r.status_code == 422, r.text


def test_terugzetten_na_update_met_migraties_is_geblokkeerd(client: Any, auth: Any, hub: Any) -> None:
    """HARDE EIS (contract §2.3 regel 7a, EX15; R-HARDE-EIS-1): na een update van een app met migraties geeft het
    terugzetplan geen ``app.rollback`` vrij — alleen de image terug zou de oude versie op de gemigreerde database
    zetten. Het plan noemt de herstelpunten voor de eigenaar; melden van een stap wordt geweigerd."""
    p2 = meetbaar(hub, h.gotify_record())
    view = _update(client, auth)
    pid = view["id"]
    goed = {"staat": "RUNNING", "digests": [f"{h.GOTIFY_REPO}@{h.GOTIFY_DIGEST}"]}
    for stap, readback in (("stoppen", {"staat": "STOPPED"}), ("snapshot-data", {"staat": "AANWEZIG"}),
                           ("starten", {"staat": "RUNNING"}), ("kloon-data", {"staat": "AANWEZIG"}),
                           ("schaduw-installeren", goed), ("bijwerken", goed)):
        assert _meld(client, auth, pid, stap, readback=readback).status_code == 200
    p2.zet_apps(h.gotify_record(tag="3.1.1", digest=h.GOTIFY_DIGEST))
    terug = client.post(PREVIEW.format("rollback"), json={"update_plan_id": pid}, headers=auth("operator", idem=True))
    assert terug.status_code == 201, terug.text
    tv = terug.json()
    assert (tv["status"], tv["vrijgegeven_stappen"]) == ("geblokkeerd", []) and blokkades(tv) == {
        "terugzetten_vraagt_data"}
    details = tv["plan"]["blokkades"][0]["details"]
    assert details["herstelpunten"] == view["plan"]["vastgepind"]["herstelpunten"] and details["migrations"] is True
    r = _meld(client, auth, tv["id"], "terugzetten",
              readback={"staat": "RUNNING", "digests": [f"{h.GOTIFY_REPO}@{h.OUDE_DIGEST}"]})
    assert (r.status_code, r.json()["code"]) == (409, "plan_geblokkeerd")


def test_niet_gestart_plan_vervalt(client: Any, auth: Any, hub: Any, clean_db: str) -> None:
    meetbaar(hub)
    view = client.post(PREVIEW.format("install"), json=body(), headers=auth("operator", idem=True)).json()
    engine = create_engine(clean_db)
    with engine.begin() as conn:
        conn.execute(text("UPDATE lifecycle_plans SET expires_at = now() - interval '1 minute'"))
    engine.dispose()
    assert view["vrijgegeven_stappen"] == ["installeren"]
    gelezen = client.get(f"/lifecycle/v1/plans/{view['id']}", headers=auth("viewer")).json()
    assert gelezen["vervallen"] is True
    assert gelezen["vrijgegeven_stappen"] == []  # een vervallen plan biedt geen stap meer aan
    r = _meld(client, auth, view["id"], "installeren", readback={"staat": "RUNNING"})
    assert (r.status_code, r.json()["code"]) == (409, "plan_vervallen")
