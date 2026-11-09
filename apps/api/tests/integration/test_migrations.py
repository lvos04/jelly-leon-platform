"""Migraties: upgrade/downgrade/upgrade, geen drift tussen modellen en migraties, en een mislukte migratie laat de
database exact op de vorige revisie met data intact (B08-004, eigen app)."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from lifecycle.migrations_util import MIGRATIONS_DIR, alembic_config, downgrade, head_revision, upgrade
from lifecycle.models import Base

PG_ADMIN = "postgresql+psycopg://postgres@127.0.0.1:5432/postgres"


@pytest.fixture
def fresh_db(pg_url: str) -> Iterator[str]:
    base = pg_url.rsplit("/", 1)[0]
    name = f"jlp_mig_{uuid.uuid4().hex[:8]}"
    admin = sa.create_engine(base + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    yield f"{base}/{name}"
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _rev(url: str) -> str | None:
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        rev = MigrationContext.configure(conn).get_current_revision()
    engine.dispose()
    return rev


def test_up_down_up(fresh_db: str) -> None:
    upgrade(fresh_db)
    assert _rev(fresh_db) == head_revision()
    downgrade(fresh_db, "base")
    assert _rev(fresh_db) is None
    engine = sa.create_engine(fresh_db)
    assert sa.inspect(engine).get_table_names() == ["alembic_version"]
    engine.dispose()
    upgrade(fresh_db)
    assert _rev(fresh_db) == head_revision()


def test_models_match_migrations(fresh_db: str) -> None:
    upgrade(fresh_db)
    engine = sa.create_engine(fresh_db)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    engine.dispose()
    assert diff == [], f"drift tussen modellen en migraties: {diff}"


def test_failed_migration_leaves_old_schema_and_data(fresh_db: str, tmp_path: Path) -> None:
    upgrade(fresh_db)
    engine = sa.create_engine(fresh_db)
    run_id = uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO lifecycle_runs (id, app_id, kind, state, requested_by_type, requested_by_id, trace_id, "
                "evidence, version, created_at, updated_at) VALUES (:id, 'immich', 'update', 'discovered', 'user', "
                "'u', :t, '{}'::jsonb, 1, now(), now())"
            ),
            {"id": run_id, "t": uuid.uuid4()},
        )
    # kopie van de migratiemap met twee nieuwe revisies na de huidige kop: de eerste slaagt (kolom erbij), de tweede
    # faalt halverwege
    head = head_revision()
    scripts = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "versions" / "9001_ok.py").write_text(
        f'revision = "9001"\ndown_revision = "{head}"\nbranch_labels = None\ndepends_on = None\n'
        "import sqlalchemy as sa\nfrom alembic import op\n"
        "def upgrade():\n    op.add_column('lifecycle_runs', sa.Column('nieuw', sa.Text(), nullable=True))\n"
        "def downgrade():\n    op.drop_column('lifecycle_runs', 'nieuw')\n"
    )
    (scripts / "versions" / "9002_faalt.py").write_text(
        'revision = "9002"\ndown_revision = "9001"\nbranch_labels = None\ndepends_on = None\n'
        "from alembic import op\n"
        "def upgrade():\n    op.execute('DELETE FROM lifecycle_runs')\n    op.execute('SELECT 1/0')\n"
        "def downgrade():\n    pass\n"
    )
    cfg = alembic_config(fresh_db)
    cfg.set_main_option("script_location", str(scripts))
    with pytest.raises(Exception, match="division by zero"):
        command.upgrade(cfg, "head")
    assert _rev(fresh_db) == head  # niets half toegepast
    with engine.connect() as conn:
        cols = [c["name"] for c in sa.inspect(conn).get_columns("lifecycle_runs")]
        assert "nieuw" not in cols
        assert conn.execute(sa.text("SELECT count(*) FROM lifecycle_runs")).scalar_one() == 1
    engine.dispose()


def _p0_row(url: str, vault_dir: Path, **values: Any) -> None:
    """Een rij in de oude tabel ``hub_link`` zoals P0 (0.1.0 vóór 1.2) die schreef."""
    from lifecycle.secrets_store import Vault

    engine = sa.create_engine(url)
    row = {"id": 1, "state": "gekoppeld", "hub_url": "http://hub.example:30400", "service_id": "svc-1",
           "service_token_last4": None, "service_token_sha256": None, "linked_via": "token", "attempts": 0,
           "enrolled_at": sa.func.now(), "updated_at": sa.func.now(), "jwks": None, "policy_public_key": None,
           "service_token_enc": None, "used_enrollment_token_sha256": "c" * 64}
    row.update(values)
    if isinstance(row.get("service_token_enc"), str):
        row["service_token_enc"] = Vault.load(vault_dir).encrypt(row["service_token_enc"], "hub_link.service_token")
    table = sa.Table("hub_link", sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(table).values(**row))
    engine.dispose()


def test_p0_link_is_transferred_to_hub_link_json_once(fresh_db: str, data_dir: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    from hub_sim import HubState

    from lifecycle.hublink import HubLinkStore

    sim = HubState()
    token = "svc_" + "Z" * 43
    upgrade(fresh_db, "0001")
    _p0_row(fresh_db, data_dir / "secrets", service_token_enc=token, jwks=sim.jwks(),
            policy_public_key=sim.policy_jwk()["x"])
    monkeypatch.setenv("LIFECYCLE_DATA_DIR", str(data_dir))
    upgrade(fresh_db)
    link = HubLinkStore(data_dir / "secrets").read()
    assert link.linked and link.service_token == token and link.hub_url == "http://hub.example:30400"
    assert link.keyset_fingerprint == sim.keyset_fingerprint()  # zelfde sleutels → zelfde vingerafdruk
    assert link.doc["confirmed_at"] is None and link.doc["first_linked_at"]  # worker bevestigt via services/self
    assert link.used_tokens == ["c" * 64]
    engine = sa.create_engine(fresh_db)
    tables = sa.inspect(engine).get_table_names()
    engine.dispose()
    assert "hub_link" not in tables and "jwt_replay" in tables
    # terug en weer op: het bestaande bestand wordt nooit overschreven
    downgrade(fresh_db, "0001")
    _p0_row(fresh_db, data_dir / "secrets", service_token_enc="svc_" + "Y" * 43, jwks=sim.jwks(),
            policy_public_key=sim.policy_jwk()["x"])
    upgrade(fresh_db)
    assert HubLinkStore(data_dir / "secrets").read().service_token == token


def test_incomplete_p0_link_becomes_unlinked_fail_closed(fresh_db: str, data_dir: Path,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    from lifecycle.hublink import HubLinkStore

    upgrade(fresh_db, "0001")
    _p0_row(fresh_db, data_dir / "secrets", service_token_enc="svc_" + "Z" * 43)  # P0 kreeg geen JWKS mee
    monkeypatch.setenv("LIFECYCLE_DATA_DIR", str(data_dir))
    upgrade(fresh_db)
    link = HubLinkStore(data_dir / "secrets").read()
    assert link.state == "ontkoppeld" and link.service_token is None and link.ever_linked
    assert link.doc["last_error"]["code"] == "overzetten_onvolledig"
