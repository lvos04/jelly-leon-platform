# OneDrive (offsite-doel en herstel) voor back-up verouderd (EX12)

## Bron

De Hub praat nooit met OneDrive. Hij leest de back-upstand uit `cloudsync.query` van TrueNAS
(`ai-server-manager:apps/api/src/hub/truenas/normalize.py`, functie `cloudsync`; dekking in
`apps/api/src/hub/backup/coverage.py`, gebruikt door `apps/api/src/hub/routers/backups.py`). De TrueNAS-simulator
levert die methode al, met taken uit `POST /_sim/fixtures {"cloudsync_tasks": …}` (mappingtabel in
`e2e/README.md`, rij EX12). Deze simulator voegt alleen toe wat daar ontbreekt: een doel waar de geplande taak
naartoe schrijft, dat op commando faalt of veroudert, en herstel van wat er staat. De taakvorm volgt de velden die
`normalize.cloudsync` leest (`path`, `direction`, `transfer_mode`, `enabled`, `encryption`,
`credentials.provider.type`, `job` met `state`, `error`, `time_started`, `time_finished` als `{"$date": ms}`).

## Starten

`python -m onedrive_sim`, standaardpoort 30223, alleen loopback of een intern testnet (`--host intern` in een
container zonder standaardroute).

Een scenario laat de geplande back-up draaien en geeft de taak door aan de TrueNAS-simulator:

```bash
curl -s -X POST localhost:30223/_sim/backup -H 'content-type: application/json' \
  -d '{"bestanden": {"db/export.sql": "-- testdump\n"}}'
curl -s localhost:30223/_sim/cloudsync_taken | \
  jq '{cloudsync_tasks: .}' | curl -s -X POST <truenas-sim>/_sim/fixtures -H 'content-type: application/json' -d @-
```

`/_sim/fixtures` vervangt de hele lijst cloud-sync-taken van de TrueNAS-simulator.

## Protocol

| Route | Effect |
|---|---|
| `POST /_sim/backup {"bestanden": {pad: inhoud}, "pad": "/mnt/…"}` | de geplande taak draait nu: het doel wordt gelijk aan `bestanden` (SYNC), job `SUCCESS`; `pad` (optioneel) is het bronpad van de taak, standaard `/mnt/simpool/appdata` |
| `GET /_sim/cloudsync_taken` | `[taak]` in de vorm van `cloudsync.query`; `job` is `null` zolang de taak nooit draaide (onbekend, geen succes) |
| `GET /_sim/toestand` | taak, aantal runs en de paden op het doel |
| `GET /herstel` | `{"bestanden": {pad: sha256}}` van wat op het doel staat |
| `GET /herstel/{pad}` | `{"pad", "inhoud"}`; 404 als het bestand niet op het doel staat |

De credentials in de taak hebben geen attributen: geen token of geheim, ook geen testwaarde.

## Foutinjectie en bediening

| Proef | Bediening | Effect |
|---|---|---|
| EX12 back-up mislukt | `POST /_sim/fouten {"naam": "doel_vol"}` of `{"naam": "token_verlopen"}` (optioneel `"aantal"`, `"waarde"` = eigen melding) | de volgende run(s) krijgen job `FAILED` met een melding; de vorige stand op het doel blijft staan |
| EX12 back-up verouderd | `POST /_sim/verouder {"uren": 30}` | `time_started` en `time_finished` van de laatste run schuiven `uren` terug (0 < uren ≤ 8760); zonder eerdere run 409 |
| herstel | `GET /herstel`, `GET /herstel/{pad}` | wat er na de laatste geslaagde run op het doel staat |

`POST /_sim/reset` zet doel, taak, storingen en log terug. De algemene fouten van `simkit` (`/_sim/fouten`) werken op
`/herstel`.

## Niet nagebootst

- De Microsoft Graph-API, OAuth en rclone: niemand in het platform spreekt die aan. `/herstel` is een eigen
  simulatorroute, geen Graph.
- Het schema van TrueNAS: een run gebeurt alleen op `POST /_sim/backup`, nooit vanzelf.
- Versleuteling, bandbreedte, quota in bytes en de echte foutteksten van rclone (de meldingen zijn simulatorwaarden).
- De echte offsite-back-up en een herstel op de NAS blijven NAS-werk (N-P6-02).
