# Persoonlijke zoekmachine — installatie, update, test en herstel

**Rol:** Nieuwe AI-zoekmodule en pgvector.  
**Aanpak:** Bouwen en opt-in deploy.  
**Bron:** 02_BOUWPLANNEN/B07_PERSOONLIJKE_ZOEKMACHINE.md.  
**Versiebeleid:** Nog geen image; build via eigen Hub CI.

## 1. Vooraf controleren
Data permissions, pgvector readiness en lokale ASR budget.

**Toegang / netwerk:** Interne API, alleen geautoriseerde user-facing Hub route.

**Data / mounts:** Search-index dataset, afgeleide embeddings optioneel backup.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Bronnen en toestemming
**Uitvoeren:** Opt-in Immich metadata, Jellyfin, docs en logs met exact ACL.
**Verifiëren:** Niet toegestane paden buiten index.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I25-25-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Datamodel en index
**Uitvoeren:** Normalize docs + chunks + embeddings; source SHA, model version, deletion TTL.
**Verifiëren:** No duplicate/stale results.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I25-25-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Kern FTS
**Uitvoeren:** Postgres full text eerste fase voor semantisch werk.
**Verifiëren:** Exact zoekresultaat met provenance.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I25-25-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Embeddings optioneel
**Uitvoeren:** Install pgvector en lokaal model low priority; tokens per source en consent.
**Verifiëren:** Semantisch resultaat binnen resource budget.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I25-25-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Privacy negative tests
**Uitvoeren:** Controleer ACL en prompt injection vanuit document/media.
**Verifiëren:** Geen private result leak of unauthorized actions.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I25-25-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Re-index/rollback
**Uitvoeren:** Index rebuild, export, cleanup per source; stop indexing during playback.
**Verifiëren:** Restore of reindex zonder originals verlies.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I25-25-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Geen upload van privébestanden naar Claude zonder expliciete consent per bron; ChatGPT Plus is geen server API.

## 6. Upstream documentatie en broncontrole
02_BOUWPLANNEN/B07_PERSOONLIJKE_ZOEKMACHINE.md

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.