"""Healthcheck van de alles-in-één image (gebruikt door Docker/TrueNAS): ``/readyz`` moet 200 geven
(database bereikbaar, schema op de juiste versie, geheimen aanwezig)."""

import os
import sys
import urllib.request

port = os.environ.get("LIFECYCLE_PORT", "30440")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=6) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
