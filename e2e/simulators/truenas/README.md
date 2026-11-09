# TrueNAS-simulator (alleen voor tests)

> **Dit is geen TrueNAS.** De simulator bootst het deel van de TrueNAS-middleware-API na dat het platform nodig heeft,
> zodat Hub, executor en lifecycle-dienst zonder echte NAS getest kunnen worden. **Afwijkingen van de echte API zijn
> mogelijk en zelfs zeker** (vereenvoudigde schema's, bewuste strengheid). Een test die alleen tegen deze simulator
> slaagt heet **Sim-getest**, nooit "geslaagd op de NAS" (contract §11).
>
> Bronnen waarop het gedrag is gebaseerd (alleen gelezen): `truenas/middleware` op tags **TS-25.04.2.6**
> (`244b717`) en **TS-25.10.8** (`29ed02b`), `truenas/apps` commit `f142a16` (2026-11-06), `truenas/webui`
> TS-25.10.8, `truenas/api_client` TS-25.10.8. Details per bewering: `docs/platform-v2/TRUENAS_APP_API.md`.

## Starten

```bash
pip install -e e2e/simulators/truenas            # of: docker build -t jlp-truenas-sim:dev e2e/simulators/truenas
python -m truenas_sim --host 127.0.0.1 --port 8080 --version 25.10.8   # of 25.04.2.6
```

Standaard-API-keys (alleen simulator; opvraagbaar via `GET /_sim/keys`):

| Gebruiker | Rollen | Gebruik |
|---|---|---|
| `hub-readonly` (key-ID 1) | `READONLY_ADMIN` | Hub-api (alleen lezen) |
| `executor` (key-ID 2) | `READONLY_ADMIN`, `APPS_WRITE`, `SNAPSHOT_WRITE`, `DATASET_WRITE`, `POOL_WRITE` | executor |

## Protocol

- **Transport:** WebSocket op `/api/current` (= nieuwste API-versie van het profiel) of `/api/<api-versie>`
  (bijv. `/api/v25.04.2`). `GET /api/versions` geeft de lijst, `GET /api/boot_id` het huidige boot-ID.
  Onbekende versie → verbinding direct gesloten (code 1008); API onbereikbaar → 1013.
- **Berichten:** JSON-RPC 2.0. Verzoek `{"jsonrpc":"2.0","id":<int|str>,"method":"<naam>","params":[…]}`;
  antwoord `{"jsonrpc":"2.0","id":…,"result":…}` of `{"jsonrpc":"2.0","id":…,"error":{"code":…,"message":…,"data":…}}`.
  `params` moet een lijst zijn. Gelijktijdige aanroepen per verbinding: max. 20 (daarna `-32000`).
- **Foutcodes** (zoals `truenas_api_client.jsonrpc.JSONRPCError`): `-32700` ongeldige JSON, `-32600` ongeldig
  verzoek, `-32601` "Method does not exist", `-32602` "Invalid params" (validatie; `data.extra` =
  `[[attribuut, melding, errno]]`), `-32001` "Method call error" met
  `data = {"error": errno, "errname": …, "reason": …, "trace": null, "extra": …}`.
  Niet aangemeld → `errno 207 ENOTAUTHENTICATED` ("Not authenticated"); geen recht → `errno 13 EACCES`
  ("Not authorized").
- **Aanmelden:** `auth.login_with_api_key("<id>-<key>")` → `true|false`, of
  `auth.login_ex({"mechanism":"API_KEY_PLAIN","username":…,"api_key":…})` → `{"response_type":"SUCCESS"|"AUTH_ERR"}`.
- **Jobs:** job-methoden (o.a. `app.create/update/delete/start/stop/redeploy/rollback/upgrade`, `catalog.sync`)
  geven direct een **job-ID** (int). Een key zonder `FULL_ADMIN` ziet, volgt en breekt alleen **eigen** jobs af
  (anders `errno 1 EPERM` "Job is not owned by current session"; ook jobevents worden niet doorgegeven), zoals
  middlewared `job.py` `credential_access_error` (TS-25.10.8). Status via `core.get_jobs([["id","=",<id>]])`
  (`state` WAITING → RUNNING → SUCCESS | FAILED | ABORTED, `progress`, `result`, `error`, `exc_info`), resultaat via
  `core.job_wait(<id>)` (fout als de job faalde), afbreken via `core.job_abort`. Argumenten met waarden worden
  getoond als `"********"`. Tijden als `{"$date": <epoch-ms>}` (aanname uit middlewared-JSON).
- **Events:** `core.subscribe("core.get_jobs" | "app.query")` → abonnement-ID; daarna notificaties
  `{"jsonrpc":"2.0","method":"collection_update","params":{"msg":"added|changed|removed","collection":…,"id":…,"fields":…}}`.
- **Filters/opties** voor `*.query`: `[[veld, op, waarde], …]` met `= != > < >= <= in nin ^ $ ~` en `["OR", […]]`;
  opties `get`, `limit`, `offset`, `order_by`, `select`, `count`, en bij `app.query` `extra.retrieve_config`.

## Methoden per versie

| Methode | 25.04 | 25.10 | Opmerking |
|---|---|---|---|
| `auth.login_with_api_key`, `auth.login_ex`, `auth.me`, `auth.logout` | ✓ | ✓ | |
| `core.ping`, `core.get_methods`, `core.get_jobs`, `core.job_wait`, `core.job_abort`, `core.subscribe`, `core.unsubscribe` | ✓ | ✓ | `get_methods`: vereenvoudigde metadata (`job`, `roles`, `filterable`) |
| `system.info`, `system.version`, `system.version_short`, `system.boot_id`, `system.ready` | ✓ | ✓ | versie instelbaar per profiel |
| `pool.query`, `pool.dataset.query`, `pool.dataset.create` | ✓ | ✓ | gezondheid instelbaar |
| `zfs.snapshot.query/create/clone/rollback` | ✓ | — | in 25.10 privé; simulator: **afwezig** (`-32601`) |
| `pool.snapshot.query/create/clone/rollback` | — | ✓ | nieuw publiek in 25.10 |
| `pool.snapshottask.query`, `cloudsync.query`, `replication.query`, `alert.list` | ✓ | ✓ | taken standaard leeg/minimaal (D04 onbekend) |
| `disk.query`, `disk.temperatures`, `smart.test.results` | ✓ | ✓ | SMART-methodenaam per versie **onzeker** |
| `app.query`, `app.config`, `app.used_ports`, `app.gpu_choices`, `app.image.query`, `app.rollback_versions`, `app.upgrade_summary` | ✓ | ✓ | |
| `app.create`, `app.update`, `app.delete`, `app.start`, `app.stop`, `app.redeploy`, `app.rollback`, `app.upgrade` | ✓ | ✓ | jobs |
| `app.available`, `catalog.get_app_details`, `catalog.config`, `catalog.trains`, `catalog.update`, `catalog.sync` | ✓ | ✓ | nep-catalogus |

**Standaard Custom App** = `app.create({"app_name", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8",
"values": {image, ports, storage, envs, resources, …}})` — exact het formulier van de TrueNAS-web-UI.
Nep-catalogus: `stable/ix-app` (1.4.7*, 1.4.8) en `community/` `authentik`, `immich`, `uptime-kuma`, `gotify`,
`node-red`, `adguard-home`, `beszel-hub` — telkens de echte laatste versie uit `truenas/apps` plus één **fictieve
vorige versie** (`*`, app_version met "(sim-vorige)") om upgrade en rollback te kunnen testen. Community-apps zijn pas
zichtbaar in `app.available` na `catalog.update({"preferred_trains": ["stable","community"]})`.

## Gedrag dat het echte systeem volgt (bewust)

- `app.update` doet bij catalogusapps een **ondiepe merge** van `values` (alleen `{"image":{"tag":…}}` sturen wist
  `repository` → validatiefout). Stuur altijd het volledige subobject.
- `app.rollback` werkt alleen tussen eerder geïnstalleerde **catalogusversies**; een image-tagwijziging van een
  ix-app is daarmee niet terug te draaien (`rollback_versions` = `[]`). `app.upgrade` op een ix-app wisselt geen
  versie maar haalt alleen images opnieuw op.
- Poortconflicten en een niet-bestaand hostpad laten de **job** mislukken met een validatiefout (zoals de echte
  `normalize_and_validate_values` binnen de job).
- Mislukt `app.create` ná de validatie (bijv. storing `job_fail`), dan ruimt de job op: de app verdwijnt
  (`collection_update` removed) en tijdens die installatie aangemaakte ixVolume-datasets ook (zoals
  `crud.py` `create_internal`/`remove_failed_resources`). Alleen een herstart midden in de job laat de app half
  toegepast (`CRASHED`) achter.
- `app.delete` verwijdert ixVolumes alleen met `remove_ix_volumes=true`; hostpaden nooit.
- API-key over een onversleutelde verbinding → key **ingetrokken** (storing `insecure_transport`, naar de NEP-regel
  in `auth.py`).

## Bewuste afwijkingen (strenger dan echt)

| Afwijking | Waarom |
|---|---|
| `app.create`/`app.update` met `custom_app=true`, `custom_compose_config(_string)` → **geweigerd** (`-32001`, "SIM-BELEID") | het platform mag nooit via YAML/compose installeren; echte TrueNAS accepteert dit wél |
| Tag leeg/`latest`, catalogusversie `latest`, `privileged`, `host_network`, `remove_ix_volumes=true`, snapshot-rollback | worden uitgevoerd zoals echt, maar **geregistreerd** in `GET /_sim/violations` zodat tests ze kunnen afkeuren |
| `zfs.snapshot.*` in 25.10 afwezig | echt is het privé (aanroepbaar met waarschuwing); afwezig dwingt het publieke `pool.snapshot.*` af |
| Geen echte schema-validatie (pydantic) per methode | alleen de velden die het platform gebruikt worden gecontroleerd |
| Job-zichtbaarheid zonder `read_roles` per job | echt mag een rol uit de `read_roles` van een job die job ook lezen; de simulator toont alleen eigen jobs (strenger) |
| `core.get_methods` levert geen `accepts`/`returns`-schema's | lijst + `job`/`roles` volstaat voor capability-detectie |

## Test-beheer-API (`/_sim`, zonder auth — alleen in test/CI, bind op 127.0.0.1)

| Route | Doel |
|---|---|
| `GET /_sim/state`, `GET /_sim/keys`, `GET /_sim/violations`, `GET /_sim/calls` | toestand, keys, overtredingen, aanroeplog (met `write`-vlag per aanroep) |
| `POST /_sim/reset {"version": "25.04.2.6"}` | begintoestand (optioneel andere versie) |
| `POST /_sim/reboot {"downtime_ms": 500}` | herstart: verbindingen dicht, jobs weg, nieuw boot-ID |
| `POST /_sim/fixtures {apps, datasets, cloudsync_tasks, replication_tasks, snapshot_tasks, extra_alerts, gpus}` | scenario-opbouw (bijv. bestaande Jellyfin/MediaGuard als geïnstalleerde apps) |
| `POST /_sim/faults/<naam>` / `DELETE /_sim/faults/<naam>` | storing aan/uit |

Storingen: `pool_degraded {"pool","status"}`, `api_unreachable`, `key_revoked {"key_id"}`, `slow {"delay_ms"}`,
`job_fail {"method","count","error"}`, `reboot_during_apply {"method","downtime_ms"}` (de wijziging is half
toegepast: app blijft `CRASHED`, job verdwijnt), `smart_fail {"disk"}`, `insecure_transport`.

## Tests

```bash
cd e2e/simulators/truenas && python -m pytest -q
```
