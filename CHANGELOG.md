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

### Review P0 (onafhankelijke QA)

- **Beveiliging:** JWT's alleen nog met sleutels van de Hub waarmee de koppeling geslaagd is; een mislukte
  koppelpoging met een vreemd Hub-adres gaf eerder admin-toegang, ontkoppelen liet oude sleutels geldig (ADR-0009).
- Evidence en redenen van overgangen worden geredigeerd opgeslagen; evidence begrensd op 16 KiB.
- TrueNAS-simulator: read-only key ziet/stopt geen jobs van de executor meer (zoals middlewared); mislukte
  `app.create` laat geen spookapp of ixVolume meer achter.
- CI: `ci.yml` was ongeldige YAML (zou niet starten); gerepareerd, actionlint + shellcheck toegevoegd.
- Image-E2E: Docker-healthcheck moet `healthy` worden; herstart en harde kill zonder verlies van runs/koppeling.
- `INSTALLATIE.md`: controle op een openbaar GHCR-pakket gecorrigeerd (privé/onbekend = `DENIED`, HTTP 403).
