# HomeWizard (P1-meter en Energy Socket) voor huis en energie

## Bron

De lokale API v1 van HomeWizard Energy over HTTP, zoals my_house die leest
(`my_house:integrations/src/shc_integrations/homewizard/adapter.py` op de commit in `../my-house.commit`):
`GET /api`, `GET /api/v1/data` en voor de socket `GET /api/v1/state`. De veldnamen volgen de fixtures van my_house
(`integrations/tests/fixtures/homewizard/v1_*.json`); de waarden zijn testwaarden. Een test leest de P1-meting met
`normalize_v1` van my_house op die commit.

## Starten

`python -m homewizard_sim --apparaat p1` of `--apparaat socket`, standaardpoort 30221, alleen loopback of een intern
testnet (`--host intern` in een container zonder standaardroute). Eén proces is één apparaat; start voor de P1-meter
en de socket elk een eigen proces met een eigen poort of netwerkalias. my_house koppelt op adres met poort
(`<host>:<poort>`).

## Protocol

| Route | P1-meter | Energy Socket |
|---|---|---|
| `GET /api` | `HWE-P1`, API v1 | `HWE-SKT`, API v1 |
| `GET /api/v1/data` | tellers T1/T2 import/export en totalen, vermogen, gas met `gas_timestamp` (YYMMDDhhmmss, lokale tijd zonder zone) | teller T1 import/export en totalen, vermogen |
| `GET /api/v1/state` | 404 | `power_on`, `switch_lock`, `brightness` |
| elk ander verzoek met PUT, POST, PATCH of DELETE | 403 | 403, ook `PUT /api/v1/state` |

**Alleen lezen (EX27).** Een schakelopdracht verandert niets: hij wordt vastgelegd en geweigerd met 403
(`{"error":{"id":"sim_403","description":…}}`; de `id` is een simulatorwaarde). `GET /_sim/verzoeken` toont elk
verzoek, met bij een schakelopdracht `details.power_on`. `GET /_sim/toestand` geeft onder `schakelverzoeken` alleen
de schrijvende verzoeken op `/api…`. Een scenario bewijst zo dat geen route het boilerstopcontact schakelt: die
lijst moet leeg blijven en `power_on` ongewijzigd.

## Foutinjectie en bediening

| Proef | Bediening | Effect |
|---|---|---|
| EX28 meterreset | `POST /_sim/meterreset {"stand": 0}` | alle tellers terug naar 0, import T1 naar `stand`: de tellerstand springt terug |
| EX28 zomertijd | `POST /_sim/zomertijd {"richting":"naar_zomertijd"\|"naar_wintertijd","jaar":2026,"voor_s":1800}` | simulatorklok `voor_s` seconden voor de EU-wissel (laatste zondag van maart/oktober, 01:00 UTC) |
| klok | `POST /_sim/klok {"utc":"…Z"}`, `{"verder_s":3600}` of `{"echt":true}` | vaste of doorgeschoven tijd voor `gas_timestamp`; `utc` zonder zone geeft 422 |
| meting | `POST /_sim/meting {"waarden":{"active_power_w":2000}}` | alleen bekende meetvelden per apparaat, anders 422 |

Bij de wissel naar wintertijd geeft de P1 twee keer dezelfde lokale `gas_timestamp` met een uur ertussen; bij de
wissel naar zomertijd slaat hij een uur over. `GET /_sim/toestand` geeft de UTC-klok erbij, zodat een scenario kan
nagaan welk moment my_house kiest. `POST /_sim/reset` zet tellers, klok en log terug. Daarnaast gelden de algemene
fouten van `simkit` (`/_sim/fouten`).

## Niet nagebootst

- API v2 (HTTPS met token en `/api/measurement`): my_house valt bij een v1-apparaat terug op v1. De v2-koppeling
  blijft NAS-werk.
- mDNS-ontdekking (`_hwenergy._tcp`), de knop op het apparaat, firmware-updates, `identify` en `system`.
- Een echte P1-telegramstroom en de meetcadans van het apparaat: waarden veranderen alleen via de bediening.
- Werkelijk schakelen. Ook een toegestane socket schakelt hier nooit.
