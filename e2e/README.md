# Integrale E2E-omgeving (opzet P0; uitvoering in P6)

Deze map bevat de testomgeving waarin **alle** platform-apps samen draaien tegen simulators, om de scenario's
EX01–EX50 uit `docs/platform-v2/05_TESTS/INTEGRALE_E2E_TESTMATRIX.md` te draaien zonder echte NAS. `docker compose`
is hier toegestaan (contract §1: alleen ontwikkeling, CI en deze E2E-omgeving) — het is **geen installatiemethode**.

> **Eerlijkheid:** een geslaagde run hier is **Sim-geslaagd** (validatie in EVIDENCE.json), nooit "Geslaagd". Status
> blijft `Bezig` of `Geblokkeerd` tot een echte run op de NAS (contract §11). Scenario's met echte hardware,
> accounts of clients zijn hier per definitie **NIET GEVALIDEERD**.

## Wat er nu (P0) al is

| Onderdeel | Pad | Status |
|---|---|---|
| TrueNAS-simulator (JSON-RPC 2.0/WebSocket, 25.04/25.10, apps/catalogus/snapshots/jobs, storingsinjectie) | `simulators/truenas/` | gebouwd + eigen tests (Sim-getest) |
| Hub-simulator volgens contract 1.2 (enroll exact §3.3, `services/self`, rotatie, dienst-JWT, `key-rotations`, inbox) — alleen voor geïsoleerde dienst-tests | `simulators/hub/hub_sim.py` | gebouwd, gebruikt in `tests/e2e`, `tests/conformance` en `apps/api/tests` |
| Conformiteitstests Hub ↔ dienst (contract 1.2, JP10): dezelfde tests tegen de simulator (CI) en tegen de echte Hub | `../tests/conformance/`, `conformance/echte_hub.py` | gebouwd; tegen de echte Hub lokaal gedraaid (zie hieronder) |
| Dienst-E2E jelly-leon-platform (alles-in-één image + Hub-simulator) | `../tests/e2e/` | gebouwd (Sim-getest) |
| Integrale compose + scenariorunner | `integral/` (P6) | **nog niet gebouwd** |

## Conformiteit met contract 1.2 (JP10)

`tests/conformance` controleert, met de eigen clientcode van deze dienst, of een Hub zich volgens §3.3–§3.8 en §4.5
gedraagt (422 zonder het token te verbruiken, één 401-code, volledige antwoordcontrole + vingerafdruk, toegestane
herhaling vóór het eerste gebruik, `services/self`, `self/rotate`, `jwks`, `key-rotations`), en koppelt daarna de eigen
worker echt met die Hub en gebruikt een door die Hub gemaakte gebruikers-JWT op de eigen API (pinning, `token_use`,
`jti`-replaycache, outbox naar de inbox).

- Tegen de **simulator**: `python -m pytest -q tests/conformance` (ook in CI).
- Tegen de **echte Hub** (naburige checkout `../ai-server-manager` met eigen `.venv`, lokale PostgreSQL):
  `python e2e/conformance/echte_hub.py`. Het script start de Hub met een wegwerp-datamap en een eigen
  `jlp_`-database, maakt het eerste beheerdersaccount met de setupcode uit de log, vraagt twee enrollment-tokens aan,
  draait dezelfde tests en ruimt alles op. Dit is een lokale integratietest, **geen** NAS-test.

## Opbouw (P6)

```
e2e/
  integral/compose.yaml        # bouwt de alles-in-één images uit de naburige repo-mappen (../../<repo>)
  integral/scenarios/EXnn_*.py # één pytest-bestand per scenario, met eigenaar, build/digest en bewijs
  integral/run.py              # draait een selectie, schrijft bewijs naar evidence/<run-id>/ en EVIDENCE.json-regels
  simulators/<naam>/           # één simulator per externe afhankelijkheid
```

**Images uit de naburige repo-mappen** (werkmap met de 11 repos naast elkaar; in CI: checkout van elke repo op
dezelfde branch). Elke repo levert één alles-in-één image (`deploy/allinone/Dockerfile`):

| Dienst in de E2E | Bron (build-context) | Poort | Rol in de test |
|---|---|---|---|
| `ai-server-manager` (Hub) | `../../ai-server-manager` | 30400 | echte Hub: policy, approvals, UI (Playwright 360/390/768/1440) |
| `ai-server-manager-executor` | `../../ai-server-manager` (executor-Dockerfile) | 30410 | echte executor → TrueNAS-simulator |
| `home-control-hub` | `../../home-control-hub` | 30420 | integraties B10/B11/B13/B14 |
| `appstore-en-fork` | `../../appstore-en-fork` | 30430 | manifesten, import, forks |
| `jelly-leon-platform` | `../..` (deze repo) | 30440 | lifecycle (install/update/rollback/uninstall vanaf P2) |
| `zoeken-en-persoonlijke-ai` | `../../zoeken-en-persoonlijke-ai` | 30450 | zoeken + spraak |
| `jelly-leon` | `../../jelly-leon` (+ web) | 8096→30460 | mediaserver-fork |
| `mediaguard` | `../../media-guard-ultimate` | eigen | bestaande app (echte image, testdata) |
| `media-source-optimizer` | `../../media-source-optimizer` (`deploy/allinone`) | 38081 | bestaande app |
| `my_house` | `../../my_house` | eigen | bestaande app (alleen met apparaat-simulators) |

**Simulators** (gebouwd = ✓; overige volgen in P6, telkens met README "afwijkingen mogelijk"):
✓ `truenas` · ✓ `hub` (alleen geïsoleerd) · `arr` (Sonarr/Radarr/Prowlarr/Seerr-API) · `sabnzbd` · `qbittorrent` ·
`gree` (lokaal UDP-protocol) · `homewizard` (P1/Energy Socket, alleen lezen) · `zigbee` · `immich` (alleen-lezen-API) ·
`onedrive` (offsite-doel + herstel) · `claude-credits` (lege credits) · `stt` (onzekere transcriptie) ·
`upstream-git` (Jellyfin-upstream met incompatibele commit). Officiële images (Uptime Kuma, Gotify, Node-RED,
AdGuard Home, NetAlertX) draaien op gepinde digest in de compose (alleen E2E).

Regels: geen echte credentials (alle keys uit de simulators), geen uitgaand internet tijdens scenario's
(`internal: true`-netwerk), alles op `127.0.0.1`, resources beperkt (deze bouwomgeving: 4 CPU / 15 GB gedeeld —
images één voor één bouwen, `nice`), opruimen na de run.

## Mapping EX01–EX50

Legenda uitvoerbaar: **Sim** = volledig met simulators; **Sim+** = met simulator, maar de kern vergt later een echte
run; **Echt** = alleen op echte NAS/hardware/clients/accounts (hier: Geblokkeerd / NIET GEVALIDEERD).

| ID | Scenario | Betrokken | Simulator / fixture | Uitvoerbaar |
|---|---|---|---|---|
| EX01 | MediaGuard offline | Hub, MSO, MediaGuard | container stoppen | Sim |
| EX02 | MSO offline | MediaGuard, MSO | container stoppen + arr/sab-sim | Sim |
| EX03 | Seerr-episode gerouteerd | MediaGuard, Seerr | arr-sim | Sim |
| EX04 | Anime-verzoek | MediaGuard | arr-sim (anime-instance) | Sim |
| EX05 | Verkeerd episodebestand | MediaGuard | testbestand + quarantaine | Sim |
| EX06 | MediaGuard-noodstop | MediaGuard, Hub, MSO | API-noodstop | Sim |
| EX07 | Stilstaande A310-lane | MediaGuard, Hub | worker-stub | Sim+ (GPU echt) |
| EX08 | Playback tijdens AV1 | Jelly Leon, MediaGuard | — | Echt (GPU) |
| EX09 | VMAF-fout | MediaGuard | foutinjectie | Sim |
| EX10 | HDR/Dolby Vision | Jelly Leon, clients | — | Echt (clients) |
| EX11 | ZFS-pool gedegradeerd | Hub, executor, **lifecycle** | truenas-sim `pool_degraded` | Sim |
| EX12 | Back-up verouderd | Hub (B12), **lifecycle** | onedrive-sim + truenas-sim `cloudsync` | Sim |
| EX13 | OneDrive-hersteltest | Hub (B12) | — (echt account) | Echt (account) |
| EX14 | Snapshot-restore naar sandbox | executor, **lifecycle** | truenas-sim `pool.snapshot.clone` | Sim |
| EX15 | DB-schema terugzetten | **lifecycle**, eigen apps | twee image-tags + migratie | Sim |
| EX16 | Credential verlopen | Hub, connectors | truenas-sim `key_revoked`, hub-sim | Sim |
| EX17 | Prompt-injectie in log | Hub (AI-runner) | logfixture | Sim |
| EX18 | Policy-klasse vervalst | Hub policy | voorstel met R0-claim | Sim |
| EX19 | Approval-drift (digest) | Hub, executor, **lifecycle** | truenas-sim + digestwissel | Sim |
| EX20 | Approval-replay | Hub, executor | dubbele ExecutionRequest | Sim |
| EX21 | AI-credits op | Hub | claude-credits-sim | Sim |
| EX22 | Hub offline | Uptime Kuma, Gotify | Hub stoppen | Sim+ (telefoon echt) |
| EX23 | Volledige NAS offline | — | — | Echt (externe host) |
| EX24 | Gree offline | my_house | gree-sim | Sim |
| EX25 | Gree shared-outdoor-conflict | my_house | gree-sim | Sim |
| EX26 | Afstandsbediening Gree OFF | my_house | gree-sim | Sim+ (apparaat echt) |
| EX27 | Boiler-schakelverbod | my_house, Hub, Node-RED | homewizard-sim | Sim |
| EX28 | P1-meterreset + zomertijd | home-control-hub (B14) | homewizard-sim | Sim |
| EX29 | Zigbee-sensor stale | my_house | zigbee-sim | Sim |
| EX30 | Gree-firmware incompatibel | my_house | gree-sim (bind faalt) | Sim+ |
| EX31 | Router-DNS-pilot | AdGuard, home-control-hub | AdGuard-image + testclient | Echt (router) |
| EX32 | AdGuard DNS offline | AdGuard | container stoppen | Sim+ |
| EX33 | Nieuw IoT-apparaat | NetAlertX, home-control-hub | NetAlertX-fixture | Sim+ |
| EX34 | Tailscale/tunnel ingetrokken | — | — | Echt (account) |
| EX35 | Jellyfin offline-profiel | Jelly Leon, clients | — | Echt (telefoon) |
| EX36 | Kind-ACL in zoekindex | zoeken, Jelly Leon | testbibliotheek | Sim |
| EX37 | Anime absolute numbering | MediaGuard, Bazarr | arr-sim | Sim |
| EX38 | LiveTV DVR-overlap | Jelly Leon | tuner-fixture | Sim+ |
| EX39 | MSO category `*` | MSO | sab-sim | Sim |
| EX40 | MSO providerfout | MSO | sab-sim (servers vallen uit) | Sim |
| EX41 | Private torrent | MSO | qbittorrent-sim | Sim |
| EX42 | Prowlarr-lus | MSO, Prowlarr | arr-sim | Sim |
| EX43 | Immich-privacy | zoeken, Hub | immich-sim | Sim |
| EX44 | Zoekbron intrekken | zoeken | immich-sim | Sim |
| EX45 | Node-RED-flow privilege | Node-RED, Hub | flow-fixture | Sim |
| EX46 | Node-RED-event 5× | Hub inbox | dubbele events | Sim |
| EX47 | Ongeldig manifest (privileged/hostmount) | appstore, **lifecycle**, executor | manifest-fixture + truenas-sim `violations` | Sim |
| EX48 | Fork-upstream incompatibel | appstore (B08-005), jelly-leon | upstream-git-sim | Sim |
| EX49 | Onzekere STT "verwijder" | zoeken (B15), Hub | stt-sim | Sim |
| EX50 | Migratie naar andere host | **lifecycle** (B08-006), Hub, my_house | twee truenas-sim-instanties (ander IP) | Sim+ |

**lifecycle** = deze repo (jelly-leon-platform) is eigenaar of mede-eigenaar van het scenario. Per scenario komt in
P6 een testbestand met: eigenaar, datum, build/digest van alle images, geredigeerde request/response, en de
EVIDENCE.json-regel (`validatie: "Sim-geslaagd"`/`"Sim-mislukt"`).
