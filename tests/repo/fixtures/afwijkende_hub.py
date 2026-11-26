"""Pytest-plugin voor de uitvoerproef. De simulator herhaalt een token en beantwoordt een foutverzoek met HTTP 200."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="module")
def afwijkende_hub(target: Any) -> Iterator[httpx.MockTransport]:
    from hub_sim import HubState, create_hub_app

    state = HubState()
    state.add_grant("token", "jelly-leon-platform", target.token)
    with TestClient(create_hub_app(state)) as sim:
        def antwoord(verzoek: httpx.Request) -> httpx.Response:
            r = sim.request(verzoek.method, verzoek.url.path, content=verzoek.content, headers=verzoek.headers)
            data = {**r.json(), "service_token": os.environ["JLP_PROEFTOKEN"]}
            return httpx.Response(200, json=data, headers={"Cache-Control": "no-store"})

        yield httpx.MockTransport(antwoord)


@pytest.fixture(autouse=True)
def gebruik_simulator(request: pytest.FixtureRequest, afwijkende_hub: httpx.MockTransport,
                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request.module, "client", lambda: httpx.Client(transport=afwijkende_hub, trust_env=False))
