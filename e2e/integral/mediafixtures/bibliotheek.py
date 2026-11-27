"""Maakt en meet uitsluitend de videobeelden, geluidstonen en ondertitels in het eigen testvolume."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

MEDIA = Path("/media")
STAGING = MEDIA / "sonarr-series/De vuurtoren/staging"
ANIME = MEDIA / "sonarr-anime/De maanreis"
REFERENTIE = MEDIA / "referentie"


def clip(pad: Path, nummer: int, *, encode: bool = False, ondertitel: Path | None = None) -> None:
    pad.parent.mkdir(parents=True, exist_ok=True)
    # De twee afleveringen hebben verschillend beeld én geluid. De encodeproef heeft genoeg bitrate voor AV1.
    beeld = "testsrc2=size=1280x720:rate=12" if encode else f"color=c={('blue', 'red')[nummer - 1]}:s=96x64:r=8"
    extra = ["-i", str(ondertitel)] if ondertitel else []
    sub = ["-map", "3:s", "-c:s", "srt", "-metadata:s:s:0", "language=nld"] if ondertitel else []
    subprocess.run(["ffmpeg",  # noqa: S603, S607 - vaste fixture op het eigen testvolume
                    "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i", beeld,
                    "-f", "lavfi", "-i", f"sine=frequency={220 * nummer}",
                    "-f", "lavfi", "-i", f"sine=frequency={330 * nummer}", *extra,
                    "-t", "3" if encode else "0.5", "-map", "0:v", "-map", "1:a", "-map", "2:a", *sub,
                    "-metadata:s:a:0", "language=jpn", "-metadata:s:a:1", "language=eng",
                    "-c:v", "libx264", "-threads", "2", "-preset", "ultrafast", "-c:a", "aac",
                    str(pad)], check=True)


def maak(soort: str) -> None:
    clip(MEDIA / "bestaand/bewaard.mkv", 1)
    if soort in {"serie", "quarantaine"}:
        for n in (1, 2):
            naam = f"S01E{n:02}.mkv"
            clip(REFERENTIE / naam, n)
            STAGING.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REFERENTIE / naam, STAGING / naam)
    elif soort == "anime":
        ANIME.mkdir(parents=True)
        for n in (1, 2):
            pad = ANIME / f"S01E{n:02}.mkv"
            sub = pad.with_suffix(".nl.srt")
            sub.write_text(f"1\n00:00:00,000 --> 00:00:00,400\nAflevering {n}\n")
            clip(pad, n, ondertitel=sub)
    elif soort == "kwaliteit":
        fout = MEDIA / "kwaliteitsproef/fout.mkv"
        clip(fout, 1, encode=True)
        # Een al aanwezige bibliotheek heeft stabiele mtime. De scanner stelt verse downloads een uur uit.
        os.utime(fout, (1_600_000_000, 1_600_000_000))
        shutil.copy2(fout, fout.with_name("controle.mkv"))
    else:
        raise ValueError("Onbekende testbibliotheek")


def inventaris() -> dict[str, Any]:
    bestanden = {}
    for root in (MEDIA, Path("/work/quarantine")):
        for pad in sorted(root.rglob("*")):
            if pad.is_file():
                assert not pad.is_symlink() and pad.stat().st_size > 0
                bestanden[str(pad)] = hashlib.sha256(pad.read_bytes()).hexdigest()
    with sqlite3.connect("file:/config/mediaguard.db?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        registraties = {r["path"]: dict(r) for r in db.execute("SELECT * FROM mediaserver_watch ORDER BY path")}
        regels = {str(r["id"]): dict(r) for r in db.execute("SELECT * FROM job_logs ORDER BY id")}
    return {"sha256": bestanden, "registraties": registraties, "logregels": regels}


def kerncontext() -> Any:
    from mediaguard.app import create_app  # type: ignore[import-not-found]

    return create_app().state.ctx


async def vul_voortgang(job: int) -> None:
    from mediaguard.jobs import JobContext  # type: ignore[import-not-found]
    from mediaguard.mediaserver import servers  # type: ignore[import-not-found]
    from mediaguard.mediaserver.service import poll_sessions  # type: ignore[import-not-found]

    ctx = kerncontext()
    taak = JobContext(job, "library.sync", {}, "sonarr-series", None, ctx)
    taak.log("Bestaande proefreeks ingepland")
    taak.progress(0.625)
    taak.log("Bestaande voortgang vastgelegd")
    server = servers.legacy(ctx)
    assert server and server.kind == "jellyfin" and server.url == "http://kijkserver-inhoud:8096"
    resultaat = await poll_sessions(ctx, servers.client(ctx, server), server)
    assert resultaat == {"sessions": 2, "watching": 0}


def onthoud_referenties() -> None:
    from mediaguard.automation import background_identity  # type: ignore[import-not-found]
    from mediaguard.identity.adapters import remember_hash  # type: ignore[import-not-found]
    from mediaguard.identity.engine import EpisodeRef  # type: ignore[import-not-found]

    ctx = kerncontext()
    # Dezelfde eerste bestandscontrole als in de achtergrondwerker na het synchroniseren.
    assert background_identity(ctx) == 2
    for n in (1, 2):
        digest = hashlib.sha256((REFERENTIE / f"S01E{n:02}.mkv").read_bytes()).hexdigest()
        remember_hash(ctx.db, "sha256", digest, EpisodeRef(1, n), source="testreferentie", instance_id="sonarr-series")


if __name__ == "__main__":
    actie, *args = sys.argv[1:]
    if actie == "maak":
        maak(args[0])
    elif actie == "noodstopclip":
        pad = Path("/data/uitvalproef/proefreeks.mkv")
        clip(pad, 1)
        # Een reeds afgeronde testdownload is ouder dan de stabiliteitsgrens van de encoder.
        ouder = pad.stat().st_mtime - 120
        os.utime(pad, (ouder, ouder))
    elif actie == "voortgang":
        asyncio.run(vul_voortgang(int(args[0])))
    elif actie == "referenties":
        onthoud_referenties()
    elif actie == "verwissel":
        shutil.copyfile(REFERENTIE / "S01E02.mkv", STAGING / "S01E01.mkv")
    elif actie == "ondertitel":
        pad = Path(args[0])
        assert pad.parent == ANIME and pad.name in {"S01E01.mkv", "S01E02.mkv"}
        data = subprocess.check_output(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(pad),  # noqa: S603,S607
                                        "-map", "0:s:m:language:nld", "-f", "srt", "-"], text=True)
        los = pad.with_suffix(".nl.srt").read_text()
        print(json.dumps([data.strip().splitlines()[-1], los.strip().splitlines()[-1]]))
    elif actie == "inventaris":
        print(json.dumps(inventaris()))
    else:
        raise ValueError("Onbekende fixturehandeling")
