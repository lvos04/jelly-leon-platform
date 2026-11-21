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
| Seerr en arr | `arr/` | `arr_sim` · poort **30216** | Seerr-aanvragen, normale en anime-instanties van Sonarr/Radarr, zoekopdrachten en bestandskoppelingen | MediaGuard, EX02–EX04, EX37 |
| SABnzbd | `sabnzbd/` | `sabnzbd_sim` · poort **30217** | MSO-mock met categorie-terugval en afzonderlijke providerstoringen | MediaGuard, MSO, EX02, EX39–EX40 |
| qBittorrent | `qbittorrent/` | `qbittorrent_sim` · poort **30218** | MSO-mock met private torrent, DHT/PeX-voorkeuren en seedlimieten | MSO, EX41 |
| Prowlarr | `arr/prowlarr.py` | `arr_sim --prowlarr` · poort **30219** | MSO-mock met Newznab-terugoproep en meetbare zoekdiepte | MSO, EX42 |
| Gree | `gree/` | `gree_sim` · udp **7000**, bediening **30220** | de nagebootste unit van my_house (`FakeGreeUnit`, gepinde commit) met onbereikbaar, bind mislukt, van buitenaf uit en een eigen MAC per unit | my_house, EX24–EX26, EX30 |
| HomeWizard | `homewizard/` | `homewizard_sim --apparaat p1\|socket` · poort **30221** | lokale API v1 van P1-meter en Energy Socket, alleen lezen; meterreset, zomertijdwissel, schakelverzoek vastgelegd en geweigerd | my_house, EX27, EX28 |
| Zigbee | `zigbee/` | `zigbee_sim` · bediening **30222** | Zigbee2MQTT op een MQTT-broker: brug, apparaat, meetberichten; stoppen met meetberichten | my_house, EX29 |

## Installeren en testen

```bash
pip install -e 'e2e/simulators/truenas[test]' -e 'e2e/simulators[test]'
(cd e2e/simulators/truenas && python -m pytest -q)     # TrueNAS
(cd e2e/simulators && python -m pytest -q)             # derden-simulators, downloadketen, huis en energie
```

Andere repos mogen deze simulators **lezen en gebruiken**, niet wijzigen (bijv. de Hub in zijn E2E-tests:
`pip install -e ../jelly-leon-platform/e2e/simulators` en `python -m gotify_sim --port …`).

## Downloadketen en hergebruik van de MSO-container

De integrale testomgeving gebruikt de container uit `media-source-optimizer/apps/mocks/Dockerfile`.
De bron is vastgezet op `a10b920874724dfbbb11b4ab85720d50d17649b1` in `mso-mocks.commit`.
SABnzbd, qBittorrent en Prowlarr draaien de oorspronkelijke `msomocks`-API in die image. De drie
startmodules voegen de gedeelde testbediening toe. Newznab en NNTP zijn ook als dienst in die image
beschikbaar. De Radarr-contractmock van MSO heeft een vaste film en wachtrij. De integratiefakes in
`media-guard-ultimate/tests/integration/fakes/` leveren een indexer en SAB met proefbestanden.
De Seerr-routering en de twee nummeringen van een anime-aflevering staan in de arr-simulator.

`make test-sim` leest de gepinde MSO-testbron uit de lokale Git-kopie in `../media-source-optimizer`.
Stel bij een aparte werkkopie `MSO_BRON` in op de locatie van die repository. De tests pakken alleen
`apps/mocks/msomocks` van die commit uit in hun tijdelijke map. Voor handmatig gebruik zonder container
moet die uitgepakte map `apps/mocks` op `PYTHONPATH` staan.

Bouw de MSO-image uit de vastgelegde bron, vanuit de hoofdmap van deze repo. Gebruik de bouwlock van
de gedeelde machine. Onderstaand voorbeeld gebruikt de lokale MSO-repository en een bestaand, uitsluitend
intern Docker-testnet in `TESTNET` (`docker network create --internal`). Publiceer geen hostpoorten.

```bash
pin=$(cat e2e/simulators/mso-mocks.commit)
git -C "${MSO_BRON:-../media-source-optimizer}" archive "$pin:apps/mocks" | docker build -t "mso-mocks:$pin" -
docker run --rm --network "$TESTNET" --network-alias sabnzbd \
  -v "$PWD/e2e/simulators:/sim:ro" -e PYTHONPATH=/sim:/opt/mocks \
  --entrypoint python "mso-mocks:$pin" -m sabnzbd --host intern
```

Gebruik voor qBittorrent `-m qbittorrent --host intern`, voor Prowlarr `-m arr --prowlarr --host intern`
en voor Seerr met de vier arr-instanties `-m arr --host intern`. Geef iedere dienst zijn eigen netwerkalias.
Voor de NZB-overdracht heeft arr ook `--sab-url http://sabnzbd:30217` en de testomgevingsvariabele
`SIM_SAB_APIKEY` nodig. De startmodules gebruiken standaard loopback. `--host intern` vereist een
Docker-container zonder actieve standaardroute. De protocol- en foutproeven staan in de map `tests/` per
simulator. De latere scenario’s meten het handelen van MediaGuard en MSO.

## Huis en energie en hergebruik van my_house

De Gree-opstelling schrijft het protocol niet opnieuw: elke unit is de `FakeGreeUnit` uit
`my_house:integrations/tests/fake_gree.py`, die `shc_integrations.gree.protocol` gebruikt. De bron is vastgezet in
`my-house.commit`. `make test-sim` pakt van die commit alleen `integrations/src/shc_integrations` en `fake_gree.py`
uit de lokale Git-kopie in `../my_house` (anders `MY_HOUSE_BRON`). Daarmee lezen de tests ook de berichten van de
HomeWizard- en Zigbee-simulator met de adapters van my_house. In de integrale omgeving draaien Gree en Zigbee in de
API-image van my_house (die heeft `shc_integrations`, FastAPI en uvicorn), op een uitsluitend intern testnet; zie de
README per simulator. Broadcast-ontdekking van Gree is in Docker niet te bewijzen en blijft NAS-werk (N-P6-06).

## Gemeenschappelijke testbediening (`simkit`)

Elke simulator van een derde app heeft dezelfde routes onder `/_sim/` (nooit in de echte app):

| Route | Doel |
|---|---|
| `GET /_sim/sleutels` | tokens/sleutels van de simulator (alleen testwaarden) |
| `GET /_sim/fouten` · `POST /_sim/fouten {"naam","aantal","pad","waarde"}` · `DELETE /_sim/fouten[?naam=]` | foutinjectie: alleen de eerstvolgende `aantal` keer, alleen paden die op de regex `pad` passen |
| `GET /_sim/verzoeken` | ontvangen verzoeken (methode, pad, querynamen, of er aanmelding bij zat — nooit de aanmeldgegevens zelf; een geheim in het pad, zoals de push-token van Uptime Kuma, wordt geredigeerd) |
| `POST /_sim/reset` | begintoestand (nieuwe tokens en sleutels) |

Elke verzoekregel bevat in `fouten` de werkelijk toegepaste algemene fouten. De SAB-bediening vermeldt ook
de protocolhandeling in `details`, omdat een GET bij SAB configuratie kan wijzigen.

Algemene fouten in elke simulator: `http_500`, `http_502`, `http_503`, `traag` (`waarde` seconden), `ongeldige_json`,
`te_groot` (`waarde` MiB, zonder `Content-Length`) en `redirect` (302 naar een andere host; een client hoort die nooit
te volgen). De specifieke fouten staan in de README per simulator.

Simulatorwaarden (hostnamen onder `.invalid`, pool `simpool` in de TrueNAS-simulator) zijn nooit echte NAS-gegevens.

De stand-ins voor fase P2 zijn **geen** kopie van de echte Hub of executor: de echte Hub heeft de routes voor
voorstellen, goedkeuring en catalogusbeschikbaarheid nog niet, en de echte executor wordt in `ai-server-manager`
gebouwd. Ze volgen contract 1.3 §8 en `ai-server-manager:docs/EXECUTION_REQUEST.md`; waar het contract niets
vastlegt, staat de gekozen (veiligste) lezing in `docs/adr/ADR-0022-plannen-installatie-update-terugzetten.md`.
Zodra de echte routes bestaan, moeten dezelfde tests daartegen draaien; tot dan is alles **Sim-getest**.
