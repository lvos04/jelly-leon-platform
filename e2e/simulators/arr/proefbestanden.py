"""Schijfhandelingen in de staging- en quarantainemappen van de lokale inhoudsproef."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from fastapi import HTTPException


def geweigerd() -> HTTPException:
    return HTTPException(409, "De proef vereist gewone bestanden en mappen zonder koppelingen")


@contextmanager
def mapgreep(pad: Path, *, vanaf: int | None = None) -> Iterator[int]:
    # Ook de volume- en seriemap worden zonder volgen geopend. De ouder blijft open tot na de handeling.
    if ".." in pad.parts or pad.is_absolute() != (vanaf is None):
        raise geweigerd()
    try:
        with ExitStack() as sluit:
            ouder = vanaf
            for deel in pad.parts:
                ouder = os.open(deel, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=ouder)
                sluit.callback(os.close, ouder)
            assert ouder is not None
            yield ouder
    except FileNotFoundError:
        raise
    except OSError as fout:
        raise geweigerd() from fout


def _grootte(mapfd: int, naam: str) -> int:
    info = os.stat(naam, dir_fd=mapfd, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode):
        raise geweigerd()
    return info.st_size


def grootte(pad: Path) -> int | None:
    try:
        with mapgreep(pad.parent.absolute()) as mapfd:
            return _grootte(mapfd, pad.name)
    except FileNotFoundError:
        return None


def verwijder(pad: Path) -> None:
    try:
        with mapgreep(pad.parent.absolute()) as mapfd:
            _grootte(mapfd, pad.name)
            os.unlink(pad.name, dir_fd=mapfd)
    except FileNotFoundError:
        raise HTTPException(404, "Bestand ontbreekt") from None


def _sha256(mapfd: int, naam: str) -> str:
    fd = os.open(naam, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=mapfd)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise geweigerd()
        with os.fdopen(fd, "rb", closefd=False) as bestand:
            return hashlib.file_digest(bestand, "sha256").hexdigest()
    finally:
        os.close(fd)


def voor_verwijderen(bron: Path, quarantaine: Path) -> dict[str, str]:
    try:
        with mapgreep(bron.parent.absolute()) as staging:
            details = {"bron_sha256": _sha256(staging, bron.name)}
    except FileNotFoundError:
        return {}
    kopieen = []
    try:
        with mapgreep(quarantaine.absolute()) as archief:
            for naam in os.listdir(archief):
                if naam.startswith("txn-"):
                    try:
                        with mapgreep(Path(naam), vanaf=archief) as transactie:
                            kopieen.append(_sha256(transactie, bron.name))
                    except FileNotFoundError:
                        continue
    except FileNotFoundError:
        return details
    if len(kopieen) == 1:
        details["quarantaine_sha256"] = kopieen[0]
    return details
