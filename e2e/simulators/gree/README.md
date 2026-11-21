# Gree (airco-binnenunits) voor huis en energie

## Bron

Geen eigen protocol. De unit is de nagebootste Gree-unit van my_house:

- `my_house:integrations/tests/fake_gree.py` (`FakeGreeUnit`: scan, bind, status, cmd over echt UDP), die zelf
  `shc_integrations.gree.protocol` gebruikt (v1 AES-ECB en v2 AES-GCM);
- `my_house:infra/e2e/fake_gree_unit.py` (de dienst die my_house zelf in `infra/e2e/compose.e2e.yaml` start, met
  één verwarmende unit en zonder bediening).

De commit staat vast in `../my-house.commit`. De tests pakken alleen `integrations/src/shc_integrations` en
`integrations/tests/fake_gree.py` van die commit uit (`MY_HOUSE_BRON`, standaard `../my_house` naast deze repo).

## Starten

Zonder extra toestanden: start `fake_gree_unit.py` zoals my_house dat doet (API-image van my_house, entrypoint
`python /e2e/fake_gree_unit.py`, `fake_gree.py` ernaast gekoppeld).

Met de toestanden voor de scenario's: `gree_sim` in dezelfde API-image (die heeft `shc_integrations`, FastAPI en
uvicorn). Eén container is één binnenunit met een eigen netwerkalias. Gebruik een uitsluitend intern Docker-testnet
in `TESTNET` en publiceer geen hostpoorten. `MY_HOUSE_IMAGE` is de API-image van my_house.

```bash
pin=$(cat e2e/simulators/my-house.commit)
mkdir -p "$PROEF/gree" && git -C "${MY_HOUSE_BRON:-../my_house}" show "$pin:integrations/tests/fake_gree.py" > "$PROEF/gree/fake_gree.py"
docker run --rm --network "$TESTNET" --network-alias gree-a --read-only --cap-drop ALL \
  -v "$PWD/e2e/simulators:/sim:ro" -v "$PROEF/gree:/e2e:ro" -e PYTHONPATH=/sim \
  --entrypoint python "$MY_HOUSE_IMAGE" -m gree --host intern --mac f4911e00000a --naam unit-a --mod 4
```

Opties: `--udp-poort` (7000), `--port` (bediening, 30220), `--mac` (12 kleine hextekens), `--naam`, `--versie`
(`v1`/`v2`), en de begintoestand `--pow`, `--mod` (0 auto, 1 koelen, 2 drogen, 3 ventileren, 4 verwarmen),
`--settem`, `--temsen` (binnentemperatuur + 40). Zonder container mag alleen loopback.

## Bediening en scenario's

| Proef | Bediening | Meting |
|---|---|---|
| EX24 unit onbereikbaar | `POST /_sim/fouten {"naam":"onbereikbaar"}`; met `"pad":"^cmd$"` vallen alleen opdrachten weg | `/_sim/udp`: elk datagram met `fouten: ["onbereikbaar"]`; `/_sim/toestand.opdrachten` groeit niet |
| EX25 gedeelde buitenunit | twee containers, bijv. `--mod 4` en `--mod 1`, elk een eigen `--mac` | elke unit meldt zijn eigen MAC en modus; welke units een buitenunit delen legt my_house zelf vast |
| EX26 van buitenaf uit | `POST /_sim/toestand {"waarden":{"Pow":0}}` (zoals de afstandsbediening) | status leest `Pow` 0; `/_sim/udp` heeft een regel `soort: "extern"`; `opdrachten` blijft leeg tot my_house zelf iets stuurt |
| EX30 bind mislukt | `POST /_sim/fouten {"naam":"bind_mislukt"}` | scan antwoordt, bind blijft onbeantwoord (de fake negeert een bind in een niet-ondersteunde versie); `gebonden: false`, `bindpogingen` toont de pogingen |

`GET /_sim/toestand` geeft MAC, naam, gebonden, versie, de Gree-kolommen, bindpogingen en ontvangen opdrachten.
`GET /_sim/udp` geeft per datagram de soort (`scan`, `bind`, `status`, `cmd`, `onleesbaar`, `onbekend`, `extern`)
en de toegepaste fout, nooit de inhoud of sleutel. `POST /_sim/reset` geeft de begintoestand (niet gebonden, fouten
en log leeg). Een onbekende kolom of een waarde die geen geheel getal is, geeft 422.

## Niet nagebootst

- Broadcast-ontdekking zoals thuis: in een intern Docker-netwerk gaat broadcast anders. Koppel daarom op adres. Of
  my_house de unit via broadcast vindt, blijft NAS-werk (N-P6-06).
- De koppeling tussen binnen- en buitenunit: het LAN-protocol meldt die niet. De weigering bij tegengestelde modus
  is regelwerk van my_house.
- Echte firmwarevarianten. Hier mislukt de bind doordat de unit hem negeert, net als de fake in my_house.
- Tijd, wifi-sterkte en de terugkoppeling van de unit na een opdracht zoals een echte unit (de fake voert een
  opdracht direct uit en geeft de gevraagde waarde terug).
