# ADR-0010 — Review P0-afronding: enroll-schema van de Hub, verzoekgrootte, onveranderlijke releases

- Status: aanvaard · Datum: 2026-11-09 · Contract: §1, §3, §5, §10 (1.1); vooruitlopend op §3.3 en §5 van 1.2
- Herziet: ADR-0004 (verzoekschema van de enrollment)

## Aanleiding (bevindingen van de onafhankelijke review na "P0 afgemaakt")
1. **Enrollment zou tegen de echte Hub altijd mislukken.** De client stuurde `kind`, `capabilities` en
   `schema_version` mee, en `use`/`alg` in de JWK. De Hub in `ai-server-manager` (`EnrollIn`, `extra="forbid"`) en
   het exacte schema van contract 1.2 §3.3 weigeren onbekende velden met 422; deze client ziet 422 als definitief en
   markeert het eenmalige token als gebruikt. De Hub-simulator accepteerde alles, dus de tests merkten het niet.
2. **Geen grens op de verzoekgrootte.** FastAPI leest de body vóór de authenticatie volledig in het geheugen; één
   groot verzoek (ook zonder aanmelding, ook op `POST /koppelen`) kon de app met de geheugenlimiet uit het
   Custom App-formulier laten vastlopen.
3. **Release kon een bestaande versie overschrijven.** `release.yml` pushte bij `workflow_dispatch` op elke branch
   `ghcr.io/lvos04/jelly-leon-platform:<VERSION>`. Een versie-tag kreeg dan stilletjes andere inhoud; een NAS die
   die tag al draait zou bij een herstart (pull policy) andere code krijgen.
4. Kleinere punten: `target_version` weigerde alleen precies `latest` (niet `repo:latest`), versievelden namen
   willekeurige tekst aan; `/appdata/backups` was 0755 met dumps 0644 terwijl de dumps de hele database bevatten;
   de snelheidsbegrenzing van de koppelpagina was module-globaal; elke healthcheck schreef twee Alembic-regels in de log.

## Besluit
1. Enroll-verzoek bevat **alleen** `app_id`, precies één van `enrollment_token`/`pairing_code`, `version`,
   `base_url` en de publieke JWK `{kty, crv, x, kid}` (`kid` = RFC 7638-thumbprint). De Hub-simulator is even streng
   als de echte Hub (422 bij onbekende velden, één foutcode 401 `invalid_enrollment`, 200 + `Cache-Control: no-store`).
   De overige 1.2-regels (vingerafdruk pinnen, `hub-link.json`, `services/self`) volgen met de 1.2-conformiteit.
2. ASGI-laag `lifecycle.limits.BodySizeLimitMiddleware`: `Content-Length` > 1 MiB direct 413; zonder
   `Content-Length` (chunked) tellen tijdens het lezen en afbreken. Antwoord `413 payload_too_large` met
   `details.max_bytes` en trace-id (contractformaat). Dit is de grens uit contract 1.2 §5; 1.1 noemt geen grens,
   de veiligste interpretatie is er nu al een te hebben.
3. `release.yml` publiceert (inloggen, pushen, SBOM, artefact, GitHub-release) **alleen bij een tag**; handmatig
   starten is een droogloop. Vóór de push: bestaat de versie-tag al in GHCR (of is dat niet vast te stellen), dan
   stopt de run. Een repo-controle bewaakt beide.
4. Versievelden volgen `^[A-Za-z0-9][A-Za-z0-9._+:@/-]*$`; `latest` als tag in elke schrijfwijze → 422
   `latest_forbidden` (alleen `target_version`; `from_version` is een meting en mag `latest` zijn).
5. De procesbewaker zet `umask 077` voor alle processen en `/appdata/backups` op 0700 (bestaande dumps 0600).
   Koppelpogingen worden per app-instantie begrensd (5 per 15 min); de API-log toont geen Alembic-INFO meer per
   healthcheck.

## Gevolgen
- Enrollment is alleen tegen de **simulator** getest (Sim-getest); tegen een draaiende Hub NIET GEVALIDEERD.
- De release-workflow is na deze wijziging niet uitgevoerd (er wordt in deze ronde niet getagd); alleen actionlint en
  de repo-controle zijn gedraaid.
- Bodies tot 1 MiB blijven toegestaan; evidence blijft daarbinnen apart begrensd op 16 KiB (ADR-0009).
