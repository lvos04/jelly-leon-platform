# ADR-0025 — Herstel na de review van P2: nooit alleen de image terug, beschermde apps, volledig herstelpunt en een vaste validator

- Status: aangenomen · Datum: 2026-11-11 · Contract: `contracts 1.3` §2.3 (regels 3 en 7), §6.2, §8, §9
- Bouwplan: B08-003, B08-004, C1.3-JLP-14, I10-10-02…05
- Wijzigt: ADR-0021 (validator in CI), ADR-0022 besluiten 7, 8 en 11, ADR-0023 besluiten 2, 5 en 9, ADR-0024 besluit 3
  en "Bekende beperkingen"
- Bron: de vier reviewrapporten van stap S-P2-395 (R-TECH, R-HARDE-EIS, R-MANIFEST, R-VEILIG) over `2f5fa76..bef3e03`.
  Deze ADR beschrijft alleen wat in de herstelcommits van die stap staat.

## Aanleiding (bevindingen van de review)

1. **Terugzetten zette alleen de image terug** (R-HARDE-EIS-1, blokkerend). Het terugzetplan gaf `app.rollback` vrij
   zonder bekend herstelpunt (alleen een waarschuwing) en ook voor apps met databasemigraties. Na zo'n terugzetting
   meldde het plan `voltooid`, terwijl de oude versie op de gemigreerde database draaide. Contract §2.3 regel 7(a):
   "terugzetten = vorige image én snapshot samen, nooit alleen de image"; manifestregel EX15 zegt hetzelfde.
2. **De planner kende de beschermde apps niet** (R-HARDE-EIS-2). Een updateplan voor `emby` gaf `app.stop` vrij. Alleen
   het beleid van de Hub hield het tegen; de Hub-stand-in van deze repo niet (V-001).
3. **Een herstelpunt zonder de database** (R-HARDE-EIS-3). Opslag van soort `config` of `data` buiten
   `backup.snapshot_datasets` gaf alleen een waarschuwing; `bijwerken` kwam toch vrij.
4. **Een plan bleef een uur geldig op een meting van bijna 900 s** (R-TECH-2). De versheid werd alleen bij het maken
   van het plan getoetst.
5. **Het Node-RED-hulpmiddel** (R-VEILIG-1 en R-VEILIG-2, blokkerend). Een bundel met `HUB_URL` op het tabblad kwam
   door `lint` en `importeer`; de echte image stuurde het service-token daarna naar een ander adres. `exporteer`
   gebruikte een tabblad-id uit Node-RED als bestandsnaam en kon buiten de exportmap schrijven.
6. **De validator in CI was niet vastgezet** (R-TECH-1, R-MANIFEST-1, blokkerend). CI en `make validator-venv` namen de
   branch-HEAD van appstore-en-fork; na de vertaling met vault-verwijzingen (C1.3-ASF-14) werd `tests/repo` rood
   zonder eigen wijziging.
7. Kleinere punten: geen test voor `herstelpunt_ontbreekt` (R-TECH-3), eigenaars ontbraken bij de bekende beperkingen
   van ADR-0024 (R-TECH-4), `gemeten_op` ontbrak in één blokkade (R-TECH-5), geen migratietest op een gevulde
   database van de vorige revisie (R-HARDE-EIS-4), `[` en `]` in `FLOWS` (R-VEILIG-3), wie een stapresultaat meldt
   (R-VEILIG-4).

## Besluiten

1. **Terugzetten geeft nooit alleen de image terug als dat niet veilig is.** Het terugzetplan zet alleen de image
   terug (`app.rollback` = `app.update` met de vorige waarden). Het plan is daarom geblokkeerd:
   - zonder bekend herstelpunt van vóór de update: `herstelpunt_onbekend`. Dat geldt voor de route `herstel_van`, voor
     terugzetten naar een catalogusversie en voor een updateplan zonder herstelpunten. Alleen het updateplan kent de
     snapshots van vóór de update; de Hub eist voor `app.rollback` ook een `backup_checkpoint_id`;
   - als het manifest van de update niet zegt dat alleen de image terugzetten veilig is: `terugzetten_vraagt_data`.
     Veilig betekent hier `upgrade.migrations: false` **en** `upgrade.tag_only_rollback_safe: true`. Het updateplan
     legt daarom nu ook `migrations` vast in `eigenaar_herstel`. Een ouder updateplan zonder dat veld telt als
     onbekend, dus niet veilig. De blokkade noemt de herstelpunten en de notities uit het manifest. Het voorstel aan
     de eigenaar (R3): stop de app in TrueNAS, zet de datasets terug naar de herstelpunten en zet daarna de vorige
     image terug. Het platform zet nooit zelf data terug.

   Gevolg: van de manifesten in deze repo kan alleen `beszel-agent` via een plan worden teruggezet (het enige manifest
   met `migrations: false` en `tag_only_rollback_safe: true` dat niet beschermd is). Het manifest van
   `ai-server-manager-executor` noemt alleen de image terugzetten ook veilig, maar de Hub-regel `APP-EXECUTOR`
   (besluit 2; code `executor_niet_zelf`) weigert `app.update` en `app.rollback` voor de executor, dus ook dat plan
   is geblokkeerd. Voor alle andere apps is terugzetten een handeling van de eigenaar. De regels voor de digest
   (ADR-0024 besluit 3) gelden voor de plannen die wel vrijkomen. (Herstelpoging 2, R-HARDE-EIS-6: de eerdere tekst
   noemde de executor ten onrechte als terug te zetten; de code was al strenger dan de tekst.)
   *Niet gekozen:* een eigenaarsstap "data terugzetten" in het plan, met daarna `app.rollback` (het voorstel van de
   review). De volgorde en de foutgevallen zijn niet vast te stellen zonder echte NAS. Wanneer staat de app stil? Start
   `app.update` een gestopte app? (ONBEKEND: de TrueNAS-simulator laat haar gestopt; de echte NAS is niet gemeten.) Wat
   als de eigenaarsstap mislukt? Dat is een ontwerpvraag en een contractvraag (§2.3 regel 7a zegt niet in welke vorm
   het platform "image én snapshot samen" uitvoert). Tot die vragen beantwoord zijn, geldt de veiligste stand: de
   blokkade.
2. **Beschermde apps volgens het beleid van de Hub.** `apps/api/src/lifecycle/beleid/apps.yaml` is een byte-gelijke,
   gepinde kopie van ai-server-manager `policy/apps.yaml` (`7d3a8ce`; `beleid/KOPIE.json`, `tools/beleid_kopie.py`,
   drifttest `tests/repo/test_beleid_kopie.py`). `lifecycle.beschermd` gebruikt alleen `herkenning` en `beschermd`, en
   herkent een app zoals de Hub: op de appnaam en op de basisnaam van de image, exact of met het langste voorvoegsel
   plus `-` of `_`. Elke app-actie in een plan gaat langs die regels. Een treffer blokkeert het plan met de code van
   de Hub: `emby_nooit_door_platform` (alle acties), `mediaserver_migratie_vraagt_rapport` (Jelly Leon en Jellyfin:
   update, terugzetten, verwijderen) en `mediaguard_upgradebewijs_ontbreekt`. Is het beleid niet leesbaar, dan
   blokkeert `beleid_onbekend` (nooit open bij een fout). De regels maken alleen strenger: de mediaserver installeren
   en de dienst `jelly-leon-platform` zelf bijwerken blijven mogelijk. De Hub-stand-in (`hub_p2.propose`) weigert zo'n
   voorstel met 403 en dezelfde code. De Hub blijft de eigenaar van deze regels.
3. **Een herstelpunt is volledig of er komt geen update.** Elke opslag van soort `config` of `data` (geen tmpfs) die
   niet in `backup.snapshot_datasets` staat, geeft `herstelpunt_onvolledig`. Dat is strenger dan de standaard; ADR-0022
   besluit 2 staat dat toe. `herstelpunt_ontbreekt` (geen enkele snapshot-dataset) heeft nu een eigen test.
4. **Een plan vervalt zodra zijn meting te oud is.** `expires_at` is het vroegste van (aanmaken + 1 uur) en (de oudste
   gebruikte meting + 900 s), via `api_plans.vervaltijd` en `hubread.geldig_tot`. Daarna biedt het plan geen stap meer
   aan en weigert het een melding (409 `plan_vervallen`). Een gestart plan vervalt nog steeds niet (zie "Bekende
   beperkingen" in ADR-0024).
5. **Wie een stapresultaat meldt.** `…/steps/{id}/result` blijft open voor de rollen `admin` en `operator` en voor de
   Hub (`service`). Reden: handmatige stappen (`export`, `kloon-opruimen`) meldt een mens, de eigenaar. De readback is
   wat de melder doorgeeft; deze dienst kan haar niet zelf nagaan (ADR-0022 besluit 7 is daarop aangepast). Wat
   ertegen beschermt: het plan voert niets uit. Elke volgende stap is weer een `ActionProposal` dat de Hub met een
   eigen verse readback en ETag beoordeelt; de goedkeuring hangt aan de diff; de uitvoerder controleert de
   precondities opnieuw. Zo weigert de uitvoerder-stand-in een `app.update` waarvan het herstelpunt niet als snapshot
   bestaat (`backup_checkpoint_ontbreekt`). Tegen de echte executor is dat NIET GEVALIDEERD.
6. **Node-RED-hulpmiddel.** `lint` laat op een tabblad alleen `id`, `type`, `label`, `disabled` en `info` toe en
   weigert `env` en `g` (groep) op elke node, met de code `veld_niet_toegestaan`. `importeer` leest na de import terug
   dat de eigen tabbladen geen env of groep hebben en dat de globale `HUB_URL` precies de opgegeven Hub-URL is.
   *Herstelpoging 2 (herreview R-VEILIG-1):* een eigenschap in een `change`-regel is alleen een eenvoudige naam
   (`EIGENSCHAP_RE`: ASCII-letters, cijfers, `_` en punten) en nooit met de wortel `msg`. Node-RED
   (`RED.util.setMessageProperty`) haalt `msg.` er zelf af en verstaat aanhalingstekens en haken, zodat een tweede
   regel met `msg.url`, `'url'`, `"url"` of `msg['url']` na de vaste route `msg.url` op een adres zonder schema zette
   en door de letterlijke vergelijking van de wortel kwam; op de echte image ging het service-token zo naar een
   luisteraar. Zulke regels geven nu `change_regel`, vóór de url- en VERBODEN-controles. Daarnaast laat `readback`
   de teruggelezen nodes van elk eigen tabblad opnieuw door `lint` gaan (aan of uit telt niet mee) en is één
   bevinding een Fout; `importeer` zet dan de flows van vóór de import terug (lukt dat niet, dan zegt de melding
   dat) in plaats van de flows met het token te laten draaien. De adrescontrole (`ADRES_RE`) is bewust niet
   verbreed naar hostnamen zonder schema: JSONata-teksten en labels zouden dan vals rood worden, en een adres kan
   alleen nog in `msg.url` komen via de ene toegestane regel met een vaste Hub-route.
   `exporteer` gebruikt alleen een lock-naam of een eigen id (`jlp-…`) als bestandsnaam. Elk ander id krijgt
   `tab-<sha256[:16]>`, en het bestand moet direct in de exportmap staan. `[` en `]` in `FLOWS` blijven toegestaan,
   omdat de JSON-lijsten ze nodig hebben. Veilig is dat alleen zolang 568 de werkmap van de entrypoint niet kan
   beschrijven; dat is gemeten bij de gepinde digest (ADR-0023 besluit 2).
7. **Validator en kopie op één commit.** `ASF_COMMIT` = `1967069fad62beadf5da9f31160ff4c025a5a7bf`
   (appstore-en-fork: de vertaling met vault-verwijzingen, C1.3-ASF-14, plus het herstel `1fbe145` na de review
   daarvan). Die commit geldt voor beide CI-jobs, voor `make validator-venv` en voor de rooktest (keuze van de
   regisseur; een eigen, gemelde wijziging volgens P-INSTALLATIE punt 1). Daarvoor was de validator niet vastgezet; de
   laatste groene run gebruikte `7b3159d`. Een eerdere keuze (`cbf4fd2`) is vervangen omdat `1fbe145` daarin een gat
   dicht. De vertaaltest van Authentik toetst nu codes en paden, geen meldtekst: een opgegeven geheim geeft
   `parameter_value_not_allowed`; zonder waarde zet `jl-manifest translate` (het eigen bestand, de `app_id` staat vast)
   alleen `vault://app/authentik/<parameter>`. Een geheim komt nooit in de uitvoer.
   **De kopie van de standaard in de dienst gaat mee naar dezelfde commit** (ADR-0021; `tools/validator_kopie.py
   bijwerken`, byte-gelijk, drifttest groen), zodat de dienst niet met een andere versie vertaalt dan de validator. De
   planner roept de vertaling aan zonder `trusted_app_id`: het manifest van een plan komt van de aanvrager en de
   `app_id` ervan staat niet vast. Een geheime parameter krijgt dan geen vault-verwijzing maar
   `parameter_value_not_allowed`; een installatieplan met geheimen (Authentik) blijft dus geblokkeerd, zoals met de
   vorige kopie. Of de Hub en de executor vault-verwijzingen invullen, is S-P2-070 (ai-server-manager) en in deze stap
   niet vastgesteld (ONBEKEND); een plan met verwijzingen komt pas als de `app_id` vastligt en de Hub ze invult.

## Tests

- `apps/api/tests/unit/test_planner.py`: `test_terugzetten_zonder_veilig_alleen_image_geeft_niets_vrij` (vijf
  combinaties), `test_terugzetten_na_update_met_migraties_geeft_niets_vrij`,
  `test_terugzetten_met_alleen_herstel_van_kent_geen_herstelpunt_en_geeft_niets_vrij` (vervangt
  `test_terugzetten_met_alleen_herstel_van_bewijst_minstens_dat_de_huidige_digest_weg_is` uit ADR-0024), de
  catalogustest met `herstelpunt_onbekend` en `gemeten_op`; `test_update_van_een_beschermde_app_geeft_niets_vrij`,
  `test_beschermde_app_wordt_ook_aan_de_image_herkend`, `test_beschermd_maakt_alleen_strenger_en_herkent_niet_te_ruim`,
  `test_zonder_leesbaar_beleid_geeft_geen_plan_iets_vrij`; `test_update_met_opslag_buiten_het_herstelpunt_geeft_niets_vrij`,
  `test_update_zonder_herstelpunt_geeft_niets_vrij`. De digest-regels van ADR-0024 worden getoetst met een
  testvariant van het Gotify-manifest zonder migraties (`planhulp.gotify_alleen_image_veilig`).
- `apps/api/tests/integration/test_plans_api.py`: `test_terugzetten_na_update_met_migraties_is_geblokkeerd`,
  `test_plan_vervalt_zodra_de_meting_eronder_te_oud_is`.
- `apps/api/tests/integration/test_keten_plan_hub_executor_nas.py`: in `test_update_met_schaduwtest_en_daarna_terugzetten`
  geeft het terugzetplan niets vrij en gaat er niets naar de NAS; `test_terugzetten_alleen_image_door_de_hele_keten`;
  `test_hub_stand_in_weigert_een_voorstel_voor_een_beschermde_app`.
- `apps/api/tests/integration/test_migrations.py`: `test_upgrade_naar_de_kop_houdt_de_data_van_de_vorige_revisie`
  (met een tegenproef: een revisie die één rij wist, wordt gezien). Herstelpoging 2 (herreview R-HARDE-EIS-5): `_vul`
  vult ook elke kolom die NULL mag zijn (geen enkele waarde is NULL na het vullen), en een tweede tegenproef eist dat
  een revisie die zo'n kolom op NULL zet (`lifecycle_runs.reason`, `idempotency_records.response_body`) rood wordt.
- `tests/repo/test_nodered.py`: nieuwe gevallen in `test_own_check_refuses`,
  `test_import_checks_after_reading_back_that_only_the_global_hub_url_counts`,
  `test_export_never_writes_outside_the_target_directory`, `test_export_keeps_lock_names_and_own_ids`,
  `test_render_keeps_brackets_only_because_json_lists_need_them`. Herstelpoging 2: de gevallen `change-msg-url`,
  `change-url-enkele-aanhalingstekens`, `change-url-dubbele-aanhalingstekens`, `change-msg-url-haken`,
  `change-msg-headers` (en meer) in `test_own_check_refuses`;
  `test_property_names_in_change_rules_are_plain_or_refused`; dezelfde bundels in
  `test_import_refuses_before_touching_node_red`; het terugzetten in
  `test_import_checks_after_reading_back_that_only_the_global_hub_url_counts` en
  `test_import_reports_when_restoring_the_previous_flows_fails_too`; `test_readback_runs_the_own_check_on_the_tabs_it_reads`.
  Mutatieproeven (controle op de eigenschapsnaam weg: 16 rood; eigen controle bij het teruglezen weg: 3 rood;
  terugzetten weg: 2 rood) en een proef op de echte image tegen een luisteraar op de Docker-bridge (de luisteraar
  ontvangt niets, de Hub-simulator krijgt de heartbeat wel) staan in het rapport van de herstelpoging.
- `tests/repo/test_beleid_kopie.py`; `tests/repo/test_repo_rules.py::test_validator_of_appstore_is_pinned_to_one_commit_in_ci_and_make`;
  `apps/api/tests/unit/test_planner.py::test_geheime_waarden_komen_nooit_in_een_plan` (ook zonder waarde geen
  vault-verwijzing in een plan).
- Elke test die een fout aantoont, is eerst rood gezien: op de code van vóór het herstel, of met een mutatie (een
  regel tijdelijk teruggezet, niet gecommit). `test_export_keeps_lock_names_and_own_ids` en
  `test_render_keeps_brackets_only_because_json_lists_need_them` bewaken bestaand gedrag; de migratietest heeft een
  eigen tegenproef.

## Bekende beperkingen en vervolgpunten (elk met eigenaar)

- **Image én data samen terugzetten via het platform** is niet gebouwd. Contractvraag aan de contracteigenaar
  (ai-server-manager): in welke vorm voert het platform "vorige image én snapshot samen" uit (§2.3 regel 7a, §8)?
  Daarna een stap in deze repo (regisseur; nog niet gepland).
- **Terugzetten naar een catalogusversie** is altijd geblokkeerd, omdat die route geen herstelpunt kent. Eigenaar:
  jelly-leon-platform, samen met de contractvraag hierboven.
- **Geheimen in een plan:** een installatieplan met geheime parameters blijft geblokkeerd tot de `app_id` van het
  manifest vastligt (bijvoorbeeld een geldige catalogusversie) en de Hub vault-verwijzingen invult. Eigenaar:
  S-P2-070 (ai-server-manager), daarna de regisseur voor deze repo; ADR-0022 besluit 11 verandert dan mee.
- **Node-RED** (S-P2-410): een `post_install_check` "exec en function niet geladen" (`GET /nodes`; met besluit C-11
  alleen met een beheerder); de tekst van `description_nl` in het manifest (R-MANIFEST-2: het manifest levert nog geen
  flows, beheerder of Hub-token); een rooktest-stap die op de echte image laat zien dat een bundel met env op het
  tabblad, of met een tweede regel op `msg.url` in de `change`-node (herreview R-VEILIG-1), geweigerd wordt en dat
  een luisteraar niets ontvangt. Die weigering gebeurt al vóór het hulpmiddel Node-RED aanroept (in herstelpoging 2
  met een losse proef op de echte image aangetoond), en het bewaarde rooktestrapport
  (`docs/platform-v2/bewijs/nodered-rooktest-p2.json`) zou dan opnieuw gemaakt moeten worden.
  *Stand S-P2-410:* gebouwd. De controle na installatie `exec_en_function_niet_geladen` staat in het manifest
  (handmatig: na besluit C-11 kan `GET /nodes` alleen met een beheerder), `description_nl` is bijgewerkt, rooktest N10
  toont de weigering en dat een luisteraar niets ontvangt, en het rapport is opnieuw gemaakt. Ook R-VEILIG-5
  (terugzetten bij elke fout van het teruglezen), de prototypesegmenten, de oude vorm van de `change`-node en de
  doorverwijzing (de bearer-kop ging mee; nu `msg.followRedirects = false`) zijn afgehandeld; zie ADR-0023 punt 5.
- **Melden door de Hub alleen.** Zodra de Hub de resultaten van de executor zelf doorgeeft (contractvraag over de vorm
  van dat resultaat, ADR-0024), kan `…/result` voor actiestappen alleen voor `service` open. Eigenaar: de
  contracteigenaar (ai-server-manager) voor de resultaatvorm, daarna de regisseur (nog niet gepland).
- **Late melding van `stoppen` na het vervallen** (vervolgpunt R-TECH van S-P2-395, vastgelegd in S-P2-400; eigenaar:
  de regisseur, nog geen stap gepland). Een updateplan waarvan nog geen stap is gemeld, vervalt op `expires_at`
  (besluit 4), soms al enkele seconden na het maken. Komt de melding van `stoppen` pas daarna binnen, terwijl de app
  via de Hub en de executor al gestopt kan zijn, dan weigert `report_step` haar met 409 `plan_vervallen`. Het plan
  geeft daardoor ook `starten` (de herstelstap van `stoppen`, ADR-0022 besluit 6) niet vrij en biedt het opnieuw
  starten niet aan; de app kan gestopt blijven. De eigenaar start de app dan zelf of maakt een nieuw plan op een verse
  meting. Voor beschermde apps die de planner herkent, speelt dit niet (geen enkele stap vrij). Dat geldt nu niet voor
  elke app met kijkgeschiedenis: de planner herkent een beschermde app nog niet op de catalogusapp (bijvoorbeeld een
  catalogusapp `emby` onder een andere naam); zie ADR-0026, Context "Beschermde apps" en Gevolgen, bouwpunt 1. Het gedrag is bewust niet gewijzigd: wanneer
  een melding van een al uitgevoerde stap na het vervallen nog mag, is een ontwerpvraag. Het huidige gedrag staat vast
  in `apps/api/tests/integration/test_plans_api.py::test_late_melding_van_stoppen_na_het_vervallen_geeft_starten_niet_vrij`
  (karakteriseringstest; rood als de weigering in `report_step` wegvalt).
  *Ontwerp:* ADR-0026 (S-P2-415). Het plan blijft vervallen en geeft niets vrij; een late melding met `execution_id`
  en readback wordt vastgelegd en de eigenaar krijgt een duidelijke melding. Een Custom App blijft gestopt tot de
  eigenaar hem in TrueNAS start (ai-server-manager ADR-0030 besluit 21). Eigenaar van het vervolg: de regisseur; de
  bouw is voorgesteld voor S-P2-420 bouwpunt 2 (zie ADR-0026, kop "Gevolgen"). Tot die bouw geldt het gedrag
  hierboven.
- Alles hierboven is **Sim-getest**. Tegen de echte Hub, de echte executor en een echte NAS: NIET GEVALIDEERD.
