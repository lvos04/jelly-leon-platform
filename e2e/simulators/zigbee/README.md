# Zigbee (via Zigbee2MQTT) voor huis en energie

## Hoe my_house Zigbee ontvangt

Vastgesteld op de commit in `../my-house.commit`: my_house praat niet met Zigbee zelf. Zigbee2MQTT bezit de
USB-coördinator en publiceert op de MQTT-broker (Mosquitto in de stack van my_house, `infra/compose/compose.yaml`).
`shc_integrations.zigbee` abonneert als gebruiker `shc` op `zigbee2mqtt/#` en leest `bridge/state`,
`bridge/devices`, `<naam>/availability` en `<naam>` (de meting). De ACL (`infra/compose/config/acl`) geeft alleen
de gebruiker `zigbee2mqtt` schrijfrecht op die boom.

## Wat deze simulator doet

Hij staat in voor de Zigbee2MQTT-container: hij verbindt met de broker (MQTT 3.1.1, QoS 0, standaard als
gebruiker `zigbee2mqtt`), publiceert retained de brug (`online`), de apparatenlijst (coördinator en één
temperatuur- en vochtsensor `testkamer_sensor`, testadres) en de beschikbaarheid, en daarna elke `--interval`
seconden een meetbericht. Bij een storing van de broker verbindt hij na 2 s opnieuw.

```bash
docker run --rm --network "$TESTNET" --network-alias zigbee-sim --read-only --cap-drop ALL \
  -v "$PWD/e2e/simulators:/sim:ro" -v "$PROEF/mqtt-wachtwoord:/run/secrets/mqtt:ro" -e PYTHONPATH=/sim \
  --entrypoint python "$MY_HOUSE_IMAGE" -m zigbee --host intern --mqtt-host mosquitto \
  --mqtt-wachtwoordbestand /run/secrets/mqtt --interval 30
```

Opties: `--port` (bediening, 30222), `--mqtt-host`, `--mqtt-poort` (1883), `--mqtt-gebruiker` (`zigbee2mqtt`),
`--mqtt-wachtwoordbestand` (het testwachtwoord nooit op de opdrachtregel), `--base-topic` (`zigbee2mqtt`),
`--interval`. Elke image met Python 3.12, FastAPI en uvicorn volstaat; de API-image van my_house heeft ze.

## Bediening

| Proef | Bediening | Effect |
|---|---|---|
| EX29 meetberichten stoppen | `POST /_sim/meetberichten {"aan": false}` | er komt geen meetbericht meer; de verbinding blijft open (alleen PINGREQ), de beschikbaarheid blijft `online`, zoals een batterijsensor die zwijgt |
| hervatten | `POST /_sim/meetberichten {"aan": true}` | meetberichten komen weer bij het volgende interval |
| meting | `POST /_sim/meting {"waarden":{"temperature":18.5}}` | volgende meetberichten dragen die waarde; onbekende eigenschap geeft 422 |

`GET /_sim/toestand` geeft `verbonden`, `meetberichten`, de meting en per gepubliceerd bericht tijd, onderwerp en
retain (geen inhoud). `POST /_sim/reset` hervat de meetberichten met de beginmeting.

## Niet nagebootst

- Zigbee zelf, de coördinator, koppelen (`permit_join`), hernoemen en `bridge/request/...`: verzoeken van my_house
  worden niet beantwoord.
- `bridge/info`, `bridge/event` (koppelverloop) en het last-will-bericht `offline` van Zigbee2MQTT.
- MQTT met TLS, QoS 1/2 en MQTT 5. my_house abonneert met QoS 1; de broker levert berichten van deze simulator met
  QoS 0.
- Meer dan één sensor en andere apparaatsoorten (lampen, knoppen).
