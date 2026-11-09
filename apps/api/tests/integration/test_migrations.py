"""Migraties: upgrade/downgrade/upgrade, geen drift tussen modellen en migraties, en een mislukte migratie laat de
database exact op de vorige revisie met data intact (B08-004, eigen app)."""

from __future__ import annotations

import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

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
    # kopie van de migratiemap met twee nieuwe revisies: 0002 slaagt (kolom erbij), 0003 faalt halverwege
    scripts = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    (scripts / "versions" / "0002_ok.py").write_text(
        'revision = "0002"\ndown_revision = "0001"\nbranch_labels = None\ndepends_on = None\n'
        "import sqlalchemy as sa\nfrom alembic import op\n"
        "def upgrade():\n    op.add_column('lifecycle_runs', sa.Column('nieuw', sa.Text(), nullable=True))\n"
        "def downgrade():\n    op.drop_column('lifecycle_runs', 'nieuw')\n"
    )
    (scripts / "versions" / "0003_faalt.py").write_text(
        'revision = "0003"\ndown_revision = "0002"\nbranch_labels = None\ndepends_on = None\n'
        "from alembic import op\n"
        "def upgrade():\n    op.execute('DELETE FROM lifecycle_runs')\n    op.execute('SELECT 1/0')\n"
        "def downgrade():\n    pass\n"
    )
    cfg = alembic_config(fresh_db)
    cfg.set_main_option("script_location", str(scripts))
    with pytest.raises(Exception, match="division by zero"):
        command.upgrade(cfg, "head")
    assert _rev(fresh_db) == "0001"  # niets half toegepast
    with engine.connect() as conn:
        cols = [c["name"] for c in sa.inspect(conn).get_columns("lifecycle_runs")]
        assert "nieuw" not in cols
        assert conn.execute(sa.text("SELECT count(*) FROM lifecycle_runs")).scalar_one() == 1
    engine.dispose()
