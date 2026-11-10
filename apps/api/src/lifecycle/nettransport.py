"""Uitgaande verbindingen naar de Hub met de verboden bereiken van contract 1.3 §3.2, **ook na DNS-resolutie**.

Een hostnaam in ``HUB_URL`` of in het Hub-adres op de statuspagina kan (al dan niet kwaadwillig) naar loopback,
link-local, ``0.0.0.0``/``::`` of multicast wijzen. Daarom lost deze laag bij **elke verbinding** de naam zelf op,
weigert als één van de adressen in een verboden bereik ligt (fail-closed) en verbindt daarna met precies het
gecontroleerde IP-adres (geen tweede resolutie, dus geen wedloop met een wisselend DNS-antwoord). TLS controleert het
certificaat nog steeds tegen de oorspronkelijke naam (httpcore geeft de naam als SNI/servernaam mee).

Gebruikt door ``lifecycle.app.default_http_client_factory`` (API en worker): redirects nooit volgen, proxy-variabelen
negeren (§3.2, §5).
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx
from httpcore._backends.sync import SyncBackend  # standaard backend van httpcore (geen publieke naam)

from lifecycle.config import ip_problem


class VerbodenAdres(httpcore.ConnectError):
    """De naam wijst naar een verboden bereik (§3.2); er is geen verbinding gemaakt."""


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        addr = str(sockaddr[0])
        if addr not in seen:
            seen.append(addr)
    return seen


class GuardedBackend(SyncBackend):
    """``connect_tcp`` alleen naar adressen buiten de verboden bereiken; loopback alleen met de ontwikkelvlag."""

    def __init__(self, *, allow_loopback: bool) -> None:
        self.allow_loopback = allow_loopback

    def check(self, host: str, port: int) -> str:
        try:
            literal = ipaddress.ip_address(host.strip("[]"))
        except ValueError:
            literal = None
        addresses = [str(literal)] if literal is not None else _resolve(host, port)
        if not addresses:
            raise VerbodenAdres(f"geen adres gevonden voor {host}")
        for addr in addresses:
            problem = ip_problem(ipaddress.ip_address(addr.split("%", 1)[0]), self.allow_loopback)
            if problem:
                raise VerbodenAdres(f"{host} wijst naar een verboden adres ({problem})")
        return addresses[0]

    def connect_tcp(self, host: str, port: int, timeout: float | None = None, local_address: str | None = None,
                    socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        try:
            address = self.check(host, port)
        except OSError as exc:  # DNS-fout: gewoon onbereikbaar (geen verboden adres)
            raise httpcore.ConnectError(f"naam niet op te lossen: {type(exc).__name__}") from exc
        return super().connect_tcp(address, port, timeout=timeout, local_address=local_address,
                                   socket_options=socket_options)


def guarded_transport(*, allow_loopback: bool) -> httpx.HTTPTransport:
    """``httpx.HTTPTransport`` (geen proxy uit de omgeving) waarvan de verbindingspool de bewaakte backend gebruikt."""
    transport = httpx.HTTPTransport(trust_env=False)
    pool = transport._pool  # httpx 0.28 biedt geen eigen backend-optie; versie is hash-gepind
    if not hasattr(pool, "_network_backend"):
        raise RuntimeError("onverwachte httpcore-versie: bewaakte verbindingen niet mogelijk")
    pool._network_backend = GuardedBackend(allow_loopback=allow_loopback)
    return transport
