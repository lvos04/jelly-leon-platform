# Seerr — installatie, update, test en herstel

**Rol:** Bestaande mediaverzoeken.  
**Aanpak:** Behouden, via MediaGuard.  
**Bron:** https://github.com/seerr-team/seerr.  
**Versiebeleid:** Huidige versie en repo/patch status controleren.

## 1. Vooraf controleren
MediaGuard vier arr instances/sonarr radarr mapping.

**Toegang / netwerk:** Bestaande UI port/API in NAS inventaris.

**Data / mounts:** Request DB en configuratie.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. MediaGuard inrichting
**Uitvoeren:** Controleer Seerr naar vier arr forks via NAS LAN-IP, niet container 127.0.0.1.
**Verifiëren:** Test vier services gezond.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I19-19-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Anime routing
**Uitvoeren:** Vraag anime via Seerr, bevestig naar anime instance en correcte category.
**Verifiëren:** Geen verkeerde instance.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I19-19-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Request lifecycle
**Uitvoeren:** Vraag, ophalen, geleverd, bewust verwijderd, retry, stop.
**Verifiëren:** Verwijderde titel komt niet terug na polling.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I19-19-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Backups
**Uitvoeren:** Seerr requests DB/keys consistente backup.
**Verifiëren:** Herstel vragen en status correct.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I19-19-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Hub/Jellyfin links
**Uitvoeren:** Read-only request status in Hub en universele speler.
**Verifiëren:** Geen tweede request engine.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I19-19-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Update
**Uitvoeren:** Test compat en SEERR API na image update.
**Verifiëren:** Geen nieuwe profieloverschrijving.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I19-19-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Alle Seerr writes via eigenaar MediaGuard; geen dubbele Seerr sync in Hub.

## 6. Upstream documentatie en broncontrole
https://github.com/seerr-team/seerr

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.