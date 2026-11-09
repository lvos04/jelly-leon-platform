# Authentik-simulator (alleen voor tests)

> **Dit is geen Authentik.** Hij bootst (1) de OIDC-provider na zoals de Hub die gebruikt (aanmelden met
> Authorization Code + PKCE, groep → rol) en (2) de beheer-API `/api/v3` zoals `home-control-hub` die leest en in
> zijn voorstellen gebruikt (B13). Een test die alleen hiertegen slaagt is **Sim-getest**.

Bron (alleen gelezen): `goauthentik/authentik` tag **version/2026.8.5** (`4ec0d19`):
`authentik/providers/oauth2/urls.py` en `views/provider.py` (discovery), `authentik/root/monitoring.py`
(`/-/health/live/` 200, `/-/health/ready/` 200/503); de vorm van de beheer-API zoals `home-control-hub`
(`apps/api/src/integrations/domains/sso/authentik/{client,reader}.py`) hem gebruikt. Catalogusapp in het manifest:
`community/authentik` 1.3.64 (`ghcr.io/goauthentik/server:2026.8.5@sha256:602227a8…bb53979a5`).

## Starten

```bash
python -m authentik_sim --host 127.0.0.1 --port 30140 [--versie 2026.8.5]
```

`GET /_sim/sleutels` → `{"api_lees_token", "api_schrijf_token", "hub_client": {"slug":"hub","client_id",
"client_secret","redirect_uri"}}`. Wie er in de gesimuleerde browser bij Authentik is aangemeld:
`POST /_sim/oidc/ingelogd {"gebruiker": "anna"|null}` (begintoestand `anna`, lid van `hub-beheerders`).

## Protocol — OIDC

| Route | Gedrag |
|---|---|
| `GET /application/o/<slug>/.well-known/openid-configuration` | `issuer` = `<basis>/application/o/<slug>/`; endpoints zoals hieronder; `id_token_signing_alg_values_supported ["RS256"]`; `code_challenge_methods_supported ["plain","S256"]`; `token_endpoint_auth_methods_supported` post + basic |
| `GET /application/o/<slug>/jwks/` | `{"keys":[{kty:RSA, alg:RS256, use:sig, kid, n, e}]}` |
| `GET /application/o/authorize/` | `response_type=code`, `client_id`, `redirect_uri` (**exact** geregistreerd, anders `400` en **nooit** doorsturen), `scope` (met `openid`), `state`, `nonce`, `code_challenge` + `_method` (S256/plain). Aangemeld → `302 <redirect_uri>?code=…&state=…`; niet aangemeld → `302 /if/flow/default-authentication-flow/?next=…`; fout in een geldige aanvraag → `302 <redirect_uri>?error=…&state=…` |
| `POST /application/o/token/` | form; client via Basic of `client_id`+`client_secret`. `authorization_code` (eenmalig, 60 s, zelfde `redirect_uri`, PKCE-controle) en `refresh_token` (rotatie: oud token vervalt) → `{access_token, token_type:"Bearer", expires_in:300, scope, id_token, refresh_token}` met `Cache-Control: no-store`. Fouten: `400 invalid_grant`, `401 invalid_client`, `400 unsupported_grant_type` |
| `GET|POST /application/o/userinfo/` | Bearer access-token → `sub` (+ `email`, `email_verified`; `name`, `given_name`, `preferred_username`, `nickname`, `groups` bij `profile`); anders `401` |
| `POST /application/o/introspect/` · `/revoke/` | client-aanmelding; `{"active":…}` · `200` |
| `GET /application/o/<slug>/end-session/` | meldt de browser af; `post_logout_redirect_uri` alleen als geregistreerd |

ID-token (RS256, header `kid`): `iss, aud (= client_id), exp, iat, auth_time, acr, amr, sid, nonce, at_hash, sub`
(gehashte gebruikers-ID) + de claims van de scopes.

## Protocol — beheer-API `/api/v3`

Aanmelding `Authorization: Bearer <token>`; zonder → `403 {"detail":"Authentication credentials were not provided."}`,
ongeldig → `403 {"detail":"Token invalid/expired"}`, geen recht → `403 {"detail":"You do not have permission to perform
this action."}`. Lijsten: `{"pagination":{"next":n|0,"previous","count","current","total_pages","start_index",
"end_index"},"results":[…],"autocomplete":{}}` met `page` en `page_size` (max 100); pagina buiten bereik → `404`.

Lezen (lees- of schrijftoken): `admin/version/`, `core/users/me/`, `providers/oauth2/`, `core/applications/`,
`core/groups/?include_users=true`, `core/users/?include_groups=true`, `flows/instances/`,
`propertymappings/provider/scope/`, `crypto/certificatekeypairs/`, `core/brands/`, `managed/blueprints/`,
`outposts/instances/`, `policies/bindings/?target=`, `events/events/?actions=&action=&ordering=`,
`core/authenticated_sessions/?user__username=`, `authenticators/admin/all/?user=` (lijst, niet gepagineerd) en
`flows/instances/<slug>/export/` (YAML-tekst). Versieafhankelijk (zoals de fake van home-control-hub): `grant_types`
op providers pas vanaf 2026.5; de filter `actions` pas vanaf 2025.8 — oudere versies **negeren** hem en geven alles.

Schrijven (alleen schrijftoken; voor de toekomstige uitvoering van de voorstellen van home-control-hub):
`POST providers/oauth2/`, `PATCH providers/oauth2/<pk>/`, `POST core/applications/`,
`PATCH core/applications/<slug>/`, `POST policies/bindings/`, `DELETE core/authenticated_sessions/<uuid>/`.
Elk verzoek staat (zonder token) in `GET /_sim/verzoeken`.

## Foutinjectie

Algemeen: `http_500/502/503`, `traag`, `ongeldige_json`, `te_groot`, `redirect` (met `aantal`/`pad`). Specifiek:

| Fout | Effect |
|---|---|
| `niet_gereed` | `/-/health/ready/` → 503 |
| `geen_rechten` | beheer-API 403 (met `pad` voor één onderdeel) |
| `discovery_down` · `jwks_down` · `token_500` · `userinfo_401` | dat endpoint faalt |
| `issuer_fout` · `aud_fout` · `verlopen` · `nonce_fout` | ID-token met verkeerde `iss`/`aud`/`exp`/`nonce` |
| `handtekening_fout` · `kid_onbekend` · `alg_none` | ongeldige handtekening, onbekende `kid`, of `alg: none` |
| `groepen_ontbreken` | geen `groups`-claim (rolmapping hoort dan naar de laagste rol of weigeren) |
| `sleutelrotatie` | ondertekenen met een nieuwe sleutel; de JWKS toont alleen de nieuwe (cache verversen) |

Extra testbediening: `POST /_sim/oidc/client {slug, naam, client_id, redirect_uri}`, `POST /_sim/groep {naam, leden}`.

## Niet nagebootst (afwijkingen)

Echte inlogflows (wachtwoord, MFA, herstel), consent-schermen, implicit/hybrid/device/client_credentials/password-grants,
versleutelde ID-tokens, dynamic client registration, outposts, back-channel-logout, en de rest van `/api/v3`.
Gedrag van een echte Authentik op de NAS, domeinnaam en TLS (D17): **NIET GEVALIDEERD**.
