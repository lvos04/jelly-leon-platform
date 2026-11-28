# Installeerbare manifests per 03_INSTALLATIE-document (I01–I26)

Deze repo levert per I-document een manifest volgens contract 1.3 §9 (eigenaar van de standaard: `appstore-en-fork`),
of — voor I-documenten die niets installeren — een controlelijst (`install/checklists/`, documentsoort
`kind: checklist` met `install.method: none`, ADR-0015/0019).
Installatieregel (contract §1/§2): standaard Custom App (`ix-app`) met één gepinde image, of de **officiële
TrueNAS-catalogusapp** met vaste versie voor apps met meerdere containers. Nooit YAML/compose.

> **Status (eerlijk):** alle manifesten hieronder zijn gevalideerd met `jl-manifest validate --strict` van
> appstore-en-fork volgens contract 1.3 (geaccepteerde waarschuwingen in `accepted_findings`, alleen het
> monitorplan van de Beszel-agent in `overgang-1.3.yaml`). Voor de P1-manifesten is de vertaling naar
> `app.create`-waarden tegen de TrueNAS-simulator gecontroleerd (**Sim-getest**). Voor I25 zijn de manifestkopie,
> de image-digest en de vertaling naar het Custom App-formulier gecontroleerd (**Unit-getest**). Op een echte TrueNAS is niets
> geïnstalleerd: **NIET GEVALIDEERD (echte NAS)** — installeren gebeurt via de Hub en de executor (P2).
>
> De vier upstream-images (Beszel hub en agent, Gotify, Uptime Kuma) zijn bovendien echt gestart, maar in Docker en
> niet op TrueNAS: op digest, als 568:568 met een lege eigen map, met de omgevingsvariabelen uit het manifest
> (`tools/upstream_rooktest.py`, `make upstream-rooktest`, CI-job `rooktest`). Gecontroleerd: digest-readback, alle
> processen als 568, de healthcheck-route, de kernroutes in dezelfde vorm als de simulators in `e2e/simulators/`,
> herstart zonder dataverlies, en Beszel-agent ↔ hub in SSH-modus met alleen de publieke sleutel. Ook dat is
> **Sim-getest**, geen NAS-validatie.
>
> Node-RED (P2) heeft een eigen proef met de echte image tegen de Hub-simulator (`tools/nodered_rooktest.py`,
> `make nodered-rooktest`, dezelfde CI-job): beveiliging volgens het manifest, controleproef zonder de instellingen,
> import van de flowbundels, melding, actieaanvraag, hartslag, herstel van een flowversie, herstart en stoppen, en
> dat het service-token alleen naar de Hub gaat (geen doorverwijzing; geen proxy uit de omgeving, ook niet met de
> ingebouwde proxy van Node.js aan; een vreemde node die een eigen node raakt laat de import weigeren) —
> **Sim-getest**. Het manifest levert op een NAS alleen een beveiligde, lege Node-RED zonder anonieme toegang (besluit
> C-11: zonder beheerder helemaal dicht, ook lezen); beheerder, flows en service-token zijn **Geblokkeerd**
> (`../node-red/README.md`, ADR-0023).

## Wat er nu is (P1 en P2)

| I-doc | Bestand | Methode | Versie · image of catalogus | Bijzonderheden |
|---|---|---|---|---|
| I01 | `../checklists/I01_TRUENAS_BASIS.checklist.yaml` | controlelijst | — | 8 stappen, alleen-lezen-methoden per TrueNAS-versie, geen terminal |
| I02 | `../checklists/I02_BACKUP_EN_RESTORE.checklist.yaml` | controlelijst | — | 8 stappen; OneDrive-stappen NIET GEVALIDEERD (account) |
| I03 | `../checklists/I03_MEDIAGUARD.checklist.yaml` | controlelijst | — | `03_INSTALLATIE/I03_MEDIAGUARD.md`; 8 handmatige controles, back-up vóór update, releasegebonden upgradebewijs en vorige image én passende snapshot; Hub-UI wacht op S-P3-070; NIET GEVALIDEERD (echte NAS/hardware) |
| I04 | `../checklists/I04_MEDIA_SOURCE_OPTIMIZER.checklist.yaml` | controlelijst | — | `03_INSTALLATIE/I04_MEDIA_SOURCE_OPTIMIZER.md`; 8 handmatige controles, T-12 bij stappen 4/6, herstelgate vóór mutaties; machine-sleutel wacht op S-P3-220; NIET GEVALIDEERD (echte NAS/Hub) |
| I05 | `../checklists/I05_MY_HOUSE.checklist.yaml` | controlelijst | — | `03_INSTALLATIE/I05_MY_HOUSE.md`; 8 stappen, inrichten_en_testen; installatie via het formulier van de standaard Custom App volgens `deploy/truenas/CUSTOM_APP.md` van my_house, wacht op de alles-in-één-release (BLOK-06); boiler alleen meten, bediening alleen van geteste apparaten (D12), Zigbee alleen bij D13; Geblokkeerd, NIET GEVALIDEERD (echte NAS/hardware) |
| I06 | [Controlelijst Jellyfin](../checklists/I06_JELLYFIN.checklist.yaml) | controlelijst | — | `03_INSTALLATIE/I06_JELLYFIN.md` · 8 stappen. Controleer de inventaris en het herstelbewijs. De Jellyfin-proeven gelden als D07 Jellyfin aantreft. Emby blijft de mediaserver. |
| I15 | `../checklists/I15_IMMICH.checklist.yaml` | controlelijst | — | `03_INSTALLATIE/I15_IMMICH.md`; 6 handmatige controles; foto- en databaseherstel uitsluitend geïsoleerd; OneDrive-account bij stap 2; adapter/Hub wacht bij stappen 4/5; Bezig, NIET GEVALIDEERD (echte NAS/account/Hub) |
| I16 | `../checklists/I16_PROWLARR.checklist.yaml` | controlelijst | — | `03_INSTALLATIE/I16_PROWLARR.md`; 6 handmatige controles; MediaGuard alleen eigen tags, geen tweede reconciler; MSO-lus en herstel afzonderlijk bewijzen; Hub-meldingsketen open; Bezig, NIET GEVALIDEERD (echte NAS/Hub) |
| I17 | `../checklists/I17_SABNZBD.checklist.yaml` | controlelijst | — | `03_INSTALLATIE/I17_SABNZBD.md`; 6 handmatige controles; categorie-eigenaars behouden, laatste server beschermd; back-upoverdracht en geïsoleerd herstel nog aantonen; Bezig, NIET GEVALIDEERD (echte NAS) |
| I18 | `../checklists/I18_QBITTORRENT.checklist.yaml` | controlelijst | — | `03_INSTALLATIE/I18_QBITTORRENT.md`; 6 handmatige controles; private guard, VPN-binding en seedpayload behouden; hersteloverdracht en functionele proeven open; Bezig, NIET GEVALIDEERD (echte NAS) |
| I19 | `../checklists/I19_SEERR.checklist.yaml` | controlelijst | — | `03_INSTALLATIE/I19_SEERR.md`; 6 handmatige controles; aanvragen via MediaGuard, Hub alleen viewer; hersteloverdracht en functionele proeven open; Bezig, NIET GEVALIDEERD (echte NAS/Hub) |
| I20 | `../checklists/I20_BAZARR.checklist.yaml` | controlelijst | — | `03_INSTALLATIE/I20_BAZARR.md`; 5 handmatige controles; behoud tot bewezen pariteit en gebruikersbesluit; echte spelerproef en hersteloverdracht open; Bezig, NIET GEVALIDEERD (echte NAS/hardware) |
| I24 | [Controlelijst Jelly Leon-release](../checklists/I24_UNIVERSELE_MEDIASERVER.checklist.yaml) | controlelijst | — | `03_INSTALLATIE/I24_UNIVERSELE_MEDIASERVER.md` · 6 stappen. Volg voor de nieuwe app naast Emby `deploy/truenas/CUSTOM_APP.md` in de repo `jelly-leon`. Controleer de terugval en het rapport per gebruiker vóór een overstap. |
| I07 | `beszel-hub.manifest.yaml` | Custom App | 0.21.1 · `ghcr.io/henrygd/beszel/beszel@sha256:8514a69d…d695b770` | 568:568; meetdekking: `BESZEL_MEETDEKKING.md` |
| I07 | `beszel-agent.manifest.yaml` | Custom App | 0.21.1 · `ghcr.io/henrygd/beszel/beszel-agent@sha256:cb686801…bf052e41c` | geen catalogusapp; SSH-modus met alleen de publieke hubsleutel; geen host network, Docker-socket of GPU |
| I08 | `uptime-kuma.manifest.yaml` | Custom App | 2.5.9 · `ghcr.io/louislam/uptime-kuma@sha256:e88415a9…725591bd` | 568:568 (de catalogusapp draait als root); SQLite |
| I09 | `authentik.manifest.yaml` | catalogusapp | `community/authentik` 1.3.64 (app 2026.8.5) | vier datasets uit de datasetkiezer; geheimen door de Hub gegenereerd; geen Docker-socket |
| I12 | [Manifest AdGuard Home](adguard-home.manifest.yaml) | catalogusapp | `community/adguard-home` 1.3.24 (app v1.0.0) | DNS op het eigen proefadres, hostpoort 30553 voor TCP en UDP. De bestaande DNS-dienst op poort 53 blijft beschikbaar. |
| I10 | `node-red.manifest.yaml` | Custom App | 5.0.9-24 · `nodered/node-red@sha256:2ccd38b4…64d6896f` | 568:568 (de catalogusapp draait als 1000, zonder login en met alle nodes); beveiligd via `FLOWS`; geen anonieme toegang (besluit C-11); monitor: 401 op `/diagnostics`; flowbundels in `../node-red/` |
| I11 | [Controlelijst NetAlertX](../checklists/I11_NETALERTX.checklist.yaml) | controlelijst | 26.10.28 · bron en digest hieronder | Zes handmatige controles. De installatie is geblokkeerd op de netwerkrechten. |
| I13 | `gotify.manifest.yaml` | Custom App | 3.1.1 · `ghcr.io/gotify/server@sha256:44fc5bbd…eb203b86` | 568:568; poort 8080 in de container; registratie uit |
| I23 | `ai-server-manager.manifest.yaml`, `ai-server-manager-executor.manifest.yaml` | Custom App (handmatig) | kopie van ai-server-manager `f5c8f2c` | Hub `0.1.0`, executor `0.1.1`. [Formuliertags en digests in stap 1](../truenas/INSTALLATIE.md). |
| I03 | `mediaguard.manifest.yaml` | Custom App | kopie · bron `lvos04/media-guard-ultimate` `deploy/manifest/mediaguard.manifest.yaml` @ `9824153` | doel: formulier van de standaard Custom App voor I03; de controlelijst verwijst ernaar (I03-03-04); digest volgt bij de release (`accepted_findings` in de bron) |
| I04 | `media-source-optimizer.manifest.yaml` | Custom App | kopie · bron `lvos04/media-source-optimizer` `deploy/manifest/media-source-optimizer.manifest.yaml` @ `335e26e` | doel: formulier van de standaard Custom App voor I04; de controlelijst verwijst ernaar (I04-04-01); digest volgt bij de release |
| I05 | `my_house.manifest.yaml` (app_id `smart-home`) | Custom App | kopie · bron `lvos04/my_house` `deploy/manifest/my_house.manifest.yaml` @ `cdc739e` | doel: formulier van de standaard Custom App voor I05; de controlelijst I05 (S-P3-440) verwijst ernaar; digest volgt bij de release |
| I25 | [Zoekmanifest](zoeken-en-persoonlijke-ai.manifest.yaml) | Custom App | 0.1.1 · broncommit `6ccef1f` | Eén image met PostgreSQL en pgvector. Alleen de API-poort wordt gepubliceerd. |

Manifestkopieën (I23, I03, I04, I05, I25) komen alleen met `python tools/manifest_kopie.py bijwerken --repo <repo> --bron
../<repo> --ref <commit>` uit de gecommitte bron; de herkomstkop (2 regels: bron `<repo>@<commit>:<pad>` en sha256)
bewaakt de rest byte voor byte (`python tools/manifest_kopie.py controleer`, `make manifesten`). Een kopie houdt de
bestandsnaam van de bron, ook als die niet gelijk is aan de `app_id` (my_house).

Controlelijsten (`install/checklists/`): hooguit één lijst per I-document zonder installatie, met de bestandsnaam van
het document (`I0x_NAAM.md` → `../checklists/I0x_NAAM.checklist.yaml`); geen document heeft twee lijsten, een document
met een installeerbaar manifest van deze repo hierboven heeft geen lijst (een manifestkopie van een andere repo mag
ernaast staan; de lijst verwijst er dan naar in `verwijzing`), en elk ander bestand in `install/checklists/` is een fout.
Naast I01 en I02 krijgen de documenten van de bestaande apps (I03, I04, I05 en I15 tot en met I20) zo'n lijst
(`docs/adr/ADR-0027-controlelijsten-bestaande-apps.md`). Een lijst installeert niets en vervangt geen manifest of
import; ze beschrijft alleen de weg via de TrueNAS-webinterface of het formulier van de standaard Custom App, nooit een
terminal of YAML als weg. Titel en acceptatie blijven de letterlijke Excel-tekst, ook waar die YAML noemt (de acceptatie
van I05-05-02; de titels zijn hernoemd, besluit Z-18 optie 2). Elke nieuwe lijst krijgt een rij in de tabel hierboven (naam en document) in de stap die haar maakt.

Keuze Custom App of catalogusapp en digests: `docs/adr/ADR-0014-manifesten-p1.md`; contract 1.3 (monitorplan,
`post_install_checks`, `stopped_snapshot` voor Gotify en Uptime Kuma, `install.reason` en `same_as_host` voor Authentik,
restrisico zonder no-new-privileges, `accepted_findings`): `docs/adr/ADR-0019-contract-1-3.md`. Node-RED als Custom
App in plaats van de catalogusapp, de beveiliging via `FLOWS` en de grenzen daarvan: `docs/adr/ADR-0023-node-red-als-custom-app.md`.

### Controleren

```bash
make validator-venv     # validator van appstore-en-fork in .venv-validator (gecommitte inhoud, alleen lezen)
make manifesten         # jl-manifest validate --strict (accepted_findings; overgang-1.3.yaml exact), I23-kopie, controlelijsten
make upstream-rooktest  # upstream-images op digest als 568:568 met lege map: health, API-vorm, herstart (Docker)
make nodered            # Node-RED: FLOWS in het manifest = settings-overrides.json, flowbundels en lock in orde
make nodered-rooktest   # Node-RED op digest als 568:568 tegen de Hub-simulator: stappen N1–N10 (Docker)
```

### Na de installatie (verplicht; `post_install_checks` in het manifest, de Hub toont ze als taak en de app is tot de bevestiging niet gezond)

| App | Wat de gebruiker direct doet (in de eigen web-UI van de app) | Controle door de Hub |
|---|---|---|
| Gotify | standaardaccount `admin` een eigen wachtwoord geven; per bron een applicatie (Hub, Uptime Kuma, back-up) | Basic `admin:admin` op `/current/user` → 401 |
| Beszel | eerste account aanmaken (tot dan kan iedereen op het LAN dat) | `GET /api/beszel/first-run` → `firstRun: false` |
| Uptime Kuma | eerste beheerder aanmaken; API-sleutel voor de Hub (alleen `/metrics`) | alleen via Socket.IO controleerbaar → bevestiging door de gebruiker |
| Authentik | initiële setup (`akadmin`) met MFA; API-token voor home-control-hub | `/-/health/ready/`; beheer via home-control-hub (B13) |
| Node-RED | niets in te stellen: zonder beheerder is de editor helemaal dicht, ook lezen (besluit C-11), geen `accounts`: deze image kan geen beheerder of dienstaccount via de voorziening van C-12 krijgen (ADR-0016 punt 7 van appstore-en-fork, "Wachten"; Geblokkeerd tot een nieuw besluit) | zonder aanmelding: `/diagnostics` → 401, `GET /flows`, `/nodes` en `/settings` → 401, `POST /flows` → 401; `/auth/login` → type `credentials`; "exec en function niet geladen" alleen met een beheerder (handmatig; blijft open tot er een beheerder is, en zo lang is Node-RED niet gezond: de bedoelde stand) |

Tokens en sleutels die de gebruiker zo maakt, gaan alleen in de vault van de Hub (nooit in een manifest of formulier).

### NetAlertX (I11)

Op 2026-11-28 wees de stabiele tag `v26.10.28` naar broncommit `6f3cb9bd4385`.
De gemeten OCI-index van `ghcr.io/netalertx/netalertx:26.10.28` is
`sha256:ebce55cdbccc0f71544e6997541fe81db803b8c8bec3079435b8cd0a40d49b7e`. [Upstream-release](https://github.com/netalertx/NetAlertX/releases/tag/v26.10.28)

De meegeleverde eerste configuratie plant al ARP-scans (`ARPSCAN_RUN='schedule'`).
Een passief profiel moet die planning vóór de eerste dienststart uitschakelen. [Standaardconfiguratie van de release](https://github.com/netalertx/NetAlertX/blob/v26.10.28/back/app.conf#L29-L37)

Voor directe passieve LAN-waarneming heeft deze image toegang tot het hostnetwerk nodig.
De Python-interpreter van de dienst vereist daarnaast `NET_RAW` en `NET_ADMIN` in de bounding set,
ook voor een passieve bestandsimport. [Netwerkmodus](https://github.com/netalertx/NetAlertX/blob/v26.10.28/docs/docker-troubleshooting/network-mode.md) · [Opbouw van de image](https://github.com/netalertx/NetAlertX/blob/v26.10.28/Dockerfile#L178-L183)

Bij de lokale manifestproef weigerde de strikte validator hostnetwerk met `NET_ADMIN`
(`capability_forbidden`). Met een eigen netwerknamespace accepteerde de validator de proef.
Het installatiebeleid van de Hub weigerde de extra rechten in de vertaalde formulierwaarden
(`capabilities_verboden` in `asm_policy.params.validate_install`). Zonder extra rechten gaf de
gepinde interpreter bij een lokale start als 568:568 de melding `operation not permitted`
(exitcode 255). [Startproef en formulierproef](../../docs/platform-v2/bewijs/i11-netalertx-rechten.json)

Stap I11-11-02 blijft
geblokkeerd tot een gepinde image en toegelaten rechtenprofiel samen werken. De controlelijst
legt de passieve scope, de alleen-lezen-koppeling en het herstel van de eigen dataset vast.

## Overzicht alle I-documenten (plan)

Catalogusgegevens: `truenas/apps` commit `f7a38eb` (2026-11-09). De catalogus houdt per app alleen de nieuwste
versie bij (`docs/platform-v2/TRUENAS_APP_API.md` §5): dit is een momentopname, geen pin.

| I-doc | App | Aanpak (I-doc) | Installatiemethode | Catalogus (trein · versie · app_version) | Manifest |
|---|---|---|---|---|---|
| I01 | TrueNAS basis | behouden en valideren | — (host; alleen lezen via API) | — | **P1: controlelijst** |
| I02 | OneDrive / ZFS / herstel | inrichten en testen | — (TrueNAS-taken in de web-UI; methode D04) | — | **P1: controlelijst** |
| I03 | MediaGuard | upgrade na versiecontrole | bestaande app behouden (import, B08-002) | — | **P3: controlelijst + manifestkopie** (S-P3-450); import blijft afzonderlijk werk |
| I04 | Media Source Optimizer | behouden, 1.6-gate | bestaand; eigen alles-in-één image als Custom App | — | **P3: controlelijst + manifestkopie** (S-P3-450); import blijft afzonderlijk werk |
| I05 | my_house | installeren met apparaatgate | eigen image(s) als Custom App | — | **P3: manifestkopie** (S-P3-450); controlelijst `I05_MY_HOUSE.checklist.yaml` (S-P3-440) |
| I06 | Jellyfin | inventaris en herstelbewijs | bestaande Jellyfin behouden als D07 die meet | community · 1.3.24 · 12.4 | **P4: controlelijst**. Praktijkproeven volgen in P6. |
| I07 | Beszel | nieuw | Custom App (hub + agent) | (community `beszel-hub` · 1.1.22 · 0.21.1 bestaat) | **P1** |
| I08 | Uptime Kuma | nieuw | Custom App | (community · 1.2.25 · 2.5.9 bestaat) | **P1** |
| I09 | Authentik | nieuw | catalogusapp (meerdere containers) | community · 1.3.64 · 2026.8.5 | **P1** |
| I10 | Node-RED | nieuw | Custom App (ADR-0023) | (community · 1.4.35 · 5.0.9-24 bestaat) | **P2** |
| I11 | NetAlertX | nieuw | Custom App geblokkeerd op netwerkrechten | upstream 26.10.28 | [P5-controlelijst](../checklists/I11_NETALERTX.checklist.yaml) |
| I12 | AdGuard Home | proef op één apparaat | catalogusapp | community · 1.3.24 · v1.0.0 | [P5-manifest](adguard-home.manifest.yaml) |
| I13 | Gotify | nieuw | Custom App | (community · 1.1.17 · 3.1.1 bestaat) | **P1** |
| I14 | PostgreSQL + pgvector | alleen bij AI-search | ingebouwd in `zoeken-en-persoonlijke-ai` (alles-in-één) | (community `postgres` bestaat, niet nodig) | n.v.t. |
| I15 | Immich | behouden en beschermen | bestaand behouden (import) | community · 1.14.62 · v3.3.6 | **P3: controlelijst**; import blijft afzonderlijk werk, zonder installeerbaar manifest naast deze lijst |
| I16 | Prowlarr | behouden | bestaand (import) | community · 1.5.34 · 3.0.0.5737 | **P3: controlelijst**; import blijft afzonderlijk werk, zonder installeerbaar manifest naast deze lijst |
| I17 | SABnzbd | behouden | bestaand (import) | community · 1.3.22 · 5.1.3 | **P3: controlelijst**; import blijft afzonderlijk werk, zonder installeerbaar manifest naast deze lijst |
| I18 | qBittorrent | behouden | bestaand (import) | community · 1.4.21 · 5.3.0 | **P3: controlelijst**; herstel en import blijven afzonderlijk werk |
| I19 | Seerr | behouden, via MediaGuard | bestaand (import) | community · 1.0.20 · v3.5.1 | **P3: controlelijst**; herstel en import blijven afzonderlijk werk |
| I20 | Bazarr | behouden tot parity | bestaand (import) | community · 1.3.18 · 1.6.2 | **P3: controlelijst**; pariteit, herstel en gebruikersbesluit afzonderlijk bewijzen |
| I21 | Tailscale / Cloudflared | behouden, scope verstrengen | bestaand (import) | community `tailscale` 1.4.26 · v1.104.4; `cloudflared` 2.0.36 · 2026.10.7 | P5 (import) |
| I22 | Navidrome (optioneel) | niet automatisch | catalogusapp alleen na besluit D18 | community · 1.3.23 · 0.64.2 | P4 (optioneel) |
| I23 | Home Control Hub (`ai-server-manager` + executor) | bouwen, daarna installeren | eerste installatie handmatig: `install/truenas/INSTALLATIE.md` | — | **P1: kopie + rooktest** |
| I24 | Jelly Leon (fork) | release apart beproeven | eigen image als Custom App naast Emby | — | **P4-controlelijst**. Praktijkproeven volgen in P6. |
| I25 | Zoeken en persoonlijke AI | bouwen, opt-in | eigen alles-in-één image als Custom App | — | **P5** · [Gepind zoekmanifest](zoeken-en-persoonlijke-ai.manifest.yaml) |
| I26 | Overige (Snapotter, Teeltboek, Uren, Plex, Emby, JellyPlex Watched, FlareSolverr, Readarr) | inventariseren, niet verwijderen | bestaand (import); nooit automatisch verwijderen | o.a. stable `plex`, stable `emby`, community `readarr`, `flaresolverr`, `snapotter` | P5 (import) |

Versies in de kolom "Catalogus" zijn alleen voor de apps van P1 opnieuw gecontroleerd in `f7a38eb`; de andere zijn de
stand van `f142a16` en worden in hun eigen fase opnieuw vastgesteld. "Bestaand (import)": het manifest beschrijft de
**werkelijke** installatie zoals `app.query`/`app.config` die teruggeeft (B08-002, eigenaar `appstore-en-fork`), zonder
herinstallatie of rechtenwijziging. Werkelijke versies, paden en poorten komen van de NAS (D02, D07).

Regels voor elk manifest (contract 1.2 §9): poorten **alleen** in `network.ports`, geen
`network.joins_platform_network`; GPU alleen met `privileges.gpu: intel_render` + reden + D08 en `custom_app.gpu:
{param: gpu}`; de tag gaat het formulier in als `<tag>@<digest>`; `HUB_*`-variabelen staan nooit in een manifest (de
Hub geeft ze mee bij `app.install`); een verdwenen catalogusversie geeft `version_unavailable`, nooit stil een nieuwere.
