"""Echte server (uvicorn) + echte WebSocket-client (websockets): het draadprotocol zoals een externe client het ziet."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from websockets.asyncio.client import connect

from truenas_sim.server import SimServer, create_app


@pytest.fixture
def live() -> Iterator[str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(SimServer("25.10.8")), host="127.0.0.1", port=port,
                                           log_level="warning", ws="websockets-sansio"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def test_wire_protocol(live: str) -> None:
    async def scenario() -> None:
        async with connect(f"ws://{live}/api/current") as ws:
            async def call(i: int, method: str, *params: object) -> dict[str, object]:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": list(params)}))
                while True:
                    msg = json.loads(await ws.recv())
                    if msg.get("id") == i:
                        return dict(msg)

            key = "2-simwritekey00000000000000000000000000000000000000000000000000000"
            assert (await call(1, "auth.login_with_api_key", key))["result"] is True
            info = await call(2, "system.info")
            assert info["result"]["version"] == "25.10.8"  # type: ignore[index]
            bad = await call(3, "app.bestaat_niet")
            assert bad["error"]["code"] == -32601  # type: ignore[index]

    asyncio.run(scenario())
