"""Alleen gemount in de lokale EX21-Hub. Bewaar gebeurtenistypes zonder adressen of argumenten."""

import os
import socket
import sys
from pathlib import Path


def start_meting(map_: Path) -> None:
    bestand = os.open(map_ / str(os.getpid()), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
    os.write(bestand, f"{os.getppid()} {os.geteuid()}\n".encode())
    netwerk = {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
               "socket.gethostbyname", "socket.gethostbyaddr"}
    processen = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty"}

    def tel(event: str, _args: tuple[object, ...]) -> None:
        if event in netwerk:
            os.write(bestand, b"netwerk\n")
        elif event in processen:
            os.write(bestand, b"hulpproces\n")

    with socket.socket() as server, socket.socket() as client:
        server.bind(("localhost", 0))
        server.listen(1)
        sys.addaudithook(tel)
        client.connect(server.getsockname())
    os.write(bestand, b"controle_klaar\n")


if sys.orig_argv[-3:] == ["-m", "hub", "ai-runner"] and os.geteuid() == 569:
    if os.environ.get("HUB_AI_ENABLED", "").lower() != "false":
        raise RuntimeError("EX21 vereist een uitgeschakelde runner")
    start_meting(Path("/ex21-meting"))
