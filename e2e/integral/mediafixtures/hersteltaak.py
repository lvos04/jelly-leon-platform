"""Houd één echte bibliotheeksynchronisatie bij een bekend tussenpunt vast op het eigen testvolume."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from mediaguard.app import create_app  # type: ignore[import-not-found]
from mediaguard.jobs import HANDLERS, JobContext  # type: ignore[import-not-found]


async def voer_uit(job_id: int) -> None:
    ctx = create_app().state.ctx
    synchroniseer = HANDLERS["library.sync"]
    vrijgave = Path(f"/work/vervolg-{job_id}")

    async def wacht_op_herstel(taak: JobContext) -> Any:
        assert taak.job_id == job_id and not vrijgave.exists()
        taak.log("Bibliotheeksynchronisatie wacht op herstelproef")
        taak.progress(0.375)
        while not vrijgave.exists():
            await asyncio.sleep(0.25)
        return await synchroniseer(taak)

    HANDLERS["library.sync"] = wacht_op_herstel
    await ctx.services["jobs"].run_until_idle(timeout=600)


if __name__ == "__main__":
    asyncio.run(voer_uit(int(sys.argv[1])))
