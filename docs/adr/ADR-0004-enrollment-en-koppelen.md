# ADR-0004 — Enrollment-client en koppelen via de statuspagina

- Status: aanvaard (voorlopig schema) · Datum: 2026-11-09 · Contract: §3

## Context
Contract §3 beschrijft het mechanisme (`POST {HUB_URL}/api/v1/services/enroll`, eenmalig token of koppelcode) maar
**niet** het verzoek- en antwoordschema. De Hub wordt tegelijk gebouwd in `ai-server-manager`.

## Besluit (veiligste interpretatie, tolerant aan beide kanten)
- Verzoek: `{"app_id","version","kind":"token"|"koppelcode","base_url","public_key_jwk"(OKP/Ed25519),
  "capabilities","schema_version":"1.0"}` plus **`enrollment_token`** (kind token) of **`pairing_code`**
  (kind koppelcode).
- Antwoord: `service_token` (of `token`), `service_id` (of `id`), optioneel `jwks` en `policy_public_key`
  (string of JWK met `x`). Een token korter dan 16 tekens of zonder ID wordt geweigerd.
- 400/401/403/404/409/410/422 = definitief: het eenmalige token wordt **nooit** opnieuw geprobeerd; status `fout`
  met NL-uitleg "maak een koppelcode". Netwerkfout/5xx = later opnieuw met oplopende wachttijd (max. 10 min).
- Opslag: service-token AES-256-GCM-versleuteld (`vault.key`, AAD `hub_link.service_token`) + SHA-256 voor
  verificatie; alleen laatste 4 tekens zichtbaar. Het enrollment-token zelf wordt nooit opgeslagen (alleen SHA-256).
- Koppelcode via `POST /koppelen` op de statuspagina: alleen als niet gekoppeld; CSRF (dubbel ingediend token,
  cookie `SameSite=Strict`, HttpOnly); max. 5 pogingen per 15 min; Hub-URL gevalideerd (http/https, geen
  gebruikersnaam/wachtwoord). Ontkoppelen alleen met admin-JWT (`POST /lifecycle/v1/hub-link/unlink`).

## Gevolgen
- Moet worden afgestemd met de Hub-implementatie (contract-issue gemeld). De Hub-simulator
  (`e2e/simulators/hub`) implementeert dit schema; tegen de echte Hub is het **niet gevalideerd**.
