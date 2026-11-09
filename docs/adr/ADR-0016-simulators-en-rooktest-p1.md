# ADR-0016 — Simulators van derden en de installatie-rooktest zonder YAML (P1)

- Status: aanvaard · Datum: 2026-11-09 · Contract: 1.2 §1, §2, §2.2, §3.2, §3.9, §5, §11 · Excel: I07, I08, I09, I13,
  I23-23-02 · Integrale E2E: EX22 (voorbereiding)

## Besluit — simulators

1. **Vier simulators** in `e2e/simulators/{beszel,uptime_kuma,gotify,authentik}` (één Python-pakket
   `jlp-derden-sims`, alleen tests). Ze bootsen **alleen** de API-delen na die de Hub en home-control-hub gebruiken,
   met de vorm uit de gelezen bron (tag + commit per README), en documenteren wat niet is nagebootst.
2. **Gedeelde testbediening** (`simkit`): `/_sim/sleutels`, `/_sim/fouten` (met `aantal` en `pad`), `/_sim/verzoeken`
   (zonder aanmeldgegevens) en `/_sim/reset`, plus algemene fouten (5xx, traag, ongeldige JSON, te groot, redirect).
   Zo test elke client time-outs, groottegrenzen en "nooit een redirect volgen" op dezelfde manier.
3. **Bewust nagebootste valkuilen**: PocketBase geeft zonder token een *lege* lijst (geen 401); Gotify start met
   admin/admin; Authentik-tokens met verkeerde `iss`/`aud`/`exp`/`nonce`/handtekening/`kid`/`alg none` en
   sleutelrotatie; Uptime Kuma-sleutels op `/metrics` met Basic-aanmelding. Een goede client faalt hierop dicht.
4. Map `uptime_kuma` (met onderstrepingsteken) omdat een map met koppelteken geen Python-pakket kan zijn (mypy).

## Besluit — rooktest

1. **Zoals TrueNAS een standaard Custom App start**: `docker run` met precies één map → `/appdata` en één poort,
   **zonder** `--cap-drop` of `no-new-privileges` (het formulier zet die niet), zonder compose of YAML. Poorten en de
   simulator alleen op de Docker-bridge van de testmachine (niet op het LAN).
2. **Images van de Hub en executor worden lokaal gebouwd uit de gecommitte inhoud** van `../ai-server-manager`
   (`git archive`, alleen lezen; daar werkt een andere agent en wordt niets gecommit). In CI op een vaste, geteste
   commit (`ASM_COMMIT`), zodat een tussentijdse wijziging daar deze CI niet onverwacht rood maakt; bijwerken = bewust.
3. **TrueNAS-simulator over WSS** met een zelfondertekend certificaat: de Hub weigert eerst (`tls_untrusted`), een
   verkeerde vingerafdruk geeft 409, de juiste maakt de koppeling `pinned`.
4. **Uitvoering via de lifecycle bestaat pas in P2**: de rooktest legt een run vast (`discovered`) en verwacht bij
   plannen eerlijk `501 capability_unavailable` (uitkomst `verwacht_niet_beschikbaar`, geen "geslaagd").
5. **Simulatorcontrole van de preview** door de testharnas (executor-key van de simulator): `app.create` met precies
   de preview-waarden + digest-readback, plus een negatieve controle (hostpad zonder dataset → `EINVAL`). Dit is
   expliciet géén installatie via het platform; het toont dat de vertaling geldige `ix-app`-waarden oplevert.
6. **Rapport zonder geheimen** (`docs/platform-v2/bewijs/rooktest-p1.json`): alleen statussen, redencodes, versies,
   publieke vingerafdrukken en digests; het script weigert op te slaan als iets op een token of key lijkt.

## Gevolgen en bevindingen

- Lokaal geslaagd (Hub/executor `efb7f3b`, lifecycle `5c8042f`, validator `59217fd`). **Sim-getest; NIET
  GEVALIDEERD (echte NAS)**: de echte TrueNAS-web-UI, GHCR-pakketten en TLS van de NAS zijn niet gebruikt.
- Bevinding voor de Hub: de D07-meting op een NAS zonder apps geeft `protocol_error` ("Geen apps gevonden") in plaats
  van een geldige lege meting.
- Bevinding voor het installatieplan (P2) en appstore-en-fork: de vertaling controleert niet of het hostpad een
  bestaande dataset is; `app.create` weigert dat pas in de job.
