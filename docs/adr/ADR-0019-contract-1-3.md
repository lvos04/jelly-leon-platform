# ADR-0019 — Contract 1.3 en Peil 1.1.1 in jelly-leon-platform

Status: aangenomen · Datum: 2026-11-10 · Contract: `contracts 1.3` (`ai-server-manager@0a7143b`) · Peil 1.1.1 ·
Werklijst: `docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md` §4 (C1.3-JLP-01…14)

## Context

Contract 1.3 en Peil 1.1.1 zijn vastgesteld in `ai-server-manager` (ADR-0027/0028 daar). De conformiteitslijst noemt
voor deze repo dertien punten met moment **NU** (C1.3-JLP-01…13; -09 pas na de validator C1.3-ASF-08) en één voor P2
(C1.3-JLP-14). Niets hiervan raakt mediaservers: deze dienst leest of schrijft geen kijkgeschiedenis en geen
MediaGuard-gegevens (HARDE EIS §2.3 regel 7 blijft onaangeraakt).

## Besluiten per punt

| Punt | Wat er nu is | Waar |
|---|---|---|
| JLP-01 herkomst | `Origin` exact `<schema>://<Host>` (ontbreekt/`null` → 403), `Sec-Fetch-Site` alleen `same-origin`/`none`, `Host`-beperking (IP-adres in de vorm van §3.2, naam zonder punt, `.local`/`.lan`/`.internal`/`.home.arpa`; anders 403 `host_niet_toegestaan`). Een herkomstweigering telt niet als poging. Browsertest: Chromium verstuurt zelf een formulier van een andere app op hetzelfde adres (same-site), van een andere site (cross-site), van een pagina zonder oorsprong (`Origin: null`), via een niet-toegestane naam (`--host-resolver-rules`, Origin = Host zoals bij DNS-rebinding) en zonder cookie; geen enkel verzoek bereikt de Hub. | `statuspage.py`, `config.host_header_allowed`, `tests/ui/live/live.mjs` |
| JLP-02 koppelformulier | Elke POST die de herkomstcontrole doorstaat telt mee, ook `al_gekoppeld`, `koppelstatus_onleesbaar` en `koppelen_bezig` (409); invoer als geheel (vingerafdruk ≤ 64); ongeldige invoer 422; precies één logregel per poging (code + trace-ID, geen adres, geen geheim; httpx-verzoeklog uit). Melding na succes alleen via een sleutel in het geheugen (15 min, gebonden aan de CSRF-cookie). Gereed = `/readyz` + gekoppeld + `confirmed_at`. | `statuspage.py`, `cli.py` |
| JLP-03 koppelbestand | Schrijft `"1.3"`, leest elke `1.x`; onleesbaar ook bij andere major, ontbrekend verplicht veld (`schema_version`, `state`) of verkeerde eigenaar/rechten. Vervangen bij de eerste schrijfactie van een poging met een nieuw env-token (RV6), origineel als `hub-link.json.onleesbaar-<UTC-tijd>` (0600), `unreadable_replaced_at`; daarna "onleesbaar" tot de eerste geslaagde koppeling. Vaste codes van 1.3 erbij; `token_formaat_ongeldig` → `token_ongeldig` (oude code wordt nog gelezen). Extra velden `token_rotation_requested_at`, `key_rotation_blocked_sequence`. | `hublink.py`, `enrollment.py`, `keyrotation.py`, `worker.py` |
| JLP-04 JWT | `aud` als lijst → 401 `invalid_token`; reasons volgens §4.7; elke mutatie verbruikt de `jti`; authenticatie vóór de body: de handler voor `RequestValidationError`/400 authenticeert eerst (incl. `jti`) als de afhankelijkheden nog niet gedraaid hebben. | `auth.py`, `errors.py`, `replay.py` |
| JLP-05 sleutels en adres | Vingerafdruk over alle policy-sleutels (testvector `HN69-2PHK-XV2D-X85W`); `rotation_sequence` uit het enroll-antwoord (Hub 1.2: 0); canonieke IP-vormen; verboden bereiken ook na DNS-resolutie per verbinding (`nettransport.py`); ongeldige `HUB_URL`/`HUB_KEY_FINGERPRINT` → de dienst start, `hub_adres_ongeldig`/`vingerafdruk_ongeldig`, token blijft bruikbaar; ongeldige `LIFECYCLE_PUBLIC_URL` → `base_url: null`. Hub-simulator volgt 1.3. | `keys.py`, `config.py`, `nettransport.py`, `hub_sim.py` |
| JLP-06 image | Bewaker en healthcheck legen bij root eerst de bounding set (`PR_CAPBSET_DROP` tot `cap_last_cap`, mislukt = stoppen), dan 568 + no_new_privs; healthcheck `--controle`; image-E2E meet `CapBnd`/`CapAmb` per proces en de healthcheck apart. | `deploy/allinone/` |
| JLP-07 events | Vaste `service.*`-namen met `entity` = de dienst, `service.enrollment.completed` vervalt (geen overgangsrelease met beide: de Hub verwerkt geen van beide namen, gemeten in `ai-server-manager@0a7143b`); eigen domein `lifecycle`; inboxgrenzen vóór verzending (anders dead-letter met reden); `payload.ernst` met de vijf woorden. | `outbox.py`, `worker.py` |
| JLP-08 connector | Totaal = slechtste check; `worker.observed_at` = laatste hartslag; verouderd (> 3 × worker-interval, minstens 60 s) = `unknown` met `meting_verouderd`; `discover.limits.max_age_s = {health: 900, health.worker: …, read: 900}`; `/readyz` toont een deel van de witte lijst. | `health.py`, `api_connector.py`, `app.py` |
| JLP-09 manifesten | **Nog niet**: de validator van C1.3-ASF-08 staat lokaal in appstore-en-fork (`1090fc9`) maar niet op GitHub; de gepubliceerde validator weigert de nieuwe velden (`unknown_field`). Wel al gedaan, geldig met beide validators: Beszel-parameter `hub_public_key` → `beszel_hub_public_key` (`reserved_env`). Gemeten met de lokale validator wat nog moet: `healthcheck.monitor` (alle), `install.reason` (Authentik), restrisico zonder no-new-privileges in `privilege_reason` (Beszel, Gotify, Uptime Kuma), `accepted_findings` in plaats van `validatie-uitzonderingen.yaml`, `post_install_checks`, `kind: checklist`. | `install/manifests/` |
| JLP-10/11 docs | Begrippen standaard Custom App (`ix-app`, `custom_app: false`) / compose-app / catalogusapp; koppelen via de statuspagina alleen via het IP-adres of een lokale naam; env-enrollment aanbevolen. | `TRUENAS_APP_API.md`, `INSTALLATIE.md` |
| JLP-12 Idempotency-Key | Statische test over alle routes van de echte app (ook lui ingesloten routers van FastAPI 0.16x) met uitzonderingen + reden; dynamische test (zonder sleutel 400, ná de authenticatie). De P2-plaatshouders vragen nu ook een sleutel (geen extra uitzondering nodig). `Idempotent-Replay: true` (1.2-kop één release mee). | `tests/unit/test_idempotency_routes.py`, `api_lifecycle.py`, `idempotency.py` |
| JLP-13 kopie + EVIDENCE | Byte-gelijke kopie, pin in `CONTRACT_KOPIE.json`, drifttest; EVIDENCE-test volgens §11 (1.3) met negatieve gevallen; regels `C1.3-JLP-nn`. | `tools/contract_kopie.py`, `tests/repo/test_contract_kopie.py`, `tests/repo/test_repo_rules.py` |
| JLP-14 | P2 (lifecycle-planner); niet gestart. | — |

**Foutcatalogus (§5, gemeenschappelijk):** 422 `validation_error` met `details.fouten[]` (`veld`, `fout`, `type`, zoals de
Hub; vervangt `details.errors`), ook voor `limit`, `cursor` en itemsoort (waren 400 met eigen codes); 403 `forbidden`
met `details.reason`; 412 met `details.huidige_etag` (`current_etag` één release mee); 503 `replay_check_unavailable`
met `Retry-After`. De statuspagina (HTML) volgt dezelfde lijn: ongeldige invoer 422.

**Peil 1.1.1:** gepinde kopie (versie + commit), renderer in één doorgang (byte-gelijk met `bouw/sjabloon.py` op de
gepinde commit, getest), `melding_rol`, `melding_code`/`melding_pad`, de nieuwe tonen en blokken, `{{bron}}`. "In orde"
is in 1.1.1 alleen verboden als statuswoord (§11.1); de vaste uitleg van Gereed (§11.2) gebruikt het als zin, dus de
microcopy-controle kijkt nu alleen naar pillen en het totaal.

## Keuzes waar het contract ruimte laat (veiligste lezing)

1. **Driftregel van de contractkopie.** De pin is hard (kopie = pin = bron op de gepinde commit). In de bron: een andere
   tekst onder dezelfde `contracts x.y` faalt (zoals Peil E08: één versie = één tekst); een nieuwere contractversie en
   een bijgewerkte conformiteitslijst geven een waarschuwing (de lijst is een werkdocument dat de eigenaar bijwerkt).
   Dezelfde regel (pin hard, nieuwer = waarschuwing) nu ook voor de I23-manifestkopieën: een commit in
   ai-server-manager maakt de CI van deze repo niet meer rood.
2. **Herkomstfouten tellen niet als poging.** §3.7 zegt "elke POST die de herkomstcontrole doorstaat"; de CSRF-cookie
   rekenen we tot die controle (zonder cookie kwam het verzoek niet van deze pagina).
3. **`state: null` bij een onleesbaar bestand** in de witte lijst (minder tonen mag): de stabiele toestand is dan
   onbekend; `last_error.code = koppelstatus_onleesbaar` zegt waarom. `state_label_nl` en `beschadigd` vervallen (buiten
   de witte lijst).
4. **Verouderde hartslag = `unknown`, niet meer `down`.** §6.2: een meting ouder dan haar `max_age` is `unknown` met
   `meting_verouderd`; nooit groen. Een stilgevallen worker is dus "Onbekend · Verouderd", niet "Storing".
5. **`jobs` n.v.t. in P0.** De vaste naam geldt "waar van toepassing"; de worker voert in P0 geen jobs uit. Komt met de
   uitvoeringsengine (P2).
6. **Hostnaam die naar een verboden adres wijst** geeft `hub_adres_ongeldig`, niet definitief (DNS kan veranderen; het
   token blijft bruikbaar), met backoff zoals een netwerkfout.
7. **Toegangslog van uvicorn blijft** (methode, pad, status, bron-IP; geen body, geen adres van de Hub). De "ene
   logregel per poging" is de regel van de dienst zelf met code en trace-ID.

## Gevolgen

- Een Hub 1.2 blijft werken (schema major 1, `rotation_sequence` ontbreekt = 0). De Hub verwerkt de nieuwe eventnamen
  nog niet (C1.3-ASM-09); niets in de Hub leest `state_label_nl`/`beschadigd` (gemeten).
- Restrisico's ongewijzigd t.o.v. ADR-0011: verkeer op het LAN is HTTP; de koppelcode op de statuspagina blijft een
  wedloop vóór de eerste koppeling (beperkt door de `Host`-beperking; env-enrollment aanbevolen). In de praktijk stuurt
  Chromium `Sec-Fetch-Site` alleen naar vertrouwde oorsprongen (HTTPS, localhost): op het LAN via HTTP ontbreekt de
  header meestal en dragen `Origin`, de `Host`-beperking en de cookie de bescherming (gemeten in de live test).
- Niets is op een echte NAS, met echte accounts of op echte hardware gevalideerd (Sim-getest/Unit-getest).
