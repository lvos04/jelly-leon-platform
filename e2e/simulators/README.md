# Simulators (alleen voor tests en de integrale E2E-omgeving)

Geen enkele simulator is de echte app. Een test die alleen hiertegen slaagt is **Sim-getest** (contract §11), nooit
"geslaagd op de NAS". Elke simulator documenteert in zijn README de bronnen (repo + tag + commit + bestanden), het
protocol, de foutinjectie en wat hij **niet** nabootst.

| Simulator | Map | Pakket / start | Bootst na | Gebruikt door |
|---|---|---|---|---|
| TrueNAS | `truenas/` | `truenas_sim` · `python -m truenas_sim [--ssl-certfile …]` | JSON-RPC 2.0/WebSocket 25.04/25.10: apps (standaard Custom App + catalogus), snapshots, jobs, rollen | Hub, executor, lifecycle, controlelijsten I01/I02 |
| Hub | `hub/` | `hub_sim.py` (los bestand) | contract 1.2 §3.3–§4.6 (enrollment, dienst-JWT, sleutelrotatie, inbox) | dienst-tests en conformiteit van deze repo |
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
