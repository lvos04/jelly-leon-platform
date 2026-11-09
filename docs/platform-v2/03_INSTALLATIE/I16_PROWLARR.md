# Prowlarr — installatie, update, test en herstel

**Rol:** Bestaande indexer- en application synchronisatie.  
**Aanpak:** Behouden.  
**Bron:** https://wiki.servarr.com/prowlarr.  
**Versiebeleid:** Huidige versie en catalog dependencies uit NAS.

## 1. Vooraf controleren
MediaGuard vier arr forks en MSO connector controleren.

**Toegang / netwerk:** Bestaande poort (vaak 9696) verifiëren.

**Data / mounts:** Config DB dataset persistent.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Read-only inventaris
**Uitvoeren:** Exporteer indexer stats/tags/Applications en API key veilig.
**Verifiëren:** Geen onverwachte tags aangepast.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I16-16-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Sync eigenaarschap
**Uitvoeren:** MediaGuard beheert anime/normal tags en arr apps; MSO alleen eigen indexer na approval.
**Verifiëren:** Geen tweevoudige reconciler.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I16-16-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Backups
**Uitvoeren:** App config backup en snapshot met credentials encrypted.
**Verifiëren:** Restore test slaagt.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I16-16-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. MSO integration
**Uitvoeren:** Newznab/Torznab loops en capabilities test.
**Verifiëren:** Cycle detection verhindert verzoekloop.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I16-16-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Health alerts
**Uitvoeren:** Connection errors, indexer failures en rate limit naar Hub/MSO.
**Verifiëren:** Alert met bronstatus.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I16-16-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Upgrade gate
**Uitvoeren:** Release compat met MediaGuard forks en MSO.
**Verifiëren:** Zoeken en RSS na update goed.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I16-16-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Geen tweede indexer manager bouwen.

## 6. Upstream documentatie en broncontrole
https://wiki.servarr.com/prowlarr

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.