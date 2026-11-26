"""Een afwijkend Hub-antwoord moet de echte conformiteitstest laten falen met een veilige CI-melding."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_mislukte_tokenvergelijking_en_foutantwoord_blijven_geheim(tmp_path: Path) -> None:
    inschrijving = "enr_" + secrets.token_urlsafe(32)
    servicetoken = "svc_" + secrets.token_urlsafe(32)
    fixtures = Path(__file__).with_name("fixtures")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(fixtures), os.environ.get("PYTHONPATH", "")]),
           "JLP_HUB_URL": "http://hub.test", "JLP_HUB_KEY_FINGERPRINT": "", "JLP_HUB_ENROLLMENT_TOKEN": inschrijving,
           "JLP_HUB_ENROLLMENT_TOKEN_2": inschrijving, "JLP_HUB_SESSION_COOKIE": inschrijving,
           "JLP_HUB_CSRF": inschrijving, "JLP_PROEFTOKEN": servicetoken}
    suite = "tests/conformance/test_hub_conformance.py::"
    proef = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=long", "--no-showlocals", "--color=no", "-p", "no:cacheprovider",
         "-p", "afwijkende_hub", "--basetemp", str(tmp_path / "pytest"), suite + "test_unknown_token_is_one_401_code",
         suite + "test_enroll_response_passes_the_service_checks",
         suite + "test_replay_before_first_use_gives_new_token"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False, timeout=60)

    # Ook bij een regressie mag de buitenste pytest de opgevangen uitvoer niet citeren.
    for waarde in (inschrijving, servicetoken):
        if any(deel in proef.stdout for deel in (waarde, waarde[4:16], waarde[-12:])):
            pytest.fail("Een testgeheim staat in de opgevangen pytest-uitvoer", pytrace=False)
    if proef.returncode != pytest.ExitCode.TESTS_FAILED:
        pytest.fail("De afwijkende Hub moet de conformiteitstest laten falen", pytrace=False)
    for melding in ("Onbekend token niet juist geweigerd", "Servicetoken gelijk bij herhaling"):
        if f"AssertionError: {melding}" not in proef.stdout:
            pytest.fail(f"De proef mist de foutmelding ‘{melding}’", pytrace=False)
    if "2 failed, 1 passed" not in proef.stdout:
        pytest.fail("De eerste inschrijving moet lukken en beide afwijzingsproeven moeten falen", pytrace=False)
