"""Gedeelde proeven. Alleen de gepinde testbronnen van MSO en my_house worden geladen."""

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


MY_HOUSE_PADEN = ("integrations/src/shc_integrations", "integrations/tests/fake_gree.py")


@pytest.fixture(scope="session")
def my_house_bron(tmp_path_factory):
    """Map met ``fake_gree.py`` van de gepinde my_house-commit, met ``shc_integrations`` op het pad (de fake van de
    Gree-unit en de adapters waarmee de tests nagaan dat my_house de berichten van de simulators leest)."""
    root = Path(__file__).resolve().parent
    repo = os.environ.get("MY_HOUSE_BRON", root.parents[2] / "my_house")
    pin = (root / "my-house.commit").read_text().strip()
    archive = subprocess.check_output(["git", "-C", repo, "archive", pin, *MY_HOUSE_PADEN])  # noqa: S603,S607
    checkout = tmp_path_factory.mktemp("my_house")
    with tarfile.open(fileobj=io.BytesIO(archive)) as files:
        files.extractall(checkout, filter="data")
    with pytest.MonkeyPatch.context() as paths:
        paths.syspath_prepend(str(checkout / "integrations/src"))
        yield checkout / "integrations/tests"


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
