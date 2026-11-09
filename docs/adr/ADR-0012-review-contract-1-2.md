# ADR-0012 — Review contract 1.2: 413 vóór routering, koppelcode nooit over een bestaande koppeling

- Status: aanvaard · Datum: 2026-11-09 · Contract: 1.2 §3.6, §3.7, §5
- Herziet: ADR-0010 punt 2 (verzoekgrootte), ADR-0011 (koppelen via de statuspagina)

## Aanleiding (bevindingen van de onafhankelijke review na het toepassen van contract 1.2)

1. **De 1 MiB-grens zat niet vóór routering en authenticatie.** `BodySizeLimitMiddleware` telde de body alleen als
   de app hem zelf las. Een chunked verzoek > 1 MiB gaf daardoor 401 (zonder aanmelding), 404 (onbekende route) of
   405 (andere methode) — en op `POST /connector/v1/hub-link/unlink` (route zonder body) met een geldige admin- of
   dienst-JWT werd de ontkoppeling **uitgevoerd** (200). §5 eist 413 vóór routering en authenticatie, ook chunked.
   (Dezelfde fout is in de Hub gerepareerd in `ai-server-manager` 9572773.)
2. **Wedloop bij koppelen via de statuspagina.** "Alleen zolang nooit gekoppeld" (§3.7) werd alleen gecontroleerd
   vóór het verzoek naar de Hub. Een aanvaller op het LAN kon een koppelcode-poging naar zijn eigen "Hub" starten en
   pas antwoorden nadat de echte koppeling (het aanbevolen env-token in de worker) was opgeslagen; zijn antwoord
   overschreef dan `service_token`, `pins` en `hub_url` (aangetoond: 303 i.p.v. weigering). Ook kon een mislukte
   koppelcode-poging de `pending` van een lopende env-token-poging wissen.
3. **60-minutengrens zonder `pending`.** Met een onleesbaar `hub-link.json` kan `pending.started_at` niet worden
   vastgelegd; het env-token werd dan eindeloos opnieuw geprobeerd (§3.3: stoppen 60 min na de eerste poging).
4. **`--forwarded-allow-ips`** op `python -m lifecycle api` kon uvicorn `X-Forwarded-For` laten vertrouwen, terwijl
   §3.7 de rate-limit per bron-IP van de TCP-verbinding eist ("nooit `X-Forwarded-For`").
5. Kleinere punten: een leeg `LIFECYCLE_DATA_DIR`/`LIFECYCLE_DATABASE_URL` (bijv. overgenomen uit `.env.example`)
   gaf de werkmap als datamap of een lege database-URL; de regressietest "vreemde Hub zonder vingerafdruk" slaagde
   op de lokale controle van een ongeldige koppelcode en bereikte de vreemde server nooit.

## Besluit

1. De ASGI-laag leest de body **vóór alles** tellend in (hooguit 1 MiB in het geheugen): `Content-Length` > 1 MiB
   direct 413, ongeldige of dubbele `Content-Length` 400 `bad_request`, meer dan 1 MiB tijdens het lezen 413; een
   verbroken verbinding vóór het einde van de body voert niets uit. Daarna krijgt de app de gebufferde body. Zo is
   413 onafhankelijk van route, methode en aanmelding, en verbruikt een geweigerd verzoek geen `jti`.
2. Koppelen met een koppelcode wordt **onder de vergrendeling van `hub-link.json`** beslist: `pending` schrijven
   weigert (`PairingRefused`) als `first_linked_at` gezet is of er al een poging loopt, en het opslaan van het
   resultaat weigert als `first_linked_at` intussen gezet is. Een onleesbaar bestand stopt de poging vóór het
   verzoek naar de Hub (de code blijft dan geldig). Het env-token mag wel altijd koppelen (§3.8). `mark_failed`
   wist alleen de `pending` van de eigen poging.
   Gevolg (gemeld): doet een beheerder tegelijk een env-token- én een koppelcode-koppeling met **dezelfde** Hub, dan
   kan de Hub bij de tweede enrollment de tokens van de eerste intrekken (§3.6), terwijl de dienst de tweede weigert.
   De dienst ziet dat via `services/self` (401) en wordt `ingetrokken` (fail-closed); herstel = opnieuw koppelen via
   de Hub. Dit is de veiligste keuze: een koppelcode overschrijft nooit een bestaande koppeling.
3. De worker houdt per env-token de eerste poging ook in het geheugen bij; de grens van 60 minuten geldt daardoor
   ook zonder leesbaar `hub-link.json` (normaal telt `pending.started_at`, dat herstarts overleeft).
4. De API start altijd met `proxy_headers=False`; de optie `--forwarded-allow-ips` is verwijderd.
5. Lege waarden vallen terug op de standaard; `LIFECYCLE_DATA_DIR` moet een absoluut pad zijn (API, worker en
   procesbewaker), anders een Nederlandse foutmelding en geen start.

## Gevolgen

- Nieuwe tests: `test_api.py::test_body_over_1_mib_is_413_before_routing_also_chunked`,
  `::test_invalid_content_length_is_400`, `test_statuspage.py::test_pairing_that_finishes_after_another_link_never_overwrites_it`,
  `::test_second_pairing_attempt_while_one_is_pending_is_refused_under_the_lock`,
  `::test_no_pairing_form_after_unlink_and_post_is_refused_without_hub_call`,
  `test_enrollment_worker.py::test_stops_60_minutes_after_first_attempt_also_with_unreadable_hub_link`,
  `test_config.py::test_api_never_trusts_forwarded_headers`, `::test_empty_values_fall_back_to_defaults_and_data_dir_must_be_absolute`.
  De nieuwe 413- en wedloop-tests falen aantoonbaar op de code van vóór deze review.
- Alles Sim-getest (Hub-simulator, PostgreSQL 16, Docker); op een echte NAS NIET GEVALIDEERD.
