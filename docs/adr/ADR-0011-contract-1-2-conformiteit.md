# ADR-0011 — Contract 1.2 toegepast: koppeling in hub-link.json, gepinde sleutels, dienst-JWT, PID 1

- Status: aanvaard · Datum: 2026-11-09 · Contract: `contracts 1.2` (incl. R1–R12) · Werklijst:
  `docs/platform-v2/CONTRACT_1.2_CONFORMITEIT.md` §4 (JP1–JP12) en §0 (A1–A7)
- Herziet: ADR-0004 (enrollment), ADR-0005 (authenticatie), ADR-0008 (container), ADR-0009 (vertrouwensanker)

## Context
Contract 1.2 legt enrollment, opslag, Hub → dienst-authenticatie, sleutelpinning en de Custom App-realiteit exact
vast. Deze repo had in P0 een eigen invulling (tabel `hub_link` met versleuteld token, JWKS via HTTP met TTL,
inkomend service-token, `dumb-init` als root-PID 1). Hieronder de keuzes bij het omzetten, vooral waar het contract
ruimte liet; de regels zelf staan in het contract en worden niet herhaald.

## Besluiten
1. **Opslag (JP2):** `lifecycle.hublink.HubLinkStore` schrijft `/appdata/secrets/hub-link.json` precies volgens §3.5
   (O_EXCL-tijdelijk bestand → fsync → rename → fsync map, onder `flock` op `hub-link.lock`; opnieuw lezen bij een
   andere inode/mtime/grootte). Elk geschreven document wordt eerst gevalideerd; een ongeldig document wordt nooit
   geschreven. **Een onleesbaar of ongeldig bestand is fail-closed:** geen Hub-JWT geldig, de statuspagina toont geen
   koppelformulier (telt als "ooit gekoppeld"), en alleen een geslaagde koppeling met een **env-token** (wie het
   TrueNAS-formulier kan wijzigen) vervangt het; het oude bestand blijft als `hub-link.json.beschadigd-<tijd>` staan.
   Een koppelcode mag een beschadigd bestand nooit vervangen.
2. **Overzetting (migratie 0002):** een P0-rij `gekoppeld` met een te ontsleutelen token, minstens één JWK en de
   policy-sleutel wordt `gekoppeld` met die sleutels gepind (`source: overgezet`, `confirmed_at` leeg, dus de worker
   bevestigt met `services/self`). P0 bewaarde van de policy-sleutel alleen `x`; de `kid` van die pin wordt de
   RFC 7638-thumbprint. Onvolledig → `ontkoppeld` met `last_error: overzetten_onvolledig` (fail-closed). Een bestaand
   `hub-link.json` wordt nooit overschreven; daarna verdwijnt de tabel. Downgrade maakt een lege tabel (de koppeling
   blijft in het bestand); terug naar 0.1.0 na deze migratie = dump/snapshot herstellen (zoals altijd na een migratie).
3. **Enrollment (JP1):** foutafbeelding volgens §3.3; daarnaast: overige 4xx (400/403/409/410) zijn **definitief**
   (`hub_weigerde`, nooit doorhameren), 3xx is een storing (nooit gevolgd, `trust_env=False`), een env-token zonder het
   formaat `enr_`+43 wordt lokaal geweigerd (`token_formaat_ongeldig`, nooit naar de Hub). De 60 minuten tellen vanaf
   `pending.started_at`. Bij een tijdelijke fout blijft `pending` staan en wordt `last_error` alvast gezet
   (`hub_onbereikbaar`/`rate_limited`); bij succes wordt hij gewist. Een koppelcode-poging (de gebruiker wacht) wordt
   bij een tijdelijke fout niet op de achtergrond herhaald: `pending` weg, adres niet bewaard.
4. **Inkomende authenticatie (JP3):** vaste `reason`-tabel van §4.7. Waar de tabel niets noemt: ontbrekende of
   niet-`Bearer`-aanmelding = `invalid_token` (geen eigen code); een `jti` die geen UUID is = `invalid_token`
   (gecontroleerd in de `jti`-stap); `typ` moet `JWT` zijn als hij er staat (een ondertekend rotatie-statement kan zo
   nooit als aanmelding dienen); `nbf` is verplicht; `aud` is de eigen `app_id` (tekst of lijst met precies die ene);
   onbekende gebruikersrollen geven geen rechten (worden weggelaten), `service` in een gebruikers-JWT is
   `token_use_invalid`, en `kind` wint. Klokspeling 30 s. Rechten van `service:hub`: lezen en runs vastleggen
   (zoals ADR-0005), ontkoppelen; nooit `admin`.
5. **Replaycache (§4.6):** tabel `jwt_replay`, alleen mutaties; een "exacte herhaling" vereist dat er een
   `Idempotency-Key` meekwam (anders kan de idempotency-laag niets terugspelen). Database weg → 503
   `replay_check_unavailable`, ook als lezen nog zou kunnen.
6. **Sleutelrotatie (§4.5):** volledig gebouwd en getest met de simulator, ook al geeft de Hub nog `{"items":[]}`:
   alleen een op dat moment gepinde sleutel van hetzelfde doel, `sequence` zonder gaten, nooit de laatste sleutel weg,
   ongeldig statement = stoppen + `last_error: sleutelrotatie_ongeldig` + event. Een Hub zonder het endpoint (404) =
   geen rotaties. Staan er na een statement tijdelijk twee policy-sleutels, dan telt de **nieuwste** voor de
   vingerafdruk (contract kent één policy-sleutel; zie contract-issues).
7. **Statuspagina (JP5):** telling 5/15 min per TCP-bronadres + 20/15 min dienstbreed in het geheugen (één
   API-proces, mag volgens §3.7); pogingen tellen vóór de invoercontrole. `Origin` is **verplicht** en moet gelijk
   zijn aan het eigen adres (`null` of ontbrekend → 403); daarom zet de pagina `Referrer-Policy: same-origin` (bij
   `no-referrer` sturen browsers `Origin: null`).
8. **Connector (JP6):** `/connector/v1/hub-link(/unlink)` vervangt de `/lifecycle/v1`-routes (die verdwijnen; er waren
   nog geen afnemers). Antwoord = witte lijst + `pinned_kids` + `rotation_sequence` + `beschadigd`.
9. **Gezondheid:** `gekoppeld` zonder `confirmed_at` is `degraded` (`hub_unconfirmed`), `ingetrokken` is `down` voor
   het onderdeel en `degraded` (`hub_revoked`) voor de dienst; nooit `healthy` zonder bevestigde koppeling.
10. **Events:** de worker meldt overgangen met `service.enrollment.completed`, `service.hub_link.revoked` en
    `service.hub_link.unlinked` (drie segmenten, §7; zonder geheimen).
11. **Extra `last_error`-codes** (§3.5 noemt "o.a."): `token_formaat_ongeldig`, `sleutelrotatie_ongeldig`,
    `overzetten_onvolledig`, elk met een vaste NL-melding.
12. **PID 1 (JP12):** geen `dumb-init`; de procesbewaker is PID 1, vangt signalen vanaf de eerste regel af, ruimt
    verweesde processen op (`waitid` met `WNOWAIT`, zoals de Hub), wisselt naar 568 en controleert daarna zelf in
    `/proc/self/status` Uid/Gid (alle vier), CapEff/CapPrm/CapAmb en NoNewPrivs — anders stopt hij (fail-closed).
    De Docker-healthcheck (draait als de image-gebruiker root) wisselt eerst naar 568 met no_new_privs.
13. **Manifest (JP9):** pas na de 1.2-validator van appstore-en-fork (M1–M6) aangepast. `HUB_*` staan niet in het
    manifest: de Hub geeft ze mee in `app.install`/`app.update` (§3.2, §8); de validator ziet een gegenereerd token als
    formulierwaarde terecht als geheim (`secret_in_form`).
14. **Lifecycle-regels (JP7):** `lifecycle.custom_app` levert alleen pure recepten en blokkades (tag met digest,
    readback, terugzetten via `app.update` met vorige waarden, `version_unavailable`); de plan-routes blijven
    `capability_unavailable` tot P2 (uitvoering alleen via de executor).
15. **JP10:** `tests/conformance` draait tegen de simulator (CI) en — met `e2e/conformance/echte_hub.py` — lokaal tegen
    de echte Hub uit de naburige checkout. ADR-0009 punt 3 (leeftijd van een opgeslagen JWKS) vervalt: gepinde
    sleutels verouderen niet; de JWKS-instellingen (`LIFECYCLE_JWKS_*`) zijn weg.

## Niet in deze ronde
- **JP11** (statuspagina-CSS uit de ontwerptaaltokens): `ai-server-manager/docs/platform-v2/ONTWERPTAAL.md` en
  `design/ontwerptaal/` bestaan nog niet (lokaal en op de remote gecontroleerd); §13: "zolang ze ontbreken, start
  geen UI-ombouw". Geblokkeerd.
- Echte NAS: niets hiervan is op TrueNAS gevalideerd (formulier, `@` in de tag, bron-IP achter Docker-NAT).

## Gevolgen
- De dienst werkt alleen nog met een Hub die 1.2 volgt; een 1.1-Hub geeft `hub_antwoord_ongeldig` (definitief voor
  dat token). Lokaal getest tegen `ai-server-manager` `e17fdef`: 16 conformiteitstests geslaagd.
- Restrisico blijft (§3.7): vóór de eerste koppeling kan iemand op het LAN de dienst via de statuspagina aan een eigen
  "Hub" koppelen als hij ook diens vingerafdruk invult; env-enrollment blijft de aanbevolen weg.
