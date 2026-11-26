"""Maakt en meet uitsluitend de videobeelden, geluidstonen en ondertitels in het eigen testvolume."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

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


def inventaris() -> dict[str, dict[str, str]]:
    bestanden = {}
    for root in (MEDIA, Path("/work/quarantine")):
        for pad in sorted(root.rglob("*")):
            if pad.is_file():
                assert not pad.is_symlink() and pad.stat().st_size > 0
                bestanden[str(pad)] = hashlib.sha256(pad.read_bytes()).hexdigest()
    with sqlite3.connect("file:/config/mediaguard.db?mode=ro", uri=True) as db:
        kijk = db.execute("SELECT path,title,user_name,last_seen FROM mediaserver_watch WHERE path=?",
                          ("/media/bestaand/bewaard.mkv",)).fetchone()
        log = db.execute("SELECT id,job_id,ts,level,message FROM job_logs WHERE message=?",
                         ("Bestaande proefreeks ingepland",)).fetchall()
    assert kijk and len(log) == 1
    historie = {naam: hashlib.sha256(json.dumps(rij).encode()).hexdigest() for naam, rij in
                (("kijkregistratie", kijk), ("taaklog", log[0]))}
    return {"sha256": bestanden, "historie": historie}


def vul_voortgang(job: int) -> None:
    with sqlite3.connect("/config/mediaguard.db") as db:
        assert db.execute("UPDATE jobs SET progress=0.625 WHERE id=? AND status='queued'", (job,)).rowcount == 1
        db.execute("INSERT INTO job_logs(job_id,ts,level,message) VALUES (?,CURRENT_TIMESTAMP,'info',?)",
                   (job, "Bestaande proefreeks ingepland"))
        db.execute("INSERT INTO mediaserver_watch(path,title,user_name,last_seen) VALUES (?,?,?,CURRENT_TIMESTAMP)",
                   ("/media/bestaand/bewaard.mkv", "Bewaarde proefvideo", "proefkijker"))


def onthoud_referenties() -> None:
    from mediaguard.app import create_app  # type: ignore[import-not-found]
    from mediaguard.automation import background_identity  # type: ignore[import-not-found]
    from mediaguard.identity.adapters import remember_hash  # type: ignore[import-not-found]
    from mediaguard.identity.engine import EpisodeRef  # type: ignore[import-not-found]

    ctx = create_app().state.ctx
    # Dezelfde eerste bestandscontrole als in de achtergrondwerker na het synchroniseren.
    assert background_identity(ctx) == 2
    for n in (1, 2):
        digest = hashlib.sha256((REFERENTIE / f"S01E{n:02}.mkv").read_bytes()).hexdigest()
        remember_hash(ctx.db, "sha256", digest, EpisodeRef(1, n), source="testreferentie", instance_id="sonarr-series")


if __name__ == "__main__":
    actie, *args = sys.argv[1:]
    if actie == "maak":
        maak(args[0])
    elif actie == "voortgang":
        vul_voortgang(int(args[0]))
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
