# my_house Smart Home Control — installatie, update, test en herstel

**Rol:** Eén eigen klimaat-, verlichting- en energieapp.  
**Aanpak:** Installeer met echte apparaatgate.  
**Bron:** https://github.com/lvos04/my_house.  
**Versiebeleid:** v0.1.0 images my_house-api:0.1.0 en my_house-web:0.1.0; lees actuele repo docs.

## 1. Vooraf controleren
TrueNAS versie; Hue bridge, Gree modellen, HomeWizard lokale API, Zigbee dongle indien gekocht.

**Toegang / netwerk:** Documentatie HTTP 8088 als LAN-only; Cloudflare Tunnel optioneel.

**Data / mounts:** Compose stack met PostgreSQL, MQTT, API, worker, web, optioneel Zigbee2MQTT; dataset /mnt/<POOL>/apps/smart-home.

**Preflight-regels (niet overslaan):** TrueNAS-release en Docker-appconfig controleren; data-eigenaar bepalen; bestaande install herkennen; bezette ports en container netwerken vergelijken; herstelbare backup + exacte image pin eisen; secrets nooit in logs of screenshots; test op stille omgeving vóór productie.

## 2. Stappen in uitvoeringsvolgorde

### Stap 1. Dataset en repository
**Uitvoeren:** Maak apps/smart-home data+secrets; haal code/compose van gepinde tag en bekijk voor uitvoering; run install.sh voor secrets met expliciete bevestiging.
**Verifiëren:** Secrets niet in Git of public paste; ACL correct.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-01`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 2. Formulier standaard Custom App zonder Zigbee/tunnel
**Uitvoeren:** Vul het formulier voor de standaard Custom App in volgens deploy/truenas/CUSTOM_APP.md van my_house. Neem de vrijgegeven image en digest over.
**Verifiëren:** Het formulier bevat één opslagkoppeling naar de eigen appdataset en één vrije hostpoort. Zigbee en tunnel staan uit.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-02`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 3. TrueNAS Install via formulier standaard Custom App
**Uitvoeren:** Sla het formulier voor de standaard Custom App op. Vergelijk de opgehaalde digest van de alles-in-één-image met het releasebewijs.
**Verifiëren:** TrueNAS toont de app als gezond na de ingebouwde healthcheck en de portal opent.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-03`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 4. Hue koppeling
**Uitvoeren:** Hue bridge lokaal gekoppeld na druk op knop, lees scenes/knoppen, één testlicht zonder dubbele groep.
**Verifiëren:** Schakelaar + readback klopt.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-04`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 5. HomeWizard rollen
**Uitvoeren:** Enable Local API; P1 → huis totaal, Energy Socket → boiler read-only, 1-fase meter → airco totaal.
**Verifiëren:** Energie cumulatieve tellers en kwaliteit zichtbaar; boiler heeft geen write.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-05`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 6. Gree compatibiliteit
**Uitvoeren:** Koppel Gree+ 2.4G, ontdek/bind/read, als unsupported geen actuation; test conflict gedeelde buitenunit.
**Verifiëren:** Bij uitval valt regelaar terug, remote OFF blijft OFF.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-06`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 7. Zigbee optioneel
**Uitvoeren:** Installeer eerst dongle/sensor; `ls -l /dev/serial/by-id/`; gebruik adapter driver overeenkomend hardware, add zigbee compose flag.
**Verifiëren:** MQTT connect en temperatuursensor met battery/stale alarms.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-07`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

### Stap 8. Android en restore
**Uitvoeren:** Download gepinde APK uit eigen GitHub release, QR pair, token revoke; export encrypted back-up en herstel op tweede stack.
**Verifiëren:** Telefoon en web werken na reboot; verhuizing zonder dataverlies.
**Bewijs:** noteer NAS-versie, appimage digest, datum, gemaskeerde output, eventuele screenshot en status in Excel (ID `I05-05-08`).
**Als dit mislukt:** stop hier; maak oorzaak en rollbackpad inzichtelijk. Geen volgende stap starten zolang deze test of de back-upgate rood is.

## 3. Acceptatie en oplevering
Echte TrueNAS-app start, authenticatie, persistente data na herstart, firewall/poort, metrics, backup+restore en rolrechten controleren.

- **GO:** elke stap heeft bewijs en geen kritisch defect; health na herstart, functionaliteit in eigen UI en Hub, DB persistent, documenten geactualiseerd.
- **NO-GO:** geen restorepoint, onbekend secret ownership, ongeteste dataset/poort, falende health, incomplete migratie, onveilige WAN-exposure of datawijziging buiten eigenaar.

## 4. Terugdraaien / verwijderen
1. Schakel nieuwe automation/ingest uit. 2. Bewaar audit en logs. 3. Stop nieuwe container/route, herstel oude version-pinned image én passend databestand/snapshot waar schema is veranderd. 4. Controleer credentials, clients en content integrity. 5. Maak incidentrapport met wat wel/niet is teruggezet. 6. Bij decommission: data alleen na eigen, afzonderlijke bevestiging verwijderen.

## 5. Let op
Niet claimen dat my_house al met echte apparaten getest is. Boiler-schakelverbod zowel in API als via Hub/AI afdwingen.

## 6. Upstream documentatie en broncontrole
https://github.com/lvos04/my_house

Re-run broncheck vlak vóór uitvoering: deze installatiebeschrijving is een plan en geen claim dat er reeds een live installatie is gevalideerd.