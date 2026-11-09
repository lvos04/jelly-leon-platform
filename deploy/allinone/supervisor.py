"""jelly-leon-platform — alles-in-één procesbewaker (zelf PID 1 van de container, geen root-init).

Eén container draait alles: PostgreSQL 16 (alleen unix-socket, geen netwerkpoort, geen wachtwoord), eerste-start-
initialisatie (geheimen), databasemigraties, de API (poort 30440) en de worker. Eén datamap (``/appdata``) en één poort:
installeerbaar als **standaard TrueNAS Custom App** zonder YAML (zie ``deploy/truenas/CUSTOM_APP.md``).

Regels (naar het voorbeeld van MSO ``deploy/allinone``):
- Start de container als root, dan wordt alléén eigenaarschap van de eigen submappen van ``/appdata`` naar 568:568
  gezet; daarna draait **elk** proces — ook deze bewaker als PID 1 — als 568 zonder capabilities en met
  ``no_new_privs`` (prctl), ook als het TrueNAS-formulier dat niet zet (contract 1.2 §2.2, R11). De bewaker
  controleert dat zelf in ``/proc/self/status`` en stopt met een Nederlandse melding als het niet klopt.
- Als PID 1 ruimt de bewaker verweesde processen (zombies) op en vangt SIGTERM/SIGINT vanaf de eerste regel af
  (PID 1 heeft geen standaard signaalafhandeling); er draait dus geen ``dumb-init`` als root meer.
- Start hij als 568 (TrueNAS "Custom User"), dan wordt niets aangepast en alleen de schrijfbaarheid gecontroleerd.
- Duidelijke Nederlandse fout en pauze (geen snelle crashlus) als ``/appdata`` ontbreekt of niet schrijfbaar is.
- Openstaande migraties: eerst een ``pg_dump`` (als er ruimte is; de nieuwste 3 blijven), dan migreren in één
  transactie. Mislukt de migratie, dan blijft de database exact op de vorige versie en start de API niet.
- Gecrashte processen herstarten met oplopende wachttijd; SIGTERM stopt api → worker → database netjes.
"""

from __future__ import annotations

import contextlib
import ctypes
import grp
import json
import os
import pwd
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

UID = int(os.environ.get("LIFECYCLE_UID", "568"))
GID = int(os.environ.get("LIFECYCLE_GID", "568"))
DATA = Path(os.environ.get("LIFECYCLE_DATA_DIR", "/appdata"))
PGDATA = DATA / "postgres"
BACKUPS = DATA / "backups"
SOCKET_DIR = Path("/tmp/lifecycle-pg")
PORT = os.environ.get("LIFECYCLE_PORT", "30440")
DB_URL = f"postgresql+psycopg://lifecycle@/lifecycle?host={SOCKET_DIR}"
MANAGED = ("postgres", "secrets", "backups")
KEEP_BACKUPS = 3
PR_SET_NO_NEW_PRIVS = 38
STOP = threading.Event()


def log(msg: str) -> None:
    print(f"[jelly-leon-platform] {msg}", flush=True)


def fail(msg: str, code: int = 78) -> None:
    log("FOUT: " + msg)
    time.sleep(int(os.environ.get("LIFECYCLE_FAIL_PAUSE_SECONDS", "30")))  # zichtbaar houden in de TrueNAS-log
    sys.exit(code)


def set_no_new_privs() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        log("waarschuwing: no_new_privs kon niet worden gezet")


def demote() -> None:
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(GID)
        os.setuid(UID)
    os.umask(0o077)  # nieuwe bestanden (o.a. pg_dump-back-ups) alleen leesbaar voor 568
    set_no_new_privs()


def own_status() -> dict[str, str]:
    fields: dict[str, str] = {}
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            key, _, value = line.partition(":")
            fields[key] = value.strip()
    return fields


def verify_privileges() -> None:
    """Na de wissel: Uid/Gid 568 (alle vier), geen effectieve/toegestane capabilities en no_new_privs (fail-closed)."""
    st = own_status()
    uids, gids = st.get("Uid", "").split(), st.get("Gid", "").split()
    if set(uids) != {str(UID)} or set(gids) != {str(GID)}:
        fail(f"procesbewaker draait niet volledig als {UID}:{GID} (Uid {uids}, Gid {gids}); gestopt")
    if st.get("CapEff") != "0000000000000000" or st.get("CapPrm") != "0000000000000000" or \
            st.get("CapAmb", "0000000000000000") != "0000000000000000":
        fail("procesbewaker heeft na de wissel nog capabilities; gestopt")
    if st.get("NoNewPrivs") != "1":
        fail("no_new_privs (PR_SET_NO_NEW_PRIVS) kon niet worden gezet; gestopt")


def reap_orphans(known: set[int]) -> None:
    """PID 1: verweesde processen opruimen zonder de exitstatus van eigen kinderen weg te nemen."""
    if os.getpid() != 1:
        return
    while True:
        try:
            info = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return
        if info is None or info.si_pid in known:
            return
        try:
            os.waitpid(info.si_pid, os.WNOHANG)
        except ChildProcessError:
            return


def prepare_dirs() -> None:
    if not DATA.is_dir():
        fail(f"{DATA} bestaat niet. Voeg in de app-instellingen opslag toe met Mount Path {DATA}.")
    root = os.getuid() == 0
    if root:
        st = DATA.stat()
        if (st.st_uid, st.st_gid) != (UID, GID):
            log(f"eigenaar van {DATA} (alleen de map zelf) wordt {UID}:{GID}")
            os.chown(DATA, UID, GID)
        for name in MANAGED:
            path = DATA / name
            if not path.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(path):
                for entry in [dirpath, *[os.path.join(dirpath, n) for n in dirnames + filenames]]:
                    try:
                        if os.lstat(entry).st_uid != UID:
                            os.lchown(entry, UID, GID)
                    except OSError:
                        pass
    for sub in (PGDATA, DATA / "secrets", BACKUPS):
        try:
            sub.mkdir(parents=True, exist_ok=True)
            if root:
                os.chown(sub, UID, GID)
        except PermissionError:
            fail(
                f"geen schrijfrechten op {sub}. Zet 'Custom User' uit (de app regelt de rechten zelf) of geef "
                f"gebruiker {UID} (apps) schrijfrechten op de dataset."
            )
    # database, geheimen en back-ups (de dumps bevatten de hele database) alleen voor 568
    for private in (PGDATA, DATA / "secrets", BACKUPS):
        try:
            os.chmod(private, 0o700)
        except PermissionError:
            fail(f"{private} is niet van gebruiker {UID}; start de app zonder 'Custom User' of maak {UID} eigenaar.")
    for dump in BACKUPS.glob("*.dump"):  # dumps van oudere versies (toen 0644) dichtzetten
        with contextlib.suppress(OSError):
            os.chmod(dump, 0o600)
    SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SOCKET_DIR, 0o700)
    if root:
        os.chown(SOCKET_DIR, UID, GID)
    probe = subprocess.run(
        [sys.executable, "-c", f"p='{DATA}/.schrijftest';open(p,'w').close();import os;os.remove(p)"],
        preexec_fn=demote,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        fail(f"gebruiker {UID} kan niet schrijven in {DATA}. Pas de rechten van de dataset aan (Apps-gebruiker).")


def env_for(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("LIFECYCLE_DATABASE")}
    env.update(
        {
            "HOME": "/tmp",
            "LIFECYCLE_DATA_DIR": str(DATA),
            "LIFECYCLE_DATABASE_URL": DB_URL,
            "LIFECYCLE_PORT": PORT,
            "PYTHONUNBUFFERED": "1",
        }
    )
    with contextlib.suppress(KeyError):
        env["USER"] = pwd.getpwuid(UID).pw_name
    env.update(extra or {})
    return env


def run_once(name: str, cmd: list[str], capture: bool = False) -> subprocess.CompletedProcess[str]:
    log(f"{name}: {' '.join(cmd)}")
    return subprocess.run(
        cmd, env=env_for(), preexec_fn=demote, check=False, text=True, capture_output=capture
    )


class Child:
    def __init__(self, name: str, cmd: list[str], stop_signal: int = signal.SIGTERM) -> None:
        self.name, self.cmd, self.stop_signal = name, cmd, stop_signal
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        log(f"start {self.name}")
        self.proc = subprocess.Popen(self.cmd, env=env_for(), preexec_fn=demote)

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self, timeout: float = 25) -> None:
        if not self.alive():
            return
        assert self.proc is not None
        self.proc.send_signal(self.stop_signal)
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            log(f"{self.name} reageert niet; geforceerd stoppen")
            self.proc.kill()
            self.proc.wait(5)


def init_database() -> None:
    version_file = PGDATA / "PG_VERSION"
    if version_file.exists():
        major = version_file.read_text().strip()
        if major != "16":
            fail(f"database in {PGDATA} is PostgreSQL {major}; deze image gebruikt 16.")
        pid = PGDATA / "postmaster.pid"
        if pid.exists():
            log("oude postmaster.pid van een onderbroken stop verwijderd")
            pid.unlink()
        return
    log("eerste start: database aanmaken")
    rc = run_once(
        "initdb",
        ["initdb", "-D", str(PGDATA), "-U", "lifecycle", "--auth-local=trust", "--auth-host=reject",
         "--encoding=UTF8", "--locale=C.UTF-8", "--data-checksums", "--no-instructions"],
    ).returncode
    if rc != 0:
        fail("initdb mislukt (zie log hierboven)")


def postgres_child() -> Child:
    return Child(
        "database",
        ["postgres", "-D", str(PGDATA), "-c", "listen_addresses=", "-c", f"unix_socket_directories={SOCKET_DIR}",
         "-c", "max_connections=30", "-c", "shared_buffers=32MB", "-c", "jit=off", "-c", "logging_collector=off",
         "-c", "log_min_messages=warning"],
        stop_signal=signal.SIGINT,  # snelle, nette stop
    )


def wait_for_db(pg: Child, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if STOP.is_set():
            return
        if not pg.alive():
            fail("database stopte tijdens het opstarten (zie log hierboven)")
        if run_once("pg_isready", ["pg_isready", "-h", str(SOCKET_DIR), "-U", "lifecycle", "-d", "postgres", "-q"],
                    capture=True).returncode == 0:
            break
        time.sleep(1)
    else:
        fail("database niet klaar binnen 90 s")
    exists = run_once(
        "controle database",
        ["psql", "-h", str(SOCKET_DIR), "-U", "lifecycle", "-d", "postgres", "-tAc",
         "SELECT 1 FROM pg_database WHERE datname='lifecycle'"],
        capture=True,
    )
    if exists.stdout.strip() != "1" and run_once(
        "createdb", ["psql", "-h", str(SOCKET_DIR), "-U", "lifecycle", "-d", "postgres", "-qc",
                     "CREATE DATABASE lifecycle"]
    ).returncode != 0:
        fail("database 'lifecycle' aanmaken mislukt")


def backup_before_migrations() -> None:
    """``pg_dump`` vóór openstaande migraties. Blokkeert de start nooit; een mislukking staat duidelijk in de log."""
    pending = run_once("openstaande migraties", [sys.executable, "-m", "lifecycle", "pending"], capture=True)
    if pending.returncode == 0:
        return
    try:
        info = json.loads(pending.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        info = {"current": None}
    if not info.get("current"):
        return  # lege database: niets te bewaren
    db_size = run_once(
        "databasegrootte",
        ["psql", "-h", str(SOCKET_DIR), "-U", "lifecycle", "-d", "lifecycle", "-tAc",
         "SELECT pg_database_size('lifecycle')"],
        capture=True,
    )
    try:
        needed = int(db_size.stdout.strip() or "0") * 2
    except ValueError:
        needed = 0
    if shutil.disk_usage(BACKUPS).free < needed + 50 * 1024 * 1024:
        log("te weinig vrije ruimte voor de back-up vóór de upgrade; overgeslagen (maak een ZFS-snapshot)")
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = BACKUPS / f"voor-upgrade-{info.get('current')}-{stamp}.dump"
    result = run_once(
        "back-up vóór upgrade",
        ["pg_dump", "-h", str(SOCKET_DIR), "-U", "lifecycle", "-d", "lifecycle", "-Fc", "-f", str(target)],
    )
    if result.returncode != 0:
        log("back-up vóór upgrade mislukt; de migratie gaat door (transactioneel, zie log)")
        target.unlink(missing_ok=True)
        return
    dumps = sorted(BACKUPS.glob("voor-upgrade-*.dump"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in dumps[KEEP_BACKUPS:]:
        old.unlink(missing_ok=True)
    log(f"back-up vóór upgrade: {target.name}")


def shutdown(signum: int, _frame: object) -> None:
    log(f"signaal {signum}: netjes stoppen")
    STOP.set()


def main() -> None:
    signal.signal(signal.SIGTERM, shutdown)  # PID 1: zonder eigen afhandeling negeert de kernel SIGTERM
    signal.signal(signal.SIGINT, shutdown)
    log(f"lifecycle-dienst (alles-in-één) · data {DATA} · poort {PORT} · pid {os.getpid()}")
    prepare_dirs()
    demote()  # vanaf hier draait niets meer als root, ook deze bewaker (PID 1) niet
    verify_privileges()
    if run_once("eerste-start-initialisatie", [sys.executable, "-m", "lifecycle", "init"]).returncode != 0:
        fail("geheimen aanmaken mislukt (zie log hierboven)")
    init_database()
    pg = postgres_child()
    pg.start()
    wait_for_db(pg)
    if STOP.is_set():
        pg.stop(60)
        log("gestopt tijdens het opstarten")
        return
    backup_before_migrations()
    if run_once("migraties", [sys.executable, "-m", "lifecycle", "migrate"]).returncode != 0:
        pg.stop(60)
        fail("databasemigratie mislukt; de database staat ongewijzigd op de vorige versie. Zet de vorige image-tag "
             "terug (Edit → Tag) of herstel de back-up uit /appdata/backups.")

    py = sys.executable
    children = [
        Child("api", [py, "-m", "lifecycle", "api"]),
        Child("worker", [py, "-m", "lifecycle", "worker"]),
    ]
    for c in children:
        c.start()

    backoff: dict[str, float] = {}
    while not STOP.is_set():
        reap_orphans({c.proc.pid for c in [pg, *children] if c.proc is not None})
        if not pg.alive():
            log("database gestopt; opnieuw starten")
            for c in children:
                c.stop()
            init_database()
            pg.start()
            wait_for_db(pg)
            for c in children:
                c.start()
        for c in children:
            if not c.alive() and not STOP.is_set():
                wait = min(60.0, backoff.get(c.name, 1.0))
                log(f"{c.name} gestopt (code {c.proc.returncode if c.proc else '?'}); herstart over {wait:.0f} s")
                STOP.wait(wait)
                backoff[c.name] = wait * 2
                if not STOP.is_set():
                    c.start()
            elif c.alive():
                backoff[c.name] = max(1.0, backoff.get(c.name, 1.0) * 0.9)
        STOP.wait(2)

    for c in children:  # api → worker
        c.stop()
    pg.stop(60)
    log("gestopt")


if __name__ == "__main__":
    try:
        pwd.getpwuid(UID)
        grp.getgrgid(GID)
    except KeyError:
        fail(f"gebruiker {UID}:{GID} bestaat niet in de image")
    if shutil.which("postgres") is None:
        fail("postgres ontbreekt in de image")
    main()
