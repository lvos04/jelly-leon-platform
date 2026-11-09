# ADR-0018 — Review fase P1: upstream-images echt starten, simulatorconformiteit, eerlijkere evidence

- Status: aanvaard · Datum: 2026-11-09 · Contract: 1.2 §1, §2.2, §3.1, §9, §11
- Vult aan: ADR-0014 (manifesten), ADR-0016 (simulators en rooktest)

## Aanleiding (bevindingen van de onafhankelijke review van fase P1)

Alle CI-stappen zijn lokaal opnieuw gedraaid (lint, typecheck, dienst-, simulator-, conformiteits- en repotests,
manifestvalidatie met de validator van de actuele appstore-en-fork, OpenAPI-drift, actionlint, image bouwen en
starten met lege `/appdata`, image-E2E, browsercontrole en live ontwerpreview, installatie-rooktest). Die waren groen.
De digests van zeven images (Beszel hub en agent, Uptime Kuma, Gotify, Authentik-server, python- en postgres-base) zijn
met `docker buildx imagetools inspect` gecontroleerd en klopten. De review vond wel:

1. **Geheim in het verzoeklog van de Uptime Kuma-simulator.** `/_sim/verzoeken` bewaarde het volledige pad
   `/api/push/<push-token>`. De push-token is zelf het geheim (wie hem kent, houdt de dead-man's switch van EX22 op
   "up"), terwijl de README belooft dat aanmeldgegevens nooit in het log staan.
2. **Simulator wijkt af van upstream.** `/api/push` accepteerde een negatieve of te grote `ping` (upstream 2.5.9:
   `404 {ok:false, msg:"Invalid ping value…"}`), en `/api/entry-page` gaf `"dashboard"` waar een echte installatie
   `null` geeft.
3. **Onjuiste claim over de Beszel-agent.** Manifest, `BESZEL_MEETDEKKING.md` en EVIDENCE (I07-07-06) zeiden dat de
   agent zijn vingerafdruk in `/var/lib/beszel-agent` bewaart en dat dit dubbele hosts voorkomt. In de broncode
   (v0.21.1) gebruikt alleen de WebSocket-client (`TOKEN` + `HUB_URL`) de vingerafdruk; in de gekozen SSH-modus blijft
   die map leeg (gemeten). Dubbele hosts ontstaan in SSH-modus niet door een herstart: de identiteit is het
   systeemrecord in de hub.
4. **Niets startte de upstream-images echt.** De manifesten zetten Beszel, Uptime Kuma en Gotify op 568:568, terwijl
   de catalogusapps deels als root draaien. Of de images zo starten, gezond worden en na een herstart hun gegevens
   houden, was alleen afgeleid uit bronnen; de simulators waren nooit naast de echte images gelegd.
5. **Evidence te ruim:** I08-08-03 ("elk endpoint krijgt juiste expected status + timeout") stond op Sim-getest,
   maar er bestaat nog geen monitorplan met verwachte status en time-out per endpoint, en de simulatortests controleren
   alleen het uitlezen van monitorstatussen.

## Besluit

1. `simkit.Verzoeklog` krijgt `geheime_paden` (regex → vervanging). De Uptime Kuma-simulator redigeert
   `/api/push/<token>` tot `/api/push/<push-token>` en telt zo'n verzoek als "met aanmelding". Foutinjectie met een
   pad-regex blijft op het echte pad werken. Tests bewijzen beide.
2. De simulator volgt upstream: ping zoals `parseFloat(ping) || null` met de grens 0…1e11 ms, en `entryPage: null`.
3. Teksten in manifest, meetdekking, simulator en EVIDENCE gecorrigeerd. De dataset van de agent blijft staan (gegevensmap
   van de agent, nodig als later de WebSocket-modus wordt gekozen), met een juiste omschrijving.
4. Nieuw: `tools/upstream_rooktest.py` (`make upstream-rooktest`, stap in CI-job `rooktest`). Die haalt elke
   upstream-image op digest op, leest de digest terug, start de image zoals het manifest hem beschrijft (eigen lege map
   van 568:568, mountpad, containerpoort en vaste omgevingsvariabelen uit het manifest, `--user 568:568`, zonder
   `--cap-drop` of `no-new-privileges`) en controleert:
   - dat alle processen als 568 draaien en de healthcheck-route antwoordt;
   - dat de kernroutes dezelfde vorm en foutcodes hebben als de simulator (conformiteit van de simulators);
   - dat gegevens een herstart overleven;
   - dat de Beszel-agent in SSH-modus met alleen de publieke hubsleutel metingen levert, en dat er na een herstart van
     de agent precies één systeem blijft.

   `tests/repo/test_upstream_rooktest.py` dwingt af dat elk nieuw upstream-manifest in die rooktest komt.
5. I08-08-03 gaat naar `NIET GEVALIDEERD (echte NAS)` met de reden in `opmerking`. Het monitorplan (endpoint,
   verwachte status, time-out per app uit het dienstenregister) is een open punt (zie contract_issues van deze review).

## Gevolgen

- Upstream-images: **Sim-getest** (Docker, geen TrueNAS, geen dataset-ACL, geen `ix-app`-template), nooit "geslaagd
  op de NAS". Statussen in EVIDENCE blijven `Geblokkeerd`/`Bezig`.
- Gemeten en vastgelegd in `BESZEL_MEETDEKKING.md`: als 568 kan de Beszel-agent niet in `/tmp` van de image schrijven
  (waarschuwing in de log, onschadelijke terugval). De hub belt een herstarte agent na ongeveer een minuut zelf weer op;
  de rooktest wacht daarom tot vijf minuten.
- Gotify heeft na een verse installatie echt `admin/admin` (gemeten); de controle na installatie uit
  `install/manifests/README.md` blijft dus nodig.
- De CI-job `rooktest` haalt vier extra images op (op digest).
