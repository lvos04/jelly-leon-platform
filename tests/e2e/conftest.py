"""Dienst-E2E: echte processen/containers + simulators (Hub-sim, TrueNAS-sim). Geen echte NAS of Hub."""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "e2e" / "simulators" / "hub"))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def docker_ok() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.fixture
def hub_server() -> Iterator[tuple[str, Any]]:
    """Hub-simulator als echte HTTP-server (uvicorn in een thread) op 127.0.0.1."""
    import uvicorn
    from hub_sim import HubState, create_hub_app

    state = HubState()
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_hub_app(state), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", state
    server.should_exit = True
    thread.join(5)
