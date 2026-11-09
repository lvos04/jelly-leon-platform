# ADR-0005 — Authenticatie: Hub-JWT via JWKS en service-token, fail-closed

- Status: aanvaard, **vervangen voor 1.2 door ADR-0011** · Datum: 2026-11-09 · Contract: §4

> **Stand contract 1.2 (ADR-0011):** JWT's alleen met de bij de koppeling **gepinde** sleutels (geen JWKS-cache via
> HTTP meer), `token_use` `user`/`hub_service`, `nbf` + `jti` verplicht, klokspeling 30 s, persistente
> replaycache, vaste `reason`-tabel. Een inkomend `Bearer <service-token>` wordt **niet** meer geaccepteerd
> (`service_token_not_accepted`); de Hub meldt zich met een dienst-JWT. Rol `kind` wint. De tekst hieronder beschrijft
> de stand van P0.

## Besluit
- `Authorization: Bearer <JWT>`: alleen `alg=EdDSA`, `iss=hub`, `aud=jelly-leon-platform`, verplicht `exp`, `iat`,
  `sub`; levensduur `exp-iat ≤ 120 s` (5 s klokmarge); rollen uit `roles` ∩ {admin, operator, viewer, kind}.
  `alg=none`/HS256/vervalste handtekening/onbekende `kid` → 401.
- JWKS uitsluitend van de Hub waarmee de koppeling **geslaagd** is (`<gekoppelde hub_url>/api/v1/auth/jwks`, of uit
  de enrollment-respons met zijn werkelijke leeftijd) — nooit van `HUB_URL` uit de omgeving of een mislukte
  koppelpoging (ADR-0009): TTL 300 s, minimaal 30 s tussen ophaalpogingen (ook bij onbekende `kid`), maximaal 900 s
  veroudering; daarna — of zonder geslaagde koppeling — wordt **elk** JWT geweigerd.
- `Authorization: Bearer <service-token>`: het per-app token uit de enrollment (gedeeld geheim Hub ↔ app), alleen
  SHA-256 opgeslagen, vergelijking in constante tijd → principal `service:hub` met rol `service`.
- Rechten: lezen = viewer/operator/admin/service; wijzigen = operator/admin/service; ontkoppelen = admin; rol `kind`
  heeft geen toegang (geen kinderfunctie in deze dienst).
- Niet gekoppeld = geen geldige credentials = alles 401 (nooit "open access", EX16).

## Contract-issue
§4 zegt niet hoe een dienst een service-token van een **andere** partij controleert. Gekozen: alleen de Hub (met
het eigen per-app token) is service-aanroeper; andere diensten gaan via de Hub of met een Hub-JWT.
