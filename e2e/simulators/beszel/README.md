# Beszel-hub-simulator (alleen voor tests)

> **Dit is geen Beszel.** Hij bootst de alleen-lezen-API van de Beszel-hub (PocketBase) na die de Hub gebruikt
> (B09-002, I07-07-03/06). De agent zelf (SSH/WebSocket) wordt niet nagebootst; zijn toestand is testbediening.
> Een test die alleen hiertegen slaagt is **Sim-getest**.

Bron (alleen gelezen): `henrygd/beszel` tag **v0.21.1** (`e2ed147`): `internal/hub/api.go` (routes `/api/beszel/…`),
`internal/users/users.go` (eerste gebruiker), `internal/migrations/0_collections_snapshot_0_20_0.go` (collecties,
velden, listRules), `internal/entities/system/system.go` (`Info`, `Stats`), en PocketBase **v0.40.5** (`apis/health.go`,
`tools/router/error.go`). Image in het manifest: `ghcr.io/henrygd/beszel/beszel:0.21.1@sha256:8514a69d…d695b770`.

## Starten

```bash
python -m beszel_sim --host 127.0.0.1 --port 30333
```

`GET /_sim/sleutels` → `{"publieke_sleutel": "ssh-ed25519 …", "systemen": ["<id>"]}`.

## Protocol

| Route | Aanmelding | Antwoord |
|---|---|---|
| `GET /api/health` | geen | `{"message":"API is healthy.","code":200,"data":{}}` |
| `GET /api/beszel/first-run` | geen | `{"firstRun": true|false}` — **controle na installatie: moet false zijn** (anders kan iedereen op het LAN de eerste beheerder worden) |
| `POST /api/beszel/create-user` | geen | `{email,password}` → `{"msg":"User created"}`; bestaat er al een gebruiker → `403 {"err":"Forbidden"}`; leeg → `400 {"err":"Bad request"}` |
| `POST /api/collections/users/auth-with-password` | geen | `{identity,password}` → `{"token":…, "record":{…}}`; fout → `400 {"data":{},"message":"Failed to authenticate.","status":400}` |
| `POST /api/collections/users/auth-refresh` | token | nieuw token; ongeldig → `401` |
| `GET /api/beszel/info` (alias `/getkey`) | token | `{"key":"ssh-ed25519 …","v":"0.21.1","cu":false}` — de publieke sleutel voor de agent (`KEY`) |
| `GET /api/collections/systems/records` | token | PocketBase-lijst `{page,perPage,totalItems,totalPages,items:[{id,name,status(up|down|paused|pending),host,port,info:{u,cpu,mp,dp,v,la,t,ct,…},users,created,updated}]}` |
| `GET /api/collections/system_stats/records?filter=(system='<id>' && type='1m')&sort=-created&perPage=1` | token | `items:[{id,system,type,stats:{cpu,m,mu,mp,mb,mz,d,du,dp,la,t:{…},mem:[totaal,gebruikt,buff/cache,arc]},created}]` |
| `GET /api/collections/{alerts,containers,container_stats}/records` | token | lege lijst (containers vereisen de Docker-socket, die is verboden) |

Token: `Authorization: <token>` of `Bearer <token>`. Tijden in PocketBase-formaat `YYYY-MM-DD HH:MM:SS.sssZ`.
**Bewust nagebootst:** een lijst **zonder** (geldig) token geeft `200` met een **lege** lijst (PocketBase-listRule), geen
401. Een client mag "leeg" nooit als "geen systemen/gezond" lezen zonder eerst aangemeld te zijn.

## Foutinjectie

Algemeen: `http_500/502/503`, `traag`, `ongeldige_json`, `te_groot`, `redirect`. Specifiek:

| Fout | Effect |
|---|---|
| `agent_down` | systeem `down`, laatste meting 45 min oud |
| `verouderd` | systeem blijft `up`, laatste meting `waarde` min oud (standaard 30) → de Hub hoort `unknown` te tonen |
| `geen_arc` | geen `mz`, `mem[3] = 0` (ARC niet zichtbaar in de container) |
| `token_verlopen` | elk token ongeldig (401 bij refresh, lege lijsten) |
| `dubbele_host` | tweede systeemrecord met dezelfde naam (bijv. hetzelfde systeem twee keer toegevoegd, of een agent in WebSocket-modus zonder bewaarde vingerafdruk; I07-07-06) |

Extra systeem: `POST /_sim/systeem {"naam": …}`.

## Niet nagebootst (afwijkingen)

De agentverbinding, realtime-abonnementen (SSE), schrijfroutes (systemen toevoegen, alerts), universele tokens,
SMART/systemd/ZFS-routes, de PocketBase-beheer-API (`/_/`, back-ups) en filters behalve `system=` en `type=`. Of
ARC en temperaturen in een echte container zichtbaar zijn: **NIET GEVALIDEERD** (`install/manifests/BESZEL_MEETDEKKING.md`).
