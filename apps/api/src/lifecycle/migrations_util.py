"""Alembic programmatisch (migraties zitten ín het pakket, zie ADR-0002): upgrade/downgrade/status."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(database_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def head_revision() -> str | None:
    script = ScriptDirectory(str(MIGRATIONS_DIR))
    return script.get_current_head()


def migration_status(engine: Engine) -> tuple[str | None, str | None]:
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return current, head_revision()


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)
