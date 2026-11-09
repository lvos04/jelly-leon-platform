# OneDrive, ZFS snapshots en herstel — installatie, update, test en herstel

**Rol:** Bestaande externe back-up uitbreiden en testen.  
**Aanpak:** Inrichten en testen.  
**Bron:** https://www.truenas.com/docs/.  
**Versiebeleid:** OneDrive tool huidige workflow onbekend; rclone/restic alleen na inventaris.

## 1. Vooraf controleren
OneDrive-account, authmethode, backup scope, encryptie en recovery keys bekend.

**Toegang / netwerk:** Geen internet-exposed web UI nodig.

**Data / mounts:** Bestaande OneDrive fotos en app-database exports; nieuwe encrypted config export + snapshots.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Kopie naar OneDrive identificeren
**Uitvoeren:** Vind huidige job, tool, doelpad, schema en gekoppelde datasets; maak back-upmatrix met per app database/media/secrets.
**Verifiëren:** Dekking en herstelbaarheid per app zichtbaar.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I02-02-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Controle back-upbestand
**Uitvoeren:** Controleer checksum, geldige archive-structuur, DB consistent export en aanwezigheid oudere versies.
**Verifiëren:** Bestandslijst en minimaal 2 punten uit historie aangetoond.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I02-02-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Proefherstel
**Uitvoeren:** Herstel één foto en één database-export in tijdelijke directory/DB, zonder originele app te overschrijven.
**Verifiëren:** Foto leesbaar en appdatabase integriteit correct.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I02-02-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. ZFS snapshots aanmaken
**Uitvoeren:** TrueNAS Data Protection > Periodic Snapshot Tasks voor geselecteerde appdata, met retention en ruimtealerts.
**Verifiëren:** Snapshot zichtbaar en kleine restore test op nieuwe dataset geslaagd.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I02-02-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Consistente databaseback-ups
**Uitvoeren:** Per app native backup of pg_dump/SQLite backup API; niet raw copy van live Postgres/SQLite-WAL.
**Verifiëren:** Applicatie start uit herstelde database in teststack.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I02-02-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Offsite uitbreiding
**Uitvoeren:** Alleen versleutelde exports van config/secrets en manifest toevoegen aan bestaande OneDrive job, mits opslagbudget akkoord.
**Verifiëren:** OneDrive versieerbaar en encrypt key separaat geescrowed.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I02-02-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 7. Monitor en escalatie
**Uitvoeren:** Health event bij backup ouder dan beleid, verify fail, verlopen credentials, pool degraded of verzadigde opslag.
**Verifiëren:** Testalert komt buiten de Hub op telefoon aan.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I02-02-07`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 8. Periodieke rehearsal
**Uitvoeren:** Plan 90-dagen proefherstel en vóór R2/R3 update; archiveer bewijs.
**Verifiëren:** Laatste restore test binnen grens, RPO/RTO gemeten.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I02-02-08`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
ZFS snapshots zitten op dezelfde pool en zijn geen off-site back-up. Geen geheime back-up sleutels in OneDrive naast data zonder aparte encryptie.

## 6. Upstream documentatie en broncontrole
https://www.truenas.com/docs/

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.