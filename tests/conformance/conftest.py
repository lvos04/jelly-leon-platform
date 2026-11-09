"""JP10: één set 1.2-conformiteitstests voor de Hub, tegen de Hub-simulator (standaard, ook in CI) of tegen een echte
Hub (``JLP_HUB_URL`` + een vers ``JLP_HUB_ENROLLMENT_TOKEN`` voor ``jelly-leon-platform``, optioneel
``JLP_HUB_KEY_FINGERPRINT``). Tegen een echte Hub verbruikt de run dat token; maak per run een nieuw."""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "e2e" / "simulators" / "hub"))


@dataclass
class Target:
    kind: str  # "simulator" | "echte Hub"
    base_url: str
    token: str
    fingerprint: str | None
    sim: Any = None
    token2: str | None = None  # tweede vers token voor de koppeltest met de eigen worker
    session_cookie: str | None = None  # echte Hub: beheerderssessie om gebruikers-JWT's te laten maken
    csrf: str | None = None

    def mint_user_jwt(self, audience: str = "jelly-leon-platform") -> str:
        if self.sim is not None:
            return str(self.sim.mint("conformiteit", ["admin"], audience))
        import httpx

        with httpx.Client(base_url=self.base_url, trust_env=False, timeout=15,
                          cookies={"hub_session": self.session_cookie or ""}) as c:
            r = c.post("/api/v1/auth/service-jwt", json={"audience": audience},
                       headers={"X-CSRF-Token": self.csrf or ""})
            r.raise_for_status()
            return str(r.json()["token"])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def target() -> Iterator[Target]:
    url = os.environ.get("JLP_HUB_URL", "").strip().rstrip("/")
    if url:
        token = os.environ.get("JLP_HUB_ENROLLMENT_TOKEN", "").strip()
        if not token:
            pytest.fail("JLP_HUB_URL gezet zonder JLP_HUB_ENROLLMENT_TOKEN")
        yield Target("echte Hub", url, token, os.environ.get("JLP_HUB_KEY_FINGERPRINT") or None,
                     token2=os.environ.get("JLP_HUB_ENROLLMENT_TOKEN_2") or None,
                     session_cookie=os.environ.get("JLP_HUB_SESSION_COOKIE") or None,
                     csrf=os.environ.get("JLP_HUB_CSRF") or None)
        return
    import uvicorn
    from hub_sim import HubState, create_hub_app

    state = HubState()
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_hub_app(state), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield Target("simulator", f"http://127.0.0.1:{port}", state.add_grant("token", "jelly-leon-platform"),
                 state.keyset_fingerprint(), state, token2=state.add_grant("token", "jelly-leon-platform"))
    server.should_exit = True
    thread.join(5)
