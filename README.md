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

De dienst heeft alleen een kleine Nederlandse statuspagina op `http://<NAS-adres>:30440/` in de ontwerptaal **Peil**
(framework-vrij sjabloon uit `ai-server-manager`, geen JavaScript); alle schermen staan in de Hub (`ai-server-manager`).
Schermafdrukken: [`docs/screenshots/peil/statuspagina/`](docs/screenshots/peil/statuspagina/).

## Voor ontwikkelaars

```
apps/api/                 pakket 'lifecycle' (FastAPI, SQLAlchemy 2, Alembic-migraties in het pakket), tests
deploy/allinone/          Dockerfile + procesbewaker + healthcheck (PostgreSQL 16 ingebouwd, unix-socket)
deploy/truenas/           CUSTOM_APP.md (formulier)        deploy/manifest/  manifest (contract §9)
deploy/dev/compose.yaml   alleen ontwikkeling              .env.example      alle variabelen
docs/openapi/             geëxporteerde OpenAPI            docs/adr/         ADR-0001…0013
design/ontwerptaal/       versie-gepinde kopie Peil-statuspagina (HERKOMST.md)   tools/peil_kopie.py  kopie maken/controleren
tests/ui/                 browsercontrole statuspagina (Playwright + axe-core)   docs/screenshots/peil/  schermafdrukken
docs/platform-v2/         contract 1.2 + conformiteitslijst, pakketdocs, TRUENAS_APP_API.md, EVIDENCE.json
e2e/                      integrale E2E-opzet (README), simulators (truenas, hub), conformance/echte_hub.py
tests/e2e/                image-E2E (Docker)               tests/repo/       repo-regels (manifest, docs, evidence)
tests/conformance/        Hub ↔ dienst volgens contract 1.2 (simulator in CI; lokaal ook tegen de echte Hub)
```

```bash
make venv && make lint typecheck test test-sim test-repo test-conformance   # PostgreSQL op 127.0.0.1:5432
make test-e2e                                              # bouwt de image en draait de Docker-E2E
make test-ui                                               # statuspagina per toestand in Chromium (axe, 320–1440 px)
make statuspagina-shots                                    # idem + schermafdrukken naar docs/screenshots/peil/
.venv/bin/python e2e/conformance/echte_hub.py              # zelfde conformiteitstests tegen de echte Hub (naburige repo)
```

De Docker-E2E heeft geen root op de host nodig (bestanden van uid 568 worden via een wegwerpcontainer gelezen en
opgeruimd). Op een gedeelde machine serialiseert `LIFECYCLE_BUILD_LOCK=<lockbestand>` de extra image-builds van de
upgrade-E2E met `flock`.

Koppelen met de Hub (contract 1.2): de koppeling staat in `/appdata/secrets/hub-link.json` (niet in de database), de
Hub-sleutels worden bij het koppelen gepind en met een vingerafdruk gecontroleerd, en de Hub meldt zich bij deze dienst
met een door hem ondertekende dienst-JWT (ADR-0011). Elk proces in de image, ook PID 1, draait als 568 zonder
capabilities en met *no_new_privs*.

Bindend: `docs/platform-v2/PLATFORM_CONTRACTEN.md` (`contracts 1.2`; werklijst `CONTRACT_1.2_CONFORMITEIT.md`).
Licentie: **GPL-3.0** (zie [`LICENSE`](LICENSE); besluit van de eigenaar, 2026-11-09).
