"""Gedeelde proeven voor de downloadketen. Alleen de gepinde MSO-testbron wordt geladen."""

import io
import os
import subprocess
import tarfile
import time
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def mso_bron(tmp_path_factory):
    root = Path(__file__).resolve().parent
    repo = os.environ.get("MSO_BRON", root.parents[2] / "media-source-optimizer")
    pin = (root / "mso-mocks.commit").read_text().strip()
    archive = subprocess.check_output(["git", "-C", repo, "archive", pin, "apps/mocks/msomocks"])  # noqa: S603,S607
    checkout = tmp_path_factory.mktemp("mso")
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        files.extractall(checkout, filter="data")
    with pytest.MonkeyPatch.context() as paths:
        paths.syspath_prepend(str(checkout / "apps/mocks"))
        yield


@pytest.fixture
def foutproef():
    def controleer(client, path, headers, naam):
        before = client.get(path, headers=headers)
        assert before.status_code == 200
        payload = {"naam": naam, "aantal": 1, "pad": f"^{path.split('?')[0]}$", "waarde": 0.035}
        assert client.post("/_sim/fouten", json=payload).status_code == 200
        start = time.monotonic()
        reply = client.get(path, headers=headers)
        elapsed = time.monotonic() - start
        if naam == "traag":
            assert elapsed >= payload["waarde"] and reply.json() == before.json()
        elif naam == "ongeldige_json":
            assert reply.status_code == 200
            with pytest.raises(ValueError):
                reply.json()
        else:
            assert reply.status_code == 503
        assert client.get(path, headers=headers).json() == before.json()
        log = client.get("/_sim/verzoeken").json()
        assert [v["fouten"] for v in log[-3:]] == [[], [naam], []]
        assert len({v["pad"] for v in log[-3:]}) == 1

    return controleer
