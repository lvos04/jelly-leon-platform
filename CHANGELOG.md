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

### P0 afgemaakt (CI op GitHub Actions)

- Ruff: importvolgorde hing af van de werkmap (`alembic` soms als first-party gezien); nu vast in
  `[tool.ruff.lint.isort]` (`known-first-party` = `lifecycle`, `truenas_sim`; `known-third-party` = `alembic`).
- Image-E2E zonder `sudo`: eigenaarschap, sleutelcontrole, `chown` en opruimen van 568-bestanden via een
  wegwerpcontainer; `ci.yml` en `release.yml` draaien de image-E2E niet meer als root.
- TrueNAS-simulator: `smart.test.results` alleen nog in het 25.04-profiel (25.10 heeft geen publieke
  SMART-resultatenmethode volgens api.truenas.com/v25.10 en de broncode TS-25.10.8).
- `TRUENAS_APP_API.md`: controle tegen de officiële API-documentatie (nu bereikbaar) toegevoegd.
- GitHub Actions: eerste groene run (42619820171, jobs `test` en `image`). Release/GHCR-push nog niet uitgevoerd.

### Review P0-afronding (onafhankelijke QA)

- **Enrollment tegen de echte Hub:** het verzoek bevatte velden (`kind`, `capabilities`, `schema_version`, JWK
  `use`/`alg`) die de Hub (`EnrollIn`, `extra="forbid"`) en contract 1.2 §3.3 met 422 weigeren; de client zag dat
  als definitief en verbrandde het eenmalige token. Nu alleen de toegestane velden (`kid` = RFC 7638-thumbprint);
  de Hub-simulator is even streng als de Hub (ADR-0004 herzien, ADR-0010).
- **Verzoekgrootte:** bodies > 1 MiB → `413 payload_too_large` (ook chunked, vóór de authenticatie).
- **Release:** publiceren alleen bij een tag; een bestaande versie-tag in GHCR wordt nooit overschreven
  (`workflow_dispatch` op een branch pushte eerder de versie-tag).
- `target_version`: ook `repo:latest` geweigerd; versievelden met vast patroon.
- Image: `/appdata/backups` 0700 en dumps 0600 (umask 077); geen Alembic-regels meer per healthcheck in de log.
- Koppelpagina: snelheidsbegrenzing per app-instantie, koppelcode max. 32 tekens; extra negatieve tests (429, 409,
  token van een andere app, bestaande data in `/appdata` blijft onaangeroerd).
- GitHub Actions: checkout/setup-python/setup-buildx op Node 24-versies.

### Contract 1.2 toegepast (ADR-0011)

- **Koppeling** in `/appdata/secrets/hub-link.json` (0600, atomair, vergrendeld, fail-closed bij beschadiging) in
  plaats van de tabel `hub_link`; migratie 0002 zet een P0-koppeling eenmalig over.
- **Enrollment** exact volgens §3.3: volledige antwoordcontrole, sleutelset-vingerafdruk (`HUB_KEY_FINGERPRINT` of
  ingevoerd op de statuspagina), toegestane herhaling na een herstart, bevestigen met `services/self`, backoff 5 s →
  15 min, `Retry-After`, stoppen na 60 min, nooit redirects of proxy's. Intrekking pas na 401 op `services/self`;
  rotatie van het service-token bij `Hub-Token-Rotation: required`.
- **Inkomend** alleen Hub-JWT's met gepinde sleutels (`token_use` user/hub_service, `jti` verplicht met
  replaycache, vaste `reason`-tabel); het eigen service-token wordt niet meer geaccepteerd. Ondertekende
  sleutelrotatie (poll ≤ 15 min, onbekende `kid` → achtergrondpoll).
- **Statuspagina:** koppelformulier alleen zolang nooit gekoppeld, verplichte vingerafdruk, CSRF + `Origin`,
  5/15 min per bron-IP en 20/15 min dienstbreed.
- **Connector:** `/connector/v1/hub-link` en `/connector/v1/hub-link/unlink` (dienst-JWT of admin) vervangen de
  `/lifecycle/v1/hub-link`-routes.
- **Image:** geen `dumb-init` meer; de procesbewaker is PID 1 en elk proces draait als 568 zonder capabilities met
  *no_new_privs* (gecontroleerd per proces in de image-E2E, zonder `--security-opt`/`--cap-drop`).
- **Manifest** volgens §9 (geen `joins_platform_network`/`custom_app.ports`, geen `HUB_*` in het manifest).
- **Lifecycle-regels** (P2-bouwstenen): tag met digest + readback, terugzetten via `app.update` met vorige waarden,
  `version_unavailable`; TrueNAS-simulator met één catalogusversie per trein.
- **Conformiteitstests** Hub ↔ dienst (simulator in CI; lokaal tegen de echte Hub `ai-server-manager` `e17fdef`:
  16 geslaagd). Documentatie: `CUSTOM_APP.md` en `INSTALLATIE.md` (publieke image standaard, registry-login alleen
  terugval, tag met digest, executor koppelen met vingerafdruk).
- Op een echte TrueNAS: **NIET GEVALIDEERD**. Niet gedaan: statuspagina-CSS uit de ontwerptaal (JP11), die bestaat
  nog niet.
