"""Controleer twee importtaken en hun bestandsidentiteiten met de lokale MediaGuard-werker."""

import asyncio
import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
from mediaguard.app import create_app  # type: ignore[import-not-found]
from mediaguard.config import Settings  # type: ignore[import-not-found]
from mediaguard.delivery.replace import proven_wrong  # type: ignore[import-not-found]
from mediaguard.hooks.service import store_webhook  # type: ignore[import-not-found]
from mediaguard.identity import service  # type: ignore[import-not-found]


async def meet(werk: Path) -> None:
    staging = werk / "staging"
    ctx = create_app(Settings(data_dir=werk, staging_dir=staging, quarantine_dir=werk / "quarantaine",
                              start_background=False)).state.ctx
    ctx.services["encoder_transport"] = httpx.MockTransport(lambda _: httpx.Response(503))
    try:
        taken: dict[int, int] = {}
        for nummer in (1, 2):
            bestand_id = 4010 + nummer
            bericht = {"eventType": "Download", "series": {"id": 40, "seriesType": "standard"},
                       "episodes": [{"id": 400 + nummer, "seasonNumber": 1, "episodeNumber": nummer}],
                       "episodeFile": {"id": bestand_id, "path": str(staging / f"S01E{nummer:02}.mkv")}}
            antwoord = store_webhook(ctx, "sonarr-series", bericht)
            assert antwoord["status"] == "stored"
            taken[bestand_id] = antwoord["job_id"]
        await ctx.services["jobs"].run_until_idle()
        for bestand_id, taak_id in taken.items():
            assert ctx.db.fetch_one("SELECT status FROM jobs WHERE id=?", (taak_id,))["status"] == "succeeded"
            assert service.latest(ctx, "sonarr-series", "episode_file", bestand_id)["source"] == "post_import"
            assert not service.keyed_by_episode_ids(ctx, "sonarr-series", "episode_file", bestand_id)
            assert proven_wrong(ctx, "sonarr-series", "episode_file", bestand_id) is not None
        aantal_taken = len(set(taken.values()))
        assert aantal_taken == len(taken) == 2
        print(f"{aantal_taken} importtaken uitgevoerd, {len(taken)} bestandsidentiteiten bevestigd.")
    finally:
        ctx.db.close()


if __name__ == "__main__":
    with (TemporaryDirectory(dir=os.environ["TMPDIR"]) as werk,
          patch.object(socket.socket, "connect", side_effect=AssertionError("Onverwachte netwerkverbinding"))):
        asyncio.run(meet(Path(werk)))
