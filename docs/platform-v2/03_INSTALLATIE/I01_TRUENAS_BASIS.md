# TrueNAS SCALE basis — installatie, update, test en herstel

**Rol:** Platformhost en appbeheer.  
**Aanpak:** Behouden en valideren.  
**Bron:** https://www.truenas.com/docs/scale/.  
**Versiebeleid:** Versie van echte NAS eerst uitlezen; 24.10+ Docker-appbasis, API versiegebonden.

## 1. Vooraf controleren
Poolnaam, TrueNAS-versie, app network, datasetlijst, schijfopzet, vrije ruimte, UPS/SMART status inventariseren.

**Toegang / netwerk:** TrueNAS UI alleen LAN/VPN, niet publiek.

**Data / mounts:** Bestaande pools/datasets onaangeroerd; aanvullende appdata datasets onder gekozen <POOL>.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Systeem inventariseren
**Uitvoeren:** System > General, Storage > Pools/Datasets en Apps vastleggen; exporteer geschoonde screenshots en geïnstalleerde appversies.
**Verifiëren:** Versie, pool, API-documentatie en bestaande appstatus geregistreerd.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I01-01-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Snapshot en back-up gate
**Uitvoeren:** Voordat nieuwe apps worden gedeployed, van kritieke appdata consistente database-export plus snapshot en OneDrive-restoreproef.
**Verifiëren:** Minimaal een app-DB en foto terug te zetten.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I01-01-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. GPU identificatie
**Uitvoeren:** Gebruik `ls -l /dev/dri`, `stat -c "%n %g %G" /dev/dri/*` en werkende hardware-transcode in bestaand MediaGuard/Jellyfin; noteer hardwaremapping.
**Verifiëren:** A310 en UHD 770 apart, juiste rendernode en permissies bekend.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I01-01-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. JSON-RPC API read-only toegang
**Uitvoeren:** Maak user-linked API-key van minimaal noodzakelijke rol; verbind over HTTPS naar versioned WebSocket JSON-RPC; niet oude REST endpoints gebruiken.
**Verifiëren:** Versie, pools, apps read-only opvraagbaar en key revocable.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I01-01-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Bestaande app inventaris
**Uitvoeren:** Koppel huidige installaties aan data- en netwerkoverzicht, verander niets; controleer poortconflict.
**Verifiëren:** Catalogus laat Jellyfin, MediaGuard, MSO en overige apps zien.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I01-01-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Netwerkroutes verifiëren
**Uitvoeren:** Behoud Tailscale en Cloudflared; zet geen NAS UI open via internet.
**Verifiëren:** NAS console niet bereikbaar vanaf internet zonder VPN.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I01-01-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 7. Installatie voorbereiden
**Uitvoeren:** Maak datasets per nieuwe app pas na akkoord over naming, quotas, ACLs; vermijd recursive chown van mediaschijf.
**Verifiëren:** Correct owner op nieuwe datasets zonder bestaande data-effecten.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I01-01-07`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 8. Baseline hardware monitoring
**Uitvoeren:** CPU load, RAM ARC, disks SMART, GPU, netwerk, pool health verzamelen.
**Verifiëren:** Backpressure drempels en reserve bekend.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I01-01-08`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Geen sudo-root of docker.sock-mount voor nieuw webdashboard. REST API verwijderd in nieuwere TrueNAS: versiegebonden JSON-RPC gebruiken.

## 6. Upstream documentatie en broncontrole
https://www.truenas.com/docs/scale/

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.