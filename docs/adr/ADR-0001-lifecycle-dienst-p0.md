# ADR-0001 — Lifecycle-dienst als zelfstandige API-dienst; P0 zonder uitvoering

- Status: aanvaard (P0) · Datum: 2026-11-09 · Contract: 1.1 §1, §2, §6, §10

## Context
jelly-leon-platform is eigenaar van installatie, updates, rollback en verwijderen/verhuizen van het platform
(B08-003, B08-004, B08-006). Alle schermen staan in de Hub; uitvoering op de NAS loopt uitsluitend via de executor
met een door de Hub ondertekend `ExecutionRequest` (contract §8). In P0 bestaan Hub, policy en executor nog niet.

## Besluit
- Python 3.12 + FastAPI + SQLAlchemy 2 + Alembic + eigen PostgreSQL 16 (ingebouwd, unix-socket), pakket `lifecycle`,
  poort 30440, Custom App-naam `jelly-leon-platform`; alleen een kleine NL-statuspagina op `GET /`.
- P0 levert: datamodel en regels van de statusmachine, API `/lifecycle/v1`, connectorcontract `/connector/v1`
  (alleen `read`), auth, enrollment, outbox/events, audit, idempotency, health.
- Alles wat uitvoering vergt (plannen/preview, update, rollback, uninstall, move, connector propose/apply/rollback)
  antwoordt **`501 capability_unavailable`** met `planned_phase: P2`. Er wordt in P0 niets op de NAS uitgevoerd en
  niets gesimuleerd als geslaagd.
- De dienst praat **nooit** zelf met de TrueNAS-API; vanaf P2 vraagt hij de executor (via de Hub) om uitvoering.

## Gevolgen
- Hub-UI kan nu al runs tonen en het connectorcontract gebruiken; functionele B08-taken blijven `Bezig`.
- Geen dubbele uitvoeringslaag naast de executor (minder aanvalsoppervlak).
