# Uptime Kuma-simulator (alleen voor tests)

> **Dit is geen Uptime Kuma.** Hij bootst alleen de HTTP-delen na die de Hub gebruikt (B09-002: versie, monitorstatus,
> statuspagina, push-hartslag). Een test die alleen hiertegen slaagt is **Sim-getest**.

Bron (alleen gelezen): `louislam/uptime-kuma` tag **2.5.9** (`df601c5`): `server/server.js` (`/metrics` achter
`apiAuth`), `server/auth.js` (`verifyAPIKey`: `uk<id>_<sleutel>`, Basic-aanmelding, `apiKeysEnabled`),
`server/prometheus.js` (metrieknamen en labels), `server/routers/status-page-router.js`, `server/routers/api-router.js`
(`/api/entry-page`, `/api/push/:pushToken`), `server/util-server.js` (`sendHttpError`), en
`prometheus-api-metrics` 3.2.2 (`app_version{version,major,minor,patch}`). Image in het manifest:
`ghcr.io/louislam/uptime-kuma:2.5.9@sha256:e88415a9…725591bd`.

## Starten

```bash
python -m uptime_kuma_sim --host 127.0.0.1 --port 31050
```

`GET /_sim/sleutels` → `{"api_key": "uk1_…", "gebruiker": [naam, wachtwoord], "slug": "platform",
"push_tokens": {"Hub-hartslag": "…"}}`.

## Protocol

| Route | Aanmelding | Antwoord |
|---|---|---|
| `GET /metrics` | Basic; wachtwoord = API-sleutel `uk<id>_<sleutel>` (gebruikersnaam telt niet). Staan API-sleutels uit (of versie 1.x): gebruikersnaam + wachtwoord | `text/plain; version=0.0.4`: `app_version{version="2.5.9",major="2",minor="5",patch="9"} 1`, per monitor `monitor_status` (1 up, 0 down, 2 pending, 3 onderhoud), `monitor_response_time` (ms; -1 als niet up), `monitor_cert_days_remaining` (https-monitors), labels `monitor_id, monitor_name, monitor_type, monitor_url, monitor_hostname, monitor_port`. Zonder of met foute aanmelding: `401` + `WWW-Authenticate: Basic …` |
| `GET /api/status-page/<slug>` | geen | `{config:{slug,title,…,published:true}, incident:null, publicGroupList:[{id,name,weight,monitorList:[{id,name,type,sendUrl}]}], maintenanceList:[]}`; onbekende slug → `404 {"status":"fail","msg":"Status Page Not Found"}` |
| `GET /api/status-page/heartbeat/<slug>` | geen | `{heartbeatList:{"<id>":[{status,time,msg:"",ping}]}, uptimeList:{"<id>_24":<0..1>}}` (slug hoofdletterongevoelig) |
| `GET /api/entry-page` | geen | `{"type":"entryPage","entryPage":"dashboard"}` (ook healthcheck in het manifest) |
| `GET|POST /api/push/<token>?status=up|down&msg=&ping=` | token in het pad | `{"ok":true}`; onbekend → `404 {"ok":false,"msg":"Monitor not found or not active."}` |

Begintoestand: statuspagina `platform` met monitors voor Hub, TrueNAS-webinterface, MediaGuard, MSO, my_house,
Jelly Leon en een TCP-controle op de HTTPS-poort van TrueNAS (I08-08-03), plus de push-monitor **Hub-hartslag** (dead-man's switch:
blijft de Hub stil, dan meldt Uptime Kuma via Gotify — EX22). Adressen zijn simulatorwaarden onder `.invalid`.

## Foutinjectie

Algemeen: `http_500/502/503`, `traag`, `ongeldige_json`, `te_groot`, `redirect` (met `aantal`/`pad`). Specifiek:

| Fout | Effect |
|---|---|
| `sqlite_busy` | statuspagina-routes → `503 {"status":"fail","msg":"SQLITE_BUSY: database is locked"}` |
| `metrics_weigeren` | `/metrics` weigert elke aanmelding (sleutel ingetrokken of verlopen) |
| `oude_versie` | `app_version` 1.23.16 en geen API-sleutels (alleen Basic met gebruiker/wachtwoord) |

Monitortoestand: `POST /_sim/monitor/<id> {"status": 0|1|2|3, "response_ms": n}`.

## Niet nagebootst (afwijkingen)

De Socket.IO-API (monitors beheren, instellingen, `needSetup`), badges, RSS, onderhoudsvensters als object, het
setupscherm en meldingskanalen. Of een echte Uptime Kuma als 568:568 ICMP-monitors kan uitvoeren: **NIET GEVALIDEERD**.
