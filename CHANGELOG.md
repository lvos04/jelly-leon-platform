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

### Review contract 1.2 (onafhankelijke QA, ADR-0012)

- **1 MiB-grens echt vóór routering en aanmelding:** een chunked verzoek > 1 MiB gaf 401/404/405, en op
  `POST /connector/v1/hub-link/unlink` met een geldige admin- of dienst-JWT werd de ontkoppeling uitgevoerd. De
  ASGI-laag leest de body nu vóór alles tellend in (max. 1 MiB) en geeft altijd 413; ongeldige `Content-Length` → 400.
- **Koppelcode overschrijft nooit een bestaande koppeling:** de controle "nog nooit gekoppeld" gebeurt nu onder de
  vergrendeling van `hub-link.json`, ook bij het opslaan. Eerder kon een koppelcode-poging naar een eigen "Hub" die
  pas na de echte koppeling (env-token) antwoordde, token en pins overschrijven.
- 60-minutengrens van het env-token ook met een onleesbaar `hub-link.json`; een mislukte koppelcode-poging wist niet
  meer de `pending` van een lopende env-token-poging.
- Geen optie meer om `X-Forwarded-For` te vertrouwen (`--forwarded-allow-ips` weg); lege instellingen vallen terug op
  de standaard, `LIFECYCLE_DATA_DIR` moet absoluut zijn.
- Regressietest "vreemde Hub zonder vingerafdruk" bereikte de vreemde server nooit (ongeldige koppelcode); gerepareerd.
- Conformiteit opnieuw lokaal tegen de echte Hub (`ai-server-manager` `be7d593`): 16 geslaagd. Op een echte TrueNAS:
  **NIET GEVALIDEERD**.

### Statuspagina in Peil (JP11, ADR-0013)

- `GET /` gebruikt nu het framework-vrije **Peil-sjabloon 1.1.0** uit `ai-server-manager/design/ontwerptaal`, als
  versie-gepinde kopie met herkomstkop in `design/ontwerptaal/` (O-14). CI faalt bij schade aan de kopie en bij drift
  met de bron (gepinde commit én kop van de branch); `tools/peil_kopie.py` maakt en controleert de kopie.
- Precies één koppeltoestand (formulier alleen zolang nooit gekoppeld, gekoppeld = neutrale pil, ontkoppeld,
  ingetrokken; onleesbaar koppelbestand = geen formulier + foutmelding). Geen ontkoppelen, geen ontkoppelcode.
- Eerste signaal = slechtste van gezondheid en gereedheid (gereed = `/readyz` in orde én gekoppeld); nooit groen als de
  dienst niet gereed is. "In orde" heet nu "Gezond"; "Gekoppeld met de Hub" alleen als de Hub het bevestigd heeft.
- Fouten met vaste titel en tekst, code en een selecteerbare trace-ID (zonder JS; dezelfde trace-ID in de app-log);
  veldfouten per veld met `aria-invalid`. Formuliervelden volgens het sjabloon: `hub_adres`, `koppelcode`,
  `hub_vingerafdruk`; te lange of ontbrekende velden geven een HTML-fout in plaats van JSON-422.
- CSP van de pagina: `default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none';
  base-uri 'none'`.
- Tests: rendering per toestand (11 toestanden) met de echte dienst, PostgreSQL en de Hub-simulator; browsercontrole in
  Chromium (Playwright 1.65.0, axe-core 4.14.0): axe 0 overtredingen, geen overloop van 320 tot 1440 px, doelen
  ≥ 44 px, tekst ≥ 14 px, focusring, geen CSP-overtreding — licht en donker. Schermafdrukken 360/768/1440 ×
  licht/donker in `docs/screenshots/peil/statuspagina/`.
- Niet gedaan: echte schermlezer, echte telefoon/tablet, ontwerpreview door de design-agent. Op een echte TrueNAS:
  **NIET GEVALIDEERD**.

## Fase P1 — manifesten, controlelijsten, simulators en installatie-rooktest (ADR-0014…0016)

- **Manifesten** (`install/manifests/`, contract 1.2 §9): Beszel hub + agent (0.21.1), Uptime Kuma (2.5.9) en Gotify
  (3.1.1) als standaard Custom App met één upstream-image op index-digest, als 568:568, zonder host network,
  Docker-socket of GPU; Authentik als catalogusapp `community/authentik` 1.3.64 (2026.8.5) met vaste versie,
  datasets uit de datasetkiezer en door de Hub gegenereerde geheimen; I23 (Hub + executor) als versie-gepinde kopie
  van ai-server-manager met drift-controle. `tools/manifesten.py` valideert alles met `jl-manifest validate --strict`
  van appstore-en-fork; vijf waarschuwingen blijven bewust en beredeneerd (`validatie-uitzonderingen.yaml`, exact
  bewaakt). Meetdekking van Beszel in een container: `install/manifests/BESZEL_MEETDEKKING.md`.
- **Controlelijsten I01/I02** (`install/checklists/`, eigen formaat `checklist v1` + JSON Schema): precies de
  Excel-taken, alleen-lezen-methoden per TrueNAS-versie, geen terminal (GPU via `app.gpu_choices`), eerlijke validatie
  per stap; elke methode getest tegen de TrueNAS-simulator met de alleen-lezen-key.
- **Simulators** Beszel, Uptime Kuma, Gotify en Authentik (OIDC + beheer-API) in `e2e/simulators/` met gedeelde
  testbediening en foutinjectie (`simkit`), protocol per README, 40 tests.
- **TrueNAS-simulator**: catalogus naar `truenas/apps` `f7a38eb` (authentik 1.3.64, immich 1.14.63 — de oude versies
  zijn verdwenen) en optioneel HTTPS/WSS.
- **Installatie-rooktest zonder YAML** (`tools/rooktest.py`, `make rooktest`, CI-job `rooktest`): Hub en executor uit
  ai-server-manager als twee Custom Apps (één map, één poort, geen extra opties), setupcode uit de log, beheerder,
  wizard met TLS-vingerafdruk tegen de TrueNAS-simulator, metingen, executor koppelen, lifecycle-dienst koppelen met
  `HUB_*`, preview van Gotify als standaard Custom App; uitvoering via de lifecycle eerlijk `capability_unavailable`
  (P2). Lokaal geslaagd: `docs/platform-v2/bewijs/rooktest-p1.json`.
- Bevindingen: de D07-meting van de Hub geeft op een NAS zonder apps `protocol_error`; de vertaling controleert niet of
  een hostpad een bestaande dataset is (`app.create` weigert dat pas in de job).
- Op een echte TrueNAS: **NIET GEVALIDEERD**; OneDrive-stappen: **NIET GEVALIDEERD (account)**.

## Ontwerpreview statuspagina (design-agent, ADR-0017)

- Onafhankelijke review van `GET /` in alle toestanden tegen Peil 1.1.0 en WCAG 2.2 AA:
  [`docs/ontwerp/PEIL_REVIEW.md`](docs/ontwerp/PEIL_REVIEW.md). Gerepareerd (alleen uiterlijk, tekst en
  toegankelijkheid): controlerijen alleen met de vaste statuswoorden (één woord = één uiterlijk; "Niet gekoppeld" en
  "Ingetrokken" alleen nog in het koppelblok), geen "in orde" meer, kortere dienstomschrijving (status 48 px hoger op
  320 px), foutsamenvatting met link naar het eerste foute veld, korte skiplinktekst (stak op 320/360 px uit),
  duidelijkere meldingen (te veel pogingen, koppelbestand onleesbaar, vingerafdruk onjuist, fase P2).
- **Live review** (`tests/ui/live/`, `make test-ui-live`, `make ontwerpreview`, CI-job `statuspagina`): de dienst als
  echte processen (api + worker + Hub-simulator), Chromium vult het formulier zelf in; 14 toestanden, waaronder
  formulier verlopen (CSRF), te veel pogingen (429) en direct ingetrokken.
- **Browsercontrole** (`tests/ui/controle.mjs`) meet nu ook contrast in de gerenderde pagina (tekst ≥ 4,5:1, UI-grenzen
  en iconen ≥ 3:1, focusring ≥ 3:1), statusregels (groen alleen bij Gezond, totaal nooit groen bij niet gereed, icoon +
  woord, één woord = één uiterlijk), microcopy (§11.1), skiplink, tekstafstand (WCAG 1.4.12) en forced colors.
  Uitkomst live: axe 0, tekst ≥ 8,34:1, UI ≥ 4,86:1, focusring ≥ 7,79:1, geen overloop op 320 px.
- Schermafdrukken: release-set 5 toestanden × 320/360/390/768/1440 × licht/donker; live set 14 toestanden × 320/1440
  in `docs/ontwerp/shots/` (pngquant). Zeven sjabloonpunten (o.a. `novalidate`, icoon per controlerij) als voorstel
  voor Peil 1.1.1; de gepinde kopie blijft byte-gelijk.
- Niet gevalideerd: echte telefoon/tablet, schermlezer, echte browserzoom; op een echte TrueNAS niets.

## Review fase P1 (onafhankelijke QA, ADR-0018)

- Alle CI-stappen lokaal opnieuw gedraaid, inclusief manifestvalidatie met de actuele appstore-en-fork, image bouwen en
  starten met lege `/appdata` (healthy, alle processen 568 zonder capabilities, herstart zonder verlies van geheimen),
  image-E2E, browsercontrole, live ontwerpreview en de installatie-rooktest: groen. Digests van zeven images met
  `docker buildx imagetools inspect` gecontroleerd: gelijk.
- **Uptime Kuma-simulator:** de push-token stond letterlijk in `/_sim/verzoeken`; nu geredigeerd
  (`/api/push/<push-token>`, telt als aanmelding). Ping-validatie en `entryPage: null` zoals de echte 2.5.9.
- **Beszel-agent:** de claim "vingerafdruk in `/var/lib/beszel-agent` tegen dubbele hosts" klopte niet voor de
  SSH-modus (alleen de WebSocket-client bewaart een vingerafdruk); manifest, meetdekking en evidence gecorrigeerd.
- **Nieuw: `tools/upstream_rooktest.py`** (`make upstream-rooktest`, CI-job `rooktest`): Beszel hub + agent, Gotify en
  Uptime Kuma echt gestart op digest, als 568:568 met een lege eigen map zoals het manifest ze beschrijft: digest-
  readback, alle processen 568, health, kernroutes in dezelfde vorm als de simulators, herstart zonder dataverlies,
  Beszel-agent in SSH-modus met alleen de publieke hubsleutel. Docker, geen TrueNAS (**Sim-getest**).
- Evidence: I08-08-03 terug naar `NIET GEVALIDEERD (echte NAS)` (er is nog geen monitorplan met verwachte status en
  time-out per endpoint); bewijs van de upstream-rooktest bij I07, I08 en I13.

## Contract 1.3 en Peil 1.1.1 (ADR-0019)

- **Kopie contract 1.3** (`PLATFORM_CONTRACTEN.md`, `CONTRACT_1.3_CONFORMITEIT.md`) byte-gelijk, gepind op
  `ai-server-manager@0a7143b` (`CONTRACT_KOPIE.json`, `tools/contract_kopie.py`) met drifttest; EVIDENCE-test volgens
  §11 (1.3) met regels `C1.3-JLP-nn` (C1.3-JLP-13).
- **Statuspagina:** herkomst met exacte `Origin`, `Sec-Fetch-Site` en `Host`-beperking tegen DNS-rebinding; elke POST
  na de herkomstcontrole telt als poging (ook al gekoppeld, onleesbaar, bezig); invoer nooit afkappen; één logregel
  per poging; melding alleen bij een echte poging van deze browser; Gereed = `/readyz` + gekoppeld + bevestigd
  (C1.3-JLP-01/02). Browsertest met de negatieve gevallen, door Chromium zelf verstuurd.
- **Koppelbestand:** `"1.3"` schrijven, elke `1.x` lezen; onleesbaar ook bij andere major of verkeerde rechten;
  vervangen bij de eerste schrijfactie van een nieuw env-token (origineel `.onleesbaar-<UTC-tijd>`,
  `unreadable_replaced_at`); vaste codes van 1.3 (C1.3-JLP-03).
- **JWT en sleutels:** `aud` als lijst geweigerd, authenticatie vóór de body, vingerafdruk over alle
  policy-sleutels (`HN69-2PHK-XV2D-X85W`), `rotation_sequence` uit het enroll-antwoord, canonieke IP-vormen en
  verboden bereiken na DNS-resolutie, ongeldige `HUB_URL`/vingerafdruk zonder crash (C1.3-JLP-04/05).
- **Image:** bounding set leeg vóór de wissel, healthcheck met `--controle`, image-E2E met `CapBnd` en de healthcheck
  apart (C1.3-JLP-06).
- **Events en connector:** vaste `service.*`-namen, inboxgrenzen vóór verzending, totaal = slechtste check,
  verouderd = `unknown`/`meting_verouderd`, `limits.max_age_s` (C1.3-JLP-07/08); foutcatalogus (422 met
  `details.fouten[]`, `Idempotent-Replay`), statische en dynamische `Idempotency-Key`-test (C1.3-JLP-12).
- **Peil 1.1.1:** nieuwe gepinde kopie, renderer in één doorgang (byte-gelijk met de referentie), nieuwe tonen,
  `melding_rol`, blokken onleesbaar en koppelen bezig; driftregel E08 (nieuwere versie = waarschuwing). Opnieuw
  gemeten: axe 0, contrast AA, geen overloop, ook met DejaVu Sans; schermafdrukken vernieuwd.
- **Docs:** begrippen standaard Custom App / compose-app / catalogusapp, koppelen via IP-adres of lokale naam,
  env-enrollment aanbevolen (C1.3-JLP-10/11).
- **Manifesten volgens §9 (1.3)** met de gepubliceerde validator van appstore-en-fork: monitorplan,
  `accepted_findings`, `stopped_snapshot` (Gotify, Uptime Kuma), `install.reason` en `same_as_host` (Authentik),
  restrisico zonder no-new-privileges, `post_install_checks`, I01/I02 als `kind: checklist`;
  `validatie-uitzonderingen.yaml` vervalt (C1.3-JLP-09).
- **Nog open:** C1.3-JLP-14 hoort bij P2. Het monitorplan van de Beszel-agent (alleen SSH) kan de standaard niet
  uitdrukken (contract_issue).
- Op een echte TrueNAS, met echte accounts of hardware: **NIET GEVALIDEERD**.

### Review contract 1.3 (onafhankelijke QA, ADR-0020)

- Alle CI-stappen lokaal opnieuw gedraaid (lint, types, dienst-, simulator-, conformiteits- en repotests, manifesten
  met de gepubliceerde validator, OpenAPI-drift, pip-audit, actionlint, statuspagina in Chromium incl. DejaVu Sans en
  live, image bouwen + E2E): groen. Contract- en Peil-kopie byte-gelijk; conformiteit ook tegen de echte Hub op
  `ai-server-manager@5e21775`.
- **§5 `Retry-After` bij 429/503:** de outbox stopt de ronde na een 429/503 en plant het event op zijn vroegst na
  `Retry-After` (stuurde eerder alle overige events en wachtte alleen de eigen backoff); de worker spreekt de Hub tot
  dan niet aan voor bevestigen, rotatie, outbox en sleutelrotaties; enrollment volgt `Retry-After` ook bij 503 en
  kort een lange `Retry-After` niet meer in tot 15 min.
- **Tokenrotatie:** een tijdelijk mislukte rotatie op verzoek van de Hub gaat opnieuw met backoff + jitter (5 s → 60 s)
  in plaats van elke worker-ronde (elke 5 s).
- **Statuspagina:** 429 `te_veel_pogingen` met `Retry-After` (seconden tot deze bron weer een poging mag doen).
- Hub-simulator: foutinjectie `enroll_503`, `inbox_429`, `inbox_503`, `rotate_500`, `rotate_503`; regressietests die op
  de oude code falen.
- `CUSTOM_APP.md`: de probleemregel noemt de code van 1.3 (`koppelstatus_onleesbaar`, was `hub_link_onleesbaar`).
- Op een echte TrueNAS, met echte accounts of hardware: **NIET GEVALIDEERD**.

## Fase P2 — plannen voor installatie/update/terugzetten, ketentest en Node-RED (ADR-0021…0023)

- **Plannen (B08-003, B08-004):** `POST /lifecycle/v1/plans/{install|update|rollback}/preview`, `GET …/plans/{id}`
  en het melden van een stapresultaat. Een plan bevat de diff (image + digest, datasets, privileges, poorten, uitgaand
  verkeer, geheimen, afhankelijkheden), de blokkades en een expliciete volgorde van stappen; elke stap is een
  sjabloon voor één `ActionProposal` (contract §8) met een vaste `idempotency_key`. De dienst voert zelf niets uit en
  heeft geen TrueNAS-sleutel. Eén blokkade (poort in gebruik, afhankelijkheid ontbreekt, naam of pad bezet, versie
  niet beschikbaar of niet te meten) en er komt geen enkele stap vrij. Migratie `0003_lifecycle_plans`.
- **Veilige update:** stoppen → herstelpunt (snapshot) → starten → kloon → de nieuwe image als testkopie op de kloon
  → pas daarna bijwerken met digest-readback. Mislukt de migratie in de testkopie, dan wordt de draaiende app niet
  aangeraakt. Terugzetten van een Custom App = `app.update` met de vorige waarden; een catalogusversie die niet
  meer **gemeten** aanwezig is geeft `version_unavailable`. Data uit een herstelpunt terugzetten is geen stap in een
  plan (eigenaarshandeling; er wordt nooit automatisch data overschreven of verwijderd).
- **Gemeten bij de Hub (C1.3-JLP-14):** inventaris (`inventaris.lezen`) en catalogusbeschikbaarheid
  (`catalogus.lezen`). Zonder scope of route → `capability_unavailable`; onbereikbaar, verouderd of onleesbaar →
  `unknown`; beide blokkeren. Het contract noemt voor de catalogus geen pad en de Hub heeft de route nog niet:
  voorlopig `GET /api/v1/catalog/availability` (op één plek in `hubread.py`).
- **Manifeststandaard als gepinde kopie** in de dienst (`apps/api/src/appstore/`, byte-gelijk aan
  `appstore-en-fork@7b3159d`; `tools/validator_kopie.py`, ADR-0021). Bijgewerkt in de review: nu `1967069` (zie
  "Review P2").
- **Ketentest** plan → Hub → uitvoerder → TrueNAS-simulator met stand-ins in `e2e/simulators/hub/`
  (`hub_p2.py`, `contract8.py`, `executor_sim.py`): R0/R1 automatisch, R2/R3 pas na een goedkeuring die aan de diff
  gebonden is, ondertekend `ExecutionRequest` (Ed25519), de uitvoerder controleert alles opnieuw en leest terug.
  Scenario's onder andere: poortconflict en ontbrekende afhankelijkheid blokkeren; migratiefout in de testkopie laat
  de oude app met data intact. TrueNAS-simulator: datasetinhoud bij snapshot en kloon, storing `migratie_mislukt`.
- **Node-RED (I10-10-01…06, ADR-0023):** manifest `install/manifests/node-red.manifest.yaml` (standaard Custom App,
  officiële image op digest, 568:568, alleen `/data`). Beveiligd via de omgevingsvariabele `FLOWS`: inloggen
  verplicht, geen exec-, function- of bestandsnodes geladen, geen installatie van extra nodes; de monitor bewaakt of die
  beveiliging actief is (sinds besluit C-11: geen anonieme toegang en 401 op `/diagnostics`; zie "Node-RED na besluit
  C-11 en de restpunten van de review"). Flowbundels in `install/node-red/flows/` met
  eigen controle, versies en sha256-lock: `hartslag` (heartbeat §6.1 met flowvertraging), `mediaguard-offline`
  (leest de inventaris van de Hub, meldt `flow.app_watch.down`/`recovered`), `testflow-herstart` (vraagt een herstart
  aan bij de Hub; staat uit). `tools/nodered_flows.py` (render, lint, lock, importeer, exporteer) en
  `tools/nodered_rooktest.py` (stappen N1–N9 met de echte image tegen de Hub-simulator; CI-job `rooktest`).
- Hub-simulator: heartbeat-route van contract 1.3 §6.1.
- **Geblokkeerd / niet gebouwd (eerlijk):**
  - uitvoeren op een NAS: de echte Hub heeft nog geen route voor voorstellen en catalogusbeschikbaarheid (stand
    S-P2-400: in het bewijs **Bezig**, niet Geblokkeerd; open: S-P2-050, S-P2-080, S-P2-420, daarna de test op een
    echte NAS in P6);
  - Node-RED op een NAS is na installatie leeg en zonder beheerder helemaal dicht (besluit C-11): beheerder, flows,
    `HUB_URL` en service-token kan de manifeststandaard v1 niet leveren;
  - de flow "my_house sensor verouderd → melding": geen contractroute (`capability_unavailable`), niet nagebootst;
  - installatieplan van een app met geheime parameters (bijvoorbeeld Authentik): altijd geblokkeerd tot de standaard
    kluisverwijzingen kent (stand na de review: de standaard kent ze sinds `1967069`, maar het plan blijft geblokkeerd
    zolang de `app_id` van het manifest niet vastligt; ADR-0025 besluit 7);
  - schaduwtest voor catalogusapps; verwijderen en verhuizen (B08-006) geven `capability_unavailable`;
  - uitgaand verkeer beperken: het Custom App-formulier kent geen egressregels.
- Alles hierboven is **Sim-getest**. Op een echte TrueNAS, met de echte Hub/executor, echte accounts of hardware:
  **NIET GEVALIDEERD**.

### Review P2 (onafhankelijke QA, ADR-0024 en ADR-0025)

- **Plannen op verse metingen (ADR-0024):** een meting ouder dan 900 s (of zonder leesbaar tijdstip) is onbekend
  voor elk soort plan; terugzetten is pas bewezen met de digest; een vervallen plan noemt geen vrijgegeven stappen.
- **Terugzetten nooit alleen de image als dat niet veilig is (HARDE EIS, ADR-0025):** zonder bekend herstelpunt van
  vóór de update (`herstelpunt_onbekend`) of als het manifest migraties meldt of alleen de tag terugzetten niet veilig
  noemt (`terugzetten_vraagt_data`), geeft het terugzetplan niets vrij en noemt het de herstelpunten voor de eigenaar.
- **Beschermde apps:** de planner volgt de regels `beschermd` van het Hub-beleid (gepinde kopie van
  `policy/apps.yaml`, `tools/beleid_kopie.py`): geen enkele stap voor Emby; geen update, terugzetting of verwijdering
  van de mediaserver of MediaGuard. De Hub-stand-in weigert zo'n voorstel ook.
- **Volledig herstelpunt:** opslag met configuratie of gegevens buiten `backup.snapshot_datasets` blokkeert de update
  (`herstelpunt_onvolledig`).
- **Plan vervalt met zijn meting:** uiterlijk 900 s na de oudste gebruikte meting.
- **Node-RED-hulpmiddel:** geen eigen `env` of groep op een tabblad of node in een bundel; na de import wordt
  teruggelezen dat alleen de globale `HUB_URL` telt; `exporteer` schrijft nooit buiten de exportmap. Herreview: een
  eigenschap in een `change`-regel is alleen een eenvoudige naam en nooit `msg…` (Node-RED las `msg.url`, `'url'` en
  `msg['url']` als `msg.url`); het teruglezen laat de eigen tabbladen opnieuw door `lint` gaan en zet bij een
  bevinding de flows van vóór de import terug.
- **CI:** de validator van appstore-en-fork staat vast op `1967069` (`ASF_COMMIT`), ook in `make validator-venv`; de
  kopie van de standaard in de dienst staat op dezelfde commit (ADR-0021). Een plan met geheimen blijft geblokkeerd.
- Alles hierboven is **Sim-getest**. Op een echte TrueNAS, met de echte Hub/executor: **NIET GEVALIDEERD**.

### Controle P2 en bijgewerkt bewijs (S-P2-400)

- **Bewijs bijgewerkt:** B08-003, B08-004 en C1.3-JLP-14 staan op **Bezig** (Unit-getest; Sim-getest; NIET
  GEVALIDEERD (echte NAS)). Open voor B08-003 en B08-004: de routes van de echte Hub voor voorstellen en
  catalogusbeschikbaarheid (S-P2-050, S-P2-080), de proef tegen de echte Hub-code (S-P2-420) en de test op een echte
  NAS (P6). Open voor C1.3-JLP-14: de route voor catalogusbeschikbaarheid in de echte Hub (S-P2-080), de proef tegen
  de echte Hub-code (S-P2-420) en de test op een echte NAS (P6). De planroutes
  `POST /lifecycle/v1/plans/{install|update|rollback}/preview` bestaan; alleen verwijderen en verhuizen (B08-006)
  geven nog 501 `capability_unavailable`.
- **Bekende beperking vastgelegd (ADR-0025):** een melding van `stoppen` die pas na het vervallen van een updateplan
  binnenkomt, wordt geweigerd (409 `plan_vervallen`), en dan geeft het plan ook `starten` niet vrij. Het gedrag is
  niet gewijzigd; een karakteriseringstest legt het vast
  (`test_plans_api.py::test_late_melding_van_stoppen_na_het_vervallen_geeft_starten_niet_vrij`).
- Alles hierboven is **Sim-getest**. Op een echte TrueNAS, met de echte Hub/executor: **NIET GEVALIDEERD**.

### Node-RED na besluit C-11 en de restpunten van de review (S-P2-410)

- **Geen anonieme toegang (besluit C-11 van de gebruiker, 2026-11-11):** `adminAuth` is precies
  `{"type": "credentials", "users": []}`, zonder `default`. Anoniem `GET /flows`, `/nodes`, `/settings` en
  `/diagnostics` en `POST /flows` geven 401 (gemeten in de rooktest, N1). Zonder beheerder is de editor helemaal dicht,
  ook voor lezen; dat is de bedoelde stand (besluit C-12: geen gebruiker of geheim in het manifest).
  `tools/nodered_flows.py` weigert overrides met een andere `adminAuth`; `importeer` en `exporteer` vragen een beheerder
  (`--gebruiker`) en melden dat duidelijk.
- **Monitor en controles na installatie:** de monitor verwacht nu 401 op `/diagnostics` (gemeten; zonder `FLOWS` 200).
  Eerlijk: dat bewijst alleen `adminAuth` zonder anonieme rechten, niet de rest van `FLOWS` (ADR-0023 punt 3). Nieuw:
  `anoniem_geweigerd` (401 op `GET /flows`, `/nodes`, `/settings`) en `exec_en_function_niet_geladen` (handmatig: kan
  alleen met een beheerder).
- **Restpunten van de review van P2 (S-P2-395) in het hulpmiddel:** `importeer` zet de vorige flows ook terug als het
  teruglezen zelf faalt (netwerkfout, onverwachte gegevens; R-VEILIG-5), en `lint` crasht niet meer op `rules`,
  `wires` of `props` van een verkeerde vorm. `lint` weigert `__proto__`, `constructor` en `prototype` in een eigenschap
  en in een bron uit msg, flow of global, en de oude vorm van de `change`-node zonder lijst `rules`.
- **Doorverwijzingen (gemeten):** bij deze digest stuurde de `http request`-node bij een doorverwijzing (307/302) naar
  een andere poort of host de bearer-kop met het service-token mee. Elke route-node zet nu
  `msg.followRedirects = false` (verplicht in `lint`: `redirect_niet_uit`); de drie bundels staan op versie 1.1.0.
- **Rooktest:** nieuwe stap N10 (weigering van een bundel met `env` op het tabblad of een tweede regel op `msg.url`,
  geen doorverwijzing met het token, controleproef zonder de regel); het bewaarde rapport
  `docs/platform-v2/bewijs/nodered-rooktest-p2.json` is opnieuw gemaakt (met `eigen_controle` in de readback).
- Alles hierboven is **Sim-getest** (Docker, Hub-simulator). Op een echte TrueNAS: **NIET GEVALIDEERD**.
