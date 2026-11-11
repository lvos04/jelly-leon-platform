# Simulators (alleen voor tests en de integrale E2E-omgeving)

Geen enkele simulator is de echte app. Een test die alleen hiertegen slaagt is **Sim-getest** (contract §11), nooit
"geslaagd op de NAS". Elke simulator documenteert in zijn README de bronnen (repo + tag + commit + bestanden), het
protocol, de foutinjectie en wat hij **niet** nabootst.

| Simulator | Map | Pakket / start | Bootst na | Gebruikt door |
|---|---|---|---|---|
| TrueNAS | `truenas/` | `truenas_sim` · `python -m truenas_sim [--ssl-certfile …]` | JSON-RPC 2.0/WebSocket 25.04/25.10: apps (standaard Custom App + catalogus), snapshots, jobs, rollen | Hub, executor, lifecycle, controlelijsten I01/I02 |
| Hub | `hub/` | `hub_sim.py` (los bestand) | contract 1.3 §3.3–§4.6 (enrollment, dienst-JWT, sleutelrotatie, inbox) en §6.1 (heartbeat: 202, 200 bij herhaling, 429 binnen 5 s, `unknown` na 3 × het interval) | dienst-tests en conformiteit van deze repo, Node-RED-proef |
| Hub, fase P2 (stand-in) | `hub/` | `hub_p2.py`, `contract8.py` (geladen door `hub_sim.py`) | inventaris (`GET /api/v1/apps`, cursor, 409 `snapshot_changed`) en catalogusbeschikbaarheid in de vorm van de Hub; §8: voorstel → beleid (R0–R3) → goedkeuring gebonden aan de diff → ondertekend `ExecutionRequest` (Ed25519, canonieke JSON). **Voorlopige routes** (het contract noemt ze niet): `GET /api/v1/catalog/availability`, `POST /api/v1/actions/proposals` | plannen (`apps/api/tests`), ketentest, Node-RED-proef |
| Uitvoerder (stand-in) | `hub/` | `executor_sim.py` | `POST /api/v1/execute`: controleert handtekening, vervaltijd, nonce, allowlist, parameterschema, beleidshash, risicoklasse en precondities opnieuw; voert de vaste acties uit via JSON-RPC tegen de TrueNAS-simulator en leest terug | ketentest plan → Hub → uitvoerder → TrueNAS-simulator |
| Beszel (hub) | `beszel/` | `beszel_sim` · `python -m beszel_sim` | PocketBase-API van Beszel v0.21.1 (alleen lezen) | Hub (B09-002), I07 |
| Uptime Kuma | `uptime_kuma/` | `uptime_kuma_sim` · `python -m uptime_kuma_sim` | `/metrics`, statuspagina, push (2.5.9) | Hub (B09-002), I08, EX22 |
| Gotify | `gotify/` | `gotify_sim` · `python -m gotify_sim` | `/message`, `/health`, `/version`, beheer-lezen (v3.1.1) | Hub (meldingen), Uptime Kuma, I13 |
| Authentik | `authentik/` | `authentik_sim` · `python -m authentik_sim` | OIDC (code + PKCE, JWKS, userinfo, refresh) + beheer-API `/api/v3` (2026.8.5) | Hub (B01-006), home-control-hub (B13), I09 |

## Installeren en testen

```bash
pip install -e 'e2e/simulators/truenas[test]' -e 'e2e/simulators[test]'
(cd e2e/simulators/truenas && python -m pytest -q)     # TrueNAS
(cd e2e/simulators && python -m pytest -q)             # Beszel, Uptime Kuma, Gotify, Authentik
```

Andere repos mogen deze simulators **lezen en gebruiken**, niet wijzigen (bijv. de Hub in zijn E2E-tests:
`pip install -e ../jelly-leon-platform/e2e/simulators` en `python -m gotify_sim --port …`).

## Gemeenschappelijke testbediening (`simkit`)

Elke simulator van een derde app heeft dezelfde routes onder `/_sim/` (nooit in de echte app):

| Route | Doel |
|---|---|
| `GET /_sim/sleutels` | tokens/sleutels van de simulator (alleen testwaarden) |
| `GET /_sim/fouten` · `POST /_sim/fouten {"naam","aantal","pad","waarde"}` · `DELETE /_sim/fouten[?naam=]` | foutinjectie: alleen de eerstvolgende `aantal` keer, alleen paden die op de regex `pad` passen |
| `GET /_sim/verzoeken` | ontvangen verzoeken (methode, pad, querynamen, of er aanmelding bij zat — nooit de aanmeldgegevens zelf; een geheim in het pad, zoals de push-token van Uptime Kuma, wordt geredigeerd) |
| `POST /_sim/reset` | begintoestand (nieuwe tokens en sleutels) |

Algemene fouten in elke simulator: `http_500`, `http_502`, `http_503`, `traag` (`waarde` seconden), `ongeldige_json`,
`te_groot` (`waarde` MiB, zonder `Content-Length`) en `redirect` (302 naar een andere host; een client hoort die nooit
te volgen). De specifieke fouten staan in de README per simulator.

Simulatorwaarden (hostnamen onder `.invalid`, pool `simpool` in de TrueNAS-simulator) zijn nooit echte NAS-gegevens.

De stand-ins voor fase P2 zijn **geen** kopie van de echte Hub of executor: de echte Hub heeft de routes voor
voorstellen, goedkeuring en catalogusbeschikbaarheid nog niet, en de echte executor wordt in `ai-server-manager`
gebouwd. Ze volgen contract 1.3 §8 en `ai-server-manager:docs/EXECUTION_REQUEST.md`; waar het contract niets
vastlegt, staat de gekozen (veiligste) lezing in `docs/adr/ADR-0022-plannen-installatie-update-terugzetten.md`.
Zodra de echte routes bestaan, moeten dezelfde tests daartegen draaien; tot dan is alles **Sim-getest**.
