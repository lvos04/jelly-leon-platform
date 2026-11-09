# qBittorrent — installatie, update, test en herstel

**Rol:** Bestaande torrentclient.  
**Aanpak:** Behouden.  
**Bron:** https://www.qbittorrent.org/.  
**Versiebeleid:** Huidige WebAPI versie meten.

## 1. Vooraf controleren
Tracker/private policy en VPN bind interface bekend.

**Toegang / netwerk:** Bestaande LAN UI port vastleggen, torrent verkeer VPN policy.

**Data / mounts:** Config, incomplete, completed en seeding path.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Client inventory
**Uitvoeren:** Version, API, save paths, categories, share limits, tracker obligations vastleggen.
**Verifiëren:** Geen onverwachte move of delete.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I18-18-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. MSO policies
**Uitvoeren:** Read-only eerst, safe tuning en hardlink-awareness, private tracker guard.
**Verifiëren:** Geen private torrent policy breached.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I18-18-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Torrent executables preventie
**Uitvoeren:** MSO 1.5 exclude file names capability tegen echte qBit versie testen.
**Verifiëren:** Geen false exclusion van media.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I18-18-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Netwerk/VPN binding
**Uitvoeren:** Controleer adapterbinding, DNS leak checks en port reachability naar gewenste trackers.
**Verifiëren:** Geen publieke verkeerde interface.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I18-18-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Seeding en AV1 overlap
**Uitvoeren:** Hardlinks/seed state voorkomen incorrect rewrite door MediaGuard.
**Verifiëren:** Actieve torrent payload blijft intact.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I18-18-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Backup/update
**Uitvoeren:** Config snapshot en echte WebAPI contracttests.
**Verifiëren:** Geen torrents verloren na update.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I18-18-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Geen automatische torrent verwijdering, zelfs niet als AI ‘opruimen’ voorstelt; vereis expliciete goedkeuring bij verdachte bestanden.

## 6. Upstream documentatie en broncontrole
https://www.qbittorrent.org/

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.