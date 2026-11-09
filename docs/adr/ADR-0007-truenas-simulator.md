# ADR-0007 — TrueNAS-simulator: realistisch waar het telt, strenger waar het platform iets verbiedt

- Status: aanvaard · Datum: 2026-11-09

## Besluit
- Eén Python-proces (FastAPI/uvicorn) met JSON-RPC 2.0 over WebSocket op `/api/current` en `/api/<versie>`,
  profielen 25.04.2.6 en 25.10.8 (andere API-versielijst en snapshotmethoden), test-beheer-API `/_sim`.
- Realistisch gedrag overgenomen uit de broncode: jobs + `core.get_jobs`/`core.job_wait`/events, ondiepe merge bij
  `app.update`, rollback alleen tussen catalogusversies, ix-app-upgrade zonder versiewissel, validatie binnen de
  job, `remove_ix_volumes`, key-intrekking bij onversleuteld transport, foutcodes/errno's.
- Bewust strenger: `custom_compose_config`/`custom_app=true` geweigerd; `latest`, `privileged`, `host_network`,
  `remove_ix_volumes=true`, snapshot-rollback geregistreerd als overtreding (`/_sim/violations`); `zfs.snapshot.*`
  afwezig in 25.10 (echt: privé).
- Alle namen zichtbaar nep (`simpool`, `SIM-DISK-*`, "SIM Intel GPU"); nooit als NAS-gegevens gebruiken.

## Gevolgen
- Resultaten heten Sim-getest/Sim-geslaagd. Afwijkingen van de echte API zijn mogelijk (README van de simulator).
