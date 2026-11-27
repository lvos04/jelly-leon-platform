"""Sta verwijderplannen toe in de bestaande plantabel. Geen enkele rij wordt gewijzigd."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _soorten(voorwaarde: str) -> None:
    naam = op.f("ck_lifecycle_plans_soort_valid")
    op.drop_constraint(naam, "lifecycle_plans", type_="check")
    op.create_check_constraint(naam, "lifecycle_plans", voorwaarde)


def upgrade() -> None:
    _soorten("soort IN ('install', 'update', 'rollback', 'uninstall')")


def downgrade() -> None:
    # PostgreSQL weigert de volledige transactie als er al verwijderplannen zijn.
    _soorten("soort IN ('install', 'update', 'rollback')")
