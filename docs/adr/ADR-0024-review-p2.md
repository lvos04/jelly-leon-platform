# ADR-0024 — Review P2: geen plan op een oude meting, terugzetten bewezen met de digest

- Status: aangenomen · Datum: 2026-11-11 · Contract: `contracts 1.3` §2.2, §4.4, §6.2, §8
- Bouwplan: B08-003, B08-004, C1.3-JLP-14, I10-10-01…06
- Vult aan: ADR-0022 (plannen) · Nagelopen zonder wijziging: ADR-0023 (Node-RED)
- Gewijzigd door: ADR-0025 (herstel na de tweede, volledige review van P2): besluit 3 (terugzetten zonder herstelpunt
  of zonder "alleen de image is veilig" is wél geblokkeerd) en "Bekende beperkingen" (eigenaars, vervaltijd)
- Herstelcommit: `35fc44d` (code en tests); deze ADR beschrijft alleen wat in de stand van `35fc44d` of later staat

## Aanleiding (bevindingen van de onafhankelijke review van fase P2)

Proeven op de planner zelf lieten vier afwijkingen zien; ze zijn hersteld in `35fc44d`. Geen ervan voert iets uit —
een plan is een voorstel en de Hub en de executor controleren opnieuw — maar het plan beloofde meer dan gemeten was.
CI op `35fc44d`: run 42990961000 (`.github/workflows/ci.yml`) is groen in alle vier jobs: `test` (ruff, actionlint en
shellcheck, mypy, unit en integratie, simulators, conformiteit, manifesten, kopiecontroles, OpenAPI-drift,
pip-audit), `statuspagina`, `image` (image-E2E) en `rooktest` (rooktest, upstream-images, Node-RED-proef).

1. **Oude catalogusmeting telde als meting.** `catalogus.lezen` geeft `{beschikbaar, gemeten_op}`. De planner las
   `gemeten_op` wel, maar keek niet hoe oud de meting was: een `ja` van dagen geleden gaf de installatie vrij.
2. **Oude inventaris gaf stappen vrij buiten de conflictanalyse.** De leeftijdsgrens (15 minuten,
   `appstore/manifest/conflicts.py` `MAX_INVENTORY_AGE`) zat alleen in de conflictanalyse van de manifeststandaard,
   en die draait alleen bij installeren en bij de testkopie. Een update zonder testkopie (een Custom App zonder
   migraties, bijvoorbeeld Node-RED) en elk terugzetplan gaven op een twee dagen oude inventaris gewoon `stoppen`
   respectievelijk `terugzetten` vrij, zolang de Hub de meting zelf niet als verouderd markeerde.
3. **Terugzetten zonder bewijs.** Kende het plan de digest van vóór de update niet (de inventaris had hem toen niet
   gemeten, of het plan was gemaakt met alleen `herstel_van`), dan verwachtte de stap `terugzetten` alleen
   `staat: RUNNING`. Een melding `voltooid` met in de readback nog steeds de **nieuwe** digest werd aanvaard.
4. **Vervallen plan bood nog stappen aan.** `GET /lifecycle/v1/plans/{id}` gaf voor een vervallen plan
   `vervallen: true` én een gevulde lijst `vrijgegeven_stappen`. Het melden van een resultaat werd wel geweigerd
   (409 `plan_vervallen`).

## Besluiten

1. **Eén versheidsgrens voor alle plannen.** `hubread.verse_inventaris` en `hubread.verse_meting` maken van een meting
   ouder dan `MAX_METING_LEEFTIJD` (900 s) of meer dan `MAX_METING_TOEKOMST` (2 minuten) in de toekomst `unknown` met
   reden `meting_verouderd`; een catalogusmeting `ja`/`nee` zonder leesbaar tijdstip wordt `onbekend` met reden
   `meting_zonder_tijdstip`. De planner past `verse_inventaris` toe aan het begin van **elk** plan (installeren,
   update, terugzetten) en `verse_meting` op elke catalogusmeting (`planner._meet` en het terugzetten naar een
   catalogusversie in `planner.plan_rollback`). Een verouderde inventaris levert geen apps meer aan het plan. Het
   tijdstip van de laatste meting blijft zichtbaar in `bronnen.inventaris.gemeten_op` en `bronnen.catalogus.gemeten_op`;
   een blokkade op de catalogusmeting noemt het ook in haar details (`gemeten_op`). De blokkade op de inventaris
   (`inventaris_onbekend`) noemt alleen de reden (`meting_verouderd`), niet het tijdstip. Ook een oude `nee` is geen
   meting meer: `version_unavailable` komt alleen uit een verse meting (§2.2), anders `version_availability_unknown`.
2. **Waarom 900 s.** §6.2 zegt: een meting ouder dan haar `max_age` is `unknown` met `meting_verouderd`, de standaard
   is 900 s, en een afnemer mag strenger zijn dan de bron maar nooit soepeler. §4.4 noemt voor `inventaris.lezen` en
   `catalogus.lezen` geen eigen `max_age`. Veiligste lezing: de standaard, als vaste grens in de code
   (`hubread.MAX_METING_LEEFTIJD`). Een `max_age` die de Hub zelf meegeeft, leest de dienst niet; dat bij zo'n waarde
   de kleinste van de twee geldt, is **nog niet gebouwd; vervolgpunt** (zie "Bekende beperkingen"). Of
   `inventaris.lezen` en `catalogus.lezen` een eigen `max_age` krijgen en hoe de Hub die doorgeeft, is een open
   contractvraag voor de eigenaar van het contract.
3. **Terugzetten is pas bewezen met de digest.** Volgorde van streng naar minder streng (Custom App):
   - de digest van vóór de update is gemeten (`diff.image.van` van het updateplan) → de readback moet precies die
     digest tonen (`verwacht.digest`; bestond al);
   - die digest is niet gemeten, maar het plan kent de digest waar de update naartoe ging (`diff.image.naar` van het
     updateplan) of, bij alleen `herstel_van`, de digest die bij het maken van het plan draaide →
     `verwacht.digest_niet`: de readback moet een digest van deze repository tonen en dat mag **niet** die digest
     zijn; anders `readback_afwijking`;
   - er is geen enkele digest bekend → alleen `staat: RUNNING` is te controleren.
   In de laatste twee gevallen krijgt het terugzetplan de waarschuwing `vorige_digest_onbekend`. Die zegt dat niet
   bewezen is welke versie er dan draait (met `digest_niet`) of dat de vorige versie terug is (zonder digest). Een
   updateplan voor een Custom App waarvan de geïnstalleerde digest niet gemeten is, waarschuwt vooraf met
   `huidige_digest_onbekend`. Het terugzetplan wordt door deze digest-regels **niet** geblokkeerd: het plan is alleen
   eerlijk over wat het kan aantonen. Terugzetten naar een catalogusversie controleert de `versie` in de readback; daar
   gelden de digest-regels en de waarschuwing niet.
   *Gewijzigd door ADR-0025 besluit 1:* het terugzetplan zet alleen de image terug en is daarom geblokkeerd zonder
   bekend herstelpunt van vóór de update (`herstelpunt_onbekend`; ook de route met alleen `herstel_van` en het
   terugzetten naar een catalogusversie) en als het manifest van de update niet zegt dat alleen de image terugzetten
   veilig is (`terugzetten_vraagt_data`; contract §2.3 regel 7a). De digest-regels hierboven gelden voor de plannen die
   wel vrijkomen.
4. **Een vervallen plan noemt geen vrijgegeven stappen.** `vrijgegeven_stappen` is leeg zodra `vervallen` waar is
   (`api_plans.plan_view`).

## Wat de review naliep zonder wijziging

- **Geen update zonder poort en readback.** Elke blokkade maakt het plan `geblokkeerd` en geeft geen stap vrij;
  `bijwerken` komt pas vrij na het herstelpunt en (bij migraties) na een geslaagde testkopie; `voltooid` telt alleen
  met een `execution_id` en een readback met de gepinde digest. Elke planroute vraagt een rol; een plan bevat geen
  geheime waarden.
- **Node-RED-manifest.** Standaard Custom App met één image op tag + `sha256`-digest, 568:568, niet `privileged`,
  geen `cap_add`, geen apparaten, geen hostnetwerk, geen Docker-socket, geen compose en geen `latest`. De proef
  `tools/nodered_rooktest.py` (N1–N9) start de echte image tegen de Hub-simulator; op `35fc44d` is ze groen in CI-run
  42990961000 (job `rooktest`, stap "Node-RED volgens het manifest tegen de Hub-simulator"): **Sim-getest**.
- **Test-stand-ins** (`contract8.py`, `hub_p2.py`, `executor_sim.py`) staan alleen in de testmap en noemen zichzelf
  geen productiecode. Alles wat ermee is aangetoond heet Sim-getest; op een echte NAS: NIET GEVALIDEERD.

## Bekende beperkingen (niet opgelost; elk een open contractvraag of vervolgpunt)

Eigenaars aangevuld in ADR-0025 (review P2, R-TECH-4). "Contracteigenaar" is ai-server-manager (contract 1.3).

- **Vorm van het resultaat van de echte executor** (contractvraag; eigenaar: contracteigenaar ai-server-manager, en in
  deze repo S-P2-420 bouwpunt 2: de stand-ins naast de echte Hub- en executorcode). De executor in `ai-server-manager`
  (stand `0001116`) antwoordt op `POST /api/v1/execute` met `status: "uitgevoerd"`, een `request_id` en een readback per
  soort doel (app: `staat`, `versie`, `images`, `digests` en meer; snapshot en kloon: `aanwezig`). Het plan van deze
  dienst vraagt `status: voltooid|mislukt`, een `execution_id` en een readback met alleen `staat`, `versie`, `digests`,
  `images`, `etag`. `herstel_van` is bij de echte executor het `request_id` van de update. Wie het resultaat doorgeeft
  (Hub of flow) moet dit vertalen; het contract legt de vorm nog niet vast. Tegen de echte executor: NIET GEVALIDEERD.
- **Snapshot- en kloonstappen hebben geen verwachte readback** (contractvraag; eigenaar: contracteigenaar
  ai-server-manager, §8; in deze repo S-P2-420 bouwpunt 2). Ze tellen op de melding `voltooid` met
  een `execution_id`; de vorm van hun readback staat niet in het contract. In de ketentest weigert de
  uitvoerder-stand-in een `app.update` waarvan het `backup_checkpoint_id` niet als snapshot bestaat
  (`backup_checkpoint_ontbreekt`).
- **De testkopie slaagt op `RUNNING` + digest** (vervolgpunt; eigenaar: aan Hub-kant S-P2-080 bouwpunt 3
  (`post_install_checks` als poort), in deze repo de regisseur: nog geen stap gepland). De `post_install_checks` uit het
  manifest staan alleen in het installatieplan (`controles_na_installatie`) en zijn nergens een eigen stap die
  `bijwerken` tegenhoudt. Een migratiefout die de app laat crashen wordt gevonden; een fout die de app laat draaien met
  een kapotte database niet.
- **Een gestart plan vervalt niet** (vervolgpunt; eigenaar: de regisseur, nog geen stap gepland; voor P4 bij de
  mediaserver: een laatste snapshot direct vóór `bijwerken` of een toets op het RPO). De vervaltijd geldt voor plannen
  waarvan nog geen stap is gemeld: het vroegste van 1 uur (`api_plans.PLAN_TTL`) en de oudste gebruikte meting + 900 s
  (ADR-0025 besluit 4). Bij een plan in uitvoering kan `bijwerken` lang na het herstelpunt worden aangeboden; de Hub en
  de executor toetsen dan opnieuw, het plan zelf niet.
- **CI-proef tegen de Hub** (`rooktest`, vervolgpunt; eigenaar: S-P2-420 bouwpunt 6) gebruikt de vastgezette Hub-stand
  `5e21775` (`ASM_COMMIT` in `.github/workflows/ci.yml`), van vóór de executor met acties; de keten plan → Hub →
  executor is daarom alleen met de stand-ins getest.
- **`max_age` van de Hub wordt niet gelezen** (vervolgpunt, met de contractvraag uit besluit 2; eigenaar:
  contracteigenaar ai-server-manager voor §4.4/§6.2, daarna de regisseur voor deze repo). De grens is vast
  900 s; een `max_age` van de Hub voor de inventaris of de catalogusmeting telt nog niet mee.

## Tests

- `apps/api/tests/unit/test_planner.py`, kop "versheid van de metingen (contract 1.3 §6.2)":
  `test_verouderde_inventaris_geeft_voor_geen_enkel_soort_plan_een_stap_vrij` (901 s oud, 2 dagen oud, 121 s in de
  toekomst) en `test_verse_inventaris_binnen_de_grens_geeft_wel_vrij` (900 s oud, nu, 120 s in de toekomst), elk voor
  de vijf soorten plannen uit `elk_soort_plan` (installeren, update met en zonder testkopie, terugzetten met
  `herstel_van` en via het updateplan); `test_verouderde_catalogusmeting_is_onbekend_ook_als_ze_ja_zei` (901 s oud,
  2 dagen oud, 121 s in de toekomst, zonder tijdstip, onleesbaar tijdstip; installeren, update met testkopie en
  terugzetten naar een catalogusversie; ook een oude `nee`) en `test_verse_catalogusmeting_binnen_de_grens_telt`.
- Ook in `test_planner.py`: `test_terugzetten_zonder_gemeten_vorige_digest_eist_dat_de_nieuwe_digest_weg_is` (readback
  met de nieuwe digest, zonder digest, met een andere repository of gestopt: `mislukt`; met de oude digest:
  `voltooid`), `test_terugzetten_met_alleen_herstel_van_bewijst_minstens_dat_de_huidige_digest_weg_is` (ook "niets
  gemeten": alleen `RUNNING`) en `test_terugzetten_met_gemeten_vorige_digest_waarschuwt_niet`. (Sinds ADR-0025 heet de
  tweede `test_terugzetten_met_alleen_herstel_van_kent_geen_herstelpunt_en_geeft_niets_vrij`; de andere twee draaien op
  een testvariant van het Gotify-manifest zonder migraties.)
- `apps/api/tests/integration/test_plans_api.py`: `test_oude_meting_die_de_hub_niet_verouderd_noemt_geeft_niets_vrij`
  (over HTTP met de Hub-simulator: een inventaris van twintig minuten oud bij een update- en een terugzetplan, een
  catalogusmeting van twintig minuten oud bij installeren); `test_update_en_terugzetten_via_het_updateplan` (met
  gemeten vorige digest; readback met de nieuwe digest: `mislukt`, met de oude digest: `voltooid`);
  `test_niet_gestart_plan_vervalt` (`vrijgegeven_stappen` leeg bij een vervallen plan).
- Mutatieproef (2026-11-11): met `apps/api/src/lifecycle/hubread.py`, `planner.py` en `api_plans.py` van `4ae0d20`
  (vóór de review) en de tests van `35fc44d` falen 12 van de 67 tests in deze twee bestanden: tien unit-gevallen
  (drie van de verouderde inventaris, vijf van de verouderde catalogusmeting, de twee terugzettests zonder vorige
  digest) en de integratietests `test_oude_meting_die_de_hub_niet_verouderd_noemt_geeft_niets_vrij` en
  `test_niet_gestart_plan_vervalt`. Op `35fc44d` zelf slagen alle 67. Op beide standen slagen de tests binnen de
  grens (`…_verse_…`) en `test_terugzetten_met_gemeten_vorige_digest_waarschuwt_niet` (zij bewaken dat de regels niet
  te streng worden), en `test_update_en_terugzetten_via_het_updateplan` (de regel met gemeten vorige digest bestond
  al).
