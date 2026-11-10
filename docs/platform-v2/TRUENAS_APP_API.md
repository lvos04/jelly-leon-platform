# TrueNAS-API voor apps: standaard Custom App en catalogusapps (25.04 / 25.10)

Onderzoek voor B08-003/B08-004/B08-006 en de executor (`ai-server-manager-executor`). Doel: precies vastleggen hoe
het platform via de TrueNAS-API een **standaard Custom App** (het gewone formulier) en een **catalogusapp** aanmaakt,
bijwerkt, terugzet en verwijdert — **nooit** via `custom_compose_config`/*Install via YAML*.

> **Status: onderzoek op broncode, NIET GEVALIDEERD op een echte TrueNAS.** Alles hieronder is gelezen in de
> publieke broncode (tabel "Bronnen"). De officiële API-documentatie (`api.truenas.com`) was in de oude bouwomgeving
> niet bereikbaar; op 2026-11-09 (nieuwe machine) wél, en een steekproef is ertegen gecontroleerd — zie
> "Controle tegen de officiële API-documentatie" hieronder. Op de echte NAS moeten methodenamen en schema's altijd worden ontdekt met
> `core.get_methods` en `system.version` (contract §8). Het testbaar model hiervan is de TrueNAS-simulator
> (`e2e/simulators/truenas`, Sim-getest).

## Bronnen (alleen gelezen)

| Afkorting | Repository | Versie / commit |
|---|---|---|
| **MW-25.04** | `github.com/truenas/middleware` | tag `TS-25.04.2.6` = `244b717370fe35fb9eefc3096acfbc25f12b57b6` (laatste 25.04-release) |
| **MW-25.10** | `github.com/truenas/middleware` | tag `TS-25.10.8` = `29ed02b8082f38494b0d2896bada8151f0d59716` (laatste 25.10-release op 2026-11-09) |
| **APPS** | `github.com/truenas/apps` | `master` commit `f142a16a1e15da5f8517658f5a9ad34400972fc4` (2026-11-06) |
| **WEBUI** | `github.com/truenas/webui` | tag `TS-25.10.8` = `e85f786463fd2220c138bc6313eed02d53f97616` |
| **CLIENT** | `github.com/truenas/api_client` | tag `TS-25.10.8` = `53663ff1f1076a6789e1e2e7c48f2a5212d88d6c` |

Ook gezien (niet onderzocht): tags `TS-26.0.0-BETA.1…3` en `TS-27.0.0-RC.1` bestaan; deze bèta's/RC's zijn buiten
scope en mogen pas na een eigen broncheck worden ondersteund.

## 1. Verbinden en aanmelden

| Bewering | Bron | Zekerheid |
|---|---|---|
| JSON-RPC 2.0 over WebSocket op `GET /api/<versie>` en `/api/current` (= nieuwste API-versie van die release). | MW-25.10 `src/middlewared/middlewared/main.py` (`_add_api_route`, `_create_apis`: `"current"`) | zeker (code) |
| API-versies = mapnamen `api/v25_04_0 …` met `_` → `.`; `GET /api/versions` geeft de lijst. 25.10.8 kent `v25.04.0 … v25.10.5` (current = `v25.10.5`); 25.04.2.6 kent `v25.04.0 … v25.04.2`. **API-versie ≠ releaseversie.** | MW-25.10 `main.py` `_load_api_versions`, `api_versions_handler`; directorylisting `api/` | zeker |
| Aanmelden: `auth.login_with_api_key("<id>-<key>")` → `true/false`, intern `auth.login_ex` met `mechanism: API_KEY_PLAIN`. | MW-25.10 `plugins/auth.py` r. 1162–1195 | zeker |
| **Een API-key die over een onversleutelde verbinding (ws://, http) wordt gebruikt, wordt automatisch ingetrokken** ("API key revoked due to insecure transport"). De executor moet dus `wss://` gebruiken met certificaatcontrole (TrueNAS-certificaat vastpinnen of eigen CA). | MW-25.10 `plugins/auth.py` r. 937–950; MW-25.04 r. 933–941 | zeker (code); gevolg voor zelfondertekende certs **onzeker** |
| Rollen: methoden hebben `roles=[…]` (bijv. `APPS_READ`, `APPS_WRITE`, `CATALOG_READ`, `SNAPSHOT_WRITE`); `READONLY_ADMIN` omvat alle `*_READ`. API-keys worden **altijd** op rollen gecontroleerd. | MW-25.10 `role.py` r. 133–260; `main.py` `authorize_method_call` r. 881–931 | zeker |
| Niet aangemeld → `CallError('Not authenticated', ENOTAUTHENTICATED=207)`; geen recht → `CallError('Not authorized', EACCES=13)`. | MW-25.10 `main.py` r. 915–931; CLIENT `exc.py` (`ENOTAUTHENTICATED = 207`) | zeker |
| JSON-RPC-foutcodes: -32700, -32600, -32601 ("Method does not exist"), -32602 (validatie), -32603, -32000 (te veel gelijktijdige aanroepen), -32001 ("Method call error", `data = {error, errname, reason, trace, extra}`). | CLIENT `truenas_api_client/jsonrpc.py`; MW-25.10 `api/base/server/ws_handler/rpc.py` | zeker |
| Methoden ontdekken: `core.get_methods(service=None, target="WS")`. | MW-25.10 `api/v25_10_0/core.py` `CoreGetMethodsArgs` | zeker |
| Versie: `system.version` (volledige naam, `sw_info()['fullname']`), `system.version_short` en `system.info().version` (kort). Exacte string van `fullname` (bijv. `TrueNAS-25.10.8`) niet gezien. | MW-25.10 `plugins/system/product.py` r. 79–115, `plugins/system/info.py` r. 84–96 | formaat **onzeker** |

## 2. Jobs en events

- Methoden met `@job` (o.a. `app.create/update/delete/start/stop/redeploy/rollback/upgrade`, `catalog.sync`) geven het
  **job-ID** terug. Volgen: abonneren op `core.get_jobs` (`core.subscribe("core.get_jobs")` → notificatie
  `collection_update` met `msg` added/changed) of pollen met `core.get_jobs([["id","=",id]])`; wachten met
  `core.job_wait(id)`. Jobvelden: `state` (WAITING/RUNNING/SUCCESS/FAILED/ABORTED), `progress`, `result`, `error`,
  `exception`, `exc_info`. — Bron: MW-25.10 `api/v25_10_0/core.py` (`CoreGetJobsItem`, `CoreJobWaitArgs`,
  `CoreSubscribeArgs`), `rpc.py` `send_event`; CLIENT `__init__.py` (abonneert op `core.get_jobs` bij `job=True`).
  *Zeker.*
- Na een herstart van de NAS zijn lopende jobs weg: na een verbroken verbinding altijd **readback** (`app.query`,
  `app.config`) en nooit aannemen dat een job slaagde. *Afgeleid (jobs leven in het middlewared-proces); niet getest.*
- **Jobs zijn per gebruiker afgeschermd:** een sessie zonder `FULL_ADMIN` ziet in `core.get_jobs` alleen eigen jobs
  (of jobs waarvan zij een `read_roles`-rol heeft) en kan alleen eigen jobs afbreken/volgen; anders
  `CallError EPERM` "Job is not owned by current session". Gevolg: de **read-only key van de Hub ziet de jobs van de
  executor niet**; de executor volgt zijn eigen jobs en meldt de uitkomst (met readback) aan de Hub. — Bron: MW-25.10
  `service/core_service.py` r. 98–141 en 202–205 (`get_jobs`, `job_abort`), `job.py` r. 350–367
  (`credential_access_error`), `utils/privilege.py` r. 90–94 (`credential_is_limited_to_own_jobs`). *Zeker (code);
  welke jobs `read_roles` hebben is niet onderzocht.* (Toegevoegd bij de review van P0; de simulator volgt dit.)

## 3. Standaard Custom App = catalogusapp `ix-app`

**Begrippen (contract 1.3 §2, §9; C1.3-JLP-10):**

| Begrip | Wat het technisch is | In `app.query` | Voor het platform |
|---|---|---|---|
| *standaard Custom App* | het formulier *Apps → Discover Apps → Custom App* = de catalogusapp **`ix-app`** (trein `stable`) | `custom_app: false` | toegestaan; `install.method: custom_app` in het manifest betekent uitsluitend dit |
| *compose-app* | *Install via YAML* (`app.create` met `custom_app: true` en `custom_compose_config(_string)`) | `custom_app: true` | **altijd verboden**; de Hub toont zo'n bestaande app als `custom_compose` |
| *catalogusapp* | een andere app uit de officiële TrueNAS-catalogus (bijv. `authentik`) | `custom_app: false` | toegestaan met vaste trein en versie; bij één container alleen met `install.reason` |

Let op de valkuil in de naam: het TrueNAS-veld `custom_app: true` betekent **niet** "standaard Custom App", maar een
compose-app. Een derden-app met **één** container installeert het platform standaard als standaard Custom App (één
gepinde upstream-image, `install.method: custom_app`); een catalogusapp voor één container alleen met een reden in
`install.reason` (`meerdere_containers` of een Nederlandse reden). Bronnen: de tabel hieronder (zeker) en contract 1.3
§2 "Begrippen".

| Bewering | Bron | Zekerheid |
|---|---|---|
| De knop **Custom App** in *Apps → Discover Apps* opent de generieke app-wizard voor `train=stable`, `app=ix-app`; opslaan roept `app.create({values, catalog_app: "ix-app", app_name, train: "stable", version})` aan. | WEBUI `constants/catalog.constants.ts` (`customApp = 'ix-app'`, `customAppTrain = 'stable'`), `custom-app-button.component.ts` r. 55, `app-wizard.component.ts` r. 270–290 | zeker |
| **Install via YAML** roept `app.create({custom_app: true, app_name, custom_compose_config_string})` aan → **verboden voor het platform**. | WEBUI `custom-app-form.component.ts` r. 108–117 | zeker |
| `app.create`-argumenten: `custom_app=False`, `values`, `custom_compose_config`, `custom_compose_config_string`, `catalog_app`, `app_name` (`^[a-z]([-a-z0-9]*[a-z0-9])?$`, 1–40), `train='stable'`, `version='latest'`. Met `custom_app=true` gaat het naar `app.custom.create` (compose). | MW-25.04 `api/v25_04_2/app.py` r. 92–107; MW-25.10 `api/v25_10_0/app.py` r. 137–162; `plugins/apps/crud.py` r. 111–148 | zeker |
| **Standaard `version='latest'`**: zonder versie installeert TrueNAS de nieuwste catalogusversie. Het platform geeft **altijd** een exacte versie. | idem | zeker |
| `ix-app` in de stable-trein: versie **1.4.8** (`min_scale_version 24.10.2.2`, lib 2.3.18). | APPS `trains/stable/ix-app/app_versions.json`, `ix-dev/stable/ix-app/app.yaml` | zeker voor die commit; verandert met de catalogus |
| Formulier-/values-schema (`ix-app` 1.4.8): `image{repository, tag (standaard "latest"), pull_policy missing/always/never}`, `TZ`, `envs[{name,value}]`, `restart_policy` (**standaard "no"**), `disable_builtin_healthcheck`, `devices[{host_device,container_device}]`, `privileged`, `capabilities.add[]`, `run_as_custom_user` + `run_as{user,group}` (568), `host_network`, `ports[{bind_mode published/exposed, port_number, container_port, protocol, host_ips[]}]`, `networks`, `dns_config`, `portals`, `storage[{type host_path/ix_volume/cifs/nfs/tmpfs, read_only, mount_path, host_path_config{acl_enable, path}, ix_volume_config{acl_enable, dataset_name}, …}]`, `labels`, `resources{enable_resource_limits, limits{cpus, memory (MB)}, gpus}`. | APPS `ix-dev/stable/ix-app/questions.yaml` | zeker voor 1.4.8 |
| Lege tag ⇒ template gebruikt `latest` (`values.image.tag or "latest"`). Altijd een exacte tag meegeven. | APPS `ix-dev/stable/ix-app/templates/docker-compose.yaml`; lib `container.py` `_resolve_image` (`f"{repo}:{tag}"`) | zeker |
| **Digest-pinning:** het formulier kent alleen `repository` + `tag`; de image-referentie wordt `"{repository}:{tag}"`. Een tag als `0.1.0@sha256:<digest>` levert dus `repo:0.1.0@sha256:…` (geldige Docker-syntax) — **niet getest**. Daarom: exacte tag in de waarden + na installatie de digest **teruglezen** met `app.image.query` (`repo_digests`) en bij afwijking stoppen. | APPS lib `container.py` r. 125–139; MW-25.10 `api/v25_10_0/app_image.py` (`repo_digests`) | **onzeker** |
| Beveiligingscontext: het ix-app-template roept `clear_caps()` aan en **verwijdert `no-new-privileges`** (gewone catalogusapps: `cap_drop: ALL` + `no-new-privileges`). Een Custom App draait dus met de standaard Docker-capabilities. Daarom zet de alles-in-één image zelf `no_new_privs` (prctl) na het afschalen naar 568. | APPS `ix-app/templates/docker-compose.yaml` r. 80–88; lib `container.py` r. 55–57, `security_opts.py` | zeker |
| Healthcheck: zonder `disable_builtin_healthcheck` gebruikt het template de `HEALTHCHECK` van de image. | APPS template (`c1.healthcheck.use_built_in()`) | zeker |
| Poortformaat wijzigde in ix-app **1.2.0** (oud: `published/target`; nieuw: `bind_mode/port_number/container_port/host_ips`), met automatische migratie. | APPS `ix-dev/stable/ix-app/app_migrations.yaml`, `migrations/ip_port_migration` | zeker |
| Validatie (poorten, paden, schema) gebeurt **binnen de job** (`app.schema.normalize_and_validate_values` in `create_internal`); een fout laat de job mislukken en ruimt half aangemaakte resources op. | MW-25.10 `crud.py` r. 151–198 | zeker |
| Bezette poorten vooraf: `app.used_ports()`; GPU's: `app.gpu_choices()` (sleutel = PCI-slot); vrije ruimte: `app.available_space`; IP's: `app.ip_choices`. | MW-25.10 `api/v25_10_0/app.py` (`AppUsedPorts*`, `AppGpuChoices*`, `AppAvailableSpace*`, `AppIpChoices*`), `plugins/apps/resources.py` r. 104–123 | zeker (namen); GPU-selectie voor Intel per kaart **onzeker** (D08) |

**Voorbeeld (jelly-leon-platform; alle `<…>` uit wizard/datasetkiezer, nooit voorbeeldwaarden):**

```json
{"jsonrpc":"2.0","id":7,"method":"app.create","params":[{
  "app_name":"jelly-leon-platform","catalog_app":"ix-app","train":"stable","version":"1.4.8",
  "values":{"image":{"repository":"ghcr.io/lvos04/jelly-leon-platform","tag":"0.1.0","pull_policy":"missing"},
    "TZ":"<tijdzone>","envs":[{"name":"HUB_URL","value":"<hub-url>"},{"name":"HUB_ENROLLMENT_TOKEN","value":"<eenmalig>"}],
    "restart_policy":"unless-stopped","disable_builtin_healthcheck":false,"privileged":false,
    "capabilities":{"add":[]},"run_as_custom_user":false,"host_network":false,
    "ports":[{"bind_mode":"published","port_number":30440,"container_port":30440,"protocol":"tcp","host_ips":[]}],
    "storage":[{"type":"host_path","mount_path":"/appdata","read_only":false,
                "host_path_config":{"acl_enable":false,"path":"/mnt/<dataset uit datasetkiezer>"}}],
    "resources":{"enable_resource_limits":true,"limits":{"cpus":1,"memory":512},"gpus":{}}}}]}
```

## 4. Bijwerken, terugzetten, upgraden, verwijderen

| Bewering | Bron | Zekerheid |
|---|---|---|
| `app.update(app_name, {values})` (job). Bij catalogusapps (ook ix-app): `config.update(data['values'])` = **ondiepe merge op het hoogste niveau** — stuur altijd volledige subobjecten (bijv. heel `image`), anders verdwijnen velden. Draait `compose up --force-recreate` als de app niet STOPPED is. | MW-25.10 `crud.py` r. 217–270 | zeker |
| `app.rollback(app_name, {app_version, rollback_snapshot=true})` (job): alleen naar een **eerder geïnstalleerde, lagere catalogusversie** (`app.rollback_versions`); niet in STOPPED; zet zo nodig de ixVolume-snapshot `<ds>@<versie>` terug. **Een image-tagwijziging van een ix-app is géén versie** ⇒ niet met `app.rollback` terug te draaien. Terugzetten van een Custom App = `app.update` met de vorige waarden + (bij gewijzigd schema) data via de eigenaar/snapshot herstellen. | MW-25.10 `plugins/apps/rollback.py` (identiek in MW-25.04), `ix_apps/rollback.py` `get_rollback_versions` | zeker |
| `app.upgrade(app_name, {app_version='latest', values, snapshot_hostpaths=false})` (job): vereist `upgrade_available`; maakt vóór de upgrade een recursieve snapshot van de ixVolume-dataset (`@<oude versie>`), hostpaden alleen met `snapshot_hostpaths=true` (alleen datasets onder `/mnt`). Samenvatting/changelog vooraf: `app.upgrade_summary`. | MW-25.10 `upgrade.py` r. 37–170 | zeker |
| **25.10:** `app.upgrade` op een ix-app (of custom app) haalt alleen de images opnieuw op en herstart (geen versiewissel). **25.04:** alleen voor `custom_app`; ix-app volgt daar het versiepad. Gebruik voor eigen apps dus altijd `app.update` met een nieuwe tag. | diff MW-25.04 ↔ MW-25.10 `upgrade.py` r. 88–106 | zeker |
| `app.delete(app_name, {remove_images=true, remove_ix_volumes=false, force_remove_ix_volumes=false, force_remove_custom_app=false})` (job): `compose down` met `remove_volumes=True` (alleen Docker named volumes), daarna de app-map; **ixVolume-datasets alleen met `remove_ix_volumes=true`; hostpaden nooit.** Het platform stuurt altijd `remove_ix_volumes=false` (contract §8); `remove_images` mag `false` blijven voor snelle terugkeer. | MW-25.10 `crud.py` r. 272–340; `api/v25_10_0/app.py` r. 190–210 | zeker |
| `app.start` / `app.stop` / `app.redeploy` zijn jobs met alleen `app_name`. | MW-25.10 `api/v25_10_0/app.py` r. 233–260 | zeker |
| WEBUI: *Edit* → `app.update`, *Roll Back* → `app.rollback` (+ `app.rollback_versions`), *Update* → `app.upgrade`, *Delete* met vinkje **Remove iXVolumes** → `app.delete`; logs via kaart **Workloads** → **View Logs**. | WEBUI `app-info-card.component.*`, `app-rollback-modal.component.ts`, `app-delete-dialog.component.html`, `app-workloads-card.component.html` | zeker |

## 5. Catalogusapps opzoeken en installeren

| Bewering | Bron | Zekerheid |
|---|---|---|
| `app.available(filters, options)` toont alleen apps uit de **voorkeurstreinen** (`catalog.config().preferred_trains`; standaard `stable`). Community-apps (o.a. authentik, immich, uptime-kuma, gotify, node-red, adguard-home, beszel-hub, prowlarr, sabnzbd, qbittorrent, seerr, bazarr, tailscale, cloudflared, jellyfin, navidrome) staan in trein **community** ⇒ eerst `catalog.update({"preferred_trains": ["stable","community"]})` (rol `CATALOG_WRITE`, R2) en `catalog.sync` (job). | MW-25.10 `plugins/catalog/apps.py` r. 31–58, `update.py`, `sync.py`; APPS `trains/community/*` | zeker |
| `catalog.get_app_details(app_name, {"train": …})` geeft o.a. `versions{<versie>: {…, app_metadata.app_version}}`, `latest_version`. Werkt voor elke trein in de catalogus, ook buiten de voorkeur. | MW-25.10 `plugins/catalog/app_version.py` r. 18–50 | zeker |
| Installeren: `app.create({app_name, catalog_app, train, version: "<exact>", values})`; `version` moet in `versions` staan, anders `CallError ENOENT "Version … not found"`. | MW-25.10 `crud.py` r. 133–148 | zeker |
| **Pinning-risico:** de treinen in `truenas/apps` bevatten per app alleen de **nieuwste** versie (bijv. `community/immich` alleen `1.14.62`). Een vastgezette catalogusversie is dus alleen installeerbaar zolang de catalogus die versie nog heeft; daarna faalt `app.create`. Al geïnstalleerde versies blijven lokaal staan (rollback kan). Gevolg voor B08-003/004: het installatieplan moet `version_unavailable` als blokkade tonen en een manifest-update (nieuwe vaste versie + test) voorstellen; nooit stil naar `latest`. | APPS `trains/community/immich/` (alleen `1.14.62`), `app_versions.json` met één sleutel; MW-25.10 `crud.py` r. 140–146 | waarneming op één commit; of TrueNAS oude versies ergens anders bewaart is **onzeker** |
| Catalogusversie ≠ upstream-versie: bijv. immich catalogus `1.14.62` = app_version `v3.3.6`; authentik `1.3.63` = `2026.8.5`; uptime-kuma `1.2.25` = `2.5.9`; gotify `1.1.17` = `3.1.1`; node-red `1.4.35` = `5.0.9-24`; adguard-home `1.3.21` = `v0.107.80`; beszel-hub `1.1.22` = `0.21.1`. Manifesten pinnen de **catalogusversie** (`catalog.version`) en tonen de app_version. | APPS `trains/community/<app>/app_versions.json` | zeker voor commit f142a16 |

## 6. Snapshots, datasets en overige leesmethoden (per versie)

| Bewering | Bron | Zekerheid |
|---|---|---|
| **25.04:** `zfs.snapshot.query/create/clone/rollback` zijn publiek; er is geen `pool.snapshot.create`. | MW-25.04 `plugins/zfs_/snapshot.py`, `snapshot_actions.py` (geen `private`), `api/v25_04_2/` (geen `pool_snapshot.py`) | zeker |
| **25.10:** `pool.snapshot.query/create/update/delete/clone/rollback/hold/release/rename` zijn publiek (`clone`: rollen `SNAPSHOT_WRITE`+`DATASET_WRITE`; `rollback`: `SNAPSHOT_WRITE`+`POOL_WRITE`); `zfs.snapshot.*` is **privé**. `pool.snapshot.clone({snapshot, dataset_dst, dataset_properties})`. | MW-25.10 `plugins/pool_/snapshot.py`, `plugins/zfs_/snapshot.py` (`private = True`), `api/v25_10_0/pool_snapshot.py` | zeker |
| Privé methoden via WebSocket worden (nog) uitgevoerd met alleen een waarschuwing ("FIXME: Eventually, prohibit this"). Het platform gebruikt ze **niet**. | MW-25.10 `rpc.py` `process_message` | zeker |
| Verder beschikbaar (beide versies, lezen): `pool.query`, `pool.dataset.query`, `pool.snapshottask.query`, `disk.query`, `alert.list`, `cloudsync.query`, `replication.query`. SMART: 25.04 heeft `smart.test.*`; **25.10 heeft geen publieke SMART-resultatenmethode** (plugin verwijderd, alleen privé `disk.smart_test`; SMART-meldingen blijven via `alert.list`) → in 25.10 `capability_unavailable`. | MW-25.04 `plugins/smart.py` (`namespace = 'smart.test'`); MW-25.10 zonder `plugins/smart.py`, met `plugins/disk_/smart.py` (`@private smart_test`) en migratie `alembic/versions/25.10/2025-03-20_11-52_drop_smart.py`; api.truenas.com/v25.10 zonder `smart.*` | zeker (broncode + officiële docs) |
| Private registry (GHCR privé): `app.registry.create({name, description, username, password, uri})`; in de UI *Apps → Configuration → Sign-in to a Docker registry*. | MW-25.10 `api/v25_10_0/app_registry.py`; WEBUI `app-settings-button.component.html` r. 45–49 | zeker (bestaan); gedrag met GHCR-tokens **niet getest** |

## Onafhankelijke controle (review P0, 2026-11-09)

Een reviewer heeft een steekproef opnieuw in de broncode nagelezen (sparse checkout, alleen lezen) en **bevestigd**:
tags/commits in de tabel "Bronnen" (`git ls-remote`); intrekken van een API-key bij onversleuteld transport
(MW-25.10 `plugins/auth.py` r. 936–950); `version == 'latest'` → nieuwste catalogusversie (`crud.py` r. 142–143);
ondiepe merge bij `app.update` (`config.update(data['values'])`, `crud.py` r. 243); opruimen na mislukte installatie
(`crud.py` r. 186–192); `remove_ix_volumes` (`crud.py` r. 327); `app.rollback` alleen naar `get_rollback_versions` en
niet in STOPPED (`rollback.py` r. 35–38); `ix-app` 1.4.8 met `min_scale_version 24.10.2.2` en
`remove_security_opt("no-new-privileges")` + `clear_caps()` (APPS r. 82–83); `community/immich` heeft één versie
(`1.14.62`). De overige beweringen zijn niet opnieuw gecontroleerd en houden hun eigen zekerheidsniveau. Niets
hiervan is op een echte TrueNAS uitgevoerd (**NIET GEVALIDEERD**).

## Controle tegen de officiële API-documentatie (2026-11-09)

Op de nieuwe machine (geen proxy) waren `https://api.truenas.com/v25.10/`, `https://api.truenas.com/v25.04/`,
`https://www.truenas.com/docs/scale/25.10/` en `https://apps.truenas.com/resources/` bereikbaar (HTTP 200). Alleen
gelezen; gecontroleerd (methode-index en methodepagina's, geen echte TrueNAS):

- **Methodenamen van de simulator vs. index v25.10** (863 methodepagina's): van de 47 vaste simulatormethoden staan
  er 46 in, plus de vier 25.10-snapshotmethoden `pool.snapshot.query/create/clone/rollback`; `zfs.snapshot.*` staat er
  niet in (privé, klopt met §6). De enige afwijking was `smart.test.results`: die ontbreekt in de docs en de
  broncode MW-25.10 heeft geen `plugins/smart.py` meer (zie §6) → de simulator biedt `smart.test.*` sinds deze
  controle alleen nog in het 25.04-profiel aan.
- **v25.04** documenteert maar een deel van de API (439 pagina's; o.a. `pool.query`, `system.info`, `core.get_methods`,
  `zfs.snapshot.query` ontbreken, `app.*` grotendeels aanwezig): voor 25.04 blijft de broncode de referentie.
- **Parameters (v25.10-pagina's):** `app.delete` heeft `remove_ix_volumes` met standaard `false` (en
  `force_remove_ix_volumes: false`, `remove_images: true`); `app.image.query` levert `repo_digests`; `app.rollback`
  heeft `app_version` en `rollback_snapshot`; `app.create`/`app.update` kennen `custom_compose_config`, `custom_app`,
  `catalog_app` en `values` (wij gebruiken alleen `catalog_app="ix-app"` + `values`, nooit compose); `app.used_ports`
  levert gehele getallen. Dit bevestigt §3/§4.
- **Niet in de docs gevonden** (blijft broncode-zekerheid): het intrekken van een API-key bij onversleuteld transport
  (staat niet op de pagina van `auth.login_with_api_key`).

Ook dit is documentatie- en broncodeonderzoek: **NIET GEVALIDEERD** op een echte TrueNAS (D01/D07).

## 7. Gevolgen voor het platform (vastgelegd in ADR-0006)

1. Eigen apps: `app.create` met `catalog_app="ix-app"`, `train="stable"`, **exacte** `version` (ix-app) en **exacte**
   image-tag; `restart_policy="unless-stopped"` expliciet; nooit `custom_app=true` (dat is een compose-app, zie de
   begrippen in §3). Derden-apps met één container ook zo, tenzij het manifest een `install.reason` voor een
   catalogusapp noemt (contract 1.3 §9).
2. Updates eigen apps: `app.update` met volledig `image`-object (nieuwe tag); daarna digest-readback via
   `app.image.query`. Vooraf snapshot van de appdata-dataset (`pool.snapshot.create` / `zfs.snapshot.create`).
3. Terugzetten eigen apps: `app.update` met de vorige waarden; bij migratie eerst data herstellen via de eigenaar
   (R3 bij DB-restore). `app.rollback` alleen voor catalogusapps.
4. Catalogusapps: trein expliciet, versie expliciet; ontbrekende versie = blokkade, nooit `latest`.
5. Verwijderen: altijd `remove_ix_volumes=false`; data-verwijdering (`app.uninstall_data`) wordt niet aangeboden.
6. Methodenamen per versie via `core.get_methods`; snapshot-methoden verschillen tussen 25.04 en 25.10.
7. Alleen `wss://` met certificaatcontrole voor de schrijfkey (anders trekt TrueNAS de key in).
8. De Hub (read-only key) kan jobs van de executor niet zien: de executor volgt zijn eigen jobs, leest daarna terug
   en rapporteert; de Hub vertrouwt nooit op `core.get_jobs` voor uitvoeringen van een ander (zie §2).
