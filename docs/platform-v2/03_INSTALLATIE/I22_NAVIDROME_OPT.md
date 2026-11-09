# Navidrome (optioneel) — installatie, update, test en herstel

**Rol:** Gespecialiseerde muziekbackend.  
**Aanpak:** Optioneel; niet automatisch toevoegen.  
**Bron:** https://www.navidrome.org/docs/installation/docker/.  
**Versiebeleid:** Pin actuele officiële image als gekozen.

## 1. Vooraf controleren
Alleen installeren als Jellyfin muziek niet voldoet.

**Toegang / netwerk:** Eigen LAN-only HTTP poort na conflictcontrole.

**Data / mounts:** /data persistent en muziekmap read-only.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Value gate
**Uitvoeren:** Vergelijk Jellyfin muziek, mobiele clients en offline use cases.
**Verifiëren:** Navidrome alleen als aantoonbare meerwaarde.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I22-22-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Dataset en mount
**Uitvoeren:** Config dataset en muziekmap read-only.
**Verifiëren:** Geen dubbele bestandsmutatie.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I22-22-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Install pinned image
**Uitvoeren:** Official compose/TrueNAS Custom App current stable.
**Verifiëren:** UI healthy en library scan.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I22-22-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Client compat
**Uitvoeren:** Test Subsonic en experimentele Jellyfin API afhankelijk release.
**Verifiëren:** Audio, playlists, artwork werken op echte clients.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I22-22-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Backup en rollback
**Uitvoeren:** Config en DB backup, playlists export.
**Verifiëren:** Geen playlistverlies.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I22-22-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Optioneel, want één universele mediaserver blijft hoofddoel.

## 6. Upstream documentatie en broncontrole
https://www.navidrome.org/docs/installation/docker/

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.