"""HTTP vanuit de eigen Hub-container; ook de simulatorbesturing blijft op het interne netwerk."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from e2e.integral.bewijs import schrijf, uitwisseling
from e2e.integral.project import Proeffout, Project

# De aanvraag (inclusief sessiecookie) gaat uitsluitend via stdin; docker-argumenten zijn openbaar.
HTTP_CLIENT = """
import http.client, json, ssl, sys
p = json.load(sys.stdin)
if p['ca']:
    conn = http.client.HTTPSConnection(p['host'], p['port'], timeout=10,
                                       context=ssl.create_default_context(cadata=p['ca']))
else:
    conn = http.client.HTTPConnection(p['host'], p['port'], timeout=10)
conn.request(p['method'], p['path'], bytes.fromhex(p['body']), p['headers'])
r = conn.getresponse()
print(json.dumps([r.status, r.getheaders(), r.read().hex()]))
"""


def json_body(inhoud: bytes) -> object:
    try:
        return json.loads(inhoud)
    except (ValueError, UnicodeError):
        return None


class Testnet(httpx.BaseTransport):
    def __init__(self, project: Project, dossier: Path):
        self.project = project
        self.dossier = dossier
        self.ca: dict[str, str] = {}
        self.verkeer: list[dict[str, Any]] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host not in self.project.diensten or url.scheme not in {"http", "https"}:
            raise Proeffout("HTTP-doel ligt buiten het eigen testproject")
        ca = self.ca.get(url.host)
        if (url.scheme == "https") != bool(ca):
            raise Proeffout("Simulatorcertificaat ontbreekt of protocol komt niet overeen")
        body = request.read()
        invoer = {"method": request.method, "host": url.host, "port": url.port, "path": url.raw_path.decode("ascii"),
                  "body": body.hex(), "headers": dict(request.headers), "ca": ca}
        try:
            raw = self.project.compose("exec", "-T", "hub", "python", "-c", HTTP_CLIENT, invoer=json.dumps(invoer),
                                       tijd=18)
        except Proeffout:
            raise httpx.ConnectError("Testdienst gaf geen HTTP-antwoord", request=request) from None
        status, headers, hexbody = json.loads(raw)
        inhoud = bytes.fromhex(hexbody)
        regel = uitwisseling(url.host, request.method, str(url), json_body(body), status, json_body(inhoud))
        self.verkeer.append(regel)
        schrijf(self.dossier / "verkeer.json", self.verkeer)
        return httpx.Response(status_code=status, headers=headers, content=inhoud)
