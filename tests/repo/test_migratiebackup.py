"""Een onzekere migratiestatus of onbruikbare dump moet de procesbewaker vóór migrate stoppen."""

import json
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from deploy.allinone import supervisor


@pytest.mark.parametrize("fout", ["status", "ongeldige_status", "onbekende_revisie",
                                  "grootte", "ruimte", "dump", "archief"])
def test_start_stopt_voor_migratie_bij_ontbrekend_herstelarchief(tmp_path, monkeypatch, fout):
    bestaand = tmp_path / "voor-upgrade-0003-eerder.dump"
    bestaand.write_bytes(b"eerdere herstelkopie")
    pg = Mock()
    for naam in ("prepare_dirs", "demote", "verify_privileges", "init_database", "wait_for_db"):
        monkeypatch.setattr(supervisor, naam, Mock())
    monkeypatch.setattr(supervisor.signal, "signal", Mock())
    monkeypatch.setattr(supervisor, "postgres_child", lambda: pg)
    monkeypatch.setattr(supervisor, "BACKUPS", tmp_path)
    monkeypatch.setenv("LIFECYCLE_FAIL_PAUSE_SECONDS", "0")
    vrij = 0 if fout == "ruimte" else 2**30
    monkeypatch.setattr(supervisor.shutil, "disk_usage", lambda _: SimpleNamespace(free=vrij))
    opdrachten = []

    def voer_uit(naam, cmd, capture=False):
        opdrachten.append(cmd)
        code, tekst = 0, ""
        if cmd[-1] == "pending":
            code = 1 if fout == "status" else 10
            tekst = json.dumps({"current": "0003", "head": "0004"})
            if fout == "ongeldige_status":
                tekst = "?"
            if fout == "onbekende_revisie":
                tekst = json.dumps({"head": "0004"})
        elif cmd[0] == "psql":
            tekst = "?" if fout == "grootte" else "8192"
        elif cmd[0] == "pg_dump":
            Path(cmd[cmd.index("-f") + 1]).write_bytes(b"gedeeltelijke dump")
            code = int(fout == "dump")
        elif cmd[0] == "pg_restore":
            code = int(fout == "archief")
        elif cmd[-1] == "migrate":
            code = 1  # De oude bewaker bereikt hier ten onrechte de migratie.
        return CompletedProcess(cmd, code, tekst, "")

    monkeypatch.setattr(supervisor, "run_once", voer_uit)
    with pytest.raises(SystemExit):
        supervisor.main()
    assert all(cmd[-1] != "migrate" for cmd in opdrachten), "Migratie gestart zonder gecontroleerde dump"
    pg.stop.assert_called_once()
    assert list(tmp_path.iterdir()) == [bestaand]
    assert bestaand.read_bytes() == b"eerdere herstelkopie"


def test_herstel_bindt_aan_precies_een_nieuw_archief():
    from e2e.integral.scenarios.EX15_schema_terugzetten import upgrade_archief

    assert upgrade_archief({"eerder"}, {"eerder", "upgrade"}) == "upgrade"
    for na in ({"eerder"}, {"eerder", "upgrade", "gelijktijdig"}):
        with pytest.raises(AssertionError, match="eenduidig"):
            upgrade_archief({"eerder"}, na)


@pytest.mark.parametrize("fase", ["laden", "activeren"])
def test_gewijzigd_archief_stopt_voor_databasebewerkingen(tmp_path, fase):
    import hashlib
    import subprocess

    from e2e.integral.scenarios.EX15_schema_terugzetten import HERSTEL

    archief = tmp_path / "proef.dump"
    archief.write_bytes(b"eerste archief")
    sha = hashlib.sha256(archief.read_bytes()).hexdigest()
    archief.write_bytes(b"ander archief")
    script = HERSTEL.replace("/appdata", str(tmp_path))
    res = subprocess.run(["sh", "-ec", script, "herstel", fase, str(archief), sha], check=False)
    assert res.returncode == 1
    assert list(tmp_path.iterdir()) == [archief]


@pytest.mark.parametrize(("net_aangemaakt", "dumps"), [(False, 1), (True, 0)])
def test_alleen_een_nieuwe_database_mag_zonder_revisie_en_dump_starten(tmp_path, monkeypatch, net_aangemaakt, dumps):
    opdrachten = []

    def run(naam, cmd, capture=False):
        opdrachten.append(cmd[0])
        if cmd[-1] == "pending":
            return CompletedProcess(cmd, 10, '{"current":null,"head":"0003"}', "")
        if cmd[0] == "pg_dump":
            Path(cmd[cmd.index("-f") + 1]).write_bytes(b"testarchief")
        return CompletedProcess(cmd, 0, "1024", "")

    monkeypatch.setattr(supervisor, "BACKUPS", tmp_path)
    monkeypatch.setattr(supervisor, "run_once", run)
    supervisor.backup_before_migrations(net_aangemaakt=net_aangemaakt)
    assert opdrachten.count("pg_dump") == opdrachten.count("pg_restore") == dumps
    assert len(list(tmp_path.glob("*.dump"))) == dumps


@pytest.mark.parametrize(("bestond", "aangemaakt"), [(True, False), (False, True)])
def test_bewaker_onderscheidt_eerste_start_van_bestaande_database(monkeypatch, bestond, aangemaakt):
    aangeroepen = []

    def run(naam, cmd, capture=False):
        aangeroepen.append(naam)
        return CompletedProcess(cmd, 0, "1" if naam == "controle database" and bestond else "", "")

    monkeypatch.setattr(supervisor, "run_once", run)
    assert supervisor.wait_for_db(Mock()) is aangemaakt
    assert ("createdb" in aangeroepen) is aangemaakt
