"""Koppel arr bij het opstarten aan de verse SAB-testsleutel op het interne netwerk."""

import os
import sys
import time

import httpx

if __name__ == "__main__":
    sab = "http://sabnzbd-sim:30217"
    einde = time.monotonic() + 45
    with httpx.Client(base_url=sab, trust_env=False, timeout=3) as client:
        while time.monotonic() < einde:
            try:
                sleutel = client.get("/_sim/sleutels").raise_for_status().json()["apikey"]
            except httpx.TransportError:
                time.sleep(0.25)
            else:
                break
        else:
            raise SystemExit("SAB-testsimulator niet bereikbaar")
    os.environ["SIM_SAB_APIKEY"] = sleutel
    os.execv(sys.executable, [sys.executable, "-m", "arr", "--host", "intern", "--sab-url", sab])  # noqa: S606
