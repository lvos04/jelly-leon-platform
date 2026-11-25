"""Synthetische IPv4-adressen voor de API-tests."""

from ipaddress import IPv4Address


def documentatie_ip(host: int) -> str:
    """Adres uit TEST-NET-1 (RFC 5737) voor een gesimuleerde client of DNS-antwoord."""
    return str(IPv4Address(bytes((192, 0, 2, host))))


def link_lokaal_ip(derde: int, vierde: int) -> str:
    return str(IPv4Address(bytes((169, 254, derde, vierde))))


CLIENT_IP = documentatie_ip(10)
ONGESPECIFICEERD_IP = str(IPv4Address(0))
