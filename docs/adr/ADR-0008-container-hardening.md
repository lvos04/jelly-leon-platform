# ADR-0008 — Alles-in-één container: root alleen voor eigenaarschap, daarna 568 met no_new_privs

- Status: aanvaard, **herzien voor 1.2 door ADR-0011 (punt 12)** · Datum: 2026-11-09 · Contract: §1, §10, §2.2 (1.2)

> **Stand contract 1.2:** `dumb-init` is weg; de procesbewaker is zelf PID 1, ruimt zombies op, wisselt naar 568 en
> controleert daarna Uid/CapEff/NoNewPrivs zelf (fail-closed). De image-E2E controleert dat voor **elk** proces, ook
> PID 1, zonder `--security-opt`/`--cap-drop`.

## Context
Het `ix-app`-template (standaard Custom App) wist de capability-beperkingen (`clear_caps`) en **verwijdert
`no-new-privileges`** (bron: `truenas/apps` ix-app 1.4.8 template). Het formulier kan dit niet aanzetten.

## Besluit
- (P0) dumb-init (PID 1) → procesbewaker; als root alleen: eigenaar van `/appdata` (de map zelf, niet recursief) en van
  de eigen submappen `postgres`, `secrets`, `backups` naar 568:568; daarna `setgid/setuid(568)` en
  `prctl(PR_SET_NO_NEW_PRIVS)` voor de bewaker én alle kindprocessen. Getest: alle python/postgres-processen
  draaien als 568 met `NoNewPrivs: 1` (`tests/e2e/test_allinone_image.py`).
- PostgreSQL 16 alleen op een unix-socket (`listen_addresses=''`, socketmap 0700), `--auth-host=reject`,
  data-checksums aan, geen wachtwoord nodig.
- Start als 568 ("Custom User") werkt ook; dan wordt niets aangepast, alleen schrijfbaarheid gecontroleerd.
- Base-images op exacte versie + `@sha256`-digest; Python-pakketten met `--require-hashes`.

## Gevolgen
- Geen extra capabilities, geen Docker-socket, geen privileged nodig.
