# Beszel — installatie, update, test en herstel

**Rol:** Lichtgewicht servermonitoring.  
**Aanpak:** Nieuw installeren.  
**Bron:** https://beszel.dev/guide/installation.  
**Versiebeleid:** Kies actuele officiële gepinde image op installatiedatum.

## 1. Vooraf controleren
TrueNAS network en CPU/GPU metrics coverage bekend.

**Toegang / netwerk:** Bij voorkeur LAN-only; poort vooraf met TrueNAS inventory bepalen.

**Data / mounts:** Dataset apps/observability/beszel.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Meetdekking en port check
**Uitvoeren:** Vergelijk Beszel app/agent support voor TrueNAS, Docker, ZFS en Intel GPU.
**Verifiëren:** Monitor kan de NAS werkelijk uitlezen, ontbrekende GPU caps bekend.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I07-07-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Datasets en image
**Uitvoeren:** TrueNAS Custom App of officieel compose manifest, versie pinned, data persistent, geen onnodige privileges.
**Verifiëren:** Container healthy; geen publieksport.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I07-07-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Hub/Agent pairing
**Uitvoeren:** Gebruik officiële onboarding/key, private LAN traffic en beperkt privileges.
**Verifiëren:** Metrics verschijnen met juiste hostnaam en timestamp.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I07-07-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. ZFS/ARC validatie
**Uitvoeren:** Vergelijk pool capacity en ARC metrics met TrueNAS zelf; GPU indien ondersteund.
**Verifiëren:** Geen misleidende RAM-in-use grafieken.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I07-07-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Alerts instellen
**Uitvoeren:** Host offline, disk full, CPU temp en memory pressure met cooldown.
**Verifiëren:** Alert door uitval geactiveerd.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I07-07-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Backup en Hub connector
**Uitvoeren:** Backup Beszel DB/keys; Hub consume read API of metrics.
**Verifiëren:** Na reboot historie bewaard en geen dubbele host.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I07-07-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Beszel agent en hub op dezelfde NAS kunnen de NAS zelf niet goed als externe watchdog bewaken. Onafhankelijke Uptime Kuma naast Beszel houden; bij volledige NAS-uitval geen push vanuit dezelfde NAS.

## 6. Upstream documentatie en broncontrole
https://beszel.dev/guide/installation

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.