# Installeerbare manifests per 03_INSTALLATIE-document (I01–I26)

Deze repo levert per I-document een manifest volgens contract 1.3 §9 (eigenaar van de standaard: `appstore-en-fork`),
of — voor I-documenten die niets installeren — een controlelijst (`install/checklists/`, documentsoort
`kind: checklist` met `install.method: none`, ADR-0015/0019).
Installatieregel (contract §1/§2): standaard Custom App (`ix-app`) met één gepinde image, of de **officiële
TrueNAS-catalogusapp** met vaste versie voor apps met meerdere containers. Nooit YAML/compose.

> **Status (eerlijk):** alle manifesten hieronder zijn gevalideerd met `jl-manifest validate --strict` van
> appstore-en-fork volgens contract 1.3 (geaccepteerde waarschuwingen in het manifest zelf, `accepted_findings`;
> `overgang-1.3.yaml` alleen voor de I23-kopieën van ai-server-manager en het monitorplan van de Beszel-agent), vertaald naar
> `app.create`-waarden en tegen de TrueNAS-simulator gecontroleerd (**Sim-getest**). Op een echte TrueNAS is niets
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
> import van de flowbundels, melding, actieaanvraag, hartslag, herstel van een flowversie, herstart en stoppen —
> **Sim-getest**. Het manifest levert op een NAS alleen een beveiligde, lege, alleen-lezen Node-RED; beheerder,
> flows en service-token zijn **Geblokkeerd** (`../node-red/README.md`, ADR-0023).

## Wat er nu is (P1 en P2)

| I-doc | Bestand | Methode | Versie · image of catalogus | Bijzonderheden |
|---|---|---|---|---|
| I01 | `../checklists/I01_TRUENAS_BASIS.checklist.yaml` | controlelijst | — | 8 stappen, alleen-lezen-methoden per TrueNAS-versie, geen terminal |
| I02 | `../checklists/I02_BACKUP_EN_RESTORE.checklist.yaml` | controlelijst | — | 8 stappen; OneDrive-stappen NIET GEVALIDEERD (account) |
| I07 | `beszel-hub.manifest.yaml` | Custom App | 0.21.1 · `ghcr.io/henrygd/beszel/beszel@sha256:8514a69d…d695b770` | 568:568; meetdekking: `BESZEL_MEETDEKKING.md` |
| I07 | `beszel-agent.manifest.yaml` | Custom App | 0.21.1 · `ghcr.io/henrygd/beszel/beszel-agent@sha256:cb686801…bf052e41c` | geen catalogusapp; SSH-modus met alleen de publieke hubsleutel; geen host network, Docker-socket of GPU |
| I08 | `uptime-kuma.manifest.yaml` | Custom App | 2.5.9 · `ghcr.io/louislam/uptime-kuma@sha256:e88415a9…725591bd` | 568:568 (de catalogusapp draait als root); SQLite |
| I09 | `authentik.manifest.yaml` | catalogusapp | `community/authentik` 1.3.64 (app 2026.8.5) | vier datasets uit de datasetkiezer; geheimen door de Hub gegenereerd; geen Docker-socket |
| I10 | `node-red.manifest.yaml` | Custom App | 5.0.9-24 · `nodered/node-red@sha256:2ccd38b4…64d6896f` | 568:568 (de catalogusapp draait als 1000, zonder login en met alle nodes); beveiligd via `FLOWS`; monitor verwacht 403 op `/diagnostics`; flowbundels in `../node-red/` |
| I13 | `gotify.manifest.yaml` | Custom App | 3.1.1 · `ghcr.io/gotify/server@sha256:44fc5bbd…eb203b86` | 568:568; poort 8080 in de container; registratie uit |
| I23 | `ai-server-manager.manifest.yaml`, `ai-server-manager-executor.manifest.yaml` | Custom App (handmatig) | kopie van ai-server-manager `55ed151` | de twee formulieren uit `../truenas/INSTALLATIE.md`; digest volgt bij de release |

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
make nodered-rooktest   # Node-RED op digest als 568:568 tegen de Hub-simulator: stappen N1–N9 (Docker)
```

### Na de installatie (verplicht; `post_install_checks` in het manifest, de Hub toont ze als taak en de app is tot de bevestiging niet gezond)

| App | Wat de gebruiker direct doet (in de eigen web-UI van de app) | Controle door de Hub |
|---|---|---|
| Gotify | standaardaccount `admin` een eigen wachtwoord geven; per bron een applicatie (Hub, Uptime Kuma, back-up) | Basic `admin:admin` op `/current/user` → 401 |
| Beszel | eerste account aanmaken (tot dan kan iedereen op het LAN dat) | `GET /api/beszel/first-run` → `firstRun: false` |
| Uptime Kuma | eerste beheerder aanmaken; API-sleutel voor de Hub (alleen `/metrics`) | alleen via Socket.IO controleerbaar → bevestiging door de gebruiker |
| Authentik | initiële setup (`akadmin`) met MFA; API-token voor home-control-hub | `/-/health/ready/`; beheer via home-control-hub (B13) |
| Node-RED | niets in te stellen: de editor is alleen-lezen tot er een route is om een beheerder en flows te leveren (Geblokkeerd) | `/diagnostics` → 403, `POST /flows` zonder login → 401, `/auth/login` → type `credentials` |

Tokens en sleutels die de gebruiker zo maakt, gaan alleen in de vault van de Hub (nooit in een manifest of formulier).

## Overzicht alle I-documenten (plan)

Catalogusgegevens: `truenas/apps` commit `f7a38eb` (2026-11-09). De catalogus houdt per app alleen de nieuwste
versie bij (`docs/platform-v2/TRUENAS_APP_API.md` §5): dit is een momentopname, geen pin.

| I-doc | App | Aanpak (I-doc) | Installatiemethode | Catalogus (trein · versie · app_version) | Manifest |
|---|---|---|---|---|---|
| I01 | TrueNAS basis | behouden en valideren | — (host; alleen lezen via API) | — | **P1: controlelijst** |
| I02 | OneDrive / ZFS / herstel | inrichten en testen | — (TrueNAS-taken in de web-UI; methode D04) | — | **P1: controlelijst** |
| I03 | MediaGuard | upgrade na versiecontrole | bestaande app behouden (import, B08-002) | — | P3 (import) |
| I04 | Media Source Optimizer | behouden, 1.6-gate | bestaand; eigen alles-in-één image als Custom App | — | P3 (import) |
| I05 | my_house | installeren met apparaatgate | eigen image(s) als Custom App | — | P3 |
| I06 | Jellyfin | behouden, later fork | bestaand behouden (import) | community · 1.3.24 · 12.4 | P4 (import) |
| I07 | Beszel | nieuw | Custom App (hub + agent) | (community `beszel-hub` · 1.1.22 · 0.21.1 bestaat) | **P1** |
| I08 | Uptime Kuma | nieuw | Custom App | (community · 1.2.25 · 2.5.9 bestaat) | **P1** |
| I09 | Authentik | nieuw | catalogusapp (meerdere containers) | community · 1.3.64 · 2026.8.5 | **P1** |
| I10 | Node-RED | nieuw | Custom App (ADR-0023) | (community · 1.4.35 · 5.0.9-24 bestaat) | **P2** |
| I11 | NetAlertX | nieuw | Custom App met één gepinde upstream-image (geen catalogusapp gevonden) | — | P5 |
| I12 | AdGuard Home | nieuw met fail-safe | catalogusapp | community · 1.3.21 · v0.107.80 | P5 |
| I13 | Gotify | nieuw | Custom App | (community · 1.1.17 · 3.1.1 bestaat) | **P1** |
| I14 | PostgreSQL + pgvector | alleen bij AI-search | ingebouwd in `zoeken-en-persoonlijke-ai` (alles-in-één) | (community `postgres` bestaat, niet nodig) | n.v.t. |
| I15 | Immich | behouden en beschermen | bestaand behouden (import) | community · 1.14.63 · v3.3.6 | P3 (import) |
| I16 | Prowlarr | behouden | bestaand (import) | community · 1.5.34 · 3.0.0.5737 | P3 (import) |
| I17 | SABnzbd | behouden | bestaand (import) | community · 1.3.22 · 5.1.3 | P3 (import) |
| I18 | qBittorrent | behouden | bestaand (import) | community · 1.4.21 · 5.3.0 | P3 (import) |
| I19 | Seerr | behouden, via MediaGuard | bestaand (import) | community · 1.0.20 · v3.5.1 | P3 (import) |
| I20 | Bazarr | behouden tot parity | bestaand (import) | community · 1.3.18 · 1.6.2 | P3 (import) |
| I21 | Tailscale / Cloudflared | behouden, scope verstrengen | bestaand (import) | community `tailscale` 1.4.26 · v1.104.4; `cloudflared` 2.0.36 · 2026.10.7 | P5 (import) |
| I22 | Navidrome (optioneel) | niet automatisch | catalogusapp alleen na besluit D18 | community · 1.3.23 · 0.64.2 | P4 (optioneel) |
| I23 | Home Control Hub (`ai-server-manager` + executor) | bouwen, daarna installeren | eerste installatie handmatig: `install/truenas/INSTALLATIE.md` | — | **P1: kopie + rooktest** |
| I24 | Jelly Leon (fork) | bouwen, stage installeren | eigen image als Custom App | — | P4 |
| I25 | Zoeken en persoonlijke AI | bouwen, opt-in | eigen alles-in-één image als Custom App | — | P5 |
| I26 | Overige (Snapotter, Teeltboek, Uren, Plex, Emby, JellyPlex Watched, FlareSolverr, Readarr) | inventariseren, niet verwijderen | bestaand (import); nooit automatisch verwijderen | o.a. stable `plex`, stable `emby`, community `readarr`, `flaresolverr`, `snapotter` | P5 (import) |

Versies in de kolom "Catalogus" zijn alleen voor de apps van P1 opnieuw gecontroleerd in `f7a38eb`; de andere zijn de
stand van `f142a16` en worden in hun eigen fase opnieuw vastgesteld. "Bestaand (import)": het manifest beschrijft de
**werkelijke** installatie zoals `app.query`/`app.config` die teruggeeft (B08-002, eigenaar `appstore-en-fork`), zonder
herinstallatie of rechtenwijziging. Werkelijke versies, paden en poorten komen van de NAS (D02, D07).

Regels voor elk manifest (contract 1.2 §9): poorten **alleen** in `network.ports`, geen
`network.joins_platform_network`; GPU alleen met `privileges.gpu: intel_render` + reden + D08 en `custom_app.gpu:
{param: gpu}`; de tag gaat het formulier in als `<tag>@<digest>`; `HUB_*`-variabelen staan nooit in een manifest (de
Hub geeft ze mee bij `app.install`); een verdwenen catalogusversie geeft `version_unavailable`, nooit stil een nieuwere.
