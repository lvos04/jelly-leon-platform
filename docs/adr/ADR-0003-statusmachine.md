# ADR-0003 — Statusmachine: snake_case, extra eindtoestand `aborted`, bewijs-guards

- Status: aanvaard · Datum: 2026-11-09 · Bron: 01_ARCHITECTUUR/05_OPERATIES_EN_UPDATES

## Context
05_OPERATIES definieert `discovered → inventory → risk-evaluated → backed-up → staged → tested → approved → deployed →
observed → accepted` of `rolled_back`. Niet gedefinieerd: wat een run is die vóór `deployed` stopt, en welk bewijs
een toestand vereist.

## Besluit
- API-namen in snake_case (`risk_evaluated`, `backed_up`), labels in het Nederlands.
- Extra eindtoestand **`aborted`** voor elke stop vóór `deployed`: productie is dan niet gewijzigd, dus
  `rolled_back` zou een terugzetting claimen die nooit plaatsvond. `rolled_back` alleen vanuit `deployed`/`observed`.
- Geen stappen overslaan (back-up en goedkeuring zijn verplicht vóór `deployed`).
- Elke toestand vereist bewijs (`inventory_ref`, `policy_decision_ref`, `backup_checkpoint_id` + `backup_verified`,
  `staging_ref`, `test_evidence_ref`, `approval_id`, `execution_evidence_ref`, `readback_ref`, `acceptance_ref`,
  `rollback_evidence_ref`); `false`/leeg telt als ontbrekend.
- In P0 mag via de API alleen `aborted` handmatig worden gezet; andere overgangen → `capability_unavailable`
  (bewijs kan alleen de P2-engine leveren — een client mag geen bewijs "invullen").

## Gevolgen
- Een status kan nooit meer claimen dan aangetoond is; de Hub-UI kan `allowed_next` en `manual_transitions_now`
  tonen.
