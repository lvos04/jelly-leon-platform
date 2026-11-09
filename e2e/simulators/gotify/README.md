# Gotify-simulator (alleen voor tests)

> **Dit is geen Gotify.** Hij bootst het deel van de Gotify-API na dat het platform gebruikt (Hub-meldingen, I13,
> EX22). Een test die alleen hiertegen slaagt is **Sim-getest**, nooit "geslaagd op de NAS".

Bron (alleen gelezen): `gotify/server` tag **v3.1.1** (`e6361ca`): `router/router.go`, `api/message.go`,
`api/health.go`, `auth/authentication.go`, `model/error.go`. Image in het manifest:
`ghcr.io/gotify/server:3.1.1@sha256:44fc5bbd…eb203b86`.

## Starten

```bash
pip install -e 'e2e/simulators[test]'
python -m gotify_sim --host 127.0.0.1 --port 30215      # --ssl-certfile/--ssl-keyfile voor HTTPS
```

Tokens (alleen simulator): `GET /_sim/sleutels` → `{"applicaties": {"hub": "A…", "uptime-kuma": "A…", "backup": "A…"},
"client": "C…", "admin_wachtwoord_is_standaard": true}`.

## Protocol

| Route | Aanmelding | Antwoord |
|---|---|---|
| `GET /health` | geen | `200 {"health":"green","database":"green"}`; database weg → `500 {"health":"orange","database":"red"}` |
| `GET /version` | geen | `{"version":"3.1.1","commit":…,"buildDate":…}` |
| `POST /message` | **applicatietoken** via `X-Gotify-Key`, `?token=` of `Authorization: Bearer`; óf clienttoken/Basic **met** `appid` | body `{"message" (verplicht), "title", "priority" (int), "extras"}` → `200 {id, appid, message, title, priority, [extras], date}`; titel en prioriteit vallen terug op die van de applicatie |
| `GET /application` | clienttoken of Basic | lijst applicaties (per bron een eigen token: hub, uptime-kuma, backup — I13-13-02) |
| `GET /message?limit=` | clienttoken of Basic | `{"messages":[…], "paging":{…}}` (nieuwste eerst; "de telefoon") |
| `GET /current/user` | clienttoken of Basic | `{"id":1,"name":"admin","admin":true}` — controle na installatie: **Basic admin:admin moet 401 geven** |

Fouten (formaat `model/error.go`): `{"error":"Unauthorized","errorCode":401,"errorDescription":"you need to provide a
valid access token or user credentials to access this api"}`; `400` bij ontbrekend `message`, bij clienttoken zonder
`appid` ("appid is required when not authenticating with an application token") en bij een onbekende `appid`.

Begintoestand = verse installatie: **admin/admin is actief** (zoals de echte image zonder `GOTIFY_DEFAULTUSER_PASS`).
`POST /_sim/admin-wachtwoord {"wachtwoord": "…"}` (≥ 12 tekens) speelt na wat de gebruiker in de Gotify-UI doet.

## Foutinjectie

Algemeen (alle simulators, zie `simkit`): `http_500`, `http_502`, `http_503`, `traag`, `ongeldige_json`, `te_groot`,
`redirect` — met optioneel `aantal` en `pad` (regex). Specifiek:

| Fout | Effect |
|---|---|
| `db_storing` | `/health` → 500 orange/red, `POST /message` → 500 |
| `tokens_ingetrokken` | elk applicatie- en clienttoken → 401 |

Bediening: `POST /_sim/fouten {"naam","aantal","pad","waarde"}`, `DELETE /_sim/fouten[?naam=]`, `GET /_sim/verzoeken`
(zonder tokens), `POST /_sim/reset`.

## Niet nagebootst (afwijkingen)

WebSocket-stroom `/stream`, plugins, gebruikersbeheer, afbeeldingen, OIDC-aanmelding van Gotify 3.x, sessie-login
(`/auth/local/login`) en client-elevatie. Prioriteit-validatie is strenger (alleen gehele getallen). Echte
pushmeldingen op een telefoon: **NIET GEVALIDEERD (hardware)**.
