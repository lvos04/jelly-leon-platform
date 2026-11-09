# Universele Jellyfin fork release — installatie, update, test en herstel

**Rol:** Nieuwe fork / uitbreiding.  
**Aanpak:** Bouwen volgens B03, daarna stage installeren.  
**Bron:** 02_BOUWPLANNEN/B03_UNIVERSELE_MEDIASERVER.md.  
**Versiebeleid:** Nog geen eigen fork-image: te bouwen, upstream Jellyfin gebruiken tot release.

## 1. Vooraf controleren
Jellyfin plugins + client matrix, MediaGuard read-only API en GPU test beschikbaar.

**Toegang / netwerk:** Client API LAN/VPN, streaming exposure volgens huidige Jellyfin.

**Data / mounts:** Config DB en bibliotheek access, compat met bestaande users.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Baseline en compat
**Uitvoeren:** Maak fork v0 milestone op exact upstream tag en tests voor bestaande clients.
**Verifiëren:** Niets verloren in testbibliotheek.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I24-24-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. GPU capability
**Uitvoeren:** Map A310/UHD770 rendernodes en benchmark 1080p/4K AV1/HDR DV tests.
**Verifiëren:** Speler direct play en fallback onder workload.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I24-24-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Sidecar/extension eerst
**Uitvoeren:** Implementeer metadata/MediaGuard/semantiek buiten core waar mogelijk.
**Verifiëren:** Upstream client blijft compatible.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I24-24-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Staging deployment
**Uitvoeren:** Nieuwe poort en datasets, alleen read-only media, no replacement Jellyfin.
**Verifiëren:** Testaccounts/episodes spelen.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I24-24-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. TV & mobiel test
**Uitvoeren:** Android/iOS/TV/web test matrix, streaming, offline downloads where implemented.
**Verifiëren:** Geen playback regressies.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I24-24-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Pilot en rollback
**Uitvoeren:** Canary users, 24-48h logs, old Jellyfin remains intact.
**Verifiëren:** Terug naar oude player met oorspronkelijke watch state.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I24-24-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
NIET huidige Jellyfin verwijderen. De fork is een bouwproject, geen bestaande downloadbare image.

## 6. Upstream documentatie en broncontrole
02_BOUWPLANNEN/B03_UNIVERSELE_MEDIASERVER.md

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.