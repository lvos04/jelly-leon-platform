"""EX20 controleert ook wat er gebeurt bij een tweede zelfstandige opdracht aan de Hub."""

from unittest.mock import Mock

import pytest

from e2e.integral.scenarios import EX20_eenmalig_akkoord as proef


@pytest.mark.parametrize("code", ["status_past_niet", "andere_reden"])
def test_verbruikt_voorstel_mag_niet_weer_naar_de_executor(monkeypatch, code):
    eerste = {"goedkeuring": {"approval_id": "eerste"}}
    tweede = {"goedkeuring": {"approval_id": "tweede"}}
    monkeypatch.setattr(proef, "richt_schrijfpad_in", Mock())
    monkeypatch.setattr(proef, "stel_voor", Mock())
    monkeypatch.setattr(proef, "akkoord", Mock(side_effect=[eerste, tweede]))
    uitvoeren = Mock(return_value={"code": code})
    monkeypatch.setattr(proef, "uitvoeren", uitvoeren)
    monkeypatch.setattr(proef, "bezorgingen", Mock(side_effect=[[[200, 200]], [[200, 200]], [[200, 200], [200]]]))
    mutaties = [["app.redeploy"], ["app.redeploy"], ["app.redeploy"] * 2]
    monkeypatch.setattr(proef, "schrijfacties", Mock(side_effect=mutaties))
    monkeypatch.setattr(proef, "meet", Mock())
    kern = Mock()
    if code == "status_past_niet":
        proef.test_eenmalig_akkoord_en_twee_geldige_akkoorden(kern)
    else:
        with pytest.raises(AssertionError):
            proef.test_eenmalig_akkoord_en_twee_geldige_akkoorden(kern)
    uitvoeren.assert_any_call(kern, eerste, geweigerd=True)
