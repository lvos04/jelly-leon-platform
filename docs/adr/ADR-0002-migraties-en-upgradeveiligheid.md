# ADR-0002 — Migraties in het pakket, één transactie, pg_dump vóór migratie

- Status: aanvaard · Datum: 2026-11-09 · Contract: §10 (apps/api/alembic) · Excel: B08-004

## Context
Contract §10 noemt `apps/api/alembic/`. De alles-in-één image installeert de code als pakket; migraties buiten het
pakket zouden apart gekopieerd moeten worden en kunnen uit de pas lopen met de code. B08-004 eist: "DB-migratiefout
laat oude app met data intact".

## Besluit
- Migraties staan in het pakket: `apps/api/src/lifecycle/migrations/` (Alembic `script_location` programmatisch,
  zie `migrations_util.py`). **Afwijking van het pad in §10, niet van het doel** (gemeld als contract-issue).
- `env.py` draait alle migraties van één upgrade in **één transactie** (PostgreSQL ondersteunt transactionele DDL):
  mislukt er één, dan blijft het schema exact op de vorige revisie, met data. Bewezen in
  `tests/integration/test_migrations.py::test_failed_migration_leaves_old_schema_and_data`.
- De procesbewaker maakt bij openstaande migraties eerst een `pg_dump -Fc` in `/appdata/backups`
  (`voor-upgrade-<rev>-<tijd>.dump`, nieuwste 3 blijven; overgeslagen bij te weinig ruimte, met logregel) en start
  de API niet als de migratie faalt.
- CI: upgrade → downgrade base → upgrade, plus drift-controle modellen ↔ migraties.

## Gevolgen
- Terugzetten naar een oudere image-tag ná een geslaagde migratie is niet veilig (`tag_only_rollback_safe: false`
  in het manifest); eerst dump/snapshot herstellen (EX15).
