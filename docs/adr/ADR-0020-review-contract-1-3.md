# ADR-0020 — Review contract 1.3: `Retry-After` bij 429/503 en backoff van de tokenrotatie

- Status: aangenomen · Datum: 2026-11-10 · Contract: `contracts 1.3` §3.3, §3.7, §3.8, §5, §7
- Vult aan: ADR-0019 (contract 1.3 en Peil 1.1.1)

## Aanleiding (bevindingen van de onafhankelijke review van contract 1.3)

Alle CI-stappen zijn lokaal opnieuw gedraaid en waren groen; de contract- en Peil-kopie zijn byte-gelijk aan de bron
(`ai-server-manager@0a7143b`, ook gelijk aan de nieuwste stand `5e21775`), en de conformiteitstests slagen ook tegen
de echte Hub op `5e21775`. De review vond wel drie afwijkingen van §5 ("bij 429 of 503 nooit eerder dan
`Retry-After`"; "retry met exponentiële backoff + jitter"), gemeten met een proef tegen de Hub-simulator:

1. **Outbox:** na een `429` of `503` met `Retry-After: 120` van de Hub-inbox verstuurde de worker in dezelfde ronde alle
   overige events (6 aanroepen in één ronde) en plande hij het event opnieuw na de eigen backoff (2 s), niet na
   `Retry-After`.
2. **Tokenrotatie:** vroeg de Hub om rotatie (`Hub-Token-Rotation: required`) en mislukte `POST services/self/rotate`
   tijdelijk (500, 503, netwerk), dan probeerde de worker het **elke ronde** opnieuw (elke 5 s), zonder backoff en zonder
   `Retry-After` (5 aanroepen in 5 rondes bij `503 + Retry-After: 120`).
3. **Enrollment met het env-token:** een `503` met `Retry-After` gaf alleen de gewone backoff (start 5 s); een `429`
   met `Retry-After` boven 15 min werd ingekort tot 15 min.

Daarnaast gaf de statuspagina bij "te veel koppelpogingen" een 429 zonder `Retry-After` (de andere statuspagina's van
het platform sturen die wel), en noemde `deploy/truenas/CUSTOM_APP.md` nog de 1.2-code `hub_link_onleesbaar`.

## Besluiten

1. **Eén wachttijd voor de Hub.** Een `429` of `503` met `Retry-After` (getalvorm; een HTTP-datum telt als ontbrekend)
   op een aanroep van de worker (inbox, bevestigen, rotatie) zet `HubSignals.retry_after_s`. Tot die tijd spreekt de
   worker de Hub niet aan voor bevestigen, rotatie, outbox en sleutelrotaties (`Worker.hub_not_before`). De langste
   wachttijd telt. Het env-token houdt zijn eigen wachttijd (vóór de koppeling).
2. **Outbox:** een 429/503 telt als poging (zoals in zoeken-en-persoonlijke-ai; dead-letter na 10 pogingen blijft
   zichtbaar in de Hub), het event krijgt `max(backoff, Retry-After)` en de ronde stopt (de Hub zegt "wacht" voor de
   hele dienst). Een 503 zonder `Retry-After` stopt de ronde ook; het event krijgt de gewone backoff.
3. **Rotatie op verzoek:** tijdelijk mislukt (alles behalve geslaagd of ingetrokken) → opnieuw met backoff + jitter,
   start 5 s, verdubbelen, hooguit 60 s (zo lukt het binnen de 5 min van §3.8 zodra de Hub weer werkt); de aanvraag
   blijft in `token_rotation_requested_at` staan. Het oude token blijft gelden.
4. **Enrollment:** 429 → precies `Retry-After` (§3.3); 503 → `max(backoff, Retry-After)`. `Retry-After` wordt alleen
   tegen onzinwaarden begrensd (24 uur): een kleinere grens zou eerder dan `Retry-After` opnieuw proberen. De
   60-minutengrens van het token blijft gelden.
5. **Statuspagina:** 429 `te_veel_pogingen` krijgt `Retry-After` = de seconden tot deze bron weer een poging mag doen
   (het oudste verzoek in het venster van 15 min, per bron en voor de dienst; minstens 1). De code blijft
   `te_veel_pogingen` (zoals de andere statuspagina's; het is een HTML-pagina met een vaste melding, geen API-fout).

## Gevolgen

- Nieuwe foutinjectie in de Hub-simulator: `enroll_503`, `inbox_429`, `inbox_503`, `rotate_500`, `rotate_503` (met
  `Retry-After: 120`). Regressietests in `apps/api/tests/integration/test_enrollment_worker.py`,
  `test_statuspage.py` en `apps/api/tests/unit/test_enroll_contract.py`; ze falen op de code van vóór deze review.
- Geen wijziging in API, OpenAPI of statuspagina-HTML. Niets is op een echte NAS gevalideerd (Sim-getest/Unit-getest).
