"""Gedeelde koppeling en simulatorbediening op een verse kern per scenario."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import ssl
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx
import pytest

from e2e.integral.bewijs import SIM_API, schrijf
from e2e.integral.project import Project
from e2e.integral.testnet import Testnet
from tools.rooktest import SETUP_RE


def wacht(naam: str, meting: Callable[[], bool], seconden: int = 150) -> None:
    einde = time.monotonic() + seconden
    while time.monotonic() < einde:
        try:
            if meting():
                return
        except httpx.HTTPStatusError as fout:
            if fout.response.status_code not in {502, 503, 504}:
                raise
        except (httpx.TransportError, json.JSONDecodeError):
            pass
        time.sleep(1)
    pytest.fail(f"Tijdslimiet bij {naam}", pytrace=False)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    # Een setupfout, collection error of skip mag geen positieve simulatie-uitkomst worden.
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if "INTEGRAAL_DOSSIER" in os.environ and reporter:
        stats = reporter.stats
        reden = ""
        for rapport in (*stats.get("skipped", []), *stats.get("xfailed", [])):
            melding = getattr(rapport, "wasxfail", "")
            if isinstance(rapport.longrepr, tuple):
                melding = rapport.longrepr[2].removeprefix("Skipped: ")
            toegelaten = json.loads(os.environ.get("INTEGRAAL_OVERSLAGREDENEN", "[]"))
            regel = rapport.location[1]
            plaats = f"testregel {regel + 1}" if isinstance(regel, int) else "de testmodule"
            reden = melding if melding in toegelaten else f"Pytest-voorwaarde in {plaats}"
            break
        schrijf(Path(os.environ["INTEGRAAL_DOSSIER"]) / "pytest.json",
                {"getoetst": bool(stats.get("passed") or stats.get("failed")),
                 "overgeslagen": bool(reden), "reden": reden, "exitcode": int(exitstatus)})


class Kern:
    def __init__(self, net: Testnet, sluit: ExitStack):
        self.net = net
        self.sluit = sluit
        self.hub = self.client("hub", 30400)
        self.executor = self.client("executor", 30410)
        self.lifecycle = self.client("lifecycle", 30440)
        self.sim = self.client("truenas", 8080, tls=True)

    def client(self, dienst: str, poort: int, *, tls: bool = False) -> httpx.Client:
        schema = "https" if tls else "http"
        return self.sluit.enter_context(httpx.Client(base_url=f"{schema}://{dienst}:{poort}", transport=self.net,
                                                     headers={"Accept-Encoding": "identity"}, trust_env=False))

    def simulator_gereed(self) -> bool:
        versies = self.sim.get("/api/versions").raise_for_status().json()
        return isinstance(versies, list) and SIM_API in versies

    def simulatorfout(self, sim: httpx.Client, naam: str) -> None:
        sim.post("/_sim/fouten", json={"naam": naam}).raise_for_status()

    def verzoeklog(self, sim: httpx.Client) -> Any:
        return sim.get("/_sim/verzoeken").raise_for_status().json()

    def wijzig(self, methode: str, pad: str, **kwargs: Any) -> httpx.Response:
        headers = {**kwargs.pop("headers", {}), "Idempotency-Key": str(uuid.uuid4())}
        return self.hub.request(methode, pad, headers=headers, **kwargs).raise_for_status()

    def richt_in(self) -> None:
        wacht("Hub ready", lambda: self.hub.get("/readyz").json().get("status") == "ready")
        wacht("executor ready", lambda: self.executor.get("/readyz").json().get("status") == "ready")
        logs = self.net.project.compose("logs", "--no-color", "hub")
        codes = SETUP_RE.findall(logs)
        if not codes:
            pytest.fail("Eenmalige setupcode ontbreekt", pytrace=False)
        setup = self.wijzig("POST", "/api/v1/setup/admin", json={"setup_code": codes[-1], "username": "rookbeheer",
                                                                "password": "Integraal-" + secrets.token_urlsafe(24)})
        self.hub.headers["X-CSRF-Token"] = setup.json()["csrf_token"]
        pem = self.net.project.compose("exec", "-T", "truenas", "cat", "/certificaat/sim.crt")
        self.net.ca["truenas"] = pem
        wacht("simulatorprofiel", self.simulator_gereed)
        sleutel = self.sim.get("/_sim/keys").raise_for_status().json()["readonly"]
        besluit = self.hub.get("/api/v1/decisions/D03").raise_for_status()
        self.wijzig("PUT", "/api/v1/decisions/D03/waarde",
                    json={"waarde": {"nas_adres": "truenas", "https_poort": 8080}},
                    headers={"If-Match": besluit.headers["etag"]})
        self.wijzig("PUT", "/api/v1/truenas/api-key", json={"api_key": sleutel})
        eerste = self.wijzig("POST", "/api/v1/truenas/test").json()
        assert eerste["verbonden"] is False and eerste["reason_code"] == "tls_untrusted"
        vingerafdruk = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
        self.wijzig("POST", "/api/v1/truenas/tls/trust", json={"fingerprint_sha256": vingerafdruk})
        assert self.wijzig("POST", "/api/v1/truenas/test").json()["verbonden"] is True
        code = self.wijzig("POST", "/api/v1/services/pairing-codes", json={"app_id": "ai-server-manager-executor"})
        formulier = self.executor.get("/").raise_for_status()
        veld = re.search(r'name="csrf" value="([^"]+)"', formulier.text)
        if not veld:
            pytest.fail("Koppelformulier van de executor ontbreekt", pytrace=False)
        koppeling = self.executor.post("/koppelen", headers={"Origin": str(self.executor.base_url).rstrip("/")},
                                       data={"csrf": veld[1], "hub_url": str(self.hub.base_url).rstrip("/"),
                                             "koppelcode": code.json()["koppelcode"],
                                             "vingerafdruk": code.json()["keyset_fingerprint"]})
        assert koppeling.status_code in {200, 303}
        wacht("gekoppelde executor", lambda: self.executor.get("/readyz").json().get("gekoppeld") is True)
        self.koppel_lifecycle()

    def koppel_lifecycle(self) -> None:
        grant = self.wijzig("POST", "/api/v1/services/enrollment-tokens", json={"app_id": "jelly-leon-platform"}).json()
        self.net.project.start(("lifecycle",), INTEGRAAL_ENROLLMENT=grant["enrollment_token"],
                               INTEGRAAL_VINGERAFDRUK=grant["keyset_fingerprint"])
        wacht("lifecycle ready", lambda: self.lifecycle.get("/readyz").json().get("status") == "ready")
        wacht("actieve lifecycle-registratie", self.geregistreerd)

    def geregistreerd(self) -> bool:
        diensten = self.hub.get("/api/v1/services").raise_for_status().json()["items"]
        return any(d["app_id"] == "jelly-leon-platform" and d["status"] == "active" for d in diensten)


@pytest.fixture(autouse=True)
def kern() -> Iterator[Kern]:
    if "INTEGRAAL_PROJECT" not in os.environ:
        pytest.skip("Gebruik de integrale runner voor een eigen gekoppelde kern")
    project = Project(os.environ["INTEGRAAL_PROJECT"])
    net = Testnet(project, Path(os.environ["INTEGRAAL_DOSSIER"]))
    with ExitStack() as sluit:
        proef = Kern(net, sluit)
        proef.richt_in()
        yield proef
