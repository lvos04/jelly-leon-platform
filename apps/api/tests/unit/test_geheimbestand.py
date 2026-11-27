"""Een bestand met modus 0600 kan op NFSv4 ook afzonderlijke toegangsregels hebben."""

import errno
import os
import struct
from pathlib import Path

import pytest

from lifecycle.geheimbestand import eigen_geheim


@pytest.mark.parametrize("naam", ["system.nfs4_acl_xdr", "system.nfs4_acl"])
@pytest.mark.parametrize(("invoer", "vertrouwd"), [
    ("eigenaar", True), ("groep", False), ("benoemde_gebruiker", False),
    ("afgekapt", False), ("onbekende_regel", False), ("onleesbaar", False),
])
def test_alle_acl_regels_op_het_geopende_bestand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, naam: str, invoer: str, vertrouwd: bool,
) -> None:
    pad = tmp_path / "proef"
    pad.touch(mode=0o600)
    st = pad.lstat()
    soort = 7 if invoer == "onbekende_regel" else 0
    # Naast de eigenaar krijgt de tweede ACE leesrecht. Een benoemde gebruiker met id 1 is geen OWNER@.
    if naam == "system.nfs4_acl_xdr":
        speciaal = int(invoer != "benoemde_gebruiker")
        wie = 2 if invoer == "groep" else 1
        data = struct.pack(">12I", 0, 2, 0, 0, 1, 3, 1, soort, 0, speciaal, 1, wie)
    else:
        wie_nfs = {"groep": b"GROUP@", "benoemde_gebruiker": b"1"}.get(invoer, b"OWNER@")
        data = (struct.pack(">5I8s", 2, 0, 0, 3, 6, b"OWNER@")
                + struct.pack(">4I", soort, 0, 1, len(wie_nfs)) + wie_nfs.ljust((len(wie_nfs) + 3) & ~3, b"\0"))

    def getxattr(fd: int, attribuut: str) -> bytes:
        assert os.path.samestat(st, os.fstat(fd))
        if attribuut != naam:
            raise OSError(errno.ENODATA, "geen ACL")
        if invoer == "onleesbaar":
            raise PermissionError(errno.EACCES, "ACL onleesbaar")
        return data[:-1] if invoer == "afgekapt" else data

    monkeypatch.setattr(os, "getxattr", getxattr)
    assert eigen_geheim(pad, st) is vertrouwd
