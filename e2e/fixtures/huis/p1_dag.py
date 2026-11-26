"""Draait in my_house. Geef de echte v1-normalisatie een vaste UTC-ontvangstklok."""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from shc_allinone.supervisor import service_env
from shc_api.db import sessionmaker
from shc_api.store import ingest_reading
from shc_api.worker import Worker
from shc_integrations.homewizard import normalize_v1

ZONE = ZoneInfo("Europe/Amsterdam")


async def dag(meter, richting):
    with httpx.Client(base_url="http://my-house:30221", trust_env=False) as sim:
        serie = sim.get("/api").raise_for_status().json()["serial"]
        wissel = sim.post("/_sim/zomertijd", json={"richting": richting, "jaar": 2026}).raise_for_status().json()
        omslag = datetime.fromisoformat(wissel["wissel_utc"])
        datum = omslag.astimezone(ZONE).date()
        begin = datetime.combine(datum, time.min, ZONE).astimezone(UTC)
        einde = datetime.combine(datum + timedelta(days=1), time.min, ZONE).astimezone(UTC)
        stap = timedelta(minutes=15)
        punten = (einde - begin) // stap + 1
        stand = 100.0
        gasuren = []
        async with sessionmaker()() as db:
            for index in range(punten):
                tijd = begin + index * stap
                klok = sim.post("/_sim/klok", json={"utc": tijd.isoformat()}).raise_for_status().json()
                ontvangen = datetime.fromisoformat(klok["klok"])
                if tijd == omslag + stap:
                    stand = 0.25
                    sim.post("/_sim/meterreset", json={"stand": stand}).raise_for_status()
                else:
                    sim.post("/_sim/meting", json={"waarden": {
                        "total_power_import_t1_kwh": stand, "total_power_import_t2_kwh": 0,
                    }}).raise_for_status()
                data = sim.get("/api/v1/data").raise_for_status().json()
                lezingen = normalize_v1(serie, data, ontvangen, ZONE)
                energie = next(r for r in lezingen if r.metric == "energy_import_kwh")
                gas = next(r for r in lezingen if r.metric == "gas_m3")
                if tijd in (omslag - 2 * stap, omslag + 2 * stap):
                    assert gas.observed_at == tijd
                    gasuren.append(str(data["gas_timestamp"])[6:10])
                assert await ingest_reading(db, meter, energie.metric, energie.value, energie.unit,
                                            energie.observed_at, energie.quality.value, energie.event_id)
                stand += 0.25
            await db.commit()
        uren = await Worker()._aggregate_chunk(meter, "energy_import_kwh", 17.25, begin, einde)
    return {"uren": uren, "gas_lokale_tijden": gasuren}


os.environ.update(service_env())
invoer = json.load(sys.stdin)
print(json.dumps(asyncio.run(dag(UUID(invoer["meter"]), invoer["richting"]))))
