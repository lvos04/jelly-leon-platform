# MediaGuard Ultimate — installatie, update, test en herstel

**Rol:** Bestaande primaire download/bibliotheek/AV1-app.  
**Aanpak:** Upgrade alleen na versiecontrole.  
**Bron:** https://github.com/lvos04/media-guard-ultimate.  
**Versiebeleid:** Release 1.2.2, image ghcr.io/lvos04/mediaguard-core:1.2.2-aio (controleer echte registry/tag).

## 1. Vooraf controleren
MediaGuard draait mogelijk al; huidige tags, datasets, poorten, ACL en backup verifiëren.

**Toegang / netwerk:** Bestaande poort 30080, interne arr 38989/38990/37878/37879 — met huidige status vergelijken.

**Data / mounts:** /config, /work en /data; originele media niet verplaatsen.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Release en huidige draaiende versie bepalen
**Uitvoeren:** Compare TrueNAS installed tag/digest met GitHub v1.2.2 en CHANGELOG; update niet vanzelf.
**Verifiëren:** Installed en latest beide bekend, verschillen geregistreerd.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I03-03-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Datamappen inspecteren
**Uitvoeren:** Controleer /config, /work, /data mounts, dataset pool, apps uid, vrije ruimte en /dev/dri A310 toegang.
**Verifiëren:** Geen recursieve chmod/chown op bibliotheek, GPU test slaagt.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I03-03-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Back-up inclusief forks
**Uitvoeren:** Gebruik MediaGuard back-up en bijpassende ZFS-snapshot van appdata, encoder en vier arr forks; bronsecret encrypted.
**Verifiëren:** Consistente herstelbundle op teststack hersteld.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I03-03-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Veilige update naar 1.2.2
**Uitvoeren:** Na green gate: Custom App pin `ghcr.io/lvos04/mediaguard-core:1.2.2-aio`; stop grace >=60s waar ondersteund; host network alleen als huidige architectuur dat gebruikt en veiligheid bekeken.
**Verifiëren:** Core, web, recovery, encoder, forks healthy na migratie.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I03-03-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Seerr/Prowlarr en clients
**Uitvoeren:** Test Seerr routes naar LAN-IP en juiste port voor vier instanties, SAB categories, indexers en episode requests.
**Verifiëren:** Geen verdwijnende of dubbele downloads.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I03-03-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. AV1 en noodstop
**Uitvoeren:** Test één toegestane proefencode en herstel; VMAF, HDR/DV integriteit, GPU-worklane, pauze op playback, noodstop.
**Verifiëren:** Origineel behouden bij falende kwaliteitsmeting; geen ongeoorloofd bestandverlies.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I03-03-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 7. Android meldingen
**Uitvoeren:** Installeer eigen release APK als gewenst, test pairing, push, dagoverzicht, offline freshness.
**Verifiëren:** Meldingen op telefoon en unpair/revoke werken.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I03-03-07`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 8. Hub-API integratie
**Uitvoeren:** Maak viewer-sleutel en read-only probe, gedeelde correlatie; MSO allowed writes blijven leidend.
**Verifiëren:** Hub toont status gelijk MediaGuard, geen owner data veranderd.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I03-03-08`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Let op v1.2.2 nieuwe background monitor/watchdog en rollback/backups; niet opnieuw bouwen. Release tested in containers, echter live versie op NAS moet gemeten worden.

## 6. Upstream documentatie en broncontrole
https://github.com/lvos04/media-guard-ultimate

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.