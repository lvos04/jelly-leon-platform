"""Controle van eigenaar en effectieve rechten op de descriptor waarmee de dienst leest of schrijft."""

from __future__ import annotations

import contextlib
import errno
import os
import stat
import struct
from pathlib import Path

NFS4_ACL_XATTRS = ("system.nfs4_acl_xdr", "system.nfs4_acl")
LEESVLAGGEN = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK

# TrueNAS nfs4xdr-acl-tools, acl_nfs4_xattr_pack.c en include/nfs4.h.
_XDR_KOP = struct.Struct(">II")
_XDR_ACE = struct.Struct(">IIIII")
_NFS_ACE = struct.Struct(">IIII")
_SPECIAL_WHO = 1
_OWNER = 1
_IDENTIFIER_GROUP = 0x40
# READ_ATTRIBUTES, READ_ACL en SYNCHRONIZE geven alleen inzage in metadata.
_METADATA = 0x80 | 0x20000 | 0x100000


def _xattr(bestand: int | Path, naam: str) -> bytes | None:
    try:
        if isinstance(bestand, int):
            return os.getxattr(bestand, naam)
        return os.getxattr(bestand, naam, follow_symlinks=False)
    except OSError as exc:
        if exc.errno in (errno.ENODATA, errno.ENOTSUP):
            return None
        return b""  # Een onleesbare ACL kan geen privérechten bevestigen.


def nfs4_acl(bestand: int | Path) -> bool:
    """Aanwezigheid van een ACL is alleen aanleiding om een te ruime leesmodus aan te scherpen."""
    return any(_xattr(bestand, naam) is not None for naam in NFS4_ACL_XATTRS)


def _veilige_ace(soort: int, vlaggen: int, masker: int, eigenaar: bool) -> bool:
    # Onbekende ACE's weigeren. Een DENY verleent niets; een extra ALLOW moet zelf veilig zijn,
    # ongeacht de volgorde van de regels. Ook overerfbare regels worden zo gecontroleerd.
    if vlaggen & ~0xCF or soort not in (0, 1):
        return False
    return soort == 1 or not (masker & ~_METADATA) or (eigenaar and not (vlaggen & _IDENTIFIER_GROUP))


def _prive_acl(naam: str, data: bytes) -> bool:
    if naam == "system.nfs4_acl_xdr":
        if len(data) < _XDR_KOP.size:
            return False
        vlaggen, aantal = _XDR_KOP.unpack_from(data)
        if vlaggen & ~0x30007 or not aantal or len(data) != _XDR_KOP.size + aantal * _XDR_ACE.size:
            return False
        for soort, flags, speciaal, masker, wie in _XDR_ACE.iter_unpack(data[_XDR_KOP.size:]):
            if speciaal not in (0, _SPECIAL_WHO) or (speciaal and wie not in (1, 2, 3)):
                return False
            if not _veilige_ace(soort, flags, masker, speciaal == _SPECIAL_WHO and wie == _OWNER):
                return False
        return True

    # Linux NFS-client: een array nfsace4 met per ACE een op vier bytes uitgelijnde naam (RFC 7530 §6.2.1).
    if len(data) < 4:
        return False
    aantal = int.from_bytes(data[:4], "big")
    positie = 4
    for _ in range(aantal):
        if len(data) - positie < _NFS_ACE.size:
            return False
        soort, flags, masker, lengte = _NFS_ACE.unpack_from(data, positie)
        positie += _NFS_ACE.size
        einde = positie + lengte
        volgende = (einde + 3) & ~3
        if not lengte or volgende > len(data) or any(data[einde:volgende]):
            return False
        if not _veilige_ace(soort, flags, masker, data[positie:einde] == b"OWNER@"):
            return False
        positie = volgende
    return aantal > 0 and positie == len(data)


def prive_rechten(fd: int, modus: int, *, aanscherpen: bool) -> bool:
    """Toets het geopende object. Bij gewone koppelbestanden blijven te ruime bestaande rechten een leesfout."""
    st = os.fstat(fd)
    juist_type = stat.S_ISDIR(st.st_mode) if modus == 0o700 else stat.S_ISREG(st.st_mode)
    if not juist_type or st.st_uid != os.geteuid():
        return False
    if not aanscherpen and st.st_mode & 0o002:
        return False
    if stat.S_IMODE(st.st_mode) & ~modus and (aanscherpen or nfs4_acl(fd)):
        with contextlib.suppress(OSError):
            os.chmod(fd, modus)
        st = os.fstat(fd)
    if st.st_mode & 0o7002 or st.st_uid != os.geteuid():
        return False
    acl_aanwezig = False
    for naam in NFS4_ACL_XATTRS:
        acl = _xattr(fd, naam)
        if acl is not None:
            if not _prive_acl(naam, acl):
                return False
            acl_aanwezig = True
    return acl_aanwezig or not (stat.S_IMODE(st.st_mode) & ~modus)


def eigen_geheim(path: Path, st: os.stat_result) -> bool:
    """Controleer bij een eerder opgevraagde lstat ook of nog hetzelfde bestand open is."""
    if not stat.S_ISREG(st.st_mode):
        return False
    try:
        fd = os.open(path, LEESVLAGGEN)
        try:
            return os.path.samestat(st, os.fstat(fd)) and prive_rechten(fd, 0o600, aanscherpen=False)
        finally:
            os.close(fd)
    except OSError:
        return False
