"""Voed de bewaking in de MediaGuard-image met één wachtende GPU-taak en een versnelde proefklok."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from mediaguard import monitor  # type: ignore[import-not-found]
from mediaguard.app import create_app  # type: ignore[import-not-found]
from mediaguard.config import load_settings  # type: ignore[import-not-found]


async def meet(stil: bool) -> None:
    begin = datetime.now(UTC) - timedelta(minutes=45)
    laatste_voortgang = begin
    lezingen = 0
    wijzigingen = 0

    def worker(request: httpx.Request) -> httpx.Response:
        nonlocal lezingen, wijzigingen
        if request.method != "GET":
            wijzigingen += 1
            return httpx.Response(405)
        if request.url.path != "/api/mediaguard/v1/health":
            return httpx.Response(404)
        lezingen += 1
        minuten = int((nu - laatste_voortgang).total_seconds() // 60)
        baan = {"baan": "gpu", "wachtend": 1, "reden": "proefworker wacht op werkgeheugen",
                "sinds": laatste_voortgang.isoformat(), "minuten": minuten}
        toestand = {"status": "ok", "lanes_paused": [], "baan_stilstand": [baan] if minuten else []}
        return httpx.Response(200, json=toestand)

    app = create_app(load_settings(data_dir=Path("/config/core"), start_background=False))
    ctx = app.state.ctx
    ctx.services["encoder_transport"] = httpx.MockTransport(worker)
    ronden = []
    for minuut in (0, 20, 31, 45):
        nu = begin + timedelta(minutes=minuut)
        if not stil:
            laatste_voortgang = nu
        resultaat = await monitor.run_once(ctx)
        rij = ctx.db.fetch_one("SELECT coalesce(sum(count), 0) AS aantal FROM notifications WHERE dedupe_key=?",
                               ("monitor.encoder.idle.gpu",))
        ronden.append({"minuut": minuut, "laatste_voortgang_min": int((laatste_voortgang - begin).total_seconds() / 60),
                       "nieuwe_melding": "encoder.idle.gpu" in resultaat["pushed"], "totaal_meldingen": rij["aantal"]})
    print(json.dumps({"ronden": ronden, "worker_gezondheidslezingen": lezingen,
                      "worker_schrijfverzoeken": wijzigingen}))


if __name__ == "__main__":
    asyncio.run(meet(sys.argv[1] == "stil"))
