# Uptime Kuma — installatie, update, test en herstel

**Rol:** URL/port health en externe watchdog.  
**Aanpak:** Nieuw installeren.  
**Bron:** https://github.com/louislam/uptime-kuma.  
**Versiebeleid:** Pin huidige stabiele image/tag; breaking changes 1.x/2.x navragen.

## 1. Vooraf controleren
Listeer kritieke apps en test-URL per service.

**Toegang / netwerk:** LAN/VPN-only (standaard apppoort kan conflicteren).

**Data / mounts:** Persistente /app/data onder observability dataset.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Official image en dataset
**Uitvoeren:** Custom App met officiële gepinde image, persistent appdata, geen host network zonder reden.
**Verifiëren:** UI en DB houden config na restart.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I08-08-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Authenticatie
**Uitvoeren:** Maak admin, kies sterke sleutel en proxy/SSO alleen ondersteund.
**Verifiëren:** Geen open publiek admin login.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I08-08-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Health monitors
**Uitvoeren:** HTTP(S)/TCP probes voor Hub, TrueNAS UI, MediaGuard, MSO, my_house, Jellyfin, backup status.
**Verifiëren:** Elk endpoint krijgt juiste expected status + timeout.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I08-08-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Notification buiten Hub
**Uitvoeren:** Koppel Gotify of mobiel push alternatief; bron outage moet alert sturen.
**Verifiëren:** Stop de Hub en meldingen komen nog binnen.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I08-08-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Flapping en maintenance
**Uitvoeren:** Alert na N fouten en hersteldmelding, onderhoudsvenster.
**Verifiëren:** Geen spam bij geplande updates.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I08-08-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Backup en herstel
**Uitvoeren:** Exporteer config en test restore.
**Verifiëren:** Geen monitors kwijt bij migratie.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I08-08-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Kuma op dezelfde NAS kan NAS-stroomuitval niet signaleren. Voor echt out-of-band toezicht is externe host of dienst nodig; mini-pc blijft voor video en krijgt hier geen nieuwe apps.

## 6. Upstream documentatie en broncontrole
https://github.com/louislam/uptime-kuma

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.