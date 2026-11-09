# ADR-0005 — Authenticatie: Hub-JWT via JWKS en service-token, fail-closed

- Status: aanvaard · Datum: 2026-11-09 · Contract: §4

## Besluit
- `Authorization: Bearer <JWT>`: alleen `alg=EdDSA`, `iss=hub`, `aud=jelly-leon-platform`, verplicht `exp`, `iat`,
  `sub`; levensduur `exp-iat ≤ 120 s` (5 s klokmarge); rollen uit `roles` ∩ {admin, operator, viewer, kind}.
  `alg=none`/HS256/vervalste handtekening/onbekende `kid` → 401.
- JWKS van `{HUB_URL}/api/v1/auth/jwks` (of uit de enrollment): TTL 300 s, minimaal 30 s tussen ophaalpogingen
  (ook bij onbekende `kid`), maximaal 900 s veroudering; daarna — of zonder Hub — wordt **elk** JWT geweigerd.
- `Authorization: Bearer <service-token>`: het per-app token uit de enrollment (gedeeld geheim Hub ↔ app), alleen
  SHA-256 opgeslagen, vergelijking in constante tijd → principal `service:hub` met rol `service`.
- Rechten: lezen = viewer/operator/admin/service; wijzigen = operator/admin/service; ontkoppelen = admin; rol `kind`
  heeft geen toegang (geen kinderfunctie in deze dienst).
- Niet gekoppeld = geen geldige credentials = alles 401 (nooit "open access", EX16).

## Contract-issue
§4 zegt niet hoe een dienst een service-token van een **andere** partij controleert. Gekozen: alleen de Hub (met
het eigen per-app token) is service-aanroeper; andere diensten gaan via de Hub of met een Hub-JWT.
