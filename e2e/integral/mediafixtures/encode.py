"""Draai de normale CPU-job in de encodercontainer. Alleen de meetprocessen krijgen een foutinjectie."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app import kwaliteit  # type: ignore[import-not-found]
from app.config import settings  # type: ignore[import-not-found]
from app.db import Database  # type: ignore[import-not-found]
from app.media.beslissen import STANDAARD  # type: ignore[import-not-found]
from app.media.probe import probe  # type: ignore[import-not-found]
from app.scanner import scan_library  # type: ignore[import-not-found]
from app.uitvoerder import create_job_from_decision, run_job  # type: ignore[import-not-found]

BIBLIOTHEEK = Path("/media/kwaliteitsproef")
WERK = Path("/work/kwaliteitsproef")


async def taak(naam: str) -> dict[str, Any]:
    # Native vervangmodus op twee afzonderlijke proefkopieën. Alle opslagcontroles blijven actief.
    config = replace(settings, library_root=BIBLIOTHEEK, pipeline_root=WERK, workspace_root=WERK,
                     cache_root=WERK / "cache", work_root=WERK / "work", config_root=WERK / "config",
                     backup_root=WERK / "backups", database_path=WERK / "config/app.db",
                     secret_key_file=WERK / "config/proefsleutel", storage_config_path=WERK / "config/opslag.json",
                     container_mode=False, replacement_mode="direct", storage_mode="systeemschijf",
                     backup_location="systeemschijf", system_device=WERK.parent.stat().st_dev)
    for map_ in config.managed_roots:
        map_.mkdir(parents=True, exist_ok=True)
    config.validate_storage()
    db = Database(config)
    db.initialize()
    if naam == "fout":
        profiel = {**STANDAARD, "drempel_720": 100, "preset_720": 12, "crf_720": 22, "svt_threads_720": 2,
                   "gpu_voor_sdr_1080": False}
        pid = db.execute_insert("INSERT INTO profiel(naam,inst) VALUES (?,?)", ("Inhoudsproef", json.dumps(profiel)))
        lid = db.execute_insert("INSERT INTO library(naam,pad,profiel_id,verwacht_device) VALUES (?,?,?,?)",
                                ("Kwaliteitsproef", str(BIBLIOTHEEK), pid, BIBLIOTHEEK.stat().st_dev))
        db.execute("UPDATE functie SET ingeschakeld=1 WHERE sleutel='vmaf'")
        scan = scan_library(db, lid)
        assert scan.gevonden == 2 and scan.uitgesteld == 0
    bron = BIBLIOTHEEK / (naam + ".mkv")
    besluit = db.fetch_one("SELECT b.id FROM besluit b JOIN bestand f ON f.id=b.bestand_id "
                           "WHERE f.pad=? AND b.actie='hercoderen' AND b.baan='cpu'", (str(bron),))
    assert besluit is not None
    assert await kwaliteit.vmaf_available()
    db.execute("UPDATE besluit SET status='goedgekeurd' WHERE id=?", (besluit["id"],))
    jid = create_job_from_decision(db, besluit["id"])
    geraakt = {"vmaf": 0, "ssim": 0}
    echte_meting = kwaliteit.run_measure

    async def meetfout(command: list[str], **kwargs: Any) -> Any:
        if "-lavfi" in command:
            filter_ = command[command.index("-lavfi") + 1]
            for naam_filter in geraakt:
                if naam_filter in filter_:
                    geraakt[naam_filter] += 1
                    # FFmpeg zelf retourneert een fout op de ongeldige meetfilter.
                    command = list(command)
                    command[command.index("-lavfi") + 1] = "ontbrekende_proefmeting"
                    break
        return await echte_meting(command, **kwargs)

    if naam == "fout":
        with patch.object(kwaliteit, "run_measure", meetfout):
            uitgevoerd = await run_job(db, config, jid)
    else:
        uitgevoerd = await run_job(db, config, jid)

    rij = db.fetch_one("SELECT status,foutsoort,vergelijking FROM job WHERE id=?", (jid,))
    meting = json.loads(rij["vergelijking"] or "{}")
    video = probe(str(bron))
    assert video
    return {"id": jid, "uitgevoerd": uitgevoerd, "status": rij["status"], "foutsoort": rij["foutsoort"],
            "codec": video["codec"], "injecties": geraakt, "metriek": meting.get("metric"),
            "gemeten": meting.get("measured"), "pogingen": meting.get("attempted"), "score": meting.get("score")}


if __name__ == "__main__":
    naam = sys.argv[1]
    assert naam in {"fout", "controle"}
    # SVT en FFmpeg zien ook op een grote gedeelde machine maximaal twee beschikbare CPU's.
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    print(json.dumps(asyncio.run(taak(naam))))
