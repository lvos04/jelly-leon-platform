# ADR-0026 — Een gestopte Custom App blijft gestopt met een duidelijke melding; een late melding van `stoppen` geeft niets vrij

- Status: aangenomen (ontwerp; nog niet gebouwd) · Datum: 2026-11-12 · Contract: `contracts 1.3` §7, §8
- Bouwplan: B08-004 (deel: ontwerp voor de bekende beperking "Late melding van `stoppen` na het vervallen" uit ADR-0025)
- Wijzigt: ADR-0022 besluit 6 (voor Custom Apps geldt "de app blijft nooit gestopt achter" niet meer) · Vult aan:
  ADR-0025, kop "Bekende beperkingen en vervolgpunten (elk met eigenaar)", alinea "Late melding van `stoppen` na het
  vervallen"
- Besluiten van de gebruiker waarop dit ontwerp rust: ai-server-manager ADR-0030 besluit 21 (2026-11-11: stoppen van
  een Custom App is eenrichtingsverkeer; weer starten alleen in de TrueNAS-webinterface) en het besluit van 2026-11-12
  "Gestopt + duidelijke melding" (zie Besluit).
- Deze ADR wijzigt geen code. De bouw is een vervolgstap (zie Gevolgen); tot die stap gedaan is, geldt het gedrag dat
  ADR-0025 beschrijft.

## Context

**Wanneer een plan vervalt.** `apps/api/src/lifecycle/api_plans.py`, functie `vervaltijd`: het vroegste van aanmaken +
`PLAN_TTL` (1 uur) en, per gebruikte meting met status `ok`, `hubread.geldig_tot(gemeten_op)` = `gemeten_op` + 900 s
(`hubread.MAX_METING_LEEFTIJD`). "Vervallen" bestaat alleen zolang het plan `klaar_voor_voorstel` is (`plan_view`,
`report_step`); zodra één stap `gemeld_op` heeft, is het plan `in_uitvoering` (`planner.plan_status`) en vervalt het
niet meer (ADR-0024, "Een gestart plan vervalt niet"). Een plan kan dus enkele seconden na het maken vervallen
(`apps/api/tests/integration/test_plans_api.py::test_plan_vervalt_zodra_de_meting_eronder_te_oud_is`; ADR-0025
besluit 4).

**Wat `report_step` dan doet.** Bij `klaar_voor_voorstel` en `expires_at <= nu` weigert `report_step` de melding met
409 `plan_vervallen`, vóór `planner.meld`. Er wordt niets vastgelegd: geen `gemeld_op`, geen `execution_id`, geen
readback, geen outbox-event `lifecycle.plan.step_reported` en geen auditregel (de transactie rolt terug). Alleen het
antwoord wordt bij de `Idempotency-Key` bewaard (`idempotency.py`, `execute`). Dat gedrag staat vast in de
karakteriseringstest `test_plans_api.py::test_late_melding_van_stoppen_na_het_vervallen_geeft_starten_niet_vrij`.

**Wat `stoppen` vrijgeeft.** `planner.plan_update`: alleen bij `backup.db_export.method: stopped_snapshot` komt er een
stap `stoppen` (`app.stop`, verwacht `STOPPED`), daarna `snapshot-<opslag>` en `starten` (`app.start`, `opruimen`,
`herstelt: stoppen`, verwacht `RUNNING`, uitleg "Wordt ook vrijgegeven als de snapshot mislukt: de app blijft nooit
gestopt achter."). `planner.doorrekenen` geeft een opruimstap pas vrij als de stap die hij herstelt `voltooid` of
`mislukt` is gemeld. Zonder geregistreerde melding van `stoppen` blijft `starten` op `wacht`.

**Wie meldt.** `…/steps/{id}/result` is open voor `admin`, `operator` en de Hub als `service` (ADR-0025 besluit 5);
de readback is wat de melder doorgeeft. De echte Hub meldt nu niets aan deze dienst: in ai-server-manager komt
`lifecycle/v1` in `apps` buiten de tests niet voor. In de praktijk meldt een mens.

**Wat de Hub en de executor op dat moment al gedaan kunnen hebben.** Het voorstel van een planstap (`planner._Bouw.stap`,
veld `voorstel`) heeft geen `expires_in_seconds`; de echte Hub geeft dan 3600 s (ai-server-manager
`apps/api/src/hub/routers/actions.py`, `ProposalIn`), een goedkeuring is hooguit 15 minuten geldig (contract §8,
`Approval.expires_at`) en een ondertekend `ExecutionRequest` 300 s (ai-server-manager `hub/actions/service.py`,
`REQUEST_TTL`). Een stop kan dus ruim na `expires_at` van het plan zijn uitgevoerd, met readback `STOPPED`.

**De bredere bevinding: een gestopte Custom App start het platform niet meer.** ai-server-manager ADR-0030 besluit 21
en `apps/policy/src/asm_policy/engine.py` (`DOEL_ONHERKENBAAR`, `STOP_EENRICHTING`): TrueNAS toont bij een gestopte app
geen image; van een gestopte Custom App is voor het platform niet vast te stellen wat erin draait. Hub en executor
weigeren dan elke actie op die app, ook `app.start` en ook met goedkeuring (`doel_niet_herkenbaar`). Daarom is
`app.stop` op een Custom App altijd R2 (goedkeuring van een beheerder, nooit automatisch); de Hub-preview zegt dat weer
starten daarna alleen in de TrueNAS-webinterface kan (`hub/actions/preview.py`). Een catalogusapp blijft gestopt
herkenbaar; daar blijven `app.stop` en `app.start` R1. Bron ook:
`docs/platform-v2/01_ARCHITECTUUR/03_RISICO_AUTONOMIE.md` van ai-server-manager. Gevolgen hier:

- Alle manifesten in deze repo met `stopped_snapshot` zijn Custom Apps (`install.method: custom_app`):
  `install/manifests/gotify.manifest.yaml`, `uptime-kuma.manifest.yaml` en `node-red.manifest.yaml`.
- Met de echte Hub en executor laat een updateplan met `stoppen` zo'n app dus **altijd** gestopt achter, ook bij een
  tijdige melding: de stap `starten` wordt geweigerd. De uitleg "de app blijft nooit gestopt achter" (ADR-0022 besluit
  6, `planner.plan_update`) klopt niet tegen de echte Hub.
- Zolang de app gestopt is, weigert de echte Hub ook `bijwerken` (`app.update`) op die app (`doel_niet_herkenbaar`
  geldt voor elke actie met een app als doel). Snapshots en klonen hebben een dataset als doel en vallen daar niet
  onder.
- `planner.RISICO` verwacht voor `app.stop` R1; de echte Hub rekent voor een Custom App R2.
- De stand-ins van deze repo (`e2e/simulators/hub/hub_p2.py`, `e2e/simulators/hub/executor_sim.py`) kennen
  `doel_niet_herkenbaar` niet en voeren `starten` gewoon uit (V-001). De ketentest
  `apps/api/tests/integration/test_keten_plan_hub_executor_nas.py::test_update_met_schaduwtest_en_daarna_terugzetten`
  bewijst daarom niets over de echte Hub.

**Beschermde apps.** `planner._beschermd` en ADR-0025 besluit 2: Emby (elke actie), Jelly Leon/Jellyfin en MediaGuard
(update, terugzetten, verwijderen) krijgen geen enkele stap, dus ook geen `stoppen`. Apps met kijkgeschiedenis vallen
buiten dit probleem zolang die regels gelden (`test_planner.py::test_update_van_een_beschermde_app_geeft_niets_vrij`).

## Eisen

1. **Een app blijft nooit stil gestopt.** De herstelstap is bereikbaar, of de eigenaar krijgt een eerlijke, duidelijke
   melding die zegt welke app gestopt is en wat hij moet doen. Voor een Custom App kan het platform de app niet weer
   starten (ADR-0030 besluit 21); daar is de melding de enige eerlijke uitweg.
2. **Een vervallen plan geeft nooit een nieuwe uitvoerende stap vrij op een oude meting** (ADR-0025 besluit 4). Ook
   niet de herstelstap, ook niet "alleen even".
3. **Een melding telt alleen met `execution_id` en readback** (ADR-0022 besluit 7).
4. **Geen nagebootst succes.** Wat niet gemeten is, heet onbekend; een melding zonder readback is geen bewijs dat de app
   gestopt (of gestart) is. Een melding "onbekend" is nooit een herstel (contract §7).
5. **Niets automatisch starten bij een Custom App** (ADR-0030 besluit 21; besluit van de gebruiker 2026-11-12).
6. **HARDE EIS:** apps met kijkgeschiedenis (Emby, Jelly Leon/Jellyfin, MediaGuard) blijven beschermd en krijgen geen
   enkele stap; niets in dit ontwerp maakt die regels zwakker.
7. **Geen wijziging van ondertekening, rechten of de resultaatvorm van de executor** in dit ontwerp (keuze van de
   regisseur; anders is een veiligheidsreview en een contractwijziging nodig).

## Opties

### Deel A — het gewone pad: `stoppen` en `starten` in een updateplan voor een Custom App

**A1. Zo laten: `starten` als actie `app.start` (huidige code).** Bron: ADR-0022 besluit 6.
- Hub en executor: weigeren `app.start` op de gestopte Custom App (`doel_niet_herkenbaar`). Het plan geeft dus een stap
  vrij die nooit kan slagen en belooft "nooit gestopt achter".
- Contract §8 / resultaatvorm: geen wijziging.
- Test: alleen de ketentest tegen de stand-in, die het verkeerde gedrag als goed toont (V-001).
- Afgewezen: in strijd met eis 1 (de melding ontbreekt) en eis 4 (het plan belooft wat de echte Hub weigert).

**A2. `starten` wordt voor een Custom App een handmatige eigenaarsstap met een duidelijke melding.** Bron: ADR-0030
besluit 21 en het besluit van de gebruiker van 2026-11-12.
- Hub en executor: hoeven niets nieuws te weten. De eigenaarsstap is geen `ActionProposal`; de Hub krijgt voor deze
  app geen `app.start`. Stoppen blijft een voorstel `app.stop` dat de Hub als R2 beoordeelt.
- Contract §8 / resultaatvorm: geen wijziging. Contract §7: een event in het eigen domein `lifecycle` (zie Besluit);
  de vorm van de envelop verandert niet.
- Test: unit- en integratietests in deze repo (zie Gevolgen); de weigering van `app.start` door de echte Hub is
  bewaakt in ai-server-manager (`apps/policy/tests/test_policy.py`, `apps/executor/tests/test_uitvoeren.py`).

**A3. Het platform stopt een Custom App nooit; de eigenaar stopt en start zelf (handmatige stap `stoppen`).**
- Hub en executor: niets nieuws.
- Contract: geen wijziging.
- Afgewezen: meer handwerk voor de eigenaar en niet wat de gebruiker koos ("Gestopt + duidelijke melding": het
  platform stopt met goedkeuring, de app blijft gestopt en de eigenaar krijgt een melding).

### Deel B — de late melding van `stoppen` na het vervallen van het plan

**B1. Een melding telt nog als de Hub de stap vóór `expires_at` ondertekende; dan komt alleen de herstelstap vrij.**
Bron: voorbeeld in de stap; contract §8 (`ExecutionRequest.issued_at`).
- Hub en executor: de Hub moet het `issued_at` en de ondertekening van het verzoek aan deze dienst doorgeven, en deze
  dienst moet die controleren (de gepinde policy-sleutel kennen). `StepResultIn` kent nu alleen `status`,
  `execution_id`, `readback` en `reden`.
- Contract / resultaatvorm: verandert wel (ADR-0024 "Vorm van het resultaat van de echte executor", ADR-0025 "Melden
  door de Hub alleen"); raakt ondertekening en rechten.
- Afgewezen: in strijd met eis 7 (keuze van de regisseur), en voor een Custom App zinloos: de herstelstap `app.start`
  wordt door de echte Hub toch geweigerd.

**B2. Zo laten: 409 `plan_vervallen` en niets vastleggen (huidige code).** Bron: ADR-0025 besluit 4.
- Hub en executor: niets nieuws.
- Contract: geen wijziging.
- Afgewezen: de app kan gestopt zijn zonder enig spoor bij deze dienst en zonder melding aan de eigenaar (eis 1). Het
  enige spoor is het idempotency-antwoord.

**B3. Het plan blijft vervallen en geeft niets vrij; een late melding met `execution_id` en readback wordt los van de
stapvoortgang vastgelegd en levert de eigenaar een duidelijke melding.** Bron: ADR-0025 besluit 4 (niets vrij),
ADR-0022 besluit 7 (melding met readback), contract §7 (event in het eigen domein; de Hub maakt van een
probleemwerkwoord een melding).
- Hub en executor: niets nieuws voor de beslissing. De Hub krijgt een event via de bestaande outbox (contract §7,
  `POST {HUB_URL}/api/v1/events/inbox`). De Hub maakt van een `lifecycle`-event van deze dienst met een
  probleemwerkwoord nu al een melding (ai-server-manager `apps/api/src/hub/incidents/rules.py`, `DOMEINEN` en
  `_generic`), maar met een algemene titel ("`jelly-leon-platform: lifecycle.app down (<naam>)`") en zonder de
  Nederlandse tekst uit de payload. Een duidelijke tekst in de Hub vraagt een regel aan de kant van de Hub (zie
  Gevolgen).
- Contract §8 / resultaatvorm: geen wijziging. §7: geen wijziging van de envelop.
- Test: integratietests in deze repo (zie Gevolgen), waaronder de karakteriseringstest die bewust van verwachting
  wisselt.

**B4. De late melding van `stoppen` aanvaarden en alleen de herstelstap `starten` vrijgeven.**
- Hub en executor: voor een Custom App weigert de Hub `app.start` (`doel_niet_herkenbaar`); voor een catalogusapp zou
  de Hub het voorstel op een verse readback beoordelen.
- Contract: geen wijziging.
- Afgewezen: in strijd met eis 2 (een uitvoerende stap uit een vervallen plan) en voor Custom Apps zinloos. Bovendien
  zet het registreren van `gemeld_op` het plan op `in_uitvoering`, waarna het niet meer vervalt en ook de snapshots en
  de rest van het plan op de oude meting vrijkomen.

## Besluit

Gekozen: **A2 voor het gewone pad en B3 voor de late melding.** Reden: het besluit van de gebruiker van 2026-11-12
(keuzemenu: "Gestopt + duidelijke melding") en ADR-0030 besluit 21 van ai-server-manager (onverkort): een Custom App
die een updateplan stopt, blijft gestopt tot de eigenaar hem in TrueNAS start, en de eigenaar krijgt een duidelijke
melding. Beide opties wijzigen contract §8, de resultaatvorm, de ondertekening en de rechten niet, en geven nooit een
stap vrij uit een vervallen plan.

1. **Een Custom App blijft na `stoppen` gestopt tot de eigenaar hem in TrueNAS start.** In een updateplan voor een
   Custom App (`install.method: custom_app`) is `starten` geen actie `app.start` meer, maar een handmatige
   eigenaarsstap (soort `handmatig`, geen `voorstel`). De vrijgaveregel blijft gelijk (`opruimen`, `herstelt:
   stoppen`, dus ook vrij als de snapshot mislukt). Titel en uitleg zeggen eerlijk wat er is: de app staat gestopt;
   start hem in TrueNAS onder Apps; het platform kan een gestopte Custom App niet starten (ADR-0030 besluit 21). De
   stappen na `starten` (kloon, testkopie, `bijwerken`) wachten zoals nu op deze stap. Meldt de eigenaar haar
   `voltooid` terwijl de app nog gestopt is, dan weigert de Hub `bijwerken` alsnog (`doel_niet_herkenbaar`): dicht
   bij twijfel. Het plan verwacht voor `stoppen` van een Custom App de risicoklasse R2, zoals de Hub die rekent
   (`STOP_EENRICHTING`). ADR-0022 besluit 6 ("de app blijft nooit gestopt achter") geldt daarmee voor Custom Apps
   niet meer.
2. **"Starten vrijgeven" geldt alleen voor catalogusapps.** Daar blijft `starten` een actie `app.start` (R1), zoals
   ADR-0022 besluit 6 zegt. Van de manifesten in deze repo heeft nu geen enkele catalogusapp `stopped_snapshot`; dit
   pad is dus alleen in unit-tests met een testmanifest te toetsen.
3. **De eigenaar krijgt een duidelijke melding, in het plan en in de Hub.** Zodra `stoppen` van een Custom App is
   gemeld met een `execution_id` en een readback met `staat: STOPPED` (status `voltooid`, of `mislukt` met die
   readback), legt deze dienst vast dat de app gestopt staat en zet hij:
   - in de planweergave (`GET /lifecycle/v1/plans/{id}`) een melding voor de eigenaar, met de appnaam, wat er is
     gebeurd en wat hij moet doen. Voorbeeld: "Gotify is gestopt voor het herstelpunt van de update en blijft gestopt.
     Start hem in TrueNAS onder Apps; daarna gaat het plan verder.";
   - één event in de outbox in het eigen domein `lifecycle` (contract §7): `lifecycle.app.down`, `entity: {type:
     "app", id: <app_name>}`, met `payload.ernst: "hoog"` en in de payload de appnaam, het plan, de stap, een vaste
     redencode (`gestopt_na_update` of `gestopt_na_vervallen_plan`) en de meldtekst. Het probleemwerkwoord `down` zorgt
     dat de Hub er nu al een melding van maakt (`incidents/rules.py`, `_generic`).
   Een herstelevent (`lifecycle.app.recovered`) komt alleen als de eigenaar de eigenaarsstap `starten` meldt met een
   readback `staat: RUNNING`. Zonder zo'n readback blijft de melding open: onbekend is nooit een herstel (eis 4).
4. **Een late melding van `stoppen` geeft niets vrij, maar wordt niet meer stil weggegooid.** Is het plan vervallen
   (`klaar_voor_voorstel` en `expires_at <= nu`) en komt er een melding binnen van een stap die een herstelstap heeft
   (nu alleen `stoppen`), dan:
   - blijft het antwoord 409 `plan_vervallen`; `planner.meld` wordt niet aangeroepen; `stappen`, `status`,
     `vervallen` en `vrijgegeven_stappen` (leeg) veranderen niet. Er komt geen stap vrij, ook niet `starten` en ook
     niet bij een catalogusapp (eis 2);
   - wordt de melding, als ze een `execution_id` en een readback heeft (eis 3), wél vastgelegd: in een eigen veld
     naast de stapvoortgang (het plandocument blijft onveranderlijk), in de auditlog en, bij een readback met `staat:
     STOPPED`, met de melding en het event van punt 3 (redencode `gestopt_na_vervallen_plan`). De vastlegging rolt niet
     mee terug met de weigering;
   - geeft het antwoord in `details` de meldtekst voor de eigenaar mee. Voor een Custom App: start hem in TrueNAS onder
     Apps. Voor een catalogusapp: start hem in TrueNAS, of laat de Hub hem starten via een nieuw voorstel dat de Hub op
     een verse readback beoordeelt; nooit vanuit dit vervallen plan;
   - zonder `execution_id` of readback wordt niets vastgelegd (zoals nu); het antwoord noemt wel dat de app gestopt
     kan zijn en dat de eigenaar dat in TrueNAS controleert. Een readback zonder `STOPPED` (bijvoorbeeld `RUNNING` bij
     een mislukte stop) geeft geen melding "gestopt", wel een auditregel.
5. **Beschermde apps blijven buiten alles.** Een plan voor een beschermde app is geblokkeerd en heeft geen stap
   (ADR-0025 besluit 2); een melding op zo'n plan blijft 409 `plan_geblokkeerd` en legt niets vast. Dit besluit voegt
   voor zulke apps geen enkele stap, voorstel of melding toe.

## Gevolgen

**Voorgestelde bouwstap in deze repo** (zonder stap-ID; de regisseur plant hem). De codewijziging is een vervolgpunt
voor **S-P2-420 bouwpunt 2** (de stand-ins naast de echte Hub- en executorcode); de regisseur beslist of het daar of in
een eigen stap komt. Eerst de tests, eerst rood:

1. `planner.plan_update`: voor een Custom App `starten` als handmatige eigenaarsstap (besluit 1), uitleg zonder "nooit
   gestopt achter"; `risicoklasse_verwacht` van `stoppen` R2 voor een Custom App. Catalogusapps ongewijzigd (besluit
   2). Tests in `apps/api/tests/unit/test_planner.py`, bijvoorbeeld (namen voorgesteld, nog te schrijven):
   een update van Gotify heeft `starten` als handmatige stap zonder voorstel; een testmanifest van een catalogusapp
   met `stopped_snapshot` houdt `starten` als `app.start`; `tot_schaduw` en
   `test_mislukte_snapshot_start_de_app_weer_en_geeft_geen_update_vrij` gaan mee. De naam van
   `test_mislukt_stoppen_slaat_het_starten_over` zegt "slaat over" terwijl de test `vrijgegeven` eist; de bouwstap
   geeft hem een naam die klopt.
2. `api_plans.report_step` en de planweergave: besluit 3 en 4 (melding voor de eigenaar, eigen veld naast de
   stapvoortgang, auditregel, outbox-event, `details` in de 409). Of daarvoor een migratie nodig is, kiest de
   bouwstap; dan geldt de migratietest op een gevulde database (ADR-0025, kop "Tests"). Vast te stellen in de
   bouwstap: of `idempotency.execute` een 409 die de handler zelf teruggeeft (in plaats van een `ApiError`) samen met
   de vastlegging bewaart (ONBEKEND - eerst vaststellen met: lezen van `apps/api/src/lifecycle/idempotency.py`,
   functie `execute`, en een test).
3. **De karakteriseringstest wisselt bewust van verwachting.**
   `test_plans_api.py::test_late_melding_van_stoppen_na_het_vervallen_geeft_starten_niet_vrij` blijft eisen: 409
   `plan_vervallen`, `vrijgegeven_stappen` leeg, `stoppen.gemeld_op` leeg, `starten` op `wacht`, 0 events
   `lifecycle.plan.step_reported`. Nieuw eist hij: bij `voltooid` met `STOPPED` één auditregel, één event
   `lifecycle.app.down` met `gestopt_na_vervallen_plan` en een melding voor de eigenaar in de planweergave; bij
   `mislukt` met `RUNNING` geen event `lifecycle.app.down`; zonder `execution_id` of readback niets vastgelegd. Een
   mutatieproef (V-003) laat zien dat de test rood wordt als de vastlegging wegvalt én als er toch een stap vrijkomt.
4. Negatieve tests (verplicht, voor R-HARDE-EIS): een late melding op een vervallen plan geeft voor geen enkel soort
   plan een stap vrij en zet het plan niet op `in_uitvoering`; een plan voor een beschermde app krijgt geen stap en
   geen melding; een herstelevent komt nooit zonder readback `RUNNING`.
5. Stand-ins `e2e/simulators/hub/hub_p2.py` en `executor_sim.py`: `doel_niet_herkenbaar` voor elke actie met een app
   als doel op een gestopte Custom App, en `app.stop` op een Custom App als R2. De ketentest
   `test_keten_plan_hub_executor_nas.py::test_update_met_schaduwtest_en_daarna_terugzetten` gaat mee: de eigenaar
   start de app (in de TrueNAS-simulator) en meldt de eigenaarsstap, daarna pas `bijwerken`. Bewijs blijft
   Sim-getest; tegen de echte Hub en NAS: NIET GEVALIDEERD.
6. Daarna: de opmerking bij B08-004 in `docs/platform-v2/EVIDENCE.json` ("nog geen stap gepland") en de beperking in
   ADR-0025 bijwerken naar de nieuwe stand (deze ADR wijzigt EVIDENCE niet).

**Voorgestelde stap in ai-server-manager** (zonder stap-ID; eigenaar ai-server-manager): een regel in de
incidentenmotor van de Hub voor `lifecycle.app.down` en `lifecycle.app.recovered` van deze dienst, met de Nederlandse
titel en meldtekst uit de payload in plaats van de algemene titel van `_generic`, en een manier waarop de melding
sluit als de Hub de app zelf weer draaiend ziet. Tot die stap er is, toont de Hub een melding met een algemene titel;
de volledige tekst staat in de planweergave van deze dienst. Hoe de melding in de Hub sluit zonder herstelevent van
deze dienst, is ONBEKEND (hoort bij die stap).

**Contract.** §8 noemt voor `app.stop`/`app.start` alleen "R1 (niet-kritiek) / R2 (kritiek …)"; de regel dat stoppen
van een Custom App R2 en eenrichtingsverkeer is, staat in ADR-0030 besluit 21 en in 03_RISICO_AUTONOMIE.md, niet in de
tabel van §8. Dit ontwerp hangt daar niet van af, maar de contracteigenaar (ai-server-manager) kan de tabel gelijk
trekken.

**ADR-0022 en ADR-0025** krijgen alleen een verwijzing naar deze ADR (besluit 6 respectievelijk de alinea "Late melding
van `stoppen` na het vervallen").

## Wat dit ontwerp niet bewijst

- Er is niets gebouwd en niets getest. Het gedrag van ADR-0025 (409 zonder vastlegging; `starten` als `app.start`)
  geldt tot de bouwstap klaar is.
- Dat de echte Hub en executor `app.start` op een gestopte Custom App weigeren, is gelezen in de code en de tests van
  ai-server-manager (ADR-0030 besluit 21, `engine.py`), niet in deze stap gedraaid. Dat TrueNAS bij een gestopte Custom
  App echt geen image toont, is in de broncode van TrueNAS gelezen (ADR-0030) en NIET GEVALIDEERD op een echte NAS.
- Of de Hub het event `lifecycle.app.down` van deze dienst echt als melding toont, is alleen afgeleid uit
  `incidents/rules.py` (`DOMEINEN`, `_generic`, `PROBLEEM_WOORDEN`); niet gedraaid en niet tegen een gekoppelde Hub
  getest. De duidelijke tekst in de Hub vraagt de voorgestelde stap in ai-server-manager.
- Dat een mens de eigenaarsstap eerlijk meldt, is niet af te dwingen (ADR-0025 besluit 5). Wat ertegen beschermt: de
  Hub weigert `bijwerken` op een app die nog gestopt is (`doel_niet_herkenbaar`); dat is NIET GEVALIDEERD tegen de
  echte Hub met een plan van deze dienst.
- Het catalogusapp-pad (besluit 2) heeft nu geen manifest in deze repo; het is alleen met een testmanifest te toetsen.
- Of een gestart plan moet kunnen vervallen (ADR-0024, "Een gestart plan vervalt niet"), valt buiten dit ontwerp.
