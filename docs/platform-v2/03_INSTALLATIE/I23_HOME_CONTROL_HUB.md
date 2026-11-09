# Home Control Hub deployment — installatie, update, test en herstel

**Rol:** Nieuw eigen managementplatform.  
**Aanpak:** Bouwen volgens B01, daarna installeren.  
**Bron:** 02_BOUWPLANNEN/B01_HOME_CONTROL_HUB.md.  
**Versiebeleid:** Nog geen werkende image: te bouwen, tags pinned via eigen CI.

## 1. Vooraf controleren
Backups, JSON-RPC adapter, monitoring, risk engine en auth minimaal klaar.

**Toegang / netwerk:** Privileged beheerroute uitsluitend LAN/Tailscale, frontend achter TLS.

**Data / mounts:** Appdata, Postgres, audit, vault, executor key gescheiden.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. GitHub repo en CI
**Uitvoeren:** Maak onafhankelijke repo, reproducible build, tests, license, release workflow, docs.
**Verifiëren:** Eerste release test groen in disposable environment.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I23-23-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Data en netwerk
**Uitvoeren:** TrueNAS datasets, internal bridge, HTTPS proxy, no exposed db or docker socket.
**Verifiëren:** Security smoke: app draait zonder root in web.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I23-23-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. Read-only start
**Uitvoeren:** Import bestaande TrueNAS apps als read-only discovery.
**Verifiëren:** Dashboard correct zonder mutatie.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I23-23-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. SSO en rollen
**Uitvoeren:** Authentik OIDC of secure temporary local login, break-glass en viewer/operator/admin.
**Verifiëren:** Viewer kan geen R1/R2 actions.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I23-23-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. Limited executor
**Uitvoeren:** Only allowlisted actions with policy, audit, previews, before/after.
**Verifiëren:** Unknown actions rejected.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I23-23-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Live connectors
**Uitvoeren:** MG, MSO, my_house, Beszel, Uptime, DNS en workflow via API, stale states.
**Verifiëren:** No owner writes by Hub.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I23-23-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 7. Release en rollback
**Uitvoeren:** Image pin, clone migration, backup and rollback tests on dedicated dataset.
**Verifiëren:** Geen downtime zonder expliciet plan.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I23-23-07`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 8. UX mobiele acceptatie
**Uitvoeren:** Complete walkthrough phone/tablet/desktop, a11y, NL, zero empty screens.
**Verifiëren:** E2E user flows pass.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I23-23-08`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
NIET direct te installeren vóór bouw; hier staat uitsluitend install-/releaseacceptatie voor de bouwer.

## 6. Upstream documentatie en broncontrole
02_BOUWPLANNEN/B01_HOME_CONTROL_HUB.md

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.