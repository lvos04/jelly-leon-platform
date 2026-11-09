# ADR-0004 — Enrollment-client en koppelen via de statuspagina

- Status: aanvaard, herzien 2026-11-09 (review P0-afronding) en **vervangen voor 1.2 door ADR-0011** · Datum:
  2026-11-09 · Contract: §3 (1.1), §3.3 (1.2)

> **Stand contract 1.2 (ADR-0011):** het antwoord wordt volledig gecontroleerd (geen aliassen `token`/`id` meer),
> de vingerafdruk is verplicht (statuspagina) of via `HUB_KEY_FINGERPRINT`, de koppeling staat in
> `/appdata/secrets/hub-link.json` (niet meer in de database), bevestiging met `GET /api/v1/services/self`,
> koppelformulier alleen zolang nooit gekoppeld, 5/15 min per bron-IP + 20 dienstbreed, ontkoppelen via
> `POST /connector/v1/hub-link/unlink`. De tekst hieronder beschrijft de stand van P0.

## Context
Contract §3 beschrijft het mechanisme (`POST {HUB_URL}/api/v1/services/enroll`, eenmalig token of koppelcode) maar
**niet** het verzoek- en antwoordschema. De Hub wordt tegelijk gebouwd in `ai-server-manager`.

## Besluit (veiligste interpretatie)
- Verzoek (herzien in de review P0-afronding, zie hieronder): **alleen**
  `{"app_id","version","base_url","public_key_jwk":{"kty":"OKP","crv":"Ed25519","x","kid"}}` plus precies één van
  **`enrollment_token`** (token uit de omgeving) of **`pairing_code`** (koppelcode via de statuspagina, ≤ 32 tekens).
  `kid` is de RFC 7638-thumbprint. Geen andere velden.
- Antwoord: `service_token` (of `token`), `service_id` (of `id`), optioneel `jwks` en `policy_public_key`
  (string of JWK met `x`). Een token korter dan 16 tekens of zonder ID wordt geweigerd.
- 400/401/403/404/409/410/422 = definitief: het eenmalige token wordt **nooit** opnieuw geprobeerd; status `fout`
  met NL-uitleg "maak een koppelcode". Netwerkfout/5xx = later opnieuw met oplopende wachttijd (max. 10 min).
- Opslag: service-token AES-256-GCM-versleuteld (`vault.key`, AAD `hub_link.service_token`) + SHA-256 voor
  verificatie; alleen laatste 4 tekens zichtbaar. Het enrollment-token zelf wordt nooit opgeslagen (alleen SHA-256).
- Koppelcode via `POST /koppelen` op de statuspagina: alleen als niet gekoppeld; CSRF (dubbel ingediend token,
  cookie `SameSite=Strict`, HttpOnly); max. 5 pogingen per 15 min; Hub-URL gevalideerd (http/https, geen
  gebruikersnaam/wachtwoord). Ontkoppelen alleen met admin-JWT (`POST /lifecycle/v1/hub-link/unlink`); daarna
  wordt elk JWT geweigerd tot er opnieuw gekoppeld is. Het adres uit een mislukte koppelcode-poging wordt niet
  bewaard (ADR-0009).

## Herziening (review P0-afronding, 2026-11-09)
De eerste versie stuurde ook `kind`, `capabilities` en `schema_version` mee, en `use`/`alg` in de JWK. De Hub in
`ai-server-manager` (`hub/routers/services.py`, `EnrollIn` met `extra="forbid"`) en het exacte schema van contract 1.2
§3.3 weigeren onbekende velden met **422**; deze client behandelt 422 als definitief en markeert het eenmalige token
als gebruikt. Tegen de echte Hub zou elke enrollment dus zijn mislukt en het token verbranden. Gecorrigeerd: het verzoek
bevat alleen de velden hierboven (`lifecycle.enrollment.REQUEST_FIELDS`). De Hub-simulator is net zo streng gemaakt
(onbekend veld, beide/geen geheim, JWK met extra velden → 422; ongeldig/verlopen/gebruikt/andere app → één code
401 `invalid_enrollment`; succes 200 met `Cache-Control: no-store`), zodat een extra veld in de client voortaan een
test laat falen. 413 is ook definitief.

Niet in deze herziening (volgt met de 1.2-conformiteit): vingerafdruk `HUB_KEY_FINGERPRINT`/`keyset_fingerprint`
controleren en pinnen, opslag in `/appdata/secrets/hub-link.json`, `GET /api/v1/services/self`, het weglaten van de
oude aliassen `token`/`id` in het antwoord en de backoff-/stopregels van §3.3.

## Gevolgen
- De Hub-simulator (`e2e/simulators/hub`) volgt nu het schema van de echte Hub; tegen de echte Hub zelf (een draaiende
  `ai-server-manager`) is het **niet gevalideerd** (Sim-getest).
