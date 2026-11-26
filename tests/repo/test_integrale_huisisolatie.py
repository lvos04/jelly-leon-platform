"""De huisproef mag de apparaten pas starten op één intern netwerk van de eigen stack."""

import json
import subprocess
from unittest.mock import Mock

import pytest

from e2e.integral.project import Proeffout
from e2e.integral.scenarios import huis


@pytest.mark.parametrize(("netten", "intern", "eigenaar"), [
    ([["huis"]], False, "proef"), ([["huis"]], True, "ander-project"),
    ([["huis", "extra"]], True, "proef"), ([["huis"], ["ander"]], True, "proef"),
])
def test_onveilig_testnet_weigert_voor_de_eerste_simulator(monkeypatch, netten, intern, eigenaar):
    kern = Mock()
    kern.net.project.naam = "proef"
    kern.net.project.compose.return_value = "\n".join(f"container{i}" for i in range(len(netten)))
    inspectie = "\n".join(json.dumps({n: {"NetworkID": n} for n in net}) for net in netten)
    netwerk = json.dumps([{"Internal": intern, "Labels": {"com.docker.compose.project": eigenaar}}])
    monkeypatch.setattr(huis, "opdracht", Mock(side_effect=[
        subprocess.CompletedProcess([], 0, inspectie), subprocess.CompletedProcess([], 0, netwerk),
    ]))
    with pytest.raises(Proeffout, match="netwerk"):
        huis.Huis(kern)
    kern.net.project.compose.assert_called_once_with("ps", "-q")
    kern.client.assert_not_called()
