# Bazarr — installatie, update, test en herstel

**Rol:** Bestaande NL ondertiteling.  
**Aanpak:** Behouden totdat MediaGuard parity aangetoond.  
**Bron:** https://www.bazarr.media/.  
**Versiebeleid:** Huidige versie en bronnen installeren niet opnieuw.

## 1. Vooraf controleren
MediaGuard Subtitle hook config en Jellyfin audio/sub settings.

**Toegang / netwerk:** Bestaande UI LAN-only.

**Data / mounts:** Config en subtitles directories.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Inventaris
**Uitvoeren:** Beschikbare providers, taalprioriteit Nederlands, sync, wrong track detection.
**Verifiëren:** Geen regressie van NL ondertitels.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I20-20-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. MediaGuard integration
**Uitvoeren:** Controleer MediaGuard subtitle adapter reeds doet en waar Bazarr nog uniek is.
**Verifiëren:** Duplicate subtitle downloads vermijden.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I20-20-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Retentietest
**Uitvoeren:** Test SRT/ASS sync, anime, multi-audio and DV files.
**Verifiëren:** Jellyfin afspeelbare subtitle selection.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I20-20-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Backup
**Uitvoeren:** Exporteer provider credentials en profiles.
**Verifiëren:** Restore verificatie.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I20-20-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Decommission gate
**Uitvoeren:** Pas als MediaGuard+Jellyfin alle cases via tests dekken, uitschakelen zonder data te verwijderen.
**Verifiëren:** Elke test slaagt, rollback beschikbaar.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I20-20-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Niet meteen verwijderen omdat ondertitels al door MediaGuard beheerd kunnen worden.

## 6. Upstream documentatie en broncontrole
https://www.bazarr.media/

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.