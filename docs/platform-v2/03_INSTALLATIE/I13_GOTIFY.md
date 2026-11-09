# Gotify — installatie, update, test en herstel

**Rol:** Pushmeldingen en onafhankelijke alerts.  
**Aanpak:** Nieuw installeren.  
**Bron:** https://gotify.net/docs/install.  
**Versiebeleid:** Pin actuele officiële Docker image.

## 1. Vooraf controleren
Telefoon toegang, push app/browser notificatie toestemming.

**Toegang / netwerk:** LAN/VPN-only HTTPS via reverse proxy.

**Data / mounts:** Data directory persistent.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Container en persistence
**Uitvoeren:** Official container met mounted data, admin password en minimum rechten.
**Verifiëren:** Health en persistentieniveau correct.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I13-13-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Apps en tokens
**Uitvoeren:** Maak per bron scoped token (Uptime Kuma, Hub, backup); sla secrets veilig op.
**Verifiëren:** Geen gedeelde admin token.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I13-13-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Mobiel ontvangen
**Uitvoeren:** Configureer Android client of web notifications; test testmelding.
**Verifiëren:** Outage push arriveert.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I13-13-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Alertbeleid
**Uitvoeren:** Severity en rate limiting, hersteld melding, quiet hours zonder kritisch incident te onderdrukken.
**Verifiëren:** Geen dagelijkse spam.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I13-13-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Back-up en herstel
**Uitvoeren:** Export config en encrypted tokens.
**Verifiëren:** Nieuwe Gotify stack kan meldingen herstellen.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I13-13-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Bij complete NAS-uitval kan lokaal gehoste Gotify niet melden. Voor out-of-band verifiëren of eigen notificatiedienst buiten NAS beschikbaar is; mini-pc niet gebruiken.

## 6. Upstream documentatie en broncontrole
https://gotify.net/docs/install

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.