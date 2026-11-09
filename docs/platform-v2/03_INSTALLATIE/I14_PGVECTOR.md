# PostgreSQL + pgvector — installatie, update, test en herstel

**Rol:** Zoek-/embeddingsopslag.  
**Aanpak:** Nieuw indien AI-search geactiveerd.  
**Bron:** https://github.com/pgvector/pgvector.  
**Versiebeleid:** Pin bij PostgreSQL versie passende extension release.

## 1. Vooraf controleren
Eerst zorgen dat een aparte Hub-DB en role/ACL bestaat.

**Toegang / netwerk:** Interne Docker netwerkpoort, niet LAN publiek.

**Data / mounts:** Separate DB volume + backup.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Data model en retentie
**Uitvoeren:** Defineer per source user ACL en vector embeddings, dimensions/version.
**Verifiëren:** Geen private documents via search voor andere user.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I14-14-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Image/installatie
**Uitvoeren:** Gebruik officiële passende Postgres/pgvector image, pinned digest; readonly mount van brondata.
**Verifiëren:** Extension CREATE VECTOR werkt in test DB.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I14-14-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Indexen en ACLs
**Uitvoeren:** Maak RLS/permissions, full text index en vector index met reindex path.
**Verifiëren:** Queries filteren vóór results getoond worden.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I14-14-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Rebuild en backup
**Uitvoeren:** Documenten opnieuw indexeren zonder originals; DB export encrypted.
**Verifiëren:** Indexed search na restore werkt.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I14-14-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. CPU/GPU budget
**Uitvoeren:** Batch en throttling, geen onverantwoord ASR tijdens media playback.
**Verifiëren:** Geen streamstotter door zoekindexeren.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I14-14-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Geen nieuwe embeddings voor al je foto's/media zonder expliciete toestemming of opslagmeting.

## 6. Upstream documentatie en broncontrole
https://github.com/pgvector/pgvector

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.