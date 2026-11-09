# SABnzbd — installatie, update, test en herstel

**Rol:** Bestaande Usenet-downloadclient.  
**Aanpak:** Behouden.  
**Bron:** https://sabnzbd.org/wiki/.  
**Versiebeleid:** Huidige versie uit API.

## 1. Vooraf controleren
Providercredits en category labels veilig behandelen.

**Toegang / netwerk:** Bestaande UI-poort, mogelijk 8080, bevestigen.

**Data / mounts:** Incomplete/complete en config mounts.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Categorie inventaris
**Uitvoeren:** MediaGuard tv/tv-anime/movies/movies-anime en MSO mso-* categories verifiëren.
**Verifiëren:** Geen verkeerde * imports.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I17-17-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Server status en retry
**Uitvoeren:** Verschil tussen auth, retention, unpack en missing articles via MSO uitlezen.
**Verifiëren:** Mislukkingen correct gelabeld.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I17-17-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Veilige settings
**Uitvoeren:** MSO voor serverprioriteit; wijzigingen preview/readback/rollback; laatste werkende server beschermd.
**Verifiëren:** Download blijft mogelijk bij 1 provider storing.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I17-17-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Path mappings
**Uitvoeren:** MediaGuard /data downloads en SAB complete path op dezelfde filesystem/hardlink waar beoogd.
**Verifiëren:** Import werkt zonder verkeerde kopieën.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I17-17-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Backup config
**Uitvoeren:** Snapshot en export van keys en settings encrypted.
**Verifiëren:** Clientherstel toont dezelfde serverlijst.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I17-17-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Health en update
**Uitvoeren:** Test echte API versie met MSO; geen blind retries.
**Verifiëren:** Een eind-tot-eind test download/categorie verwerkt.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I17-17-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Geen eigen Usenet-provider bouwen of 'gratis server' claimen zonder eigen nieuwsserver en retentiebron.

## 6. Upstream documentatie en broncontrole
https://sabnzbd.org/wiki/

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.