# Installeerbare manifests per 03_INSTALLATIE-document (I01–I26) — overzicht en plan

Deze repo levert per I-document een manifest volgens contract §9 (eigenaar van de standaard: `appstore-en-fork`).
**Fase P0 levert alleen dit overzicht en het manifest van de eigen dienst**
(`deploy/manifest/jelly-leon-platform.manifest.yaml`); de overige manifests volgen in P1/P2, telkens met een
gepinde versie die op de installatiedatum opnieuw wordt gecontroleerd (I-docs: "Pin actuele … op installatiedatum").

Installatieregel (contract §1/§2): standaard Custom App (`ix-app`) met één gepinde image, of de **officiële
TrueNAS-catalogusapp** met vaste versie voor apps met meerdere containers. Nooit YAML/compose.
Catalogusgegevens: `truenas/apps` commit `f142a16` (2026-11-06) — de catalogus houdt per app alleen de nieuwste
versie bij (zie `docs/platform-v2/TRUENAS_APP_API.md` §5), dus dit is een momentopname, geen pin.

| I-doc | App | Aanpak (I-doc) | Installatiemethode (voorstel) | Catalogus (trein · versie · app_version) | Manifest |
|---|---|---|---|---|---|
| I01 | TrueNAS basis | behouden en valideren | — (host; alleen lezen via API) | — | n.v.t. |
| I02 | OneDrive / ZFS / herstel | inrichten en testen | — (TrueNAS-taken; methode onbekend, D04) | — | n.v.t. |
| I03 | MediaGuard | upgrade na versiecontrole | bestaande app behouden (import, B08-002) | — | P1 (import) |
| I04 | Media Source Optimizer | behouden, 1.6-gate | bestaand; eigen alles-in-één image als Custom App | — | P1 (import) |
| I05 | my_house | installeren met apparaatgate | eigen image(s) als Custom App | — | P2 |
| I06 | Jellyfin | behouden, later fork | bestaand behouden (import) | community · 1.3.24 · 12.4 | P1 (import) |
| I07 | Beszel | nieuw | catalogusapp | community `beszel-hub` · 1.1.22 · 0.21.1 | P2 |
| I08 | Uptime Kuma | nieuw | catalogusapp | community · 1.2.25 · 2.5.9 | P2 |
| I09 | Authentik | nieuw | catalogusapp (meerdere containers) | community · 1.3.63 · 2026.8.5 | P2 |
| I10 | Node-RED | nieuw | catalogusapp | community · 1.4.35 · 5.0.9-24 | P2 |
| I11 | NetAlertX | nieuw | Custom App met één gepinde upstream-image (geen catalogusapp gevonden) | — | P2 |
| I12 | AdGuard Home | nieuw met fail-safe | catalogusapp | community · 1.3.21 · v0.107.80 | P2 |
| I13 | Gotify | nieuw | catalogusapp | community · 1.1.17 · 3.1.1 | P2 |
| I14 | PostgreSQL + pgvector | alleen bij AI-search | ingebouwd in `zoeken-en-persoonlijke-ai` (alles-in-één) | (community `postgres` 1.5.46 bestaat, niet nodig) | n.v.t. |
| I15 | Immich | behouden en beschermen | bestaand behouden (import) | community · 1.14.62 · v3.3.6 | P1 (import) |
| I16 | Prowlarr | behouden | bestaand (import) | community · 1.5.34 · 3.0.0.5737 | P1 (import) |
| I17 | SABnzbd | behouden | bestaand (import) | community · 1.3.22 · 5.1.3 | P1 (import) |
| I18 | qBittorrent | behouden | bestaand (import) | community · 1.4.21 · 5.3.0 | P1 (import) |
| I19 | Seerr | behouden, via MediaGuard | bestaand (import) | community · 1.0.20 · v3.5.1 | P1 (import) |
| I20 | Bazarr | behouden tot parity | bestaand (import) | community · 1.3.18 · 1.6.2 | P1 (import) |
| I21 | Tailscale / Cloudflared | behouden, scope verstrengen | bestaand (import) | community `tailscale` 1.4.26 · v1.104.4; `cloudflared` 2.0.36 · 2026.10.7 | P1 (import) |
| I22 | Navidrome (optioneel) | niet automatisch | catalogusapp alleen na besluit D18 | community · 1.3.23 · 0.64.2 | P2 (optioneel) |
| I23 | Home Control Hub (`ai-server-manager` + executor) | bouwen, daarna installeren | eerste installatie handmatig: `install/truenas/INSTALLATIE.md` | — | P1 (na release) |
| I24 | Jelly Leon (fork) | bouwen, stage installeren | eigen image als Custom App | — | P4/P5 |
| I25 | Zoeken en persoonlijke AI | bouwen, opt-in | eigen alles-in-één image als Custom App | — | P3 |
| I26 | Overige (Snapotter, Teeltboek, Uren, Plex, Emby, JellyPlex Watched, FlareSolverr, Readarr) | inventariseren, niet verwijderen | bestaand (import); nooit automatisch verwijderen | o.a. stable `plex` 1.3.25, stable `emby` 1.4.14, community `readarr` 1.2.15, `flaresolverr` 1.2.17, `snapotter` | P1 (import) |

"Bestaand (import)": de app is al geïnstalleerd; het manifest beschrijft de **werkelijke** installatie zoals
`app.query`/`app.config` die teruggeeft (B08-002, eigenaar `appstore-en-fork`), zonder herinstallatie of
rechtenwijziging. Werkelijke versies, paden en poorten komen van de NAS (D02, D07), nooit uit deze tabel.
