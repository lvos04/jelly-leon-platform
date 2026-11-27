"""Een gefaalde kwaliteitsmeting en een storingsvrije controle gaan door dezelfde CPU-encoder."""

import json
from typing import Any

from e2e.integral.media_inhoud import FIXTURES, Inhoud, inhoud
from e2e.integral.scenarios.conftest import Kern

SCENARIO = {
    "groepen": ("media", "media-inhoud"), "diensten": ("mg-inhoud", "kijkserver-inhoud", "mg-kwaliteitsmeting"),
    "eigenaar": "media-guard-ultimate",
    "verwachting": "Als VMAF én SSIM falen blijft het origineel intact. De controlejob slaagt met gemeten VMAF.",
}


def encode(proef: Inhoud, naam: str) -> dict[str, Any]:
    raw = proef.kern.net.project.compose("exec", "-T", "mg-kwaliteitsmeting", "/opt/venv/bin/python", "-", naam,
                                         invoer=(FIXTURES / "encode.py").read_text(), tijd=240)
    meting: dict[str, Any] = json.loads(raw.strip().splitlines()[-1])
    proef.noteer("job_" + naam, meting)
    return meting


def test_meetfout_en_storingsvrije_controle(kern: Kern) -> None:
    with inhoud(kern, "kwaliteit") as proef:
        foutpad = "/media/kwaliteitsproef/fout.mkv"
        controlepad = "/media/kwaliteitsproef/controle.mkv"
        assert proef.verwacht[foutpad] == proef.verwacht[controlepad]
        fout = encode(proef, "fout")
        assert fout["uitgevoerd"] is False and (fout["status"], fout["foutsoort"]) == ("mislukt", "QualityUnmeasured")
        assert fout["injecties"]["vmaf"] == fout["injecties"]["ssim"] == fout["pogingen"] > 0
        assert fout["gemeten"] == 0 and fout["score"] is None and fout["codec"] == "h264"
        assert proef.meet("na_meetfout") == proef.verwacht

        controle = encode(proef, "controle")
        assert controle["id"] != fout["id"] and controle["uitgevoerd"] is True and controle["status"] == "klaar"
        assert controle["injecties"] == {"vmaf": 0, "ssim": 0} and controle["codec"] == "av1"
        assert controle["metriek"] == "vmaf" and controle["gemeten"] > 0 and controle["score"] > 0
        na = proef.meet("na_controle")
        assert na[controlepad] != proef.verwacht[controlepad]
        proef.verwacht[controlepad] = na[controlepad]
