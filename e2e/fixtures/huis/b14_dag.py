"""Draait in home-control-hub. Lees de uur-export als viewer en bereken het B14-dagrapport."""

import json
import os
import sys
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import httpx
from integrations.db import Database
from integrations.domains.energy.hourly import home_zone, parse_export
from integrations.domains.energy.usage import usage_report
from integrations.domains.energy.usage_sync import Archive

invoer = json.load(sys.stdin)
woning = UUID(invoer["woning"])
dag = date.fromisoformat(invoer["datum"])
with httpx.Client(base_url="http://my-house:8080", cookies=invoer["cookies"], trust_env=False) as client:
    export = client.get(f"/api/v1/homes/{woning}/energy/export", params={
        "format": "json", "from": dag.isoformat(), "to": dag.isoformat(), "resolution": "hour",
    }).raise_for_status().json()
zone = home_zone(export["timezone"])
reeks = parse_export(export, woning, dag, zone)
database = Database(os.environ["HCH_DATABASE_URL"])
with database.sessions.begin() as db:
    archief = Archive(db, woning)
    archief.day(dag, zone, datetime.combine(dag + timedelta(days=2), datetime.min.time(), UTC), reeks, {"p1_net"})
with database.sessions() as db:
    rapport = usage_report(db, woning, zone, "EUR", "day", dag, "p1_net", "import")
    uren = rapport.days[0].hours
    print(json.dumps({"uren": len(uren), "tijdvakken_utc": len({u.start_utc for u in uren}),
                      "totaal_kwh": rapport.usage.value, "kwaliteit": rapport.usage.quality,
                      "reset_uren_utc": [u.start_utc.hour for u in uren if u.counter_reset],
                      "uurwaarden": [u.usage.value for u in uren]}, default=float))
database.dispose()
