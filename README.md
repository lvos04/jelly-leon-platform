# jelly-leon-platform

Installatie, updates, terugzetten en verwijderen/verhuizen van het **TrueNAS Home Platform V2** (B08-003, B08-004,
B08-006), de installeerbare manifests per `03_INSTALLATIE`-document en de integrale E2E-omgeving.

> **Fase P2.** P0 leverde de lifecycle-dienst (datamodel, API, connectorcontract), de alles-in-één image, de
> installatiedocumentatie zonder YAML, het TrueNAS-API-onderzoek en de TrueNAS-simulator. P1 voegde de installeerbare
> manifesten (I07, I08, I09, I13, I23), de controlelijsten I01/I02, simulators van Beszel, Uptime Kuma, Gotify en
> Authentik en een installatie-rooktest zonder YAML toe (ADR-0014…0016). P2 voegt toe: **plannen** voor installatie,
> update en terugzetten (diff, blokkades en een expliciete volgorde van stappen; gemeten bij de Hub: inventaris en
> catalogusbeschikbaarheid — ADR-0021, ADR-0022) en **Node-RED** als standaard Custom App met gecontroleerde
> flowbundels (ADR-0023).
>
> Deze dienst voert zelf **niets** uit op een NAS en heeft geen TrueNAS-sleutel: een plan is een voorstel; beleid,
> goedkeuring en ondertekening doet de Hub, uitvoeren doet alleen de executor (contract §8). De keten plan → Hub →
> executor → TrueNAS is getest met stand-ins en de TrueNAS-simulator (**Sim-getest**). De echte Hub heeft nog geen
> route om voorstellen te ontvangen en geen route voor catalogusbeschikbaarheid (S-P2-050, S-P2-080); de proef tegen
> de echte Hub-code is S-P2-420. Tot dan kan een plan niet via de echte Hub worden uitgevoerd en is er op een echte
> TrueNAS niets gevalideerd (**NIET GEVALIDEERD**). In het bewijs staan de plannen op **Bezig**.

## Voor de gebruiker

| Wat | Waar |
|---|---|
| Eerste installatie van het platform (twee Custom Apps via de TrueNAS-web-UI, geen terminal, geen YAML) | [`install/truenas/INSTALLATIE.md`](install/truenas/INSTALLATIE.md) |
| Deze dienst als standaard Custom App (formuliervelden) | [`deploy/truenas/CUSTOM_APP.md`](deploy/truenas/CUSTOM_APP.md) |
| Overzicht installatiemethode per I-document (I01–I26) en de manifesten (Beszel, Uptime Kuma, Gotify, Authentik, Node-RED, Hub/executor) | [`install/manifests/README.md`](install/manifests/README.md) |
| Node-RED: beveiliging, flowbundels (hartslag, MediaGuard offline → melding, testflow) en wat nog niet kan | [`install/node-red/README.md`](install/node-red/README.md) |
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
docs/openapi/             geëxporteerde OpenAPI            docs/adr/         ADR-0001…0025
design/ontwerptaal/       gepinde kopie Peil 1.1.1-statuspagina (HERKOMST.md)    tools/peil_kopie.py  kopie maken/controleren
tests/ui/                 browsercontrole statuspagina (Playwright + axe-core)   docs/screenshots/peil/  schermafdrukken
docs/platform-v2/         contract 1.3 + conformiteitslijst (gepind, CONTRACT_KOPIE.json; tools/contract_kopie.py),
                          pakketdocs, TRUENAS_APP_API.md, EVIDENCE.json
e2e/                      integrale E2E-opzet (README), simulators (truenas, hub incl. stand-ins voor beleid,
                          goedkeuring en uitvoerder, beszel, uptime_kuma, gotify, authentik + simkit),
                          conformance/echte_hub.py
install/manifests/        manifesten per I-document (+ validatie-uitzonderingen)   install/checklists/  I01/I02
install/node-red/         instellingen (bron van FLOWS in het manifest), flowbundels, lock     apps/api/src/appstore/
                          gepinde kopie van de manifeststandaard (ADR-0021; tools/validator_kopie.py)
schemas/                  checklist-v1.schema.json         tools/               manifesten, controlelijsten, rooktest
tests/e2e/                image-E2E (Docker)               tests/repo/       repo-regels (manifest, docs, evidence)
tests/conformance/        Hub ↔ dienst volgens contract 1.3 (simulator in CI; lokaal ook tegen de echte Hub)
```

```bash
make venv && make lint typecheck test test-sim test-repo test-conformance   # PostgreSQL op 127.0.0.1:5432
make validator-venv manifesten                             # manifesten met jl-manifest --strict (appstore-en-fork)
make rooktest                                              # Hub + executor + lifecycle als Custom Apps (Docker)
make nodered nodered-rooktest                              # Node-RED: manifest/bundels/lock; proef N1–N10 (Docker)
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

Plannen (P2): `POST /lifecycle/v1/plans/{install|update|rollback}/preview` legt een plan vast en meet daarvoor de
inventaris (`inventaris.lezen`) en de catalogusbeschikbaarheid (`catalogus.lezen`) bij de Hub. Eén blokkade (poort in
gebruik, afhankelijkheid ontbreekt, versie niet beschikbaar of niet te meten) en er komt geen enkele stap vrij. Een
update met databasemigraties loopt eerst als testkopie op een kloon; mislukt die, dan wordt de draaiende app niet
aangeraakt. Terugzetten zet alleen de image terug en komt dus alleen vrij met een herstelpunt van vóór de update en
als het manifest zegt dat dat veilig is; anders zet de eigenaar image en data samen terug. Voor Emby komt nooit een
stap vrij, en voor de mediaserver en MediaGuard geen update, terugzetting of verwijdering (beleid van de Hub; ADR-0024,
ADR-0025). Verwijderen en verhuizen (B08-006) geven nog `capability_unavailable`.

Bindend: `docs/platform-v2/PLATFORM_CONTRACTEN.md` (`contracts 1.3`; werklijst `CONTRACT_1.3_CONFORMITEIT.md` §4,
uitgewerkt in ADR-0019).
Licentie: **GPL-3.0** (zie [`LICENSE`](LICENSE); besluit van de eigenaar, 2026-11-09).
