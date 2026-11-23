"""Contract 1.3 §8.2 voor de updatepreview (S-P2-420, besluit regisseur): de Hub heeft nog geen gebonden
update-catalogusroute, dus losse manifest_yaml voor een update geeft geen uitvoerbare stap. Sim-getest; testwaarden.
"""

from __future__ import annotations

import uuid
from typing import Any

import planhulp as h
import pytest
from sqlalchemy import create_engine, text

UPDATE = "/lifecycle/v1/plans/update/preview"


def _body(**extra: Any) -> dict[str, Any]:
    return {"manifest_yaml": h.manifest("gotify"), "parameters": h.GOTIFY, "doel": h.DOEL_JSON,
            "schaduw": {"parameters": h.SCHADUW}, **extra}


def _vers(hub: Any) -> None:
    p2 = hub.state.sim.p2
    p2.zet_apps(h.gotify_record())
    p2.zet_beschikbaar("stable", "ix-app", "1.4.8", "ja")


def _plannen(pg_url: str) -> int:
    engine = create_engine(pg_url)
    try:
        with engine.connect() as conn:
            return int(conn.execute(text("SELECT count(*) FROM lifecycle_plans")).scalar_one())
    finally:
        engine.dispose()


def test_losse_updatepreview_geeft_geen_uitvoerbare_stap(client: Any, auth: Any, hub: Any) -> None:
    """Verse meting, geen andere blokkade: toch alleen catalogusbinding_ontbreekt, geen vrijgegeven stap, en een
    stapresultaat wordt geweigerd."""
    _vers(hub)
    r = client.post(UPDATE, json=_body(), headers=auth("operator", idem=True))
    assert r.status_code == 201, r.text
    view = r.json()
    assert {b["code"] for b in view["plan"]["blokkades"]} == {"catalogusbinding_ontbreekt"}
    assert view["status"] == "geblokkeerd" and view["vrijgegeven_stappen"] == []
    assert view["plan"]["stappen"] and all(s["geblokkeerd"] is True for s in view["plan"]["stappen"])
    assert all(s["status"] == "geblokkeerd" for s in view["voortgang"])
    melden = client.post(f"/lifecycle/v1/plans/{view['id']}/steps/stoppen/result", headers=auth("operator", idem=True),
                         json={"status": "voltooid", "execution_id": str(uuid.uuid4()),
                               "readback": {"staat": "STOPPED"}})
    assert (melden.status_code, melden.json()["code"]) == (409, "plan_geblokkeerd")


def test_updatepreview_neemt_geen_catalogusbinding_aan(client: Any, auth: Any, hub: Any, clean_db: str) -> None:
    """Ook de Hub als dienst kan een update niet vrijgeven met een binding: die route bestaat nog niet (422, geen
    plan bewaard)."""
    _vers(hub)
    body = _body(catalog_binding=h.binding())
    key = str(uuid.uuid4())
    r = client.post(UPDATE, json=body, headers=h.hub_kop(hub, key, h.claim_van(key, body, UPDATE)))
    assert r.status_code == 422, r.text
    assert _plannen(clean_db) == 0


@pytest.mark.usefixtures("gebonden_updateroute")
def test_met_gebonden_updateroute_valt_alleen_deze_blokkade_weg(client: Any, auth: Any, hub: Any) -> None:
    """Positief (VERSIE 2-schakelaar): hetzelfde verzoek geeft dan de eerste stap vrij; de blokkade is de enige
    reden dat de losse updatepreview niets vrijgeeft."""
    _vers(hub)
    view = client.post(UPDATE, json=_body(), headers=auth("operator", idem=True)).json()
    assert (view["status"], view["vrijgegeven_stappen"]) == ("klaar_voor_voorstel", ["stoppen"])
    assert view["plan"]["blokkades"] == []
