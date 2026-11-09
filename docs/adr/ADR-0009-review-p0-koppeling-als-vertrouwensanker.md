# ADR-0009 — Review P0: alleen een geslaagde koppeling is vertrouwensanker voor JWT's

- Status: aanvaard, **aangevuld voor 1.2 door ADR-0011** · Datum: 2026-11-09 · Contract: §3, §4 · Vervangt deels:
  ADR-0005 (JWKS-bron)

> **Stand contract 1.2 (ADR-0011):** het principe blijft (alleen een geslaagde koppeling is vertrouwensanker), nu
> strenger: sleutels worden bij de koppeling gepind en met de sleutelset-vingerafdruk gecontroleerd; een via HTTP
> opgehaalde JWKS voegt nooit vertrouwen toe. Punt 3 (leeftijd van een opgeslagen JWKS) vervalt. Het voorstel onder
> "Restrisico" (vingerafdruk tonen en vergelijken) is in contract 1.2 §4.5 overgenomen.

## Aanleiding (bevinding van de onafhankelijke review van P0)
De JWKS-cache haalde sleutels op bij `hub_url` uit de tabel `hub_link` **ongeacht de koppelstatus**, met als
terugval `HUB_URL` uit de omgeving. Een mislukte koppelpoging op de statuspagina sloeg het ingevulde Hub-adres wél
op. Gevolg (aangetoond met een test vóór de reparatie): iedereen die de statuspagina bereikt kon een eigen server
als "Hub" invullen met een willekeurige koppelcode; na de (mislukte) poging vertrouwde de dienst de JWKS van die server
en accepteerde een zelf ondertekend JWT met rol `admin` (HTTP 200). Ook bleef een Hub-JWT na **ontkoppelen** geldig
(sleutels in het geheugen) en werd een JWT al vóór de enrollment geaccepteerd als `HUB_URL` gezet was. Dat is in
strijd met ADR-0005 ("niet gekoppeld = alles 401") en contract §4 (nooit "open access").

## Besluit
1. JWT's worden alleen geverifieerd met sleutels van de Hub waarmee de koppeling **geslaagd** is
   (`hub_link.state = gekoppeld`): JWKS-adres = `<gekoppelde hub_url>/api/v1/auth/jwks`. `HUB_URL` uit de omgeving
   dient alleen de enrollment. Niet gekoppeld → `401 unauthorized` met `reason: not_linked`.
2. De JWKS-cache hoort bij precies één adres. Wisselt het adres of verdwijnt de koppeling, dan worden de sleutels
   direct weggegooid; ontkoppelen wist de cache ook expliciet.
3. Een uit de database geladen JWKS (enrollment-respons) telt bij een herstart met zijn **werkelijke leeftijd**
   (`jwks_fetched_at`); ouder dan `LIFECYCLE_JWKS_MAX_STALE_SECONDS` → niet gebruiken, opnieuw ophalen (fail-closed).
4. Een Hub-adres uit een **mislukte koppelcode-poging** wordt niet opgeslagen (ook niet als standaard in het formulier).
5. Evidence en redenen bij overgangen worden geredigeerd opgeslagen (contract §3) en evidence is begrensd op 16 KiB.

## Gevolgen
- Kort na een enrollment door de worker kan de API enkele seconden (cache van de koppelstatus, 2 s) nog `not_linked`
  geven; dat is fail-closed en gedocumenteerd in de image-E2E.
- Tests draaien standaard in de gekoppelde toestand (`tests/conftest.py::link_with_hub`); de niet-gekoppelde toestand
  wordt expliciet getest (`test_not_linked_refuses_every_credential`,
  `test_failed_pairing_with_foreign_hub_grants_no_access`, `test_unlink_requires_admin_and_clears_token`,
  `test_keys_belong_to_one_hub_url`, `test_seed_counts_age_so_stored_jwks_is_not_fresh_forever`).

## Restrisico (contract-issue)
Contract §3 laat handmatig koppelen toe via een koppelcode op de statuspagina van de app. De app kan de identiteit
van de Hub daarbij niet controleren: wie als eerste een koppeling **laat slagen** (met een eigen server die elke
code accepteert), koppelt de app aan zichzelf. Beperkingen nu: alleen zolang niet gekoppeld, CSRF, 5 pogingen per
15 minuten, opnieuw koppelen alleen na ontkoppelen door een admin. In P0 voert de dienst niets uit op de NAS; vanaf P2
blijft de executor bovendien alleen door de Hub-policy-sleutel ondertekende `ExecutionRequest`s uitvoeren.
Voorstel aan de contracteigenaar: na het koppelen een vingerafdruk van de Hub-sleutel op de statuspagina tonen en die
in de Hub-UI laten vergelijken (of de Hub-sleutel vooraf in de koppelcode opnemen).
