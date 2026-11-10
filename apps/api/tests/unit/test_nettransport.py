"""Contract 1.3 §3.2 (C1.3-JLP-05): de verboden bereiken gelden ook voor het **opgeloste** adres, bij elke verbinding.
De bewaakte backend lost zelf op, weigert loopback (zonder ontwikkelvlag), link-local, ``0.0.0.0``/``::`` en
multicast, en verbindt met precies het gecontroleerde adres (geen tweede resolutie)."""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest

from lifecycle.nettransport import GuardedBackend, VerbodenAdres, guarded_transport


def _resolver(*answers: str) -> Any:
    def resolve(host: str, port: int, *args: Any, **kwargs: Any) -> list[Any]:
        family = socket.AF_INET6 if ":" in answers[0] else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (a, port)) for a in answers]

    return resolve


@pytest.mark.parametrize("answer", ["127.0.0.1", "169.254.169.254", "0.0.0.0", "::1", "fe80::1", "224.0.0.1",  # noqa: S104
                                    "::ffff:127.0.0.1"])
def test_forbidden_resolved_addresses_are_refused(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolver(answer))
    with pytest.raises(VerbodenAdres):
        GuardedBackend(allow_loopback=False).check("hub.voorbeeld.invalid", 30400)


def test_mixed_answer_is_refused_and_allowed_answer_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("192.0.2.10", "127.0.0.1"))
    with pytest.raises(VerbodenAdres):  # fail-closed: één verboden adres in het antwoord is genoeg
        GuardedBackend(allow_loopback=False).check("hub.voorbeeld.invalid", 30400)
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("192.0.2.10", "192.0.2.11"))
    assert GuardedBackend(allow_loopback=False).check("hub.voorbeeld.invalid", 30400) == "192.0.2.10"


def test_loopback_only_with_dev_flag_link_local_never(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("127.0.0.1"))
    assert GuardedBackend(allow_loopback=True).check("localhost", 30400) == "127.0.0.1"
    monkeypatch.setattr(socket, "getaddrinfo", _resolver("169.254.1.1"))
    with pytest.raises(VerbodenAdres):
        GuardedBackend(allow_loopback=True).check("hub.voorbeeld.invalid", 30400)


@pytest.fixture
def local_server() -> Iterator[int]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()


def test_real_connection_every_request_is_checked(local_server: int) -> None:
    """Echte verbinding: met de ontwikkelvlag naar 127.0.0.1 lukt; zonder de vlag weigert de transportlaag, ook via de
    naam ``localhost`` (die pas na resolutie loopback blijkt)."""
    with httpx.Client(transport=guarded_transport(allow_loopback=True)) as client:
        assert client.get(f"http://127.0.0.1:{local_server}/").text == "ok"
    with httpx.Client(transport=guarded_transport(allow_loopback=False)) as client:
        for url in (f"http://127.0.0.1:{local_server}/", f"http://localhost:{local_server}/"):
            with pytest.raises(httpx.ConnectError) as exc:
                client.get(url)
            assert isinstance(exc.value.__cause__, VerbodenAdres)


def test_client_factory_uses_the_guarded_transport() -> None:
    from lifecycle.app import default_http_client_factory
    from lifecycle.config import Settings

    with default_http_client_factory(Settings())() as client:
        pool = client._transport._pool  # type: ignore[attr-defined]
        assert isinstance(pool._network_backend, GuardedBackend) and not pool._network_backend.allow_loopback
        assert client.follow_redirects is False and client.trust_env is False
