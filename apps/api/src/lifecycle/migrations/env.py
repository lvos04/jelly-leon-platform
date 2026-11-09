"""Alembic-omgeving. Alle migraties van één upgrade draaien in één transactie (PostgreSQL ondersteunt
transactionele DDL): mislukt er één, dan blijft het schema exact op de vorige revisie (B08-004)."""

from __future__ import annotations

from sqlalchemy import engine_from_config, pool

from alembic import context
from lifecycle.db import normalize_url
from lifecycle.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=normalize_url(config.get_main_option("sqlalchemy.url") or ""),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = normalize_url(section.get("sqlalchemy.url", ""))
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            transaction_per_migration=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
