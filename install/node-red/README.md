# Node-RED (I10) — instellingen, flowbundels en hulpmiddel

Hoort bij `install/manifests/node-red.manifest.yaml` (standaard Custom App, één gepinde image) en ADR-0023.
Node-RED leest bij de Hub, meldt gebeurtenissen aan de Hub en **vraagt** acties aan bij de Hub. Het voert zelf nooit
iets uit op de NAS of in een app: geen TrueNAS-sleutel, geen Docker-socket, geen exec-, function- of bestandsnodes.

> **Stand (eerlijk).** Alles hieronder is gemeten met de echte image (op digest, als 568:568, in Docker) tegen de
> Hub-simulator: `tools/nodered_rooktest.py`, rapport `docs/platform-v2/bewijs/nodered-rooktest-p2.json` —
> **Sim-getest**. Op een echte TrueNAS, met de echte Hub en de echte uitvoerder: **NIET GEVALIDEERD**.
> Het manifest levert alleen een beveiligde, lege Node-RED **zonder anonieme toegang**: zonder aanmelding van een
> beheerder geeft Node-RED niets, ook niet lezen (besluit C-11 van de gebruiker, 2026-11-11). Een beheerder, de flows en
> het service-token kan de manifeststandaard v1 niet leveren (zie "Wat nog niet kan"); tot die er zijn, is de editor
> helemaal dicht. Dat is de bedoelde stand (besluit C-12).

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
   de `-D sleutel=waarde`-delen instellingen van Node-RED. Gezet: `adminAuth` precies
   `{"type": "credentials", "users": []}` (geen `default`: zonder aanmelding geen enkel recht, ook niet lezen —
   besluit C-11; geen gebruiker in het manifest — besluit C-12), `nodesIncludes` (alleen de genoemde kern-nodes worden
   geladen — exec, function, file, watch, http in, websocket, tcp, udp en mqtt bestaan dan niet), geen installatie van
   extra nodes of modules, geen projecten, geen telemetrie, `/diagnostics` uit, en `proxyOptions` met een lege `env`:
   de `http request`-node zoekt dan zelf geen proxy in `http_proxy` of `all_proxy` van de container (gemeten in stap
   N10, met een controleproef zonder de instelling: dan kreeg de proxy de bearer-kop). Tegen de ingebouwde proxy van
   Node.js (`NODE_USE_ENV_PROXY=1` of `NODE_OPTIONS=--use-env-proxy`) helpt die instelling niet: daarvoor heeft elke
   `http request`-node in de bundels `persist: true` (punt 2). `tools/nodered_flows.py` laat in
   `settings-overrides.json` alleen deze sleutels toe; elke andere (bijvoorbeeld `adminAuth.default`, dat weer anoniem
   lezen zou geven) wordt geweigerd.
   Dit leunt op het gemeten gedrag van precies deze digest. Daarom bewaakt het manifest het zelf: de monitor
   verwacht **401 op `/diagnostics`** zonder aanmelding. Een Node-RED zonder deze instellingen antwoordt 200 en is dan
   niet gezond (controleproef N2 in de rooktest). Eerlijk: die 401 bewijst alleen dat `adminAuth` zonder anonieme
   rechten actief is, niet de rest van `FLOWS` (met `/diagnostics` aan geeft Node-RED ook 401; gemeten). Dat alleen
   kern-nodes geladen zijn, is alleen met een beheerder te zien (`GET /nodes`); tot er een beheerder is, bewijst de
   rooktest dat bij deze digest (N3). Anoniem `GET /flows`, `/nodes` en `/settings` geven 401 (N1; dezelfde paden als de
   controle `anoniem_geweigerd` van home-control-hub).
2. **Bij de import (`lint`).** Een bundel uit deze repo mag alleen: `inject`, `change`, `switch`, `http request`,
   `debug`, `catch`, `comment`. Een `http request` krijgt zijn adres uitsluitend van een `change`-node met een van de
   vaste Hub-routes hieronder (`$env('HUB_URL') & '<route>'`), met een vaste methode en het token als bearer, en heeft
   `persist: true` (`http_instelling`): een eigen verbinding, zodat ook de ingebouwde proxy van Node.js niet geldt.
   Zonder die eigen verbinding ging het verzoek met het token met `NODE_USE_ENV_PROXY=1` of
   `NODE_OPTIONS=--use-env-proxy` naar een proxy uit de omgeving, ook met `proxyOptions` (gemeten, controleproef N10).
   Een
   eigenschap in een `change`-regel is alleen een eenvoudige naam (letters, cijfers, `_` en punten, nooit `msg…`):
   Node-RED leest `msg.url`, `'url'`, `"url"` en `msg['url']` als `msg.url`, dus elke andere schrijfwijze wordt
   geweigerd (`change_regel`). Daarnaast een **witte lijst** (sinds het herstel van S-P2-410; een zwarte lijst van
   verboden woorden liet `msg.hasOwnProperty = msg.isPrototypeOf` en een inject die `flow['__proto__']` leest door):
   een `change`-regel zet in msg alleen precies `payload`, `url`, `followRedirects`, `meting` of `headers` (alleen
   wissen), een naam in de flow- of globale context is alleen kleine letters en `_` en geen eigenschap van het
   prototype (`__proto__`, `constructor`, `hasOwnProperty` …), een waarde heeft een vaste soort (`str`, `num`, `bool`,
   `json`, `date`) of komt uit msg/flow/global met zo'n naam, en JSONata mag alleen precies de uitdrukkingen uit deze
   bundels zijn (vastgelegd als sha256 in het hulpmiddel); `env` en een ontbrekende soort worden geweigerd
   (`change_regel`). Een `inject` zet alleen `payload` (en `topic`) met een vaste soort of een naam uit flow/global,
   ook via de oude velden `payload`/`payloadType` (`inject_bron`). Een `switch` leest alleen msg (`propertyType`
   `msg`) met een van die vijf namen of `meting.<naam>` (kleine letters en `_`, geen eigenschap van het prototype;
   `switch_bron`), en een regel vergelijkt alleen met een vaste soort (zonder `vt` maakt Node-RED er zelf `num` of
   `str` van) of met JSONata uit de bundels; `env`, `msg`, `flow`, `global`, `prev`, een onbekende operator en vrije
   JSONata worden geweigerd (`switch_regel`). Een `debug` voert nooit JSONata uit (`targetType` en `statusType` niet
   `jsonata`; `debug_jsonata`). Een tekst die precies `${NAAM}` of `$(NAAM)` is, vervangt Node-RED door een instelling
   of omgevingsvariabele; die wordt in elk veld geweigerd (`env_substitutie`). Ook geweigerd (`change_regel`): een
   regel die geen object is, en de oude vorm van de `change`-node zonder lijst `rules` (Node-RED maakt die zelf tot een
   regel). Elke `http request` krijgt van zijn route-node `msg.followRedirects = false` (`redirect_niet_uit` als het
   ontbreekt; een andere waarde is `verzoek_overschreven`): gemeten bij deze digest stuurt Node-RED bij een
   doorverwijzing naar een andere host of poort de bearer-kop met het token mee. Geen adressen, tokens of
   `credentials` in Git; geen globale context; geen eigen `env` of groep op een tabblad of node (Node-RED leest
   `$env('HUB_URL')` daar eerst); een flow die een actie aanvraagt staat in Git **uitgeschakeld**. Eén bevinding en er
   verandert niets in Node-RED.
3. **Bij de Hub.** Het token is een service-token met alleen de scopes die nodig zijn (`inventaris.lezen`,
   en voor de testflow de scope om voorstellen in te dienen). De Hub zet de actor van een voorstel zelf op `flow` en
   bepaalt de risicoklasse; een `risk_claim` van de flow telt niet.

**Niet af te dwingen:** het Custom App-formulier kent geen regels voor uitgaand verkeer. Een beheerder kan in de
editor een eigen `http request` naar elk adres maken, en een eigen flow zonder `msg.followRedirects = false` volgt een
doorverwijzing met het token (gemeten: controleproef in stap N10; ook met die regel als dezelfde flow
`msg.hasOwnProperty` overschrijft, wat `lint` weigert). Beperkt is dus wat deze repo levert (punt 2) en wat de Hub
toestaat (punt 3), niet het netwerk van de container. Een proxy uit de omgeving negeren de flows uit Git dankzij
`proxyOptions` (punt 1) en `persist: true` (punt 2): gemeten in stap N10 met `http_proxy`, `HTTP_PROXY` en `all_proxy`,
ook samen met `NODE_USE_ENV_PROXY=1` of `NODE_OPTIONS=--use-env-proxy`, met een Hub over HTTP (de proxy kreeg niets;
met de ingebouwde proxy aan kreeg een Hub-simulator van alle drie de bundels heartbeat, inventaris, event en voorstel
met het token). Een Hub over HTTPS achter een proxy is
niet gemeten, en of een proxy-instelling van TrueNAS zelf in de omgeving van de container komt, ook niet (geen NAS).
Gevolg van `persist`: elke `http request`-node houdt na een verzoek één verbinding met de Hub open tot de Hub die
sluit; de Hub-simulator doet dat na 5 s zonder verkeer, en in stap N10 stond na 8 s stilte geen verbinding meer open.
Een flow met een nodetype dat niet geladen is (bijvoorbeeld
exec of function), voert niets uit — ook niet als hij buiten `lint` om via de Admin-API is gezet (stap N3).

## Bundels

| Bundel | Versie | Soort | Standaard | Routes bij de Hub |
|---|---|---|---|---|
| `flows/hartslag.json` | 1.2.0 | alleen lezen en melden | aan | `POST /api/v1/monitoring/heartbeat` |
| `flows/mediaguard-offline.json` | 1.2.0 | alleen lezen en melden | aan | `GET /api/v1/apps`, `POST /api/v1/events/inbox` |
| `flows/testflow-herstart.json` | 1.2.0 | actie aanvragen | **uit** | `POST /api/v1/actions/proposals` (voorlopige route) |

De versie staat op de eerste regel van de beschrijving van het tabblad (`versie x.y.z`). Een wijziging = versie
ophogen, `python tools/nodered_flows.py lock`, committen. `make nodered` controleert manifest, bundels en lock.
Verandert een JSONata-uitdrukking, dan weigert `lint` de bundel tot de sha256 van de nieuwe uitdrukking bewust in
`TOEGESTANE_JSONATA` van `tools/nodered_flows.py` staat (de melding noemt het begin van die sha256). Die lijst houdt
de JSONata van **elke** uitgebrachte versie bij (per sha256 bundel, node, veld en versies; toevoegen, nooit vervangen),
zodat `lint` een oudere versie nooit om haar JSONata weigert. De vorige versies (1.0.0, 1.1.0, 1.1.1) staan als vaste
testkopie in `tests/repo/fixtures/node-red-versies/`; de repotest eist dat de lijst precies daarbij en bij de huidige
bundels hoort. Versie 1.1.1 (S-P2-410, herstel): alleen de beschrijving en `flow_versie` in het event. Versie 1.2.0
(herstelpoging 2): `persist: true` op elke `http request`-node, de beschrijving (proxy) en `flow_versie`.

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
              "flow_versie": "1.2.0"}
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
container. Importeren én exporteren vragen een beheerder (`--gebruiker`): Node-RED geeft zonder aanmelding niets
(besluit C-11); zonder `--gebruiker` of met een leeg wachtwoord stopt het hulpmiddel met een melding, vóór het Node-RED
aanroept. Geheimen staan nooit op de opdrachtregel:

```bash
export NODERED_BEHEER_WACHTWOORD=…   # wachtwoord van de Node-RED-beheerder
export NODERED_HUB_TOKEN=…           # service-token van de Hub
python tools/nodered_flows.py importeer --url <adres van Node-RED> --gebruiker <beheerder> --hub-url <base-URL van de Hub>
python tools/nodered_flows.py exporteer --url <adres van Node-RED> --gebruiker <beheerder> --naar <map>
python tools/nodered_flows.py importeer … --bundel hartslag=<pad naar oudere versie>  # herstel van een oude versie (vanaf een versie na 1.2.0)
```

- **Import:** eerst `lint`; dan één `POST /flows` met de bundels, de Hub-URL als globale omgevingsvariabele `HUB_URL`
  van Node-RED en het token als credential van de `http request`-nodes. Node-RED versleutelt credentials op schijf en
  geeft ze nooit terug; de import leest terug en vergelijkt per bundel de sha256 met de verwachting, laat de
  teruggelezen nodes van elk eigen tabblad opnieuw door `lint` gaan, en controleert dat op de eigen tabbladen geen
  `env` staat, dat de globale `HUB_URL` precies de opgegeven Hub-URL is en dat geen node buiten de eigen tabbladen
  bij een eigen node kan komen (zo'n node zou buiten `lint` om bijvoorbeeld `msg.url` kunnen zetten). Geweigerd wordt
  een vreemde node met hetzelfde id als een eigen node (Node-RED gaf zo'n dubbele `http request` het token van de
  eigen node; gemeten in de herreview), met `wires` die niet precies een lijst van lijsten met node-ids is (Node-RED
  leest ook een geneste lijst of een object met `length` als draad), of met een eigen id ergens als tekst (een draad,
  of een ander veld zoals `links` of `scope`). Vindt het
  teruglezen iets, of faalt het teruglezen zelf (bijvoorbeeld een netwerkfout), dan zet de import de flows van vóór
  de import terug (lukt dat niet, dan zegt de melding dat) en geeft een fout. Staat zo'n vreemde node er al vóór de
  import, dan weigert de import zonder iets te schrijven (stap N10, vier vormen). Tabbladen die niet uit deze repo
  komen en geen eigen node raken, blijven staan.
- **Export (Git-export):** één bestand per tabblad plus de vergelijking met `flows.lock.json`:
  `gelijk_aan_lock`, `afwijkend` of `onbekend` (tabblad dat niet uit deze repo komt). Een tabblad-id is nooit een
  pad: een tabblad dat niet in het lock staat en geen eigen id (`jlp-…`) heeft, krijgt de bestandsnaam
  `tab-<sha256[:16]>`.
- **Herstel:** een oudere versie terugzetten is dezelfde import met het oudere bestand (uit Git), als die versie
  door `lint` komt; daarna toont de export weer `gelijk_aan_lock` zodra de versie uit het lock terugstaat. `lint`
  weigert een versie nooit om haar JSONata, wel om een veiligheidseis die later kwam: de versies vóór 1.2.0 missen
  `persist: true` (en 1.0.0 ook `msg.followRedirects = false`) en zijn dus niet terug te zetten. Een echte oudere
  versie terugzetten kan pas als er een versie na 1.2.0 is; stap N8 zet nu een andere versie (een variant) en daarna
  de versie uit Git terug.
- **Back-up:** `/data` (flows, versleutelde credentials en de sleutel daarvan) gaat mee in de `stopped_snapshot` van
  het manifest. De bundels zelf staan in Git.

## Wat nog niet kan (geblokkeerd, gemeld als contractvraag)

| Onderdeel | Waarom | Stand |
|---|---|---|
| Beheerder van de editor op de NAS | de standaard kent geen afgeleid geheim (bcrypt-hash) en weigert geheimen in formulierwaarden; zonder beheerder is de editor helemaal dicht (besluit C-11); de voorziening zonder geheim volgt uit besluit C-12 (ontwerp S-P2-510) | Geblokkeerd |
| Flows en `HUB_URL` op de NAS plaatsen | de standaard kent geen bestanden of startopdracht; importeren vraagt een beheerder | Geblokkeerd |
| Service-token voor Node-RED | een flowruntime kan zich niet inschrijven (§3.3 vraagt een Ed25519-sleutel); geen andere route beschreven | Geblokkeerd |
| Flow "my_house sensor verouderd" | geen contractroute voor sensorversheid | Geblokkeerd (`capability_unavailable`) |
| Incident in de echte Hub of Uptime Kuma na stoppen | vraagt de echte Hub; vreemde `flow.*`-events zijn daar alleen informatie | NIET GEVALIDEERD |
| Uitgaand verkeer beperken | het Custom App-formulier kent geen egressregels | niet afdwingbaar |
| Editor alleen via vertrouwd netwerk/SSO | netwerkindeling van de NAS (D01, D03, D16) is onbekend | NIET GEVALIDEERD |
