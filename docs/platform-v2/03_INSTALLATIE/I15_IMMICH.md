# Immich — installatie, update, test en herstel

**Rol:** Bestaand primaire fotobeheer.  
**Aanpak:** Behouden en beschermen.  
**Bron:** https://docs.immich.app/install/.  
**Versiebeleid:** Huidige installatieversie eerst lezen.

## 1. Vooraf controleren
Huidige foto-backup correct en externe kopie aantoonbaar.

**Toegang / netwerk:** Bestaande bereikbaarheid behouden en beveiligen.

**Data / mounts:** Immich media + appdata + database, OneDrive kopie test.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Inventaris
**Uitvoeren:** Images/tags, datasets, originele foto's, external libs en database-schema vastleggen.
**Verifiëren:** Niets opnieuw importeren of verplaatsen.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I15-15-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. OneDrive restore
**Uitvoeren:** Foto en Immich DB export naar geïsoleerde teststack herstellen.
**Verifiëren:** Media en albums correct zonder dataverlies.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I15-15-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Performance en permissions
**Uitvoeren:** Huidige AI/ML processor lasten meten voordat zoekindex extra belasting zet.
**Verifiëren:** Geen OOM of permanente uitval.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I15-15-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Hub connector opt-in
**Uitvoeren:** Use Immich APIs for read-only recent photos, albums counts; geen copy van face embeddings.
**Verifiëren:** Hub toont alleen toegestane metadata.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I15-15-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. SSO evaluatie
**Uitvoeren:** Alleen werkelijke feature support en rolrechten gebruiken.
**Verifiëren:** Geen bypass via proxy.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I15-15-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Update veilig
**Uitvoeren:** Officiële release notes/migration on copied DB; pin exact image.
**Verifiëren:** Na rollback volledige DB consistent.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I15-15-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Immich behoudt eigendom van afbeeldingen; nieuwe Hub of AI mag geen foto's wissen, reorganiseren of vervangen.

## 6. Upstream documentatie en broncontrole
https://docs.immich.app/install/

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.