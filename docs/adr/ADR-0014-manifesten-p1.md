# ADR-0014 — Installeerbare manifesten P1: I07 Beszel, I08 Uptime Kuma, I09 Authentik, I13 Gotify, I23 Hub/executor

- Status: aanvaard · Datum: 2026-11-09 · Contract: 1.2 §1, §2, §2.2, §3.1, §8, §9 · Validator: appstore-en-fork
  `59217fd` (`jl-manifest validate --strict`)
- Bronnen (alleen gelezen): `truenas/apps` master `f7a38eb` (2026-11-09), `henrygd/beszel` v0.21.1, `louislam/uptime-kuma`
  2.5.9, `gotify/server` v3.1.1, `goauthentik/authentik` version/2026.8.5; digests met `docker buildx imagetools inspect`.

## Aanleiding

Fase P1 vraagt installeerbare manifesten (contract §9) voor I07, I08, I09, I13 en I23, met actuele stabiele versies
en `@sha256`-digests, parameters uit wizard/datasetkiezer/geheimen en validatie met de 1.2-validator in `--strict`. De
opdracht laat voor de apps met één container de keuze tussen een standaard Custom App en een TrueNAS-catalogusapp.

## Besluit

1. **Beszel (hub + agent), Uptime Kuma en Gotify → standaard Custom App (`ix-app`) met één gepinde upstream-image.**
   Contract §1: catalogusapps voor apps die *meerdere containers* nodig hebben, "anders een standaard Custom App met
   één gepinde image". Redenen om bij één container niet de catalogusapp te nemen:
   - de digest staat dan in óns manifest (contract §1/§9: digest verplicht) en de executor leest hem terug;
   - geen afhankelijkheid van een catalogusversie die verdwijnt (gezien op de peildatum: authentik 1.3.63 → 1.3.64 en
     immich 1.14.62 → 1.14.63 tussen `f142a16` en `f7a38eb`; contract 1.2 §2.2);
   - terugzetten werkt voor alle vier hetzelfde (`app.update` met de vorige waarden, §2.2);
   - voor de agent bestaat geen catalogusapp.
   Prijs: het `ix-app`-formulier haalt `no-new-privileges` en `cap_drop` weg. Daarom draaien alle vier met
   `run_as: custom 568:568` (de catalogusapp van Uptime Kuma draait zelfs als root met NET_RAW). Restrisico: setuid-
   programma's in een upstream-image kunnen binnen de container root worden (zonder no-new-privileges) — **NIET
   GEVALIDEERD**; de catalogusapps (`community/beszel-hub` 1.1.22, `uptime-kuma` 1.2.25, `gotify` 1.1.17, met dezelfde
   digests) blijven het alternatief als de echte NAS dat nodig maakt.
2. **Authentik → catalogusapp `community/authentik` 1.3.64** (app_version 2026.8.5): server, worker en PostgreSQL 18.
   De catalogusversie pint de images (`ix_values.yaml`, digests in de kop van het manifest), `mount_docker_socket:
   false`, vier datasets uit de datasetkiezer (`auto_permissions: false`, geen recursieve chown), `run_as` 568.
   Community-trein inschakelen is een eenmalige R2-actie. Verdwijnt 1.3.64, dan `version_unavailable` (nooit stil
   nieuwer).
3. **I23 (Hub + executor)**: de eigenaar is `ai-server-manager`; `install/manifests/` bevat een **byte-gelijke,
   versie-gepinde kopie** (herkomstkop met commit + sha256, `tools/manifest_kopie.py`, CI faalt bij drift). Installatie
   blijft handmatig volgens `install/truenas/INSTALLATIE.md` (twee formulieren); de manifesten beschrijven dezelfde
   formulierwaarden.
4. **`--strict` en eerlijke waarschuwingen.** `tools/manifesten.py` draait `jl-manifest validate --strict` op elk
   manifest. Zonder vermelding in `validatie-uitzonderingen.yaml` moet het schoon zijn (Beszel hub en agent zijn dat).
   Vijf waarschuwingen blijven bewust staan, omdat de alternatieve waarde onwaar zou zijn:
   - `backup_db_export_none` (Gotify, Uptime Kuma): geen export en geen shell in de container; consistente back-up =
     snapshot met de app gestopt, terugzetten na een migratie = snapshot-restore (precies wat de waarschuwing zegt);
   - `secret_in_form` (Authentik `db_password`, `secret_key`): de catalogusapp eist ze als privé-waarde;
   - `digest_pending_release` (Hub, executor): eigen images zonder release.
   De CI eist dat de set waarschuwingen per manifest **exact** gelijk is aan de lijst (geen extra, geen verouderde).
5. **Geen voorbeeldwaarden, geen geheimen in het formulier.** Hostpaden alleen via `dataset_picker`, tijdzone en
   RPO/RTO uit de wizard (D16), hostpoorten als voorstel (`source: default`, standaard van de catalogusapp) met
   conflictcontrole; de agent krijgt alleen de **publieke** hubsleutel (`KEY`, SSH-modus), nooit het `TOKEN`.
6. **Na installatie verplicht** (anders incident, nooit groen): Gotify admin/admin vervangen (controle: Basic
   admin:admin → 401), Beszel eerste account (controle: `/api/beszel/first-run` → false), Uptime Kuma eerste account
   (alleen via Socket.IO controleerbaar → handmatige bevestiging), Authentik initiële setup. De simulators bootsen de
   controleerbare checks na.

## Gevolgen

- De vertaling van appstore-en-fork levert voor de vier Custom Apps installeerbare `ix-app`-waarden met tag
  `<versie>@sha256:<digest>` en digest-readback; voor Authentik weigert ze terecht (geheimen vullen uit de vault door
  de executor bestaat pas in P2 → `capability_unavailable`).
- De rooktest (ADR-0016) liet zien dat `app.create` een hostpad weigert dat (nog) geen dataset is: het
  installatieplan (P2) moet de dataset eerst laten aanmaken of een bestaande kiezen; de vertaling controleert alleen de
  vorm.
- Contract-issues: de standaard kent geen "geaccepteerde waarschuwing" per manifest en geen consistente
  snapshotmethode voor SQLite-apps (`db_export`), en `--strict` kan een catalogusapp met verplichte geheimen nooit
  schoon valideren.
