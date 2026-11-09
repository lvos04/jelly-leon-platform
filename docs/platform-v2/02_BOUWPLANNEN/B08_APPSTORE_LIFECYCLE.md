# Appstore en fork lifecycle — volledige bouwspecificatie

**Type/eigenaar:** Nieuwe Hub-module  
**Doel:** Eigen goedgekeurde appcatalogus voor TrueNAS, upgrade-, fork-, security- en herstelbeheer.

**Afhankelijkheden:** Hub, backup, GitHub access, policy engine.

**Bron:** https://www.truenas.com/docs/

## 1. Concrete schermen en gebruikersreizen
Catalogus; Appdetail; Toevoegen; Versies; Forks; Afhankelijkheden; Builds; Review; Install; Rollback; Audit

**Per scherm minimaal:** responsief (360/390/768/1440 px), status leeg/laden/storing/offline, gedocumenteerde navigatie, zoek/filter/paginering, rolcontrole, datum van laatste update, bronherkomst, fout in het Nederlands, toegankelijk toetsenbordgebruik en zichtbaar auditspoor voor wijzigingen.

## 2. Datamodel en levensduur
app_manifest, image_digest, source_repo, upstream_repo, license, security_advisory, dependency, compatibility_rule, deploy_plan, job, approval, rollout, rollback

Dwing database-migraties met forward-schema-tests af; time-series met retentie/aggregatie; primaire gegevens alleen bij bron. Entiteiten krijgen stable ID, versie, eigenaar, timestamps en provenance waar toepasselijk. Secrets alleen als vault-reference.

## 3. API en integratie-contracten
GET /api/v1/catalog; POST /api/v1/catalog/preview; POST /api/v1/catalog/install; GET /api/v1/forks; POST /api/v1/forks/{id}/sync-plan

Bij alle endpoints: authenticatie, rol, inputvalidatie, idempotency voor mutaties, paginering of cursors voor lijsten, rate limits, time-outs, versie/capability-detectie en machineleesbare foutcodes. De genoemde uitbreidingsroutes zijn ontwerpeisen, niet automatisch bestaande endpoints. API-contracten moet de bouwer in OpenAPI vastleggen en e2e verifiëren.

## 4. Bouwfasen (verplichte volgorde)

### F0 - Inventaris & onderzoek
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B08-001 — Manifeststandaard
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Per app versie, image digest, mounts, ports, roles, data-retention, GPU, limits, capability and tests.
**Implementatie in code/diensten:** Define JSON Schema + signed manifest provenance + compatibility against TrueNAS release.
**Schermen/bediening:** Catalogus toont transparante eisen.
**Acceptatie / concrete toets:** Invalid mount outside allowlist en mutable latest refused.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F1 - Kern en contracten
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B08-002 — Import bestaande apps
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Registreer bestaande TrueNAS apps zonder ze opnieuw te installeren of rechten te wijzigen.
**Implementatie in code/diensten:** Discover installed workloads via official API, match ID by namespace and container labels, user confirmation of owner.
**Schermen/bediening:** Appoverzicht met status Geïmporteerd.
**Acceptatie / concrete toets:** Nog niet ondersteunde app blijft unmanaged zonder dataverlies.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F2 - Functionaliteit
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B08-003 — Install preview/dependency
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Plan image, datasets, privileges, poorten, network egress, secrets and dependency readiness.
**Implementatie in code/diensten:** Dry-run validation, explicit deployment DAG, pinned revisions; download unavailable image only after auth set.
**Schermen/bediening:** Diff met vereiste goedkeuring.
**Acceptatie / concrete toets:** Poortconflict/dependency ontbreken blokkeren deployment.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F3 - Integraties en veiligheid
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B08-004 — Veilige updates
**Status bij start:** Bouwen; **impact:** Hoog.
**Gewenst gedrag en grenzen:** Upstream releases volgen, changelog en CVEs beoordelen, testbuild en staging migratie.
**Implementatie in code/diensten:** Version pin, test snapshots and shadow stack, fix/test/refactor via PR rather than edit production.
**Schermen/bediening:** Versiebord en lint/test/e2e evidence.
**Acceptatie / concrete toets:** DB migratiefout laat oude app met data intact.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F4 - Automatisering
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B08-005 — Forkbeheer
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Upstream commits per fork volgen, merge/rebase voorstellen, CI, license obligations en changes vergelijken.
**Implementatie in code/diensten:** GitHub API, branch protection, changelog digest, upstream conflict triage, PR with risk.
**Schermen/bediening:** Fork-dashboard met afwijkingen t.o.v. upstream.
**Acceptatie / concrete toets:** Foutieve patch stopt vóór productie; git SHA blijft controleerbaar.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

### F5 - Hardening & UX
**Fase-entry:** voorafgaande kritieke gates geslaagd; bestaande gegevens en huidige release vastgelegd; rollback plan bekend.

#### B08-006 — Uninstall / move / recovery
**Status bij start:** Bouwen; **impact:** Middel.
**Gewenst gedrag en grenzen:** Verwijder appcontainer los van config/data; verhuizing naar andere server via exports.
**Implementatie in code/diensten:** Explicit destructive gate separate dataset deletion, backup and restore audit, reversible staging.
**Schermen/bediening:** Twee bevestigingsstappen en herstelkaart.
**Acceptatie / concrete toets:** Uninstall wist geen Immich media of MediaGuard DB.
**Foutafhandeling en bewijs:** een externe storing mag niet als succes worden gelogd; leg testscenario, gebruikte app/image-versie, screenshots, redacted request/response, timestamps, audit/rollback en incidentlink vast.

**Fase-exit:** alle in deze fase genoemde checks aantoonbaar geslaagd, code review, back-up-/rollback-gate, een gebruikersreis op mobiel en een negatieve test voor permissions. Bij een blocker niets forceren: status `Geblokkeerd` + reden + eigenaar.

## 5. Niet-regressie, randgevallen en verbodsbepalingen
Geen willekeurige niet-beoordeelde appstore met root toegang. Eigen apps gebruiken GitHub repositories, overige apps alleen officiële images. Geen mini-pc deployment.

## 6. Oplevering door bouwer
Opleveren: (1) broncode en tests, (2) migration scripts en versiecompatibiliteit, (3) Compose/TrueNAS app manifest met gepinde images, (4) .env.example zonder geheimen, (5) OpenAPI/connector schemas, (6) test- en auditrapport, (7) restore bewijs, (8) release notes, (9) gebruikershandleiding, (10) screenshots web/mobiel. Markeer niet-geteste hardware/echte accounts als `NIET GEVALIDEERD`.

## 7. Proof of Done
Alle acceptatiecriteria in Excel zijn gekoppeld aan testbewijs en codecommit; bij stroomonderbreking, uitval van afhankelijke app, NAS-herstart, gewijzigde credentials en incompatibele versie degradeert de service aantoonbaar veilig. Geen directe writes aan de database van een andere app.
