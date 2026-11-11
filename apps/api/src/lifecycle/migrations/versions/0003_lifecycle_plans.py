"""plannen voor installeren, bijwerken en terugzetten (B08-003, B08-004)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-11

Nieuwe tabel ``lifecycle_plans``: het onveranderlijke plandocument (diff, blokkades, stappen) en de voortgang per stap.
Bestaande tabellen en gegevens blijven ongemoeid. Terugzetten (downgrade) verwijdert alleen deze tabel.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lifecycle_plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("soort", sa.String(length=16), nullable=False),
        sa.Column("app_id", sa.String(length=40), nullable=True),
        sa.Column("app_name", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("preview_hash", sa.String(length=80), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=80), nullable=True),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("stappen", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("created_by_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("soort IN ('install', 'update', 'rollback')", name=op.f("ck_lifecycle_plans_soort_valid")),
        sa.CheckConstraint(
            "status IN ('geblokkeerd', 'klaar_voor_voorstel', 'in_uitvoering', 'voltooid', 'mislukt')",
            name=op.f("ck_lifecycle_plans_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lifecycle_plans")),
    )
    op.create_index(op.f("ix_lifecycle_plans_app_id"), "lifecycle_plans", ["app_id"], unique=False)
    op.create_index(op.f("ix_lifecycle_plans_created_at"), "lifecycle_plans", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_lifecycle_plans_created_at"), table_name="lifecycle_plans")
    op.drop_index(op.f("ix_lifecycle_plans_app_id"), table_name="lifecycle_plans")
    op.drop_table("lifecycle_plans")
