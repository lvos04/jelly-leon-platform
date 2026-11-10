# jelly-leon-platform

Installatie, updates, terugzetten en verwijderen/verhuizen van het **TrueNAS Home Platform V2** (B08-003, B08-004,
B08-006), de installeerbare manifests per `03_INSTALLATIE`-document en de integrale E2E-omgeving.

> **Fase P1.** P0 leverde de lifecycle-dienst (datamodel, API, connectorcontract), de alles-in-één image, de
> installatiedocumentatie zonder YAML, het TrueNAS-API-onderzoek en de TrueNAS-simulator. P1 voegt de installeerbare
> manifesten (I07, I08, I09, I13, I23), de controlelijsten I01/I02, simulators van Beszel, Uptime Kuma, Gotify en
> Authentik en een installatie-rooktest zonder YAML toe (ADR-0014…0016). Er wordt nog **niets** op een NAS
> uitgevoerd: uitvoering volgt in P2 en loopt dan uitsluitend via de executor van de Hub. Op een echte TrueNAS is
> niets gevalideerd (**NIET GEVALIDEERD**).

## Voor de gebruiker

| Wat | Waar |
|---|---|
| Eerste installatie van het platform (twee Custom Apps via de TrueNAS-web-UI, geen terminal, geen YAML) | [`install/truenas/INSTALLATIE.md`](install/truenas/INSTALLATIE.md) |
| Deze dienst als standaard Custom App (formuliervelden) | [`deploy/truenas/CUSTOM_APP.md`](deploy/truenas/CUSTOM_APP.md) |
| Overzicht installatiemethode per I-document (I01–I26) en de manifesten van P1 (Beszel, Uptime Kuma, Gotify, Authentik, Hub/executor) | [`install/manifests/README.md`](install/manifests/README.md) |
| Controlelijsten I01 (TrueNAS-basis) en I02 (back-up en herstel) | [`install/checklists/`](install/checklists/) |

De dienst heeft alleen een kleine Nederlandse statuspagina op `http://<NAS-adres>:30440/` in de ontwerptaal **Peil**
(framework-vrij sjabloon uit `ai-server-manager`, geen JavaScript); alle schermen staan in de Hub (`ai-server-manager`).
Schermafdrukken: [`docs/screenshots/peil/statuspagina/`](docs/screenshots/peil/statuspagina/).

## Voor ontwikkelaars

```
apps/api/                 pakket 'lifecycle' (FastAPI, SQLAlchemy 2, Alembic-migraties in het pakket), tests
deploy/allinone/          Dockerfile + procesbewaker + healthcheck (PostgreSQL 16 ingebouwd, unix-socket)
deploy/truenas/           CUSTOM_APP.md (formulier)        deploy/manifest/  manifest (contract §9)
deploy/dev/compose.yaml   alleen ontwikkeling              .env.example      alle variabelen
docs/openapi/             geëxporteerde OpenAPI            docs/adr/         ADR-0001…0019
design/ontwerptaal/       gepinde kopie Peil 1.1.1-statuspagina (HERKOMST.md)    tools/peil_kopie.py  kopie maken/controleren
tests/ui/                 browsercontrole statuspagina (Playwright + axe-core)   docs/screenshots/peil/  schermafdrukken
docs/platform-v2/         contract 1.3 + conformiteitslijst (gepind, CONTRACT_KOPIE.json; tools/contract_kopie.py),
                          pakketdocs, TRUENAS_APP_API.md, EVIDENCE.json
e2e/                      integrale E2E-opzet (README), simulators (truenas, hub, beszel, uptime_kuma, gotify,
                          authentik + simkit), conformance/echte_hub.py
install/manifests/        manifesten per I-document (+ validatie-uitzonderingen)   install/checklists/  I01/I02
schemas/                  checklist-v1.schema.json         tools/               manifesten, controlelijsten, rooktest
tests/e2e/                image-E2E (Docker)               tests/repo/       repo-regels (manifest, docs, evidence)
tests/conformance/        Hub ↔ dienst volgens contract 1.3 (simulator in CI; lokaal ook tegen de echte Hub)
```

```bash
make venv && make lint typecheck test test-sim test-repo test-conformance   # PostgreSQL op 127.0.0.1:5432
make validator-venv manifesten                             # manifesten met jl-manifest --strict (appstore-en-fork)
make rooktest                                              # Hub + executor + lifecycle als Custom Apps (Docker)
make test-e2e                                              # bouwt de image en draait de Docker-E2E
make test-ui                                               # statuspagina per toestand in Chromium (axe, 320–1440 px)
make statuspagina-shots                                    # idem + schermafdrukken naar docs/screenshots/peil/
.venv/bin/python e2e/conformance/echte_hub.py              # zelfde conformiteitstests tegen de echte Hub (naburige repo)
```

De Docker-E2E heeft geen root op de host nodig (bestanden van uid 568 worden via een wegwerpcontainer gelezen en
opgeruimd). Op een gedeelde machine serialiseert `LIFECYCLE_BUILD_LOCK=<lockbestand>` de extra image-builds van de
upgrade-E2E met `flock`.

Koppelen met de Hub (contract 1.3): de koppeling staat in `/appdata/secrets/hub-link.json` (niet in de database;
onleesbaar = fail-closed), de Hub-sleutels worden bij het koppelen gepind en met een vingerafdruk over alle sleutels
gecontroleerd, en de Hub meldt zich bij deze dienst met een door hem ondertekende dienst-JWT (ADR-0011, ADR-0019).
Koppelen via de statuspagina kan alleen via het IP-adres van de NAS of een lokale naam (bescherming tegen
DNS-rebinding); aanbevolen is het eenmalige koppeltoken in het formulier van de app. Elk proces in de image, ook PID 1,
draait als 568 zonder capabilities, met *no_new_privs* en (bij een start als root) een lege bounding set.

Bindend: `docs/platform-v2/PLATFORM_CONTRACTEN.md` (`contracts 1.3`; werklijst `CONTRACT_1.3_CONFORMITEIT.md` §4,
uitgewerkt in ADR-0019).
Licentie: **GPL-3.0** (zie [`LICENSE`](LICENSE); besluit van de eigenaar, 2026-11-09).
