"""Healthcheck van de alles-in-één image (gebruikt door Docker/TrueNAS): ``/readyz`` moet 200 geven
(database bereikbaar, schema op de juiste versie, geheimen aanwezig).

Docker start de healthcheck (en ``docker exec``) als de gebruiker van de image (root), buiten de procesboom van PID 1
en met de standaard-capabilities. Daarom doet dit programma als **eerste** handeling (contract 1.3 §2.2): bij root de
bounding set legen, ``setgroups([])``, ``setgid(568)``, ``setuid(568)`` en ``PR_SET_NO_NEW_PRIVS``; pas daarna
controleert het iets. Mislukt de wissel, dan exit 1 (rood). Het Dockerfile roept het aan in exec-vorm (geen shell).

``--controle``: meldt na de wissel ``Uid``, ``CapEff``, ``CapPrm``, ``CapBnd``, ``CapAmb`` en ``NoNewPrivs`` van dit
proces als JSON (voor de rooktest en de image-E2E) en geeft exit 0 alleen als ze kloppen."""

import ctypes
import json
import os
import sys
import urllib.request

UID = int(os.environ.get("LIFECYCLE_UID", "568"))
GID = int(os.environ.get("LIFECYCLE_GID", "568"))
NUL = "0000000000000000"


def wissel() -> bool:
    """Eerste handeling: bounding set leeg (bij root), 568:568, no_new_privs. ``True`` = gestart als root."""
    libc = ctypes.CDLL(None, use_errno=True)
    root = os.getuid() == 0 or os.geteuid() == 0
    if root:
        try:
            with open("/proc/sys/kernel/cap_last_cap", encoding="ascii") as fh:
                last = int(fh.read().strip())
        except (OSError, ValueError):
            last = 63
        for cap in range(last + 1):
            if libc.prctl(24, cap, 0, 0, 0) != 0:  # PR_CAPBSET_DROP
                raise OSError(ctypes.get_errno(), "PR_CAPBSET_DROP mislukt")
        os.setgroups([])
        os.setgid(GID)
        os.setuid(UID)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS mislukt")
    if 0 in (os.getuid(), os.geteuid(), os.getgid(), os.getegid()):
        raise PermissionError("rechten konden niet worden verlaagd")
    return root


try:
    started_as_root = wissel()
except OSError:
    sys.exit(1)

if "--controle" in sys.argv[1:]:
    status = {}
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            key, _, value = line.partition(":")
            if key in {"Uid", "Gid", "CapEff", "CapPrm", "CapBnd", "CapAmb", "NoNewPrivs"}:
                status[key] = value.strip()
    goed = (set(status.get("Uid", "").split()) == {str(UID)} and status.get("CapEff") == NUL
            and status.get("CapPrm") == NUL and status.get("CapAmb", NUL) == NUL and status.get("NoNewPrivs") == "1"
            and (not started_as_root or status.get("CapBnd") == NUL))
    print(json.dumps({**status, "gestart_als_root": started_as_root, "in_orde": goed}))
    sys.exit(0 if goed else 1)

port = os.environ.get("LIFECYCLE_PORT", "30440")
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # nooit via een proxy
    with opener.open(f"http://127.0.0.1:{port}/readyz", timeout=6) as resp:
        sys.exit(0 if resp.status == 200 else 1)
except Exception:
    sys.exit(1)
