"""Planner (B08-003, B08-004): pure functies, geen netwerk of database. De voorstellen worden getoetst tegen de spiegel
van de parameterregels van de Hub (``contract8``), zodat een plan nooit iets voorstelt wat de Hub weigert."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import timedelta
from typing import Any

import contract8
import pytest
from hub_p2 import app_record
from planhulp import (
    DOEL,
    GOTIFY,
    GOTIFY_DIGEST,
    GOTIFY_REPO,
    NU,
    OUDE_DIGEST,
    SCHADUW,
    codes,
    gotify_alleen_image_veilig,
    gotify_record,
    inventaris,
    manifest,
    meting,
    stap,
    status,
)

from lifecycle import planner
from lifecycle.hubread import Beschikbaarheid, Inventaris

PLAN = uuid.UUID("11111111-2222-4333-8444-555555555555")
EXEC = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
GOED = {"staat": "RUNNING", "digests": [f"{GOTIFY_REPO}@{GOTIFY_DIGEST}"]}


def installatie(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"plan_id": PLAN, "manifest_yaml": manifest("gotify"), "parameters": GOTIFY,
                            "app_name": None, "doel": DOEL, "inventaris": inventaris(), "beschikbaar": meting("ja"),
                            "now": NU}
    args.update(kw)
    return planner.plan_installatie(**args)


def update(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"plan_id": PLAN, "manifest_yaml": manifest("gotify"), "parameters": GOTIFY,
                            "app_name": None, "doel": DOEL, "schaduw_parameters": SCHADUW,
                            "inventaris": inventaris(gotify_record()), "beschikbaar": meting("ja"), "now": NU}
    args.update(kw)
    return planner.plan_update(**args)


def meld(doc: dict[str, Any], voortgang: list[dict[str, Any]], step_id: str, uitkomst: str = "voltooid",
         readback: dict[str, Any] | None = None, execution_id: str | None = EXEC) -> list[dict[str, Any]]:
    return planner.meld(doc, voortgang, step_id, status=uitkomst, execution_id=execution_id, readback=readback,
                        reden=None, gemeld_op="2026-11-11T12:00:00Z")


def geen_stap_vrij(doc: dict[str, Any]) -> None:
    voortgang = planner.begin(doc)
    assert planner.plan_status(doc, voortgang) == "geblokkeerd"
    assert all(s["status"] == "geblokkeerd" and s["reden"] == "plan_geblokkeerd" for s in voortgang)
    assert all(s["geblokkeerd"] is True for s in doc["stappen"])


# ------------------------------------------------------------------ installeren (B08-003)
def test_installatieplan_is_een_diff_met_een_voorstel_dat_de_hub_aanneemt() -> None:
    doc = installatie()
    assert doc["schema"] == "jlp-plan/1" and doc["soort"] == "install" and doc["blokkades"] == []
    assert doc["app_id"] == "gotify" and doc["app_name"] == "gotify"
    assert [s["id"] for s in doc["stappen"]] == ["installeren"]
    voorstel = stap(doc, "installeren")["voorstel"]
    assert voorstel["action_id"] == "app.install" and voorstel["target"] == {"app_id": "gotify"}
    assert voorstel["expected_version"] == "absent" and voorstel["expected_version_bron"] == "vast"
    assert contract8.parameter_fouten("app.install", voorstel["target"], voorstel["parameters"]) == []
    # vastgepind: image op digest, manifest-hash en preview-hash van de vertaling
    tag = voorstel["parameters"]["app_create"]["values"]["image"]["tag"]
    assert tag == f"3.1.1@{GOTIFY_DIGEST}" and "latest" not in json.dumps(doc)
    assert voorstel["parameters"]["manifest"]["manifest_sha256"] == doc["manifest"]["manifest_sha256"]
    assert voorstel["parameters"]["appstore_preview_hash"] == doc["vastgepind"]["preview_hash"]
    assert stap(doc, "installeren")["verwacht"] == {"staat": "RUNNING",
                                                    "digest": {"repository": GOTIFY_REPO, "expected": GOTIFY_DIGEST}}
    # de diff voor 'Diff met vereiste goedkeuring': image, datasets, privileges, poorten, netwerk, geheimen, afhank.
    diff = doc["diff"]
    assert set(diff) == {"image", "datasets", "privileges", "poorten", "netwerk", "geheimen", "afhankelijkheden"}
    assert diff["image"]["naar"]["digest"] == GOTIFY_DIGEST
    assert diff["poorten"][0]["host_poort"] == 30215
    assert diff["datasets"]["aangekoppeld"][0]["hostpad"] == "/mnt/pool-x/apps/gotify"
    assert diff["privileges"]["docker_socket"] is False
    assert diff["netwerk"]["uitgaand"]["afdwingbaar"] is False  # eerlijk: het formulier kent geen egress-regels
    assert diff["geheimen"]["waarden_in_plan"] is False
    assert doc["goedkeuring"] == {**doc["goedkeuring"], "vereist": True, "hoogste_risicoklasse_verwacht": "R2",
                                  "stappen": ["installeren"]}
    assert doc["uitvoering"]["deze_dienst_voert_uit"] is False
    voortgang = planner.begin(doc)
    assert status(voortgang) == {"installeren": "vrijgegeven"}
    assert planner.plan_status(doc, voortgang) == "klaar_voor_voorstel"


def test_plan_is_deterministisch_per_plan_id() -> None:
    assert installatie() == installatie()
    ander = installatie(plan_id=uuid.UUID("99999999-2222-4333-8444-555555555555"))
    assert stap(ander, "installeren")["voorstel"]["idempotency_key"] != \
        stap(installatie(), "installeren")["voorstel"]["idempotency_key"]


def test_poortconflict_blokkeert_de_installatie() -> None:
    bezet = app_record("iets-anders", repository="example.org/x", tag="1.0.0", poorten=[(80, 30215, "tcp")])
    doc = installatie(inventaris=inventaris(bezet))
    assert codes(doc) == {"port_in_use"}
    assert "30215" in doc["blokkades"][0]["melding_nl"] and "iets-anders" in doc["blokkades"][0]["melding_nl"]
    geen_stap_vrij(doc)


def test_ontbrekende_vereiste_afhankelijkheid_blokkeert_de_installatie() -> None:
    params = {"host_port": 45876, "state_path": "/mnt/pool-x/apps/beszel-agent", "timezone": "Europe/Amsterdam",
              "beszel_hub_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK8Zx000000000000000000000000000000000000"}
    doc = installatie(manifest_yaml=manifest("beszel-agent"), parameters=params)
    assert codes(doc) == {"dependency_missing"}
    assert doc["diff"]["afhankelijkheden"] == [{**doc["diff"]["afhankelijkheden"][0], "app_id": "beszel-hub",
                                                "soort": "required", "status": "ontbreekt"}]
    geen_stap_vrij(doc)
    hub = app_record("beszel-hub", repository="example.org/beszel", tag="1.0.0", app_id="beszel-hub")
    met = installatie(manifest_yaml=manifest("beszel-agent"), parameters=params, inventaris=inventaris(hub))
    assert "dependency_missing" not in codes(met)
    assert met["diff"]["afhankelijkheden"][0]["status"] == "aanwezig"


def test_naam_al_in_gebruik_blokkeert() -> None:
    doc = installatie(inventaris=inventaris(gotify_record(host_port=30999)))
    assert "app_name_taken" in codes(doc)
    geen_stap_vrij(doc)


def test_gemeten_afwezige_catalogusversie_geeft_version_unavailable() -> None:
    doc = installatie(beschikbaar=meting("nee"))
    assert codes(doc) == {"version_unavailable"}
    eerste = doc["blokkades"][0]
    assert eerste["details"]["pinned_version"] == "1.4.8" and eerste["details"]["gemeten_op"]
    assert doc["bronnen"]["catalogus"]["beschikbaar"] == "nee"
    assert doc["stappen"] == []  # nooit stil een nieuwere versie of 'latest'


@pytest.mark.parametrize(("lees", "code"), [
    (meting("onbekend"), "version_availability_unknown"),
    (meting("onbekend", "unknown", "hub_onbereikbaar"), "version_availability_unknown"),
    (meting("onbekend", "capability_unavailable", "scope_ontbreekt"), "capability_unavailable"),
])
def test_onbekende_beschikbaarheid_geeft_niets_vrij(lees: Any, code: str) -> None:
    doc = installatie(beschikbaar=lees)
    assert codes(doc) == {code}
    geen_stap_vrij(doc)


@pytest.mark.parametrize(("bron", "code"), [
    (Inventaris("unknown", "hub_onbereikbaar"), "inventaris_onbekend"),
    (Inventaris("unknown", "inventaris_verouderd"), "inventaris_onbekend"),
    (Inventaris("capability_unavailable", "scope_ontbreekt"), "capability_unavailable"),
])
def test_zonder_gemeten_inventaris_geeft_niets_vrij(bron: Inventaris, code: str) -> None:
    doc = installatie(inventaris=bron)
    assert code in codes(doc)
    assert doc["bronnen"]["inventaris"]["status"] == bron.status and doc["bronnen"]["inventaris"]["reden"] == bron.reden
    geen_stap_vrij(doc)


def test_ongeldig_manifest_en_onbekend_doel_blokkeren() -> None:
    assert "yaml_invalid" in codes(installatie(manifest_yaml=": : :\n- ["))
    kapot = installatie(manifest_yaml="app_id: x\n")
    assert kapot["blokkades"] and kapot["stappen"] == [] and kapot["manifest"] is None
    zonder = installatie(doel=planner.Doel())
    assert codes(zonder) == {"truenas_version_unknown", "ix_app_version_unknown"} and zonder["stappen"] == []
    assert "app_naam_ongeldig" in codes(installatie(app_name="Hoofdletters_mag_niet"))


def test_geheime_waarden_komen_nooit_in_een_plan() -> None:
    params = {"timezone": "Europe/Amsterdam", "http_port": 30140, "https_port": 30141, "rpo": "PT24H", "rto": "PT4H",
              "data_path": "/mnt/pool-x/apps/authentik/data", "templates_path": "/mnt/pool-x/apps/authentik/templates",
              "certs_path": "/mnt/pool-x/apps/authentik/certs", "postgres_path": "/mnt/pool-x/apps/authentik/pg",
              "db_password": "heel-geheim-123"}
    doc = installatie(manifest_yaml=manifest("authentik"), parameters=params)
    assert "parameter_value_not_allowed" in codes(doc) and doc["stappen"] == []
    assert "heel-geheim-123" not in json.dumps(doc)
    assert doc["diff"]["geheimen"]["parameters"] == ["db_password", "secret_key"]
    assert doc["bronnen"]["catalogus"]["app"] == "authentik"  # voor een catalogusapp telt de versie uit het manifest


# ------------------------------------------------------------------ bijwerken (B08-004)
def test_updateplan_is_een_expliciete_dag_met_herstelpunt_en_schaduwtest() -> None:
    doc = update()
    assert doc["blokkades"] == []
    ids = [s["id"] for s in doc["stappen"]]
    assert ids == ["stoppen", "snapshot-data", "starten", "kloon-data", "schaduw-installeren", "bijwerken",
                   "schaduw-verwijderen", "kloon-opruimen"]
    # elke stap wijst alleen terug: een DAG zonder kringen
    for i, s in enumerate(doc["stappen"]):
        assert set(s["hangt_af_van"]) <= set(ids[:i])
        if s["voorstel"]:
            v = s["voorstel"]
            assert contract8.parameter_fouten(v["action_id"], v["target"], v["parameters"]) == [], s["id"]
    ref = PLAN.hex[:8]
    snapshot = f"jlp-{ref}-voor-3.1.1"
    assert stap(doc, "snapshot-data")["voorstel"] == {
        **stap(doc, "snapshot-data")["voorstel"], "action_id": "zfs.snapshot.create",
        "target": {"dataset": "pool-x/apps/gotify"}, "parameters": {"naam": snapshot},
        "expected_version": "dataset:pool-x/apps/gotify"}
    kloon = stap(doc, "kloon-data")["voorstel"]
    assert kloon["target"] == {"snapshot": f"pool-x/apps/gotify@{snapshot}"}
    assert kloon["parameters"] == {"doel_dataset": f"pool-x/asm-sandbox/gotify-{ref}"}
    schaduw = stap(doc, "schaduw-installeren")["voorstel"]
    assert schaduw["target"] == {"app_id": "gotify-test"} and schaduw["expected_version"] == "absent"
    waarden = schaduw["parameters"]["app_create"]["values"]
    assert waarden["storage"][0]["host_path_config"]["path"] == f"/mnt/pool-x/asm-sandbox/gotify-{ref}"
    assert waarden["ports"][0]["port_number"] == 30216  # de testkopie raakt de productiepoort niet
    bijwerken = stap(doc, "bijwerken")
    assert bijwerken["voorstel"]["parameters"] == {
        "image": {"repository": GOTIFY_REPO, "tag": f"3.1.1@{GOTIFY_DIGEST}"},
        "manifest_sha256": doc["manifest"]["manifest_sha256"]}
    assert bijwerken["voorstel"]["expected_version"] is None  # app-ETag: de Hub leest terug en bindt de goedkeuring
    assert bijwerken["voorstel"]["expected_version_bron"] == "hub_readback"
    assert set(bijwerken["hangt_af_van"]) == {"snapshot-data", "starten", "schaduw-installeren"}
    assert bijwerken["herstelpunt"] == snapshot and bijwerken["herstelpunt_van"] == ["snapshot-data"]
    assert doc["vastgepind"] == {"snapshot_naam": snapshot, "sandbox_klonen": [f"pool-x/asm-sandbox/gotify-{ref}"],
                                 "herstelpunten": [f"pool-x/apps/gotify@{snapshot}"]}
    assert doc["diff"]["image"]["van"] == {"repository": GOTIFY_REPO, "tag": "3.0.0", "digest": OUDE_DIGEST}
    assert stap(doc, "kloon-opruimen")["soort"] == "handmatig"  # het platform verwijdert nooit zelf datasets
    assert status(planner.begin(doc))["stoppen"] == "vrijgegeven"
    assert list(status(planner.begin(doc)).values()).count("vrijgegeven") == 1


@pytest.mark.parametrize(("kw", "code"), [
    ({"schaduw_parameters": None}, "schaduwtest_parameters_ontbreken"),
    ({"schaduw_parameters": {}}, "port_in_use"),  # de testkopie zou de productiepoort pakken
    ({"inventaris": inventaris()}, "app_niet_geinstalleerd"),
    ({"inventaris": inventaris(gotify_record(dataset=None))}, "dataset_onbekend"),
    ({"beschikbaar": meting("nee")}, "version_unavailable"),
    ({"beschikbaar": meting("onbekend")}, "version_availability_unknown"),
    ({"inventaris": Inventaris("unknown", "hub_onbereikbaar")}, "inventaris_onbekend"),
])
def test_updateplan_blokkades_geven_niets_vrij(kw: dict[str, Any], code: str) -> None:
    doc = update(**kw)
    assert code in codes(doc)
    geen_stap_vrij(doc)


def test_update_van_andere_repository_of_installatiewijze_wordt_geweigerd() -> None:
    opslag = [("/mnt/pool-x/apps/gotify", "/app/data", "pool-x/apps/gotify")]
    ander = app_record("gotify", repository="example.org/anders", tag="1.0.0", app_id="gotify", opslag=opslag)
    assert "image_repository_verschilt" in codes(update(inventaris=inventaris(ander)))
    compose = {**gotify_record(), "installatiewijze": "custom_compose"}
    assert "installatiewijze_verschilt" in codes(update(inventaris=inventaris(compose)))


def test_update_zonder_migraties_vraagt_geen_schaduwtest() -> None:
    record = app_record("beszel-agent", repository="ghcr.io/henrygd/beszel/beszel-agent", tag="0.1.0",
                        app_id="beszel-agent", opslag=[("/mnt/pool-x/apps/ba", "/var/lib/beszel-agent", "pool-x/ba")])
    doc = update(manifest_yaml=manifest("beszel-agent"), parameters={}, schaduw_parameters=None,
                 inventaris=inventaris(record), beschikbaar=meting("onbekend"))
    # §2.2: voor app.update van een geïnstalleerde Custom App zonder testkopie speelt de ix-app-versie niet
    assert doc["blokkades"] == []
    assert [s["id"] for s in doc["stappen"]] == ["snapshot-state", "bijwerken"]
    assert stap(doc, "bijwerken")["herstelpunt"]


def test_update_van_catalogusapp_gebruikt_de_gemeten_catalogusversie() -> None:
    record = {**app_record("authentik", repository="ghcr.io/goauthentik/server", tag="2025.1.1", app_id="authentik",
                           catalogus_app="authentik", trein="community", versie="1.3.60"),
              "installatiewijze": "catalog_app"}
    doc = update(manifest_yaml=manifest("authentik"), parameters={}, inventaris=inventaris(record))
    # de datasets zijn in dit record niet gemeten → geen herstelpunt → geen update
    assert codes(doc) == {"dataset_onbekend"} and len(doc["blokkades"]) == 4  # één per snapshot-dataset
    assert planner.plan_update(plan_id=PLAN, manifest_yaml=manifest("authentik"), parameters={}, app_name=None,
                               doel=DOEL, schaduw_parameters=None, inventaris=inventaris(record),
                               beschikbaar=meting("nee"), now=NU)["blokkades"][0]["code"] == "version_unavailable"
    assert stap(doc, "bijwerken")["voorstel"]["parameters"] == {"catalog_version": "1.3.64"}
    assert stap(doc, "export")["soort"] == "handmatig"
    assert any(b["code"] == "schaduwtest_niet_beschikbaar" for b in doc["bevindingen"])


# ------------------------------------------------------------------ versheid van de metingen (contract 1.3 §6.2)
NODE_RED = app_record("node-red", repository="nodered/node-red", tag="5.0.8-24", digest=OUDE_DIGEST,
                      app_id="node-red", poorten=[(1880, 31880, "tcp")],
                      opslag=[("/mnt/pool-x/apps/node-red", "/data", "pool-x/apps/node-red")])


def verschoven(bron: Inventaris, verschil: timedelta) -> Inventaris:
    """Dezelfde inventaris, maar gemeten op ``NU + verschil`` — de Hub noemt haar zelf niet verouderd."""
    assert bron.inventory is not None
    moment = NU + verschil
    return replace(bron, inventory=bron.inventory.model_copy(update={"observed_at": moment}),
                   gemeten_op=moment.strftime("%Y-%m-%dT%H:%M:%SZ"))


def meting_van(gemeten_op: str | None, waarde: str = "ja") -> Any:
    def lees(train: str, app: str, version: str) -> Beschikbaarheid:
        return Beschikbaarheid(waarde, "ok", None, gemeten_op, train, app, version)

    return lees


def elk_soort_plan(bron_gotify: Inventaris, bron_node_red: Inventaris) -> dict[str, dict[str, Any]]:
    """Elk soort plan op een meting van hetzelfde moment. Alleen installeren en de testkopie doen een conflictanalyse;
    een update zonder migraties en terugzetten doen dat niet en mogen daarom evenmin op een oude meting leunen."""
    assert bron_gotify.inventory is not None
    leeg = replace(bron_gotify, inventory=bron_gotify.inventory.model_copy(update={"apps": []}), records={})
    bijgewerkt_plan, voortgang = bijgewerkt()
    return {
        "installeren": installatie(inventaris=leeg),
        "update met testkopie": update(inventaris=bron_gotify),
        "update zonder testkopie": update(manifest_yaml=manifest("node-red"), parameters={}, schaduw_parameters=None,
                                          inventaris=bron_node_red),
        "terugzetten (herstel_van)": terug(app_name="gotify", herstel_van=EXEC, inventaris=bron_gotify),
        "terugzetten (updateplan)": terug(update_plan=bijgewerkt_plan, update_stappen=voortgang,
                                          inventaris=bron_gotify),
    }


@pytest.mark.parametrize("verschil", [timedelta(seconds=-901), timedelta(days=-2), timedelta(seconds=121)])
def test_verouderde_inventaris_geeft_voor_geen_enkel_soort_plan_een_stap_vrij(verschil: timedelta) -> None:
    gotify = verschoven(inventaris(gotify_record(tag="3.1.1", digest=GOTIFY_DIGEST)), verschil)
    node_red = verschoven(inventaris(NODE_RED), verschil)
    for naam, doc in elk_soort_plan(gotify, node_red).items():
        assert "inventaris_onbekend" in codes(doc), naam
        bron = doc["bronnen"]["inventaris"]
        assert (bron["status"], bron["reden"], bron["aantal_apps"]) == ("unknown", "meting_verouderd", None), naam
        assert bron["gemeten_op"] == gotify.gemeten_op, naam  # het tijdstip van de laatste meting blijft zichtbaar
        geen_stap_vrij(doc)


@pytest.mark.parametrize("verschil", [timedelta(seconds=-900), timedelta(0), timedelta(seconds=120)])
def test_verse_inventaris_binnen_de_grens_geeft_wel_vrij(verschil: timedelta) -> None:
    gotify = verschoven(inventaris(gotify_record(tag="3.1.1", digest=GOTIFY_DIGEST)), verschil)
    node_red = verschoven(inventaris(NODE_RED), verschil)
    plannen = elk_soort_plan(gotify, node_red)
    # met alleen herstel_van kent een terugzetplan geen herstelpunt: dat blokkeert altijd, maar niet op de meting
    assert codes(plannen.pop("terugzetten (herstel_van)")) == {"herstelpunt_onbekend"}
    for naam, doc in plannen.items():
        assert doc["blokkades"] == [], naam
        assert "vrijgegeven" in status(planner.begin(doc)).values(), naam


@pytest.mark.parametrize(("gemeten_op", "reden"), [
    ("2026-11-11T11:44:59Z", "meting_verouderd"),  # 901 s oud
    ("2026-11-09T12:00:00Z", "meting_verouderd"),
    ("2026-11-11T12:02:01Z", "meting_verouderd"),  # uit de toekomst (klokverschil)
    (None, "meting_zonder_tijdstip"),
    ("geen-tijdstip", "meting_zonder_tijdstip"),
])
def test_verouderde_catalogusmeting_is_onbekend_ook_als_ze_ja_zei(gemeten_op: str | None, reden: str) -> None:
    authentik = {**app_record("authentik", repository="ghcr.io/goauthentik/server", tag="2025.1.1",
                              app_id="authentik", catalogus_app="authentik", trein="community", versie="1.3.64"),
                 "installatiewijze": "catalog_app"}
    catalog = {"train": "community", "app": "authentik", "version": "1.3.60"}
    plannen = {
        "installeren": installatie(beschikbaar=meting_van(gemeten_op)),
        "update met testkopie": update(beschikbaar=meting_van(gemeten_op)),
        "terugzetten naar catalogusversie": terug(app_name="authentik", catalog=catalog,
                                                  inventaris=inventaris(authentik),
                                                  beschikbaar=meting_van(gemeten_op)),
    }
    for naam, doc in plannen.items():
        # terugzetten naar een catalogusversie kent daarnaast geen herstelpunt (R-HARDE-EIS-1)
        assert codes(doc) - {"herstelpunt_onbekend"} == {"version_availability_unknown"}, naam
        details = next(b["details"] for b in doc["blokkades"] if b["code"] == "version_availability_unknown")
        assert (details["reden"], details["gemeten_op"]) == (reden, gemeten_op), naam
        geen_stap_vrij(doc)
    bron = plannen["installeren"]["bronnen"]["catalogus"]
    assert (bron["beschikbaar"], bron["status"], bron["reden"], bron["gemeten_op"]) == (
        "onbekend", "unknown", reden, gemeten_op)
    # ook een oude 'nee' is geen meting meer: version_unavailable mag alleen op een verse meting (§2.2)
    assert codes(installatie(beschikbaar=meting_van(gemeten_op, "nee"))) == {"version_availability_unknown"}


@pytest.mark.parametrize("gemeten_op", ["2026-11-11T11:45:00Z", "2026-11-11T12:00:00Z", "2026-11-11T12:02:00Z"])
def test_verse_catalogusmeting_binnen_de_grens_telt(gemeten_op: str) -> None:
    assert installatie(beschikbaar=meting_van(gemeten_op))["blokkades"] == []
    assert codes(installatie(beschikbaar=meting_van(gemeten_op, "nee"))) == {"version_unavailable"}


# ------------------------------------------------------------------ stappen: vrijgeven en resultaten
def tot_schaduw(doc: dict[str, Any]) -> list[dict[str, Any]]:
    v = planner.begin(doc)
    v = meld(doc, v, "stoppen", readback={"staat": "STOPPED"})
    assert status(v)["snapshot-data"] == "vrijgegeven" and status(v)["starten"] == "wacht"
    v = meld(doc, v, "snapshot-data")
    assert status(v)["starten"] == "vrijgegeven" and status(v)["kloon-data"] == "vrijgegeven"
    v = meld(doc, v, "starten", readback={"staat": "RUNNING"})
    v = meld(doc, v, "kloon-data")
    assert status(v)["schaduw-installeren"] == "vrijgegeven" and status(v)["bijwerken"] == "wacht"
    return v


def test_volledige_update_doorloopt_alle_stappen() -> None:
    doc = update()
    v = tot_schaduw(doc)
    assert planner.plan_status(doc, v) == "in_uitvoering"
    v = meld(doc, v, "schaduw-installeren", readback=GOED)
    assert status(v)["bijwerken"] == "vrijgegeven" and status(v)["schaduw-verwijderen"] == "wacht"
    v = meld(doc, v, "bijwerken", readback=GOED)
    assert status(v)["schaduw-verwijderen"] == "vrijgegeven"
    v = meld(doc, v, "schaduw-verwijderen", readback={"staat": "ABSENT"})
    assert status(v)["kloon-opruimen"] == "vrijgegeven"
    v = meld(doc, v, "kloon-opruimen", execution_id=None)  # handmatige stap: geen execution_id
    assert set(status(v).values()) == {"voltooid"} and planner.plan_status(doc, v) == "voltooid"


def test_mislukte_migratie_in_de_schaduwtest_houdt_de_productie_update_tegen() -> None:
    """B08-004-acceptatie op planniveau: de testkopie start niet gezond → 'bijwerken' komt nooit vrij."""
    doc = update()
    v = tot_schaduw(doc)
    v = meld(doc, v, "schaduw-installeren", readback={"staat": "CRASHED", "digests": GOED["digests"]})
    s = status(v)
    assert s["schaduw-installeren"] == "mislukt" and s["bijwerken"] == "geblokkeerd"
    assert next(x for x in v if x["id"] == "schaduw-installeren")["reden"].startswith("readback_afwijking: toestand")
    assert next(x for x in v if x["id"] == "bijwerken")["reden"] == "voorgaande_stap_niet_voltooid"
    assert s["schaduw-verwijderen"] == "vrijgegeven"  # de kapotte testkopie wordt wel opgeruimd
    with pytest.raises(planner.ResultaatFout) as fout:
        meld(doc, v, "bijwerken", readback=GOED)
    assert (fout.value.status, fout.value.code) == (409, "stap_niet_vrijgegeven")
    v = meld(doc, v, "schaduw-verwijderen", readback={"staat": "ABSENT"})
    v = meld(doc, v, "kloon-opruimen", execution_id=None)
    assert planner.plan_status(doc, v) == "mislukt"


def test_mislukte_snapshot_start_de_app_weer_en_geeft_geen_update_vrij() -> None:
    doc = update()
    v = planner.begin(doc)
    v = meld(doc, v, "stoppen", readback={"staat": "STOPPED"})
    v = meld(doc, v, "snapshot-data", "mislukt")
    s = status(v)
    assert s["starten"] == "vrijgegeven"  # de app blijft nooit gestopt achter
    assert s["kloon-data"] == "geblokkeerd" and s["bijwerken"] == "geblokkeerd"
    v = meld(doc, v, "starten", readback={"staat": "RUNNING"})
    s = status(v)
    assert s["schaduw-verwijderen"] == "overgeslagen" and s["kloon-opruimen"] == "overgeslagen"
    assert planner.plan_status(doc, v) == "mislukt"


def test_mislukt_stoppen_slaat_het_starten_over() -> None:
    doc = update()
    v = meld(doc, planner.begin(doc), "stoppen", "mislukt")
    assert status(v)["snapshot-data"] == "geblokkeerd" and status(v)["starten"] == "vrijgegeven"


def test_voltooid_telt_alleen_met_kloppende_readback() -> None:
    doc = installatie()
    v = planner.begin(doc)
    with pytest.raises(planner.ResultaatFout) as fout:
        meld(doc, v, "installeren", readback=None)
    assert (fout.value.status, fout.value.code) == (422, "readback_ontbreekt")
    with pytest.raises(planner.ResultaatFout) as fout:
        meld(doc, v, "installeren", readback=GOED, execution_id=None)
    assert (fout.value.status, fout.value.code) == (422, "execution_id_ontbreekt")
    with pytest.raises(planner.ResultaatFout) as fout:
        meld(doc, v, "bestaat-niet", readback=GOED)
    assert fout.value.status == 404
    andere = meld(doc, v, "installeren", readback={"staat": "RUNNING", "digests": [f"{GOTIFY_REPO}@{OUDE_DIGEST}"]})
    assert status(andere) == {"installeren": "mislukt"}
    assert "digest wijkt af" in andere[0]["reden"] and planner.plan_status(doc, andere) == "mislukt"
    geen_digest = meld(doc, v, "installeren", readback={"staat": "RUNNING"})
    assert status(geen_digest) == {"installeren": "mislukt"}
    goed = meld(doc, v, "installeren", readback={**GOED, "config": {"wachtwoord": "x"}})
    assert status(goed) == {"installeren": "voltooid"} and planner.plan_status(doc, goed) == "voltooid"
    assert "config" not in goed[0]["readback"]  # alleen de vergeleken velden worden bewaard
    with pytest.raises(planner.ResultaatFout) as fout:
        meld(doc, goed, "installeren", readback=GOED)
    assert fout.value.code == "stap_niet_vrijgegeven"  # een resultaat wordt niet overschreven


# ------------------------------------------------------------------ terugzetten (B08-004)
def bijgewerkt(*, veilig: bool = True, **kw: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Een updateplan dat tot en met 'bijwerken' liep; de update zelf mislukt bij de readback (bijvoorbeeld een
    migratiefout in productie). ``veilig``: de testvariant waarvoor alleen de image terugzetten veilig is (geen
    migraties, ``tag_only_rollback_safe``); anders het echte Gotify-manifest (migraties, testkopie)."""
    if veilig:
        doc = update(manifest_yaml=gotify_alleen_image_veilig(), schaduw_parameters=None, **kw)
        v = planner.begin(doc)
        v = meld(doc, v, "stoppen", readback={"staat": "STOPPED"})
        v = meld(doc, v, "snapshot-data")
        v = meld(doc, v, "starten", readback={"staat": "RUNNING"})
    else:
        doc = update(**kw)
        v = tot_schaduw(doc)
        v = meld(doc, v, "schaduw-installeren", readback=GOED)
    assert status(v)["bijwerken"] == "vrijgegeven", v
    v = meld(doc, v, "bijwerken", readback={"staat": "CRASHED", "digests": GOED["digests"]})
    assert status(v)["bijwerken"] == "mislukt"
    return doc, v


def terug(**kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"plan_id": uuid.UUID("77777777-2222-4333-8444-555555555555"), "app_name": None,
                                "herstel_van": None, "catalog": None, "update_plan": None, "update_stappen": None,
                                "inventaris": inventaris(gotify_record(tag="3.1.1", digest=GOTIFY_DIGEST)),
                                "beschikbaar": meting("ja"), "now": NU}
    args.update(kw)
    return planner.plan_rollback(**args)


def test_terugzetten_na_mislukte_update_is_app_update_met_vorige_waarden() -> None:
    bron, voortgang = bijgewerkt()
    assert bron["eigenaar_herstel"]["migrations"] is False and bron["eigenaar_herstel"]["tag_only_rollback_safe"]
    doc = terug(update_plan=bron, update_stappen=voortgang)
    assert doc["blokkades"] == [] and doc["soort"] == "rollback" and doc["bron"] == {"update_plan_id": str(PLAN)}
    s = stap(doc, "terugzetten")
    assert s["voorstel"]["action_id"] == "app.rollback" and s["voorstel"]["parameters"] == {"herstel_van": EXEC}
    assert contract8.parameter_fouten("app.rollback", s["voorstel"]["target"], s["voorstel"]["parameters"]) == []
    assert s["verwacht"] == {"staat": "RUNNING", "digest": {"repository": GOTIFY_REPO, "expected": OUDE_DIGEST}}
    assert s["herstelpunt"] == bron["vastgepind"]["snapshot_naam"]  # het herstelpunt van vóór de update
    assert doc["eigenaar_herstel"]["herstelpunten"] == bron["vastgepind"]["herstelpunten"]
    v = planner.begin(doc)
    oud = {"staat": "RUNNING", "digests": [f"{GOTIFY_REPO}@{OUDE_DIGEST}"]}
    assert status(meld(doc, v, "terugzetten", readback=GOED)) == {"terugzetten": "mislukt"}  # nog de nieuwe digest
    assert status(meld(doc, v, "terugzetten", readback=oud)) == {"terugzetten": "voltooid"}


@pytest.mark.parametrize("herstel", [
    {"migrations": True, "tag_only_rollback_safe": False},
    {"migrations": False, "tag_only_rollback_safe": False},  # bv. Node-RED: flows.json kan mee veranderen
    {"migrations": True, "tag_only_rollback_safe": True},
    {"tag_only_rollback_safe": True},  # een updateplan van vóór deze regel: onbekend is niet veilig
    {},
])
def test_terugzetten_zonder_veilig_alleen_image_geeft_niets_vrij(herstel: dict[str, Any]) -> None:
    """HARDE EIS (contract §2.3 regel 7a, EX15): terugzetten = vorige image én snapshot samen, nooit alleen de image.
    Het plan kan alleen de image terugzetten; zegt het manifest van de update niet dat dat veilig is, dan geeft het
    niets vrij en zegt het de eigenaar welke herstelpunten bij die terugzetting horen (R-HARDE-EIS-1)."""
    bron, voortgang = bijgewerkt()
    bron = {**bron, "eigenaar_herstel": {**{k: v for k, v in bron["eigenaar_herstel"].items()
                                            if k not in ("migrations", "tag_only_rollback_safe")}, **herstel}}
    doc = terug(update_plan=bron, update_stappen=voortgang)
    assert codes(doc) == {"terugzetten_vraagt_data"}
    geen_stap_vrij(doc)
    details = doc["blokkades"][0]["details"]
    assert details["herstelpunten"] == bron["vastgepind"]["herstelpunten"] and details["herstelpunten"]
    assert details["app_name"] == "gotify"


def test_terugzetten_na_update_met_migraties_geeft_niets_vrij() -> None:
    """Het echte Gotify-manifest (migraties, ``tag_only_rollback_safe: false``): na een update geeft het terugzetplan
    geen ``app.rollback`` vrij; alleen de image terugzetten zou de oude versie op de gemigreerde database starten."""
    bron, voortgang = bijgewerkt(veilig=False)
    assert bron["eigenaar_herstel"]["migrations"] is True
    doc = terug(update_plan=bron, update_stappen=voortgang)
    assert codes(doc) == {"terugzetten_vraagt_data"}
    geen_stap_vrij(doc)
    blokkade = doc["blokkades"][0]
    assert blokkade["details"]["herstelpunten"] == bron["vastgepind"]["herstelpunten"]
    assert blokkade["details"]["notities_nl"] == bron["eigenaar_herstel"]["notities_nl"]
    assert "eigenaar" in blokkade["voorstel_nl"] and "herstelpunten" in blokkade["voorstel_nl"]


def test_niets_terug_te_zetten_als_de_update_nooit_is_uitgevoerd() -> None:
    bron = update()
    v = tot_schaduw(bron)
    v = meld(bron, v, "schaduw-installeren", readback={"staat": "CRASHED"})
    doc = terug(update_plan=bron, update_stappen=v, inventaris=inventaris(gotify_record()))
    assert codes(doc) == {"niets_om_terug_te_zetten"} and doc["stappen"] == []
    assert codes(terug(update_plan=installatie(), update_stappen=[])) >= {"bronplan_ongeldig"}


def test_terugzetten_naar_catalogusversie_alleen_als_die_gemeten_bestaat() -> None:
    """Ook met een gemeten catalogusversie kent dit plan geen herstelpunt van vóór de update: het geeft dan niets vrij
    (``herstelpunt_onbekend``). De versieregels blijven gelden en staan ernaast."""
    record = {**app_record("authentik", repository="ghcr.io/goauthentik/server", tag="2025.1.1", app_id="authentik",
                           catalogus_app="authentik", trein="community", versie="1.3.64"),
              "installatiewijze": "catalog_app"}
    catalog = {"train": "community", "app": "authentik", "version": "1.3.60"}
    weg = terug(app_name="authentik", catalog=catalog, inventaris=inventaris(record), beschikbaar=meting("nee"))
    assert codes(weg) == {"version_unavailable", "herstelpunt_onbekend"}
    geen_stap_vrij(weg)
    onbekend = terug(app_name="authentik", catalog=catalog, inventaris=inventaris(record),
                     beschikbaar=meting("onbekend"))
    assert codes(onbekend) == {"version_availability_unknown", "herstelpunt_onbekend"}
    goed = terug(app_name="authentik", catalog=catalog, inventaris=inventaris(record))
    assert codes(goed) == {"herstelpunt_onbekend"}
    geen_stap_vrij(goed)
    s = stap(goed, "terugzetten")
    assert s["voorstel"]["parameters"] == {"catalog_version": "1.3.60"} and s["verwacht"]["versie"] == "1.3.60"
    assert contract8.parameter_fouten("app.rollback", s["voorstel"]["target"], s["voorstel"]["parameters"]) == []
    ander = terug(app_name="authentik", catalog={**catalog, "app": "iets-anders"}, inventaris=inventaris(record))
    assert "catalogusapp_verschilt" in codes(ander)


def test_terugzetten_vraagt_een_bron_en_een_geinstalleerde_app() -> None:
    assert codes(terug(app_name="gotify")) == {"bron_ontbreekt"}
    assert "herstel_van_ongeldig" in codes(terug(app_name="gotify", herstel_van="geen-uuid"))
    assert "app_niet_geinstalleerd" in codes(terug(app_name="onbekend", herstel_van=EXEC))
    assert "app_naam_ongeldig" in codes(terug(herstel_van=EXEC))
    assert "inventaris_onbekend" in codes(terug(app_name="gotify", herstel_van=EXEC,
                                                inventaris=Inventaris("unknown", "hub_onbereikbaar")))
    assert codes(terug(app_name="gotify", herstel_van=EXEC)) == {"herstelpunt_onbekend"}


def test_terugzetten_zonder_gemeten_vorige_digest_eist_dat_de_nieuwe_digest_weg_is() -> None:
    """Is de digest van vóór de update nooit gemeten, dan bewijst 'de app draait' niets: de update-digest moet weg."""
    bron, v = bijgewerkt(inventaris=inventaris(gotify_record(digest=None)))
    assert bron["blokkades"] == [] and bron["diff"]["image"]["van"]["digest"] is None
    assert "huidige_digest_onbekend" in {b["code"] for b in bron["bevindingen"]}
    doc = terug(update_plan=bron, update_stappen=v)
    assert doc["blokkades"] == []  # terugzetten blijft mogelijk: het is de herstelactie
    assert "vorige_digest_onbekend" in {b["code"] for b in doc["bevindingen"]}
    s = stap(doc, "terugzetten")
    assert s["verwacht"] == {"staat": "RUNNING", "digest_niet": {"repository": GOTIFY_REPO, "digest": GOTIFY_DIGEST}}
    begin = planner.begin(doc)
    nog_nieuw = meld(doc, begin, "terugzetten", readback=GOED)
    assert status(nog_nieuw) == {"terugzetten": "mislukt"}
    assert nog_nieuw[0]["reden"].startswith("readback_afwijking") and "niet teruggezet" in nog_nieuw[0]["reden"]
    zonder = meld(doc, begin, "terugzetten", readback={"staat": "RUNNING"})
    assert status(zonder) == {"terugzetten": "mislukt"} and "digest ontbreekt" in zonder[0]["reden"]
    andere_app = meld(doc, begin, "terugzetten", readback={"staat": "RUNNING",
                                                           "digests": [f"docker.io/library/postgres@{OUDE_DIGEST}"]})
    assert status(andere_app) == {"terugzetten": "mislukt"}
    gestopt = meld(doc, begin, "terugzetten", readback={"staat": "STOPPED",
                                                        "digests": [f"{GOTIFY_REPO}@{OUDE_DIGEST}"]})
    assert status(gestopt) == {"terugzetten": "mislukt"}
    terug_ = meld(doc, begin, "terugzetten", readback={"staat": "RUNNING", "digests": [f"{GOTIFY_REPO}@{OUDE_DIGEST}"]})
    assert status(terug_) == {"terugzetten": "voltooid"}


def test_terugzetten_met_alleen_herstel_van_kent_geen_herstelpunt_en_geeft_niets_vrij() -> None:
    """Met alleen ``herstel_van`` kent het plan geen herstelpunt van vóór de update: geblokkeerd (R-HARDE-EIS-1). Het
    plan blijft eerlijk over wat het zou controleren: minstens dat de huidige digest weg is, of alleen 'draait'."""
    doc = terug(app_name="gotify", herstel_van=EXEC)
    assert codes(doc) == {"herstelpunt_onbekend"}
    geen_stap_vrij(doc)
    assert "vorige_digest_onbekend" in {b["code"] for b in doc["bevindingen"]}
    assert stap(doc, "terugzetten")["verwacht"] == {
        "staat": "RUNNING", "digest_niet": {"repository": GOTIFY_REPO, "digest": GOTIFY_DIGEST}}
    with pytest.raises(planner.ResultaatFout) as fout:
        meld(doc, planner.begin(doc), "terugzetten", readback={"staat": "RUNNING",
                                                                "digests": [f"{GOTIFY_REPO}@{OUDE_DIGEST}"]})
    assert fout.value.code == "stap_niet_vrijgegeven"

    # niets gemeten: het plan zegt eerlijk dat alleen 'draait' te controleren is, en belooft niet meer
    blind = terug(app_name="gotify", herstel_van=EXEC,
                  inventaris=inventaris(gotify_record(tag="3.1.1", digest=None)))
    assert codes(blind) == {"herstelpunt_onbekend"}
    assert stap(blind, "terugzetten")["verwacht"] == {"staat": "RUNNING"}
    melding = next(b["melding"] for b in blind["bevindingen"] if b["code"] == "vorige_digest_onbekend")
    assert "is daarmee niet bewezen" in melding and "alleen" in melding


def test_terugzetten_met_gemeten_vorige_digest_waarschuwt_niet() -> None:
    bron, voortgang = bijgewerkt()
    doc = terug(update_plan=bron, update_stappen=voortgang)
    assert "vorige_digest_onbekend" not in {b["code"] for b in doc["bevindingen"]}
    assert "huidige_digest_onbekend" not in {b["code"] for b in bron["bevindingen"]}
    assert "digest_niet" not in stap(doc, "terugzetten")["verwacht"]
