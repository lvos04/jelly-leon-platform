"""Alleen-lezen MediaGuard-testbron op de eigen gepubliceerde simulatorpoort."""

import json
import secrets
import ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class Rapport(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_GET(self) -> None:
        if not secrets.compare_digest(self.headers.get("X-Api-Key", ""), sleutel):
            self.send_error(403)
            return
        gegevens = routes.get(self.path)
        if gegevens is None:
            self.send_error(404)
            return
        data = json.dumps(gegevens).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    invoer = Path("/certificaat/proefrapport.json")
    sleutel, pagina = json.loads(invoer.read_bytes())
    invoer.unlink()
    routes = {"/api/v1/auth/me": {"username": "apikey:hub", "role": "viewer", "scopes": None},
              "/connector/v1/discover": {"app_id": "media-guard-ultimate", "schema_version": "1.0",
                                         "capabilities": ["read"], "item_types": ["backups"], "actions": []},
              "/connector/v1/items?type=backups&limit=1": pagina}
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain("/certificaat/sim.crt", "/certificaat/sim.key")
    with HTTPServer(("truenas", 30080), Rapport) as server:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        server.serve_forever()
