"""Healthcheck van de alles-in-één image (gebruikt door Docker/TrueNAS): ``/readyz`` moet 200 geven
(database bereikbaar, schema op de juiste versie, geheimen aanwezig).

Docker start de healthcheck als de gebruiker van de image (root). Daarom wisselt dit proces eerst naar 568 zonder
capabilities en met no_new_privs, zodat ook dit kortlevende proces niet als root draait (contract 1.2 §2.2)."""

import ctypes
import os
import sys
import urllib.request

UID = int(os.environ.get("LIFECYCLE_UID", "568"))
GID = int(os.environ.get("LIFECYCLE_GID", "568"))

try:
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(GID)
        os.setuid(UID)
    ctypes.CDLL(None, use_errno=True).prctl(38, 1, 0, 0, 0)  # PR_SET_NO_NEW_PRIVS
except OSError:
    sys.exit(1)

port = os.environ.get("LIFECYCLE_PORT", "30440")
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz", timeout=6) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
