# ADR-0023 — Node-RED als standaard Custom App: beveiligd via `FLOWS`, flows als gecontroleerde bundels

- Status: aanvaard · Datum: 2026-11-11 · Bijgewerkt: 2026-11-12 (S-P2-410: besluit C-11, restpunten van de review;
  herstel na de review van S-P2-410: witte lijst in `lint`, geen proxy uit de omgeving, vaste sleutels in `FLOWS`;
  herstelpoging 2: `persist`, vreemde nodes, de JSONata van elke uitgebrachte versie, switch en debug)
  · Contract: 1.3 §3, §4.4, §6.1, §7, §8, §9 · Bouwplan: I10-10-01…06
- Manifest: `install/manifests/node-red.manifest.yaml` · Instellingen en flows: `install/node-red/`
- Hulpmiddelen: `tools/nodered_flows.py`, `tools/nodered_rooktest.py` · Bewijs:
  `docs/platform-v2/bewijs/nodered-rooktest-p2.json` (Sim-getest; NIET GEVALIDEERD (echte NAS))

## Aanleiding

I10 vraagt Node-RED "non-root met /data, credentials secret en admin-auth, geen Docker-socket", zonder exec-node en
zonder onbeheerde contrib-packages, met flows die alleen lezen en melden en acties uitsluitend bij de Hub aanvragen.
De installatieregel van het platform laat alleen het standaard Custom App-formulier toe (image, poort, opslag,
omgevingsvariabelen): geen YAML/compose, geen eigen startopdracht, geen bestanden, geen terminal. Node-RED wordt
normaal ingesteld met een bestand `settings.js` in `/data`; dat kan het formulier niet leveren.

## Besluit

1. **Custom App met de officiële image op digest, niet de catalogusapp.** `nodered/node-red:5.0.9-24` op sha256
   (dezelfde app-versie als catalogusapp `community/node-red`). De catalogusapp draait als 1000:1000 en biedt geen
   van de instellingen hieronder; het is één container, dus de regel "catalogus voor meerdere containers" geldt niet.
   Draait als 568:568 met één eigen dataset op `/data`; geen extra rechten, geen Docker-socket, geen host network.
2. **Beveiliging via de omgevingsvariabele `FLOWS`.** Gemeten aan deze digest: de entrypoint geeft `$FLOWS`
   ongequoot door aan Node-RED, zodat `flows.json -D sleutel=waarde …` instellingen worden. Het manifest zet zo:
   `adminAuth` precies `{"type": "credentials", "users": []}`: **geen** gebruikers en **geen** `default`, dus zonder
   aanmelding geen enkel recht, ook niet lezen (besluit C-11, punt 4); `nodesIncludes`
   (alleen genoemde kern-nodes; exec, function, file, watch, http in, websocket, tcp, udp en mqtt worden niet
   geladen), geen installatie van nodes/modules, geen projecten, geen telemetrie, `/diagnostics` uit, en
   `proxyOptions` met een lege `env` (sinds het herstel van S-P2-410, R-VEILIG-2): de `http request`-node leest een
   proxy anders uit de omgeving van de container (`http_proxy`, `all_proxy` …), en dan ging het verzoek met het
   service-token via die proxy (gemeten; op LAN-HTTP als platte tekst in de tunnel). Met de lege `env` negeert hij
   die variabelen; Node-RED hoeft de Hub niet via een proxy te bereiken. Dat geldt alleen voor de proxy die Node-RED
   zelf zoekt: met `NODE_USE_ENV_PROXY=1` of `NODE_OPTIONS=--use-env-proxy` gebruikt Node.js v24 (in deze image)
   de proxy uit de omgeving voor de globale agent, en ging het verzoek met het token toch naar de proxy (gemeten in
   de herreview en in de controleproef van N10). Daarom heeft elke `http request`-node in de bundels `persist: true`
   (punt 5): een eigen agent, die die proxy niet gebruikt. `nodered_flows.py` laat in de overrides
   alleen deze vaste sleutels toe (`TOEGESTANE_OVERRIDES`): een sleutel als `adminAuth.default` werd anders een eigen
   instelling die weer anoniem lezen gaf (gemeten; R-VEILIG-4).
   De waarde wordt gemaakt uit `install/node-red/settings-overrides.json` (`nodered_flows.py render --check`) en mag
   geen spaties, `$`, `*`, `?` of andere shelltekens bevatten. `[` en `]` zijn ook jokertekens, maar de JSON-lijsten
   (`nodesIncludes`) hebben ze nodig; veilig zolang 568 de werkmap van de entrypoint niet kan beschrijven (gemeten bij
   deze digest in de review van P2; ADR-0025 besluit 6).
3. **De monitor bewaakt de beveiliging.** Dit mechanisme is gemeten gedrag van één digest, geen toezegging van
   upstream. `healthcheck.monitor` verwacht daarom **401 op `/diagnostics`** zonder aanmelding: met de instellingen
   actief is dat 401, zonder `FLOWS` 200 (controleproef N2; gemeten op 2026-11-11 bij deze digest, na besluit C-11).
   Verdwijnt het gedrag bij een update, dan is de app niet gezond in plaats van stil onbeveiligd.
   `upgrade.tag_only_rollback_safe: false`; elke nieuwe digest gaat eerst door de rooktest.
   *Wat de monitor na besluit C-11 nog bewijst (eerlijk):* alleen dat `adminAuth` zonder anonieme rechten actief is.
   Vóór C-11 gaf `/diagnostics` 403, en dat antwoord kwam van de instelling `diagnostics` (lezen mocht anoniem); dat
   bewees ook een tweede deel van `FLOWS`. Nu stopt de aanmelding elke anonieme aanvraag eerder: dezelfde `FLOWS` met
   `/diagnostics` aan geeft ook 401, en elk ander pad van de Admin-API ook (gemeten). Een Node-RED zonder `FLOWS` blijft
   herkenbaar (200; N2). Een `FLOWS` die maar voor een deel aankomt (bijvoorbeeld `adminAuth` wel en `nodesIncludes`
   niet) ziet de monitor niet. Dat de kern-nodes beperkt zijn, toont `GET /nodes`, en dat kan alleen een beheerder: de
   controle na installatie `exec_en_function_niet_geladen` (handmatig, zodra er een beheerder is) en tot dan de rooktest
   bij deze digest (N3, dezelfde `FLOWS` met alleen een wegwerpbeheerder). Die handmatige controle blijft open tot
   besluit C-12 is uitgevoerd, en zo lang is Node-RED niet "gezond" (contract kop 9); dat is de bedoelde stand. Wat de
   beheerder nu wel kan nagaan, staat in de controle zelf: de digest van de geïnstalleerde image tegen die in het
   rooktestrapport, en de twee http-controles (R-MANIFEST-2 van S-P2-410).
4. **Geen anonieme toegang (besluit C-11 van de gebruiker, 2026-11-11).** De editor en de Admin-API zijn alleen
   bruikbaar na aanmelding van een beheerder. Gemeten (N1): anoniem `GET /flows`, `GET /nodes`, `GET /settings` en
   `GET /diagnostics` geven 401, `POST /flows` ook; exporteren zonder aanmelding wordt geweigerd. Dezelfde paden
   controleert home-control-hub (`anoniem_geweigerd`, 401) en de controle na installatie `anoniem_geweigerd` van dit
   manifest. Zonder beheerder is de editor daardoor helemaal dicht, ook voor lezen; dat is de bedoelde stand (besluit
   C-12: geen geheim en geen gebruiker in het manifest). Wanneer Node-RED bruikbaar wordt, bepaalt C-5 (zie "Wat het
   manifest niet kan"). `tools/nodered_flows.py` weigert overrides met een andere `adminAuth` (`render`, `render
   --check`), en `importeer` en `exporteer` vragen een beheerder (`--gebruiker`).
5. **Flows zijn bundels in Git met een eigen controle.** Eén tabblad per bestand (`install/node-red/flows/`), vaste
   ids (`jlp-…`), versie in de beschrijving, sha256 in `flows.lock.json`. `lint` laat alleen `inject`, `change`,
   `switch`, `http request`, `debug`, `catch` en `comment` toe; een `http request` mag alleen naar een vaste
   Hub-route (`$env('HUB_URL') & '<route>'`) met vaste methode en bearer; geen adressen, tokens of credentials in
   Git. Een flow die een actie aanvraagt staat in Git uitgeschakeld. De import weigert bij één bevinding.
   Sinds ADR-0025 (review P2): Node-RED zoekt `$env('HUB_URL')` eerst in de env van groep en tabblad. Daarom heeft
   een tabblad alleen `id`, `type`, `label`, `disabled` en `info`, en heeft geen node een `env` of groep
   (`veld_niet_toegestaan`). Een eigenschap in een `change`-regel is alleen een eenvoudige naam en nooit `msg…`:
   Node-RED haalt `msg.` er zelf af en verstaat aanhalingstekens en haken, dus `msg.url`, `'url'` en `msg['url']`
   zijn `msg.url` (`change_regel`; ADR-0025 besluit 6). Na de import leest het hulpmiddel terug dat alleen de
   globale `HUB_URL` telt en dat die precies de opgegeven Hub-URL is, en laat het de teruggelezen nodes van elk
   eigen tabblad opnieuw door `lint` gaan; bij een bevinding gaan de flows van vóór de import terug.
   Sinds S-P2-410 (restpunten van de review van P2):
   - *Witte lijst in plaats van verboden woorden* (herstel na de review van S-P2-410, R-TECH-1 en R-VEILIG-1;
     V-086). Eerst weigerde `lint` alleen de segmenten `__proto__`, `constructor` en `prototype` in een `change`-regel.
     Dat liet andere wegen naar het prototype van elk object open: `msg.hasOwnProperty = msg.isPrototypeOf` zette
     `msg.followRedirects` buiten werking (de `http request`-node leest die met `msg.hasOwnProperty`; gemeten: het
     token ging bij een doorverwijzing naar een andere host), en een `inject` die `flow['__proto__']` leest gaf
     `Object.prototype` als payload, waarna `payload.headers.X` een kop op elk object zette. Nu geldt alleen wat de
     bundels echt gebruiken. Een `change`-regel zet in msg alleen precies `payload`, `url` (een vaste Hub-route),
     `followRedirects` (alleen `false`), `meting` of `headers` (alleen wissen), zonder pad met punten. Een naam in de
     flow- of globale context is alleen kleine letters en `_` en geen eigenschap van het prototype; dat geldt in `p`,
     in `to` (bij een bron uit msg, flow of global; uit msg dezelfde vijf namen) en in een `inject` (ook via de oude
     velden `payload`/`payloadType`). Een waarde heeft een vaste soort (`str`, `num`, `bool`, `json`, `date`); `env`,
     `bin`, een ontbrekende soort (Node-RED maakt daar zelf `msg` van als `to` met `msg.` begint) en een `inject` met
     `jsonata` of `msg` worden geweigerd. JSONata in een `change`-regel mag alleen precies een van de uitdrukkingen uit
     de bundels zijn (sha256 in `TOEGESTANE_JSONATA`; voor `msg.url` de vaste Hub-routes), dus ook geen
     `$flowContext('__proto__')` of `$lookup`. Codes: `change_regel` en `inject_bron`. Elke eigenschap van
     `Object.prototype` (en `prototype`) valt zo af in `p` en `to` van een `change`-regel, in een `inject` en in een
     `switch` (hieronder); de tests eisen dat per naam en per plek.
   - *Switch en debug* (herstelpoging 2; vervolgpunt van de herreviews): een `switch` leest alleen msg
     (`propertyType` `msg`) met een van de vijf msg-namen of `meting.<naam>` (kleine letters en `_`, geen eigenschap
     van het prototype; `switch_bron`). Een regel heeft een bekende operator en vergelijkt alleen met een vaste soort
     (`str`, `num`, `bool`, `json`, `date`; zonder `vt` maakt Node-RED er zelf `num` of `str` van) of met JSONata uit
     `TOEGESTANE_JSONATA`, ook in `v2`; `env`, `msg`, `flow`, `global` en `prev` worden geweigerd (`switch_regel`).
     Een `debug` voert nooit JSONata uit: `targetType` en `statusType` zijn niet `jsonata` (`debug_jsonata`). Een
     `switch` en een `debug` schrijven niets; dit sluit het lezen met vrije JSONata of uit de context af.
   - *Geen env-substitutie* (KLEIN-50): Node-RED vervangt elke tekstwaarde die precies `${NAAM}` of `$(NAAM)` is
     door een instelling of omgevingsvariabele, in elk veld en bij elke soort (ook `str`). Zo'n tekst wordt overal
     geweigerd (`env_substitutie`), dus ook in `v` van een `switch`, in `to` van een `change`-regel en in een
     `inject`; een tekst die het patroon alleen bevat blijft toegestaan.
   - *Eigen verbinding per `http request`* (herstelpoging 2, R-VEILIG-2): elke `http request`-node heeft
     `persist: true` (`http_instelling` als het ontbreekt). Zonder eigen agent gebruikt got de globale agent van
     Node.js, en die volgde met `NODE_USE_ENV_PROXY=1` of `NODE_OPTIONS=--use-env-proxy` de proxy uit de omgeving,
     ook met `proxyOptions` (punt 2). Gevolg voor de Hub: elke `http request`-node houdt na een verzoek hooguit één
     verbinding open tot de Hub die sluit (keep-alive). De Hub-simulator sluit na 5 s zonder verkeer; in N10 stonden
     direct na het verkeer 3 verbindingen open en na 8 s stilte geen. De web-voorkant van de echte Hub houdt een
     verbinding volgens haar bron 65 s open (`keepAliveTimeout` in `apps/web/server/hub-server.mjs` van
     ai-server-manager; gelezen, niet gemeten): met een tik elke 60 s blijft dan per node één verbinding open, niet meer.
     De bundels kregen daarvoor versie 1.2.0.
   - *Vreemde nodes* (R-VEILIG-3; herstelpoging 2 dicht bij twijfel): een node buiten de eigen tabbladen kan buiten
     `lint` om `msg.url` zetten en het bericht naar een eigen node sturen. Geweigerd wordt een vreemde node met
     hetzelfde id als een eigen node (Node-RED gaf zo'n dubbele `http request` het token van de eigen node; gemeten in de
     herreview), met `wires` die niet precies een lijst van lijsten met node-ids is (Node-RED leest ook een geneste
     lijst of een object met `length` als draad), of met een eigen id ergens als tekst (een draad, of een veld als
     `links` of `scope`). Staat zo'n node er vóór de import, dan weigert de import zonder te schrijven (N10, vier
     vormen); ziet het teruglezen er een, dan gaan de flows van vóór de import terug. Wat een beheerder ná de import
     zelf in de editor wijzigt, valt daarbuiten (zie "Uitgaand verkeer").
   - *Oude vorm van de `change`-node:* een `change`-node zonder lijst `rules` (met `action`/`property`/`to`, die
     Node-RED zelf tot een regel maakt) wordt geweigerd (`change_regel`), net als een regel die geen object is.
   - *Doorverwijzingen:* gemeten bij deze digest stuurt de `http request`-node bij een doorverwijzing (307 en 302)
     naar een andere poort of een andere host de bearer-kop met het service-token mee. Node-RED zet de kop vóór elk
     verzoek terug als `Authorization`, zodat got hem bij een andere herkomst niet meer weghaalt (`got` 14.6.6 haalt
     alleen de kleine letters `authorization` weg). Elke `http request` krijgt daarom van zijn route-node
     `msg.followRedirects = false` (bool); ontbreekt dat, dan `redirect_niet_uit`, en elke andere regel op
     `msg.followRedirects` is `verzoek_overschreven`. De bundels kregen daarvoor versie 1.1.0; versie 1.1.1 (herstel)
     wijzigt alleen de beschrijving en `flow_versie`; versie 1.2.0 is `persist` (hierboven). Rooktest N10 meet het bij
     elke digest opnieuw, met een
     controleproef zonder de regel en een met de regel plus `msg.hasOwnProperty = msg.isPrototypeOf`.
   - *Teruglezen faalt zelf* (netwerkfout, of teruggelezen gegevens die geen lijst van objecten zijn): `importeer`
     zet ook dan de flows van vóór de import terug en geeft een Nederlandse melding (R-VEILIG-5); `lint` geeft bij
     `rules`, `wires` of `props` van een verkeerde vorm een bevinding (`change_regel`, `draad_ongeldig`,
     `inject_velden`) in plaats van te crashen.
6. **Geheimen alleen bij de import.** De Hub-URL wordt de globale omgevingsvariabele `HUB_URL` van Node-RED; het
   service-token wordt een credential van de `http request`-nodes. Beide komen uit omgevingsvariabelen van het
   hulpmiddel, nooit van de opdrachtregel en nooit in Git. Eerlijk: de sleutel waarmee Node-RED credentials
   versleutelt staat in dezelfde `/data`; wie de dataset kan lezen, kan het token lezen. Bescherming is dus de
   eigenaar (568) en de toegang tot dataset en snapshots, niet de versleuteling.
7. **Acties alleen als voorstel bij de Hub (I10-10-04).** Er is geen node die een app of de NAS kan wijzigen. De
   testflow dient een `ActionProposal` in; de Hub zet de actor (`flow`), classificeert (R1/R2) en vraagt zo nodig
   goedkeuring; uitvoeren doet alleen de uitvoerder.
8. **Monitoring (I10-10-06).** De bundel `hartslag` stuurt elke 60 s een heartbeat (§6.1) met de check
   `flow_vertraging` (wachtrijvertraging in ms). Stopt Node-RED, dan faalt de monitor en veroudert de heartbeat
   (`unknown` na 3 × het interval). Een eigen Uptime Kuma-koppeling is niet gebouwd: Uptime Kuma krijgt zijn monitors
   van de Hub uit het monitorplan van het manifest.
9. **Versies en herstel (I10-10-05).** Git + lock zijn de historie; `exporteer` schrijft per tabblad een bestand en
   vergelijkt met het lock; een oude versie herstellen is een import van het oudere bestand met checksum-readback,
   als die versie door `lint` komt. `TOEGESTANE_JSONATA` houdt de JSONata van elke uitgebrachte versie bij (per
   sha256 bundel, node, veld en versies; toevoegen, nooit vervangen; herstelpoging 2, R-TECH-2), zodat `lint` een
   oudere versie nooit om haar JSONata weigert; de vorige versies staan als vaste testkopie in
   `tests/repo/fixtures/node-red-versies/`. Een veiligheidseis die later kwam, geldt wel: de versies vóór 1.2.0 missen
   `persist` (1.0.0 ook `msg.followRedirects = false`) en zijn dus bewust niet terug te zetten; met alleen die eisen
   erbij komen ze door `lint` (repotest). Een echte oudere versie terugzetten kan pas als er een versie na 1.2.0 is;
   N8 zet nu een andere versie (een variant) en daarna de versie uit Git terug.
   Een tabblad-id is nooit een pad: alleen een lock-naam of een eigen id (`jlp-…`) wordt een bestandsnaam, elk ander
   id `tab-<sha256[:16]>` (ADR-0025 besluit 6).
   `/data` gaat mee in de `stopped_snapshot`.

## Wat het manifest niet kan (geblokkeerd)

De manifeststandaard v1 kent geen startopdracht, geen bestanden en geen afgeleide geheimen, en de vertaling weigert
geheimen in formulierwaarden. Daardoor levert het manifest op een NAS alleen een beveiligde, **lege** Node-RED die
zonder beheerder **helemaal dicht** is, ook voor lezen (besluit C-11). Contractvraag C-5 bepaalt dus wanneer Node-RED
bruikbaar wordt; voor de beheerder en het dienstaccount is besluit C-12 genomen (een voorziening zonder geheim in de
manifeststandaard; ontwerp S-P2-510, bouw na P2). Niet leverbaar zonder uitbreiding van de standaard of een route bij
de Hub:

- een beheerder (bcrypt-hash in `adminAuth.users`);
- het plaatsen van de bundels en `HUB_URL`;
- een service-token: een flowruntime kan zich niet inschrijven (§3.3 vraagt een Ed25519-sleutel) en §3.1 verbiedt
  een langlevend token in het formulier.

De rooktest zet het beheerdersaccount daarom zelf met een wegwerpwaarde in `FLOWS` en registreert de dienst bij de
simulator. Dat is een proefopstelling, geen installatiepad. Het importhulpmiddel is voor beheerder/ontwikkelaar; de
eindgebruiker heeft geen terminal.

Verder niet gebouwd of niet af te dwingen:

- **Uitgaand verkeer:** het formulier kent geen egressregels. Beperkt zijn de bundels uit deze repo (lint), wat de
  Hub een service-token toestaat, en de proxy: met `proxyOptions` uit `FLOWS` en `persist: true` in de bundels
  gebruikt een flow uit Git geen proxy uit de omgeving, ook niet met `NODE_USE_ENV_PROXY=1` of
  `NODE_OPTIONS=--use-env-proxy` (gemeten, N10, met een Hub over HTTP; een Hub over HTTPS achter een proxy is niet
  gemeten, en of een proxy-instelling van TrueNAS zelf in die omgeving komt, is niet op een NAS gemeten).
  Een beheerder kan in de editor zelf een `http request` naar elk adres maken, en een eigen flow zonder
  `msg.followRedirects = false` (of met `msg.hasOwnProperty` overschreven) volgt een doorverwijzing met het token
  (gemeten, N10).
- **Flow "my_house sensor verouderd → melding":** geen contractroute voor sensorversheid → `capability_unavailable`;
  er is geen nagebootste flow.
- **Incident bij stoppen:** in de simulator zichtbaar als verouderde heartbeat; in de echte Hub en Uptime Kuma
  NIET GEVALIDEERD. Events van het domein `flow` uit deze bron zijn voor de Hub "algemene informatie"; of en hoe
  daar een incident uit ontstaat, legt het contract niet vast.
- **Editor alleen via vertrouwd netwerk/SSO:** de netwerkindeling van de NAS (D01, D03, D16) is onbekend.
- **Formulier op echte TrueNAS:** of de waarde van `FLOWS` (JSON met aanhalingstekens) ongewijzigd door het echte
  formulier komt, is alleen via de vertaling van de validator en in Docker gemeten. Komt ze zo verminkt aan dat
  `adminAuth` wegvalt, dan falen de monitor (punt 3) en de controles na installatie; valt alleen een ander deel weg,
  dan ziet alleen een beheerder dat (controle `exec_en_function_niet_geladen`).

## Gemeten in de rooktest (Docker + Hub-simulator)

N1 container en beveiliging volgens het manifest, geen anonieme toegang (401 op `GET /flows`, `/nodes`, `/settings` en
`/diagnostics`, `POST /flows` 401, export zonder aanmelding geweigerd) · N2 controleproef zonder `FLOWS` (alles 200,
monitor faalt) · N3 beheerder met wegwerpaccount: alleen kern-nodes geladen (`GET /nodes` aangemeld), exec/function via
de rauwe Admin-API voeren niets uit, geen nodes te installeren · N4 import met versleuteld token en checksum-readback
(met de eigen controle over de teruggelezen tabbladen), export als beheerder gelijk aan het lock · N5 MediaGuard
gestopt/hersteld/onbekend ·
N6 testflow: uit = geen reactie; aan = R1 toegestaan / R2 wacht op goedkeuring, actor door de Hub gezet · N7 hartslag
met flowvertraging · N8 andere versie zichtbaar als afwijkend, herstel uit Git · N9 herstart zonder verlies van flows
en credentials, stoppen zichtbaar · N10 het token gaat alleen naar de Hub: een bundel met `env` op het tabblad, met
een tweede regel op `msg.url` of met `msg.hasOwnProperty = msg.isPrototypeOf` wordt geweigerd vóór Node-RED iets
verandert (een luisteraar ontvangt niets); een doorverwijzing (307/302) van de "Hub" naar een andere poort of host
volgt de flow uit Git niet; controleproef zonder `msg.followRedirects = false`: Node-RED volgt de doorverwijzing en
stuurt de bearer-kop mee, en het teruglezen ziet de ontbrekende regel (`redirect_niet_uit`); controleproef via de
rauwe Admin-API met die regel maar ook `msg.hasOwnProperty = msg.isPrototypeOf`: het doel van de doorverwijzing krijgt
de bearer-kop, en het teruglezen ziet de regel (`change_regel`); een vreemd tabblad (rauwe Admin-API) met een
geneste draad, een draad als object, of een node met een eigen id (inject of `http request`) laat de import weigeren
vóór Node-RED iets verandert, en een luisteraar ontvangt niets; met `http_proxy`, `HTTP_PROXY` en `all_proxy` in de
omgeving en de `FLOWS` uit het manifest krijgt een proxy niets en de Hub de heartbeat met het token; controleproef
zonder `proxyOptions`: het verzoek gaat via de proxy (`CONNECT`-tunnel) en de proxy ziet de bearer-kop; met daarbij
`NODE_USE_ENV_PROXY=1` of `NODE_OPTIONS=--use-env-proxy` krijgt de proxy van de drie bundels uit Git niets en een eigen
Hub-simulator heartbeat, inventaris, event en voorstel met het token, en staat daar na 8 s stilte geen verbinding meer
open; controleproef via de rauwe Admin-API met `persist: false`: de proxy krijgt de heartbeat met de bearer-kop, en
het teruglezen ziet het (`http_instelling`). De Hub-simulator kreeg hiervoor de heartbeat-route van §6.1.

## Afgewezen

- *Anoniem alleen lezen* (stand tot 2026-11-11: `adminAuth` met `default: {"permissions": "read"}`): afgewezen door
  besluit C-11 van de gebruiker en door B11-001 ("Viewer kan editor niet bereiken"; home-control-hub controleert dat
  met `anoniem_geweigerd`). Flows en instellingen bevatten geen geheimen, maar wel de opbouw van de automatisering en
  de geladen nodes; die horen niet bij iedereen op het netwerk.
- *Catalogusapp `community/node-red`:* draait als 1000, geen login, alle nodes (ook exec) geladen.
- *Eigen image met `settings.js` ingebakken:* B08 §5 staat voor apps van derden alleen officiële images toe.
- *`settings.js` in de dataset laten zetten door de gebruiker:* vraagt een terminal of bestandsbeheer; de
  installatieregel sluit dat uit.
- *Function-nodes toestaan "in een sandbox":* de sandbox van Node-RED is geen veiligheidsgrens; JSONata in
  `change`-nodes is genoeg voor lezen, beoordelen en melden.
- *Het token als omgevingsvariabele in het formulier:* verboden door §3.1 en zichtbaar in de TrueNAS-UI.
- *Een nagebootste my_house-flow tegen een verzonnen route:* zou succes suggereren voor iets dat niet bestaat.
