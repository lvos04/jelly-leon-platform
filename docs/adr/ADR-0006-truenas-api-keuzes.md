# ADR-0006 — TrueNAS-API: standaard Custom App via `ix-app`, versiepinning en terugzetten

- Status: aanvaard voor P2-ontwerp · Datum: 2026-11-09 · Bron: `docs/platform-v2/TRUENAS_APP_API.md`

## Besluit
1. Eigen apps: `app.create({app_name, catalog_app:"ix-app", train:"stable", version:<exacte ix-app-versie>,
   values})`; image-tag altijd exact (het formulier en de API vullen anders `latest`); `restart_policy`
   expliciet `unless-stopped`; nooit `custom_app=true`/`custom_compose_config(_string)`.
2. Updates eigen apps: `app.update` met het **volledige** `image`-object (ondiepe merge in TrueNAS), vooraf snapshot
   van de appdata-dataset, achteraf digest-readback (`app.image.query`); afwijking → stop + incident.
3. Terugzetten eigen apps: `app.update` met de vorige waarden; `app.rollback` werkt alleen tussen
   catalogusversies en dus niet voor image-tags. Na een schemamigratie eerst data herstellen (R3).
4. Catalogusapps (meerdere containers): trein en catalogusversie expliciet; staat de vastgezette versie niet
   (meer) in de catalogus → blokkade `version_unavailable` + manifest-update voorstellen, nooit `latest`.
5. Verwijderen: altijd `remove_ix_volumes=false`; data-verwijdering wordt niet aangeboden.
6. Snapshots per versie ontdekken: 25.04 `zfs.snapshot.*`, 25.10 `pool.snapshot.*` (via `core.get_methods`).
7. Schrijfkey alleen over `wss://` met certificaatcontrole: TrueNAS trekt API-keys in die over een onversleutelde
   verbinding worden gebruikt.

## Gevolgen
- B08-003 preview moet poorten (`app.used_ports`), datasets, versiebeschikbaarheid en afhankelijkheden controleren
  en blokkeren bij conflict. De TrueNAS-simulator dwingt punt 1, 2 (ondiepe merge), 3 en 5 af of registreert
  overtredingen.
