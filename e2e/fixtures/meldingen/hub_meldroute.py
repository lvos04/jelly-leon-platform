"""Uitsluitend in de proef-Hub. De negatieve controleroute verdwijnt samen met deze container."""

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit


class Meldroute(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass  # Kuma stuurt zijn applicatietoken in de query.

    def do_POST(self) -> None:
        if urlsplit(self.path).path != "/message":
            self.send_error(404)
            return
        omvang = int(self.headers.get("Content-Length", "0"))
        if not 0 < omvang <= 16384:
            self.send_error(413)
            return
        verbinding = HTTPConnection("gotify-sim", 30215, timeout=5)
        try:
            verbinding.request("POST", self.path, self.rfile.read(omvang), {"Content-Type": "application/json"})
            antwoord = verbinding.getresponse()
            antwoord.read()
            self.send_response(antwoord.status)
            self.end_headers()
        finally:
            verbinding.close()


if __name__ == "__main__":
    with HTTPServer(("hub", 31822), Meldroute) as server:
        server.serve_forever()
