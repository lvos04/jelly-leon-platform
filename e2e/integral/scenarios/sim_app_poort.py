"""De gepubliceerde app-poort van de test-NAS, begrensd tot de doelen van de geselecteerde proef."""

from __future__ import annotations

import json
import sys
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

METING = Path("/certificaat/bezorgingen.json")


class AppPoort(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass  # Een beheer- of uitvoeringsverzoek hoort nooit in een serverlog.

    def do_POST(self) -> None:
        if self.path != "/api/v1/execute":
            self.send_error(404)
            return
        omvang = int(self.headers["Content-Length"])
        if not 0 < omvang <= 65536:
            self.send_error(413)
            return
        body = self.rfile.read(omvang)
        verzoek = json.loads(body)
        voorstel = verzoek["proposal"]
        actie, doel, params = voorstel["action_id"], voorstel["target"], voorstel["parameters"]
        app = doel.get("app_id")
        toegestaan = actie == "app.restart" and app in {"proef-app-a", "proef-app-b"} and not params
        if sys.argv[1] == "opslag":
            bron = "simpool/apps/proef-config"
            toegestaan = (
                (actie == "zfs.snapshot.create" and doel.get("dataset") == bron
                 and params.get("naam") == "voor-proef" and not params.get("recursief"))
                or (actie == "zfs.snapshot.clone_to_sandbox" and doel.get("snapshot") == bron + "@voor-proef"
                    and params == {"doel_dataset": "simpool/asm-sandbox/proef-config"})
            )
        # R1-snapshots vereisen volgens het productiebeleid een ondertekend voorstel.
        # Bij een app-herstart vraagt datzelfde beleid ook een afzonderlijke goedkeuring.
        if not toegestaan or (sys.argv[1] != "opslag" and not verzoek.get("approval")):
            self.send_error(403)
            return
        headers = {k: self.headers[k] for k in ("Content-Type", "Authorization", "Idempotency-Key")}
        # Een exacte transportherhaling van de eerste goedgekeurde proefherstart. Alleen aantallen en
        # HTTP-codes verlaten het geheugen. De executor controleert elke aflevering met zijn gewone beleid.
        aantal = 2 if sys.argv[1] == "dubbel" and app == "proef-app-a" else 1
        codes = []
        for poging in range(aantal):
            verbinding = HTTPConnection("executor", 30410, timeout=45)
            try:
                verbinding.request("POST", self.path, body=body, headers=headers)
                antwoord = verbinding.getresponse()
                inhoud = antwoord.read()
                codes.append(antwoord.status)
                if poging == 0:
                    eerste_status, eerste_inhoud = antwoord.status, inhoud
                elif (antwoord.getheader("Idempotent-Replay") != "true"
                      or json.loads(inhoud) != json.loads(eerste_inhoud)):
                    self.send_error(502, "De executor bevestigde de exacte herhaling niet")
                    return
            finally:
                verbinding.close()
        metingen = json.loads(METING.read_text())
        METING.write_text(json.dumps([*metingen, codes]))
        self.send_response(eerste_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(eerste_inhoud)))
        self.end_headers()
        self.wfile.write(eerste_inhoud)


if __name__ == "__main__":
    with HTTPServer(("truenas", 30410), AppPoort) as poort:
        METING.write_text("[]")
        poort.serve_forever()
