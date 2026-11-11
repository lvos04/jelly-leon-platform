# Node-RED (I10) — instellingen, flowbundels en hulpmiddel

Hoort bij `install/manifests/node-red.manifest.yaml` (standaard Custom App, één gepinde image) en ADR-0023.
Node-RED leest bij de Hub, meldt gebeurtenissen aan de Hub en **vraagt** acties aan bij de Hub. Het voert zelf nooit
iets uit op de NAS of in een app: geen TrueNAS-sleutel, geen Docker-socket, geen exec-, function- of bestandsnodes.

> **Stand (eerlijk).** Alles hieronder is gemeten met de echte image (op digest, als 568:568, in Docker) tegen de
> Hub-simulator: `tools/nodered_rooktest.py`, rapport `docs/platform-v2/bewijs/nodered-rooktest-p2.json` —
> **Sim-getest**. Op een echte TrueNAS, met de echte Hub en de echte uitvoerder: **NIET GEVALIDEERD**.
> Het manifest levert alleen een beveiligde, lege Node-RED die voor iedereen op het vertrouwde netwerk alleen-lezen
> is. Een beheerder, de flows en het service-token kan de manifeststandaard v1 niet leveren (zie "Wat nog niet kan").

## Bestanden

| Bestand | Wat |
|---|---|
| `settings-overrides.json` | de beveiligingsinstellingen; hieruit wordt de omgevingsvariabele `FLOWS` van het manifest gemaakt |
| `flows/*.json` | flowbundels: één tabblad per bestand, in de exportvorm van Node-RED, zonder adressen of inloggegevens |
| `flows.lock.json` | per bundel versie, sha256, soort, routes en eventtypes (de "flowhistorie" samen met Git) |
| `../../tools/nodered_flows.py` | `render`, `lint`, `lock`, `importeer`, `exporteer` |
| `../../tools/nodered_rooktest.py` | de proef met de echte image tegen de Hub-simulator |

## Beveiliging: wat waar wordt afgedwongen

1. **In Node-RED zelf (via `FLOWS`).** De entrypoint van deze image geeft `$FLOWS` ongequoot door; daardoor worden
   de `-D sleutel=waarde`-delen instellingen van Node-RED. Gezet: `adminAuth` (schrijven alleen na inloggen; zonder
   beheerder kan niemand schrijven; anoniem alleen lezen), `nodesIncludes` (alleen de genoemde kern-nodes worden
   geladen — exec, function, file, watch, http in, websocket, tcp, udp en mqtt bestaan dan niet), geen installatie van
   extra nodes of modules, geen projecten, geen telemetrie, `/diagnostics` uit.
   Dit leunt op het gemeten gedrag van precies deze digest. Daarom bewaakt het manifest het zelf: de monitor
   verwacht **403 op `/diagnostics`**. Een Node-RED zonder deze instellingen antwoordt 200 en is dan niet gezond
   (controleproef N2 in de rooktest).
2. **Bij de import (`lint`).** Een bundel uit deze repo mag alleen: `inject`, `change`, `switch`, `http request`,
   `debug`, `catch`, `comment`. Een `http request` krijgt zijn adres uitsluitend van een `change`-node met een van de
   vaste Hub-routes hieronder (`$env('HUB_URL') & '<route>'`), met een vaste methode en het token als bearer. Geen
   adressen, tokens of `credentials` in Git; geen globale context; een flow die een actie aanvraagt staat in Git
   **uitgeschakeld**. Eén bevinding en er verandert niets in Node-RED.
3. **Bij de Hub.** Het token is een service-token met alleen de scopes die nodig zijn (`inventaris.lezen`,
   en voor de testflow de scope om voorstellen in te dienen). De Hub zet de actor van een voorstel zelf op `flow` en
   bepaalt de risicoklasse; een `risk_claim` van de flow telt niet.

**Niet af te dwingen:** het Custom App-formulier kent geen regels voor uitgaand verkeer. Een beheerder kan in de
editor een eigen `http request` naar elk adres maken. Beperkt is dus wat deze repo levert (punt 2) en wat de Hub
toestaat (punt 3), niet het netwerk van de container. Een flow met een nodetype dat niet geladen is (bijvoorbeeld
exec of function), voert niets uit — ook niet als hij buiten `lint` om via de Admin-API is gezet (stap N3).

## Bundels

| Bundel | Versie | Soort | Standaard | Routes bij de Hub |
|---|---|---|---|---|
| `flows/hartslag.json` | 1.0.0 | alleen lezen en melden | aan | `POST /api/v1/monitoring/heartbeat` |
| `flows/mediaguard-offline.json` | 1.0.0 | alleen lezen en melden | aan | `GET /api/v1/apps`, `POST /api/v1/events/inbox` |
| `flows/testflow-herstart.json` | 1.0.0 | actie aanvragen | **uit** | `POST /api/v1/actions/proposals` (voorlopige route) |

De versie staat op de eerste regel van de beschrijving van het tabblad (`versie x.y.z`). Een wijziging = versie
ophogen, `python tools/nodered_flows.py lock`, committen. `make nodered` controleert manifest, bundels en lock.

### Hartslag (I10-10-06)

Elke 60 s (eerste keer na 5 s) een heartbeat volgens contract 1.3 §6.1:
`{status: "healthy", observed_at, verwacht_interval_s: 60, checks: [{name: "flow_vertraging", status, detail: "<n> ms"}]}`.
`flow_vertraging` is de tijd tussen de tik en het moment waarop de flow het bericht opbouwt (de wachtrij van
Node-RED); vanaf 5000 ms is de check `degraded`. De Hub beoordeelt zelf; blijft de heartbeat 3 × het interval uit,
dan is de dienst `unknown` (`meting_verouderd`).

### MediaGuard offline → melding (I10-10-03)

Elke 60 s leest de flow de inventaris van de Hub en zoekt het item met `app_id = "mediaguard"`:

| Gemeten | Toestand | Event |
|---|---|---|
| `aanwezig: true` en `runtime_staat: RUNNING` | `up` | `flow.app_watch.recovered`, alleen na `down` |
| `aanwezig: false`, of `runtime_staat` `STOPPED`/`CRASHED` | `down` | `flow.app_watch.down`, één keer per overgang |
| iets anders, app niet in de lijst, Hub onbereikbaar of fout | `unknown` | geen event, en geen "hersteld" |

Eventschema (envelop van contract 1.3 §7, `schema_version "1.0"`):

```json
{
  "event_type": "flow.app_watch.down",
  "source_app": "node-red",
  "source_event_id": "mediaguard-offline:<uuid>",
  "entity": {"type": "app", "id": "mediaguard"},
  "actor": {"type": "flow", "id": "mediaguard-offline"},
  "classification": "internal",
  "payload": {"staat": "down", "runtime_staat": "STOPPED", "aanwezig": true, "bron": "hub_inventaris",
              "flow_versie": "1.0.0"}
}
```

`flow.app_watch.recovered` heeft dezelfde vorm met `staat: "up"`. De toestand staat in het geheugen van de flow: na
een herstart van Node-RED is de eerste meting een nieuwe overgang (een nog gestopte MediaGuard wordt dan opnieuw
gemeld — stap N9; een draaiende niet — stap N5). De flow leest alleen en wijzigt niets aan MediaGuard.

**Niet gebouwd:** de flow "my_house sensor verouderd → melding". Het contract heeft geen route waarmee een flow de
versheid van een sensor kan lezen; er is dus niets om tegen te bouwen of te testen (`capability_unavailable`). Er is
geen nagebootste versie.

### Testflow: herstart aanvragen (I10-10-04)

Handmatige inject → `ActionProposal` (`action_id: "app.restart"`, `target: {app_id}`, lege `parameters`) naar de Hub.
De flow kan alleen aanvragen. In de rooktest besliste de Hub-simulator: gewone app → R1 `toegestaan`; kritieke app
(authentik) → R2 `wacht_op_goedkeuring`. Uitvoeren doet alleen de uitvoerder, na een ondertekend verzoek van de Hub.
De bundel staat uit; inschakelen is een bewuste keuze bij de import (`--inschakelen testflow-herstart`).

## Importeren, exporteren en herstellen (I10-10-03, -05)

Dit is een hulpmiddel voor de beheerder/ontwikkelaar op een werkplek, niet voor de eindgebruiker en niet in de
container. Geheimen staan nooit op de opdrachtregel:

```bash
export NODERED_BEHEER_WACHTWOORD=…   # wachtwoord van de Node-RED-beheerder
export NODERED_HUB_TOKEN=…           # service-token van de Hub
python tools/nodered_flows.py importeer --url <adres van Node-RED> --gebruiker <beheerder> --hub-url <base-URL van de Hub>
python tools/nodered_flows.py exporteer --url <adres van Node-RED> --naar <map>     # kan zonder inloggen (alleen lezen)
python tools/nodered_flows.py importeer … --bundel hartslag=<pad naar oudere versie>  # herstel van een oude versie
```

- **Import:** eerst `lint`; dan één `POST /flows` met de bundels, de Hub-URL als globale omgevingsvariabele `HUB_URL`
  van Node-RED en het token als credential van de `http request`-nodes. Node-RED versleutelt credentials op schijf en
  geeft ze nooit terug; de import leest terug en vergelijkt per bundel de sha256 met de verwachting. Tabbladen die
  niet uit deze repo komen blijven staan.
- **Export (Git-export):** één bestand per tabblad plus de vergelijking met `flows.lock.json`:
  `gelijk_aan_lock`, `afwijkend` of `onbekend` (tabblad dat niet uit deze repo komt).
- **Herstel:** een oudere versie terugzetten is dezelfde import met het oudere bestand (uit Git); daarna toont de
  export weer `gelijk_aan_lock` zodra de versie uit het lock terugstaat.
- **Back-up:** `/data` (flows, versleutelde credentials en de sleutel daarvan) gaat mee in de `stopped_snapshot` van
  het manifest. De bundels zelf staan in Git.

## Wat nog niet kan (geblokkeerd, gemeld als contractvraag)

| Onderdeel | Waarom | Stand |
|---|---|---|
| Beheerder van de editor op de NAS | de standaard kent geen afgeleid geheim (bcrypt-hash) en weigert geheimen in formulierwaarden | Geblokkeerd |
| Flows en `HUB_URL` op de NAS plaatsen | de standaard kent geen bestanden of startopdracht; importeren vraagt een beheerder | Geblokkeerd |
| Service-token voor Node-RED | een flowruntime kan zich niet inschrijven (§3.3 vraagt een Ed25519-sleutel); geen andere route beschreven | Geblokkeerd |
| Flow "my_house sensor verouderd" | geen contractroute voor sensorversheid | Geblokkeerd (`capability_unavailable`) |
| Incident in de echte Hub of Uptime Kuma na stoppen | vraagt de echte Hub; vreemde `flow.*`-events zijn daar alleen informatie | NIET GEVALIDEERD |
| Uitgaand verkeer beperken | het Custom App-formulier kent geen egressregels | niet afdwingbaar |
| Editor alleen via vertrouwd netwerk/SSO | netwerkindeling van de NAS (D01, D03, D16) is onbekend | NIET GEVALIDEERD |
