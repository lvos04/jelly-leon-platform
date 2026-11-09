# Media Source Optimizer — installatie, update, test en herstel

**Rol:** Bron-, provider-, torrent- en downloadoptimalisatie.  
**Aanpak:** Bestaande app behouden, v1.6 gate.  
**Bron:** https://github.com/lvos04/media-source-optimizer.  
**Versiebeleid:** release/VERSION was 1.5.0 op 9 nov; 1.6.0 commits gemerged, niet als productie-tag aannemen.

## 1. Vooraf controleren
Prowlarr, SABnzbd, qBittorrent en MediaGuard URL/API read scopes bekend.

**Toegang / netwerk:** Documentatie port 38081 TCP, controleer host conflict.

**Data / mounts:** Één dataset naar /appdata, optionele /media readonly.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Installed tag vastleggen
**Uitvoeren:** Controleer TrueNAS-app, GHCR image, GitHub release/VERSION, CI, migratie 0600; geen onuitgebrachte tag installeren.
**Verifiëren:** Verschil 1.5.0/1.6 kandidaat duidelijk.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I04-04-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Image en dataset controleren
**Uitvoeren:** Aanbevolen image `ghcr.io/lvos04/media-source-optimizer:1.5.0` als werkelijke release; /appdata host path, geen privileges, port 38081.
**Verifiëren:** Start en persistentie op reboot zonder permission issues.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I04-04-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Koppeling MediaGuard
**Uitvoeren:** Gebruik scoped X-Api-Key viewer/ operator, GET routes en beperkte search; api capabilities en emergency stop testen.
**Verifiëren:** MSO connected en als MediaGuard offline is zelfstandige modus.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I04-04-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Prowlarr en clients live
**Uitvoeren:** Test categories Newznab/Torznab zonder loops, SABnzbd server status, qBit private tracker rules.
**Verifiëren:** Geen recursive search, geen verkeerde auto-delete.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I04-04-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Autopilot policies
**Uitvoeren:** Read-only baseline, vervolgens veilige acties per categorie activeren; approve risicovolle wijzigingen en hold last server.
**Verifiëren:** Geen files automatisch verwijderen, statuses en rollback zichtbaar.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I04-04-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. 1.6.0 veranderingen accepteren
**Uitvoeren:** Test echte SAB foutclassificatie, category * probe, footprint scherm, drift-safe rollback en metric uitkomsten tegen werkelijke services.
**Verifiëren:** Proefrapport met echte serviceversies voordat 1.6 live gaat.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I04-04-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 7. MSO backup/migrate
**Uitvoeren:** Voer PostgreSQL dump + image pin + Restore-test op aparte stack uit.
**Verifiëren:** Geen dubbele jobs/herzoek na restore.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I04-04-07`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 8. Hub koppelen
**Uitvoeren:** Lees KPI, alerts en voorstellen via scoped connector; Hub stuurt geen direct SAB/qBit writes.
**Verifiëren:** Owner matrix intact en geen race bij gelijktijdige goedkeuring.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I04-04-08`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
V1.6.0 nog ontwikkelkandidaat zolang release/VERSION en registry niet overeenkomen; zelfs geslaagde tests in mocks zijn geen garantie voor live TrueNAS.

## 6. Upstream documentatie en broncontrole
https://github.com/lvos04/media-source-optimizer

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.