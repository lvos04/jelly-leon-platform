"""Bouw historische testgegevens in een privé-database en laad ze één keer vóór de dienststart."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

MAP = Path("/work/staging")
MOMENTOPNAME = MAP / "inhoud-begin.db"
DIENSTDATABASE = Path("/config/mediaguard.db")


def kopieer(bron: Path, doel: Path) -> None:
    # De SQLite-back-up neemt ook de WAL mee. Publiceer pas na sluiten en overschrijf nooit een bestaand bestand.
    with TemporaryDirectory(dir=doel.parent) as tijdelijk:
        kopie = Path(tijdelijk) / "kopie.db"
        with (closing(sqlite3.connect(bron.as_uri() + "?mode=ro", uri=True)) as lezer,
              closing(sqlite3.connect(kopie)) as schrijver):
            lezer.backup(schrijver)
        os.link(kopie, doel)


async def bereid_voor(soort: str, sleutel: str, imports: list[dict[str, Any]]) -> int:
    from mediaguard import jobs  # type: ignore[import-not-found]
    from mediaguard.app import create_app  # type: ignore[import-not-found]
    from mediaguard.config import Settings  # type: ignore[import-not-found]
    from mediaguard.hooks.service import store_webhook  # type: ignore[import-not-found]
    from mediaguard.identity.adapters import remember_hash  # type: ignore[import-not-found]
    from mediaguard.identity.engine import EpisodeRef  # type: ignore[import-not-found]
    from mediaguard.mediaserver.clients import make_server  # type: ignore[import-not-found]
    from mediaguard.mediaserver.service import poll_sessions  # type: ignore[import-not-found]

    async def proefreeks(taak: jobs.JobContext) -> None:
        taak.log("Bestaande proefreeks ingepland")
        taak.progress(0.625)
        taak.log("Bestaande voortgang vastgelegd")
        raise jobs.RetryLater("De proefreeks wacht op het volgende deel", delay_seconds=86400)

    assert os.statvfs(DIENSTDATABASE.parent).f_flag & os.ST_RDONLY, "Fixture vereist alleen-lezen dienstvolume"
    with TemporaryDirectory(dir=MAP) as tijdelijk:
        werk = Path(tijdelijk)
        instellingen = Settings(data_dir=werk, staging_dir=werk / "staging", quarantine_dir=werk / "quarantaine",
                                library_roots=["/media"], start_background=False)
        ctx = create_app(instellingen).state.ctx
        try:
            jobs.job_handler("inhoud.proefreeks")(proefreeks)
            nummer: int = jobs.enqueue(ctx.db, "inhoud.proefreeks")
            kijkserver = make_server("jellyfin", ctx.services, "http://kijkserver-inhoud:8096", sleutel)
            assert await poll_sessions(ctx, kijkserver) == {"sessions": 2, "watching": 0}
            if soort == "quarantaine":
                assert len(imports) == 2
                for melding in imports:
                    ontvangst = store_webhook(ctx, "sonarr-series", melding)
                    assert ontvangst["status"] == "stored" and ontvangst["job_id"]
                for n in (1, 2):
                    pad = Path("/media/referentie") / f"S01E{n:02}.mkv"
                    digest = hashlib.sha256(pad.read_bytes()).hexdigest()
                    remember_hash(ctx.db, "sha256", digest, EpisodeRef(1, n),
                                  source="testreferentie", instance_id="sonarr-series")
            await ctx.services["jobs"].run_until_idle()
            kopieer(instellingen.db_path, MOMENTOPNAME)
        finally:
            ctx.db.close()
    return nummer


if __name__ == "__main__":
    if sys.argv[1] == "laad":
        kopieer(MOMENTOPNAME, DIENSTDATABASE)
    else:
        invoer = json.load(sys.stdin)
        print(asyncio.run(bereid_voor(sys.argv[1], invoer["kijksleutel"], invoer["imports"])))
