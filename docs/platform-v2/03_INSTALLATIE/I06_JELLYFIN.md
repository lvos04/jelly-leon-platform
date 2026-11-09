# Jellyfin — installatie, update, test en herstel

**Rol:** Huidige mediaserver en basis universele fork.  
**Aanpak:** Behouden, later fork bouwen.  
**Bron:** https://jellyfin.org/docs/general/installation/.  
**Versiebeleid:** Controleer draaiende TrueNAS-versie en Jellyfin fork upstream before commit.

## 1. Vooraf controleren
Backups + client inventory + MediaGuard library refresh connector.

**Toegang / netwerk:** Bestaande Jellyfin poort (vaak 8096) niet wijzigen zonder afhankelijkheidstest.

**Data / mounts:** Huidige Jellyfin media mount read-only waar mogelijk en config dataset.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Huidige Jellyfin inventaris
**Uitvoeren:** Schrijf huidige image, plugins, devices, gebruikers, bibliotheek en paden op.
**Verifiëren:** Geen verlies van watch-state/gebruikers.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I06-06-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Backups en compat
**Uitvoeren:** Maak metadata/config backup en test op staging Jellyfin; controleer licenties.
**Verifiëren:** Stock clients kunnen inloggen in teststack.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I06-06-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Direct Play GPU test
**Uitvoeren:** Controleer A310 en UHD 770 zichtbaar in Jellyfin, exact rendernode, FFmpeg codecs, HDR/DV scenario.
**Verifiëren:** Direct play/ transcoding correct per device met logging.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I06-06-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Basis opschonen
**Uitvoeren:** Standaard UI/TV toegankelijk houden; dubbele Plex/Emby alleen na afhankelijkheden audit.
**Verifiëren:** Geen niet-beschikbare titels na refresh.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I06-06-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Metadata/test series
**Uitvoeren:** Test film, anime absolute numbering, muziek, subtitels en liveTV if tuner.
**Verifiëren:** Library identificatie consistent.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I06-06-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Plugin/adapter eerst
**Uitvoeren:** MediaGuard read-only status, deep link request/quality, event-driven refresh in sidecar, zonder Jellyfin core patch.
**Verifiëren:** Import werkt zonder direct DB access.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I06-06-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 7. Fork alleen voor gaten
**Uitvoeren:** Maak upstream tracking branch en CI compat smoke; patch server eerst als plugin niet volstaat.
**Verifiëren:** Jellyfin client matrix groen.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I06-06-07`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 8. Release en herstel
**Uitvoeren:** Update staged with snapshot and plugin compat; rollback beide DB en binary.
**Verifiëren:** Watch-state/herstelde DB correct na revert.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I06-06-08`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Geen nieuwe universele media-app met volledige eigen protocollen from scratch. Vooral tv-app compat afhankelijk echt gebruikte apparaten.

## 6. Upstream documentatie en broncontrole
https://jellyfin.org/docs/general/installation/

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.