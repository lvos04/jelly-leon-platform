# ADR-0022 — Installatie, update en terugzetten als plan (voorstel), gemeten bij de Hub

- Status: aanvaard · Datum: 2026-11-11 · Contract: 1.3 §2.2, §4.4, §8, §9 · Bouwplan: B08-003, B08-004, C1.3-JLP-14
- Code: `apps/api/src/lifecycle/planner.py`, `hubread.py`, `api_plans.py`, migratie `0003_lifecycle_plans`
- Test-stand-ins (géén productiecode): `e2e/simulators/hub/contract8.py`, `e2e/simulators/hub/hub_p2.py`,
  `e2e/simulators/hub/executor_sim.py`

## Aanleiding

B08-003 vraagt een installatiepreview met diff, dry-run en afhankelijkheden ("poortconflict/dependency ontbreken
blokkeren deployment"); B08-004 een veilige update ("DB-migratiefout laat oude app met data intact"). Contract 1.3 §8
legt de uitvoering bij de Hub (beleid, goedkeuring, ondertekening) en de executor (uitvoeren, readback). Deze dienst
heeft geen TrueNAS-sleutel en krijgt die ook niet.

## Besluit

1. **Een plan is een voorstel, geen uitvoering.** `POST /lifecycle/v1/plans/{install|update|rollback}/preview` legt een
   plan vast met: de diff (image + digest, datasets, privileges, poorten, uitgaand verkeer, geheimen,
   afhankelijkheden), de blokkades, en een **expliciete DAG** van stappen. Elke stap is een sjabloon voor één
   `ActionProposal` (§8) met een vaste `idempotency_key` (`uuid5(plan_id, stap)`), zodat een herhaling bij de Hub
   nooit een tweede actie wordt. De dienst zet zelf niets door: `uitvoering.deze_dienst_voert_uit` is altijd `false`.
2. **Blokkade = geen enkele stap vrij.** Een plan met één blokkade heeft status `geblokkeerd`; het resultaat van een
   stap melden geeft dan 409 `plan_geblokkeerd`. Blokkades komen uit de manifeststandaard (ADR-0021: `port_in_use`,
   `dependency_missing`, `app_name_taken`, `host_path_in_use`, `version_unavailable`, …) plus eigen regels die alleen
   strenger zijn (`inventaris_onbekend`, `version_availability_unknown`, `capability_unavailable`, …).
3. **Gemeten bronnen (C1.3-JLP-14).** Bij het maken van het plan leest de dienst met zijn service-token:
   - de inventaris: `GET /api/v1/apps` (cursor over één momentopname; bij 409 `snapshot_changed` opnieuw, hooguit 3×)
     en per app `GET /api/v1/apps/{id}` — scope `inventaris.lezen`;
   - de catalogusbeschikbaarheid `{train, app, version}` → `{beschikbaar: ja|nee|onbekend, gemeten_op}` — scope
     `catalogus.lezen`. **Het contract noemt geen pad en de Hub heeft de route nog niet.** Voorlopige keuze, op één
     plek (`hubread.CATALOG_AVAILABILITY_PATH`): `GET /api/v1/catalog/availability?train=&app=&version=`.
   Zonder scope of route → `capability_unavailable`; onbereikbaar, 5xx, 429/503, verouderd of onleesbaar → `unknown`.
   Beide blokkeren. `version_unavailable` komt alleen uit een **meting** `nee`; `onbekend` wordt nooit als "aanwezig"
   gelezen en er wordt nooit stil een nieuwere versie of `latest` gekozen (§2.2). Uitzondering volgens §2.2: de update
   van een al geïnstalleerde Custom App wijzigt alleen de image; daar is de catalogusmeting geen blokkade.
   De meting gebeurt pas ná de claim van de `Idempotency-Key`: een herhaling of een verzoek zonder sleutel meet niets.
4. **`doel` komt van de aanvrager.** De Hub-inventaris levert de TrueNAS-versie en de versie van `ix-app` (nog) niet.
   Zonder die twee is de vertaling niet te controleren en is het plan geblokkeerd (`truenas_version_unknown`,
   `ix_app_version_unknown`). Er worden geen voorbeeldwaarden ingevuld.
5. **`expected_version`.** Nieuwe app: `absent`. Bestaande app: de Hub bindt de ETag bij zijn eigen preview uit een
   verse readback (`expected_version_bron: hub_readback`); het veld `versie_etag` uit de inventaris is een rijversie
   en niet de resource-ETag van §8. Dataset/snapshot: de vaste vorm `dataset:<naam>` / `snapshot:<naam>`.
6. **Veilige update (B08-004).** Volgorde voor een Custom App met `upgrade.migrations: true`:
   `stoppen` → `snapshot-<dataset>` (herstelpunt, naam `jlp-<plan>-voor-<versie>`) → `starten` (ook als de snapshot
   mislukt) → `kloon-<dataset>` naar `<pool>/asm-sandbox/…` → `schaduw-installeren` (de **nieuwe** image als tweede
   app `<naam>-test` op de kloon en een eigen poort) → pas dan `bijwerken` (`app.update` met versie + digest; de
   goedkeuring draagt het herstelpunt als `backup_checkpoint_id`) → opruimen van de testkopie. Mislukt de migratie in
   de testkopie, dan komt `bijwerken` nooit vrij: de oude app draait door op onaangeroerde data. Zonder migraties
   vervalt de schaduwtest; het herstelpunt blijft. Voor een catalogusapp bestaat (nog) geen schaduwtest
   (`schaduwtest_niet_beschikbaar`, waarschuwing) en is de export een handmatige stap.
7. **Readback telt, niet de melding.** `…/steps/{id}/result` vraagt `execution_id` + readback. Bij `bijwerken` en
   `installeren` moet de teruggelezen digest gelijk zijn aan de gepinde digest; anders is de stap `mislukt`
   (`readback_afwijking`), ook als de melder "voltooid" zegt.
8. **Terugzetten.** Custom App: `app.rollback` met `herstel_van` = de `execution_id` van de update (de executor zet de
   vorige waarden terug met `app.update`). Catalogusapp: alleen naar een versie die **gemeten** aanwezig is, anders
   `version_unavailable`. Het terugzetten van **data** uit het herstelpunt is geen stap in het plan: dat is een
   eigenaarshandeling (`eigenaar_herstel`), omdat de allowlist van §8 bewust geen `zfs rollback`/`destroy` kent en
   data nooit automatisch wordt overschreven of verwijderd.
9. **Image downloaden.** "Download unavailable image only after auth set" is gelezen als: er wordt niets opgehaald
   voordat er een goedgekeurd, ondertekend verzoek is; het ophalen doet TrueNAS zelf bij `app.create`/`app.update`.
   Deze dienst haalt nooit een image op en beheert geen registry-inloggegevens (`image_download.toelichting_nl`).
10. **Uitgaand verkeer.** Het Custom App-formulier kent geen egressregels. De diff zegt dat eerlijk
    (`netwerk.uitgaand.afdwingbaar: false`); het plan belooft geen beperking die niet af te dwingen is.
11. **Geheimen.** Een plan bevat nooit geheime waarden; de standaard weigert geheime parameters in formulierwaarden
    (`parameter_value_not_allowed`). Gevolg: het installatieplan van een app met geheime parameters (bijvoorbeeld
    authentik) is nu altijd geblokkeerd tot de standaard kluisverwijzingen kent (zie "Open punten").

## Test-stand-ins

De echte Hub heeft nog geen routes voor voorstellen/goedkeuring en de echte executor weigert nog elke actie
(allowlist leeg tot P2 daar klaar is). Voor de ketentest staan er daarom drie stand-ins **alleen in de testmap**:
`contract8.py` (canonieke JSON, ETag, allowlist met risicoklassen, parametergrenzen, Ed25519), `hub_p2.py`
(inventaris en catalogus in de vorm van de echte Hub, `propose` → `approve` → ondertekend `ExecutionRequest`) en
`executor_sim.py` (de uitvoerder: controleert het ondertekende verzoek opnieuw in de volgorde en met de codes van
`docs/EXECUTION_REQUEST.md` van de Hub, voert de vaste acties uit via JSON-RPC tegen de TrueNAS-simulator, bewaart
de vorige waarden voor terugzetten en leest terug).
De voorlopige route `POST /api/v1/actions/proposals` dwingt voor een dienst-token de actor `flow` af.

Eigen keuzes in de uitvoerder-stand-in, omdat het contract ze niet vastlegt (veiligste lezing):

- het antwoord van `POST /api/v1/execute` is `{status: voltooid|mislukt, execution_id, readback{staat, versie,
  images, digests, etag}, reden}`; `voltooid` alleen als de teruggelezen toestand klopt, een NAS-fout is `mislukt`;
- een goedkeuring is **verbruikt zodra ze is gecontroleerd**, ook als daarna een preconditie niet klopt
  (`doel_gewijzigd`, `pool_niet_gezond`): er is dan toch een nieuwe preview en een nieuwe goedkeuring nodig;
- een herhaald voorstel zonder goedkeuring (R0/R1, zelfde `idempotency_key`, nieuw verzoek) wordt niet nog eens
  uitgevoerd maar krijgt het bewaarde resultaat; een andere inhoud onder dezelfde sleutel is `idempotency_conflict`;
- `app.rollback` met een onbekende `herstel_van` is `herstelbron_onbekend`; het `backup_checkpoint_id` moet als
  snapshot op de NAS bestaan, anders `backup_checkpoint_ontbreekt`.

De ketentest (`apps/api/tests/integration/test_keten_plan_hub_executor_nas.py`) laat de vier delen samen draaien:
plan bij deze dienst → Hub-stand-in → uitvoerder-stand-in → TrueNAS-simulator, en terug via de readback.
Alles wat hiermee is aangetoond heet **Sim-getest**; tegen de echte Hub/executor en een echte NAS: NIET GEVALIDEERD.

## Open punten (gemeld als contractvragen)

- Pad en vorm van de `catalogus.lezen`-route (§4.4) en de bouw ervan in de Hub.
- Route, scope en vorm waarmee een dienst (of flow) een `ActionProposal` bij de Hub indient en het resultaat
  terugkrijgt; het voorbeeld `parameters: {"grace_seconds": 30}` in het contract past niet op het lege schema van
  `app.restart` in het beleid van de Hub.
- TrueNAS-versie en `ix-app`-versie in de inventaris van de Hub (nu: `doel` van de aanvrager).
- Geheimen: het Hub-beleid voorziet `vault://app/…`-verwijzingen, de manifeststandaard v1 nog niet.
- Wat het `backup_checkpoint_id` van een `app.rollback` is (nu: de naam van het herstelpunt van vóór de update).
- De vorm van het resultaat van `POST /api/v1/execute` (status, `execution_id`, readback) en of een goedkeuring
  verbruikt is wanneer de precondities van de executor niet kloppen.

## Afgewezen

- *Zelf uitvoeren met een TrueNAS-sleutel:* in strijd met §8 (één uitvoerder, beleid en goedkeuring in de Hub).
- *Onbekende beschikbaarheid doorlaten met een waarschuwing:* dan faalt de installatie pas op de NAS, na goedkeuring.
- *De update direct op productie proberen en bij een fout de snapshot terugrollen:* dat overschrijft data automatisch
  en vraagt een actie die de allowlist niet heeft; de schaduwtest vindt de migratiefout vóór productie wordt geraakt.
