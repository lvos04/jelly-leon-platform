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


def _echte_hub():  # type: ignore[no-untyped-def]
    import importlib.util
    spec = importlib.util.spec_from_file_location("echte_hub", ROOT / "e2e" / "conformance" / "echte_hub.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_conformiteitsrun_drukt_geen_foutteksten_af(tmp_path: Path) -> None:
    # Herreview S-P6-13: ook een uitzondering buiten de vergelijkingen kan een token citeren.
    geheim = "proefwaarde-" + secrets.token_urlsafe(24)
    (tmp_path / "test_lek.py").write_text(
        f"def test_header():\n    raise ValueError('ongeldige header: Bearer {geheim} ')\n"
        f"def test_vergelijk():\n    assert '{geheim}' == 'anders'\n")
    uit = subprocess.run([sys.executable, "-m", "pytest", *_echte_hub().PYTEST_ZWIJGEND, str(tmp_path)],
                         capture_output=True, text=True, check=False, timeout=60)
    if geheim in uit.stdout + uit.stderr:
        pytest.fail("Een testgeheim staat in de uitvoer van de conformiteitsrun", pytrace=False)
    assert uit.returncode == pytest.ExitCode.TESTS_FAILED and "2 failed" in uit.stdout


def test_voorbereiding_citeert_geen_protocolfout() -> None:
    # Herreview 3 S-P6-13: protocolfouten tijdens de voorbereiding citeren het servicetoken of de CSRF-waarde.
    import httpx

    geheim = "svc_" + secrets.token_urlsafe(24)
    for exc in (httpx.RemoteProtocolError(f"ongeldig antwoord {geheim}"),
                httpx.LocalProtocolError(f"Illegal header value b'{geheim} '"), KeyError(geheim)):
        melding = _echte_hub().foutmelding(exc)
        assert geheim not in melding and type(exc).__name__ in melding


def test_ongeldige_pg_url_citeert_geen_wachtwoord(tmp_path: Path) -> None:
    # Herreview 4 S-P6-13: een ongecodeerd '@:' laat de URL-parser het wachtwoord als poort lezen.
    nep_python = tmp_path / "hub" / ".venv" / "bin" / "python"
    nep_python.parent.mkdir(parents=True)
    nep_python.touch()
    geheim = "Pw" + secrets.token_hex(12)
    uit = subprocess.run([sys.executable, str(ROOT / "e2e" / "conformance" / "echte_hub.py"),
                          "--hub-repo", str(tmp_path / "hub"), "--pg", f"postgresql+psycopg://u:{geheim}@:x@localhost:1"],
                         capture_output=True, text=True, check=False, timeout=60)
    if geheim in uit.stdout + uit.stderr:
        pytest.fail("Het wachtwoord uit --pg staat in de uitvoer", pytrace=False)
    assert uit.returncode == 3


def test_typefout_in_optie_citeert_geen_wachtwoord() -> None:
    # Herreview 5 S-P6-13: argparse herhaalt een onbekende optie met de waarde erna.
    geheim = "Pw" + secrets.token_hex(12)
    uit = subprocess.run([sys.executable, str(ROOT / "e2e" / "conformance" / "echte_hub.py"),
                          "--pgg", f"postgresql+psycopg://u:{geheim}@localhost:1"],
                         capture_output=True, text=True, check=False, timeout=60)
    if geheim in uit.stdout + uit.stderr:
        pytest.fail("Het wachtwoord staat in de foutmelding van de opties", pytrace=False)
    assert uit.returncode == 2
