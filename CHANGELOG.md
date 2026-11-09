# Changelog

## 0.1.0 — nog niet uitgebracht (fase P0)

- Lifecycle-dienst (`lifecycle`): FastAPI + SQLAlchemy 2 + Alembic + eigen PostgreSQL 16; statusmachine als
  datamodel; `/lifecycle/v1` en connectorcontract; uitvoering (installeren/bijwerken/terugzetten/verwijderen/
  verhuizen) geeft eerlijk `capability_unavailable` tot P2.
- Auth (Hub-JWT via JWKS, service-token), enrollment (eenmalig token, koppelcode op de statuspagina), outbox.
- Alles-in-één image (standaard TrueNAS Custom App), Custom App-formulierdocumentatie, manifest, CI en release.
- Eerste installatie zonder YAML (`install/truenas/INSTALLATIE.md`), TrueNAS-API-onderzoek, TrueNAS-simulator,
  opzet integrale E2E-omgeving.
- Op echte TrueNAS: **NIET GEVALIDEERD**.
