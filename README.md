# jelly-leon-platform

Installatie, updates, terugzetten en verwijderen/verhuizen van het **TrueNAS Home Platform V2** (B08-003, B08-004,
B08-006), de installeerbare manifests per `03_INSTALLATIE`-document en de integrale E2E-omgeving.

> **Fase P0.** Deze versie levert de lifecycle-dienst (datamodel, API, connectorcontract), de alles-in-één image, de
> installatiedocumentatie zonder YAML, het TrueNAS-API-onderzoek en de TrueNAS-simulator. Er wordt nog **niets** op
> een NAS uitgevoerd: uitvoering volgt in P2 en loopt dan uitsluitend via de executor van de Hub. Op een echte
> TrueNAS is niets gevalideerd (**NIET GEVALIDEERD**).

## Voor de gebruiker

| Wat | Waar |
|---|---|
| Eerste installatie van het platform (twee Custom Apps via de TrueNAS-web-UI, geen terminal, geen YAML) | [`install/truenas/INSTALLATIE.md`](install/truenas/INSTALLATIE.md) |
| Deze dienst als standaard Custom App (formuliervelden) | [`deploy/truenas/CUSTOM_APP.md`](deploy/truenas/CUSTOM_APP.md) |
| Overzicht installatiemethode per I-document (I01–I26) | [`install/manifests/README.md`](install/manifests/README.md) |

De dienst heeft alleen een kleine Nederlandse statuspagina op `http://<NAS-adres>:30440/`; alle schermen staan in
de Hub (`ai-server-manager`).

## Voor ontwikkelaars

```
apps/api/                 pakket 'lifecycle' (FastAPI, SQLAlchemy 2, Alembic-migraties in het pakket), tests
deploy/allinone/          Dockerfile + procesbewaker + healthcheck (PostgreSQL 16 ingebouwd, unix-socket)
deploy/truenas/           CUSTOM_APP.md (formulier)        deploy/manifest/  manifest (contract §9)
deploy/dev/compose.yaml   alleen ontwikkeling              .env.example      alle variabelen
docs/openapi/             geëxporteerde OpenAPI            docs/adr/         ADR-0001…0009
docs/platform-v2/         pakketdocs, TRUENAS_APP_API.md (onderzoek), EVIDENCE.json
e2e/                      integrale E2E-opzet (README) + simulators (truenas, hub)
tests/e2e/                image-E2E (Docker)               tests/repo/       repo-regels (manifest, docs, evidence)
```

```bash
make venv && make lint typecheck test test-sim test-repo   # PostgreSQL op 127.0.0.1:5432 (LIFECYCLE_TEST_PG)
make test-e2e                                              # bouwt de image en draait de Docker-E2E
```

De Docker-E2E heeft geen root op de host nodig (bestanden van uid 568 worden via een wegwerpcontainer gelezen en
opgeruimd). Op een gedeelde machine serialiseert `LIFECYCLE_BUILD_LOCK=<lockbestand>` de extra image-builds van de
upgrade-E2E met `flock`.

Bindend: `docs/platform-v2/PLATFORM_CONTRACTEN.md` (versie 1.1). Geen licentie toegevoegd (keuze van de eigenaar).
