"""Positieve controle en onbedoeld verkeer moeten van dezelfde proefrunner komen."""

import subprocess
import sys

import pytest

from e2e.integral.project import MAP

PROEF = MAP.parent / "fixtures/hubrunner/sitecustomize.py"


@pytest.mark.parametrize("extra", [False, True])
def test_telt_ook_een_latere_lokale_poging(tmp_path, extra):
    script = """
import os, runpy, socket, sys
from pathlib import Path
meting = runpy.run_path(sys.argv[1])
meting["start_meting"](Path(sys.argv[2]))
if sys.argv[3] == "ja":
    with socket.socket() as server, socket.socket() as client:
        server.bind(("localhost", 0))
        server.listen(1)
        client.connect(server.getsockname())
print(os.getpid())
"""
    r = subprocess.run([sys.executable, "-c", script, str(PROEF), str(tmp_path), "ja" if extra else "nee"],
                       capture_output=True, text=True, check=True)
    bestand, = tmp_path.iterdir()
    assert bestand.name == r.stdout.strip()
    regels = bestand.read_text().splitlines()
    assert regels[1:3] == ["netwerk", "controle_klaar"]
    assert regels[3:] == (["netwerk"] if extra else [])
