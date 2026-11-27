# Integrale kernrook

Deze omgeving is geen installatiemethode. De kern draait de Hub, executor en lifecycle uit lokale broncode, samen met de TrueNAS-simulator. De rookproef leest de drie ready-antwoorden en het simulatorprofiel, test de verbinding vanuit de Hub en zoekt de actieve lifecycle-registratie op in het dienstenregister. De ontwikkeltests gebruiken de [ontwikkelcompose](dev/compose.yaml).

## De kern draaien

Voer de opdrachten uit vanuit de platformrepo met de Python-omgeving uit `make venv`. Bij een aparte kloon wijst `INTEGRAAL_BRONMAP` naar de map met de naburige repositories. De runner gebruikt standaard de bovenliggende map van de platformrepo. Op de gedeelde bouwmachine wijst `KETEN` naar de eigen ketenmap en `WERKMAP` naar de gedeelde werkmap.

```bash
mkdir -p "${KETEN:?}/tmp" "$KETEN/cache"
export TMPDIR="$KETEN/tmp" XDG_CACHE_HOME="$KETEN/cache"
export DOCKER_CONFIG="$XDG_CACHE_HOME/docker" BUILDX_CONFIG="$XDG_CACHE_HOME/docker/buildx"
mkdir -p "$BUILDX_CONFIG"
export INTEGRAAL_BRONMAP="${WERKMAP:?}"
flock "$WERKMAP/.lokaal/docker-build.lock" nice -n 10 .venv/bin/python e2e/integral/run.py --bouw --profiel kern
flock "$WERKMAP/.lokaal/stack.lock" nice -n 10 make test-integraal
```

`--bouw --profiel kern` beperkt de bouw tot de vier kernimages. Zonder profielfilter doorloopt `--bouw` alle bouwbare diensten, één image tegelijk. Het bouwprofiel staat op de foutregel als een vereiste image ontbreekt. Lokale tags zijn per checkout herkenbaar. Met `INTEGRAAL_TAG` kan een keten een eigen tag kiezen voor zowel de bouw als de proef. Controleer vóór een bouw of proef de beschikbare capaciteit met `free -m` en `df -h /`. De grenzen op de gedeelde machine zijn 3.000 MiB beschikbaar RAM en 15 GiB vrije schijf. Bij meer dan 90% gebruikte swap is de ondergrens 6.000 MiB RAM.

| Selectie | Wat draait |
|---|---|
| `--groep rook` | Alleen de gekoppelde kern |
| `--groep hub-veiligheid` | EX16–EX21, elk met een verse gekoppelde kern |
| `--scenario EX01,EX02` | De uitvalproeven voor MediaGuard en MSO |
| `--groep NAAM` | De bestanden die deze groep in hun metadata hebben |
| `--alles` | Alle aanwezige scenariobestanden, één voor één |
| `--bewaar` | Behoudt het project van de laatste geselecteerde proef voor onderzoek |

Geef de selectie aan Make mee als `INTEGRAAL_ARGS='--groep rook'`. Elke proef krijgt een nieuwe projectnaam, een intern netwerk, eigen volumes en verse koppelgegevens. De testclient voert zijn verzoeken uit vanuit de Hub-container. Het certificaat van de simulator ontstaat bij de start en de Hub controleert de vingerafdruk. Bij een bezette standaardpoort vraagt de runner Docker om een vrije loopbackpoort. Een expliciete `INTEGRAAL_<DIENST>_POORT` legt de hostpoort vast.

## Profielen en scenario's

| Bouwprofiel | Diensten |
|---|---|
| kern | hub, executor, truenas, lifecycle |
| media | mediaguard, mso. De invoerimages voor MediaGuard komen uit de lokale encoder-, web- en forkbronnen. |
| mso | MSO met eigen PostgreSQL in het projectvolume, plus de downloadsimulators uit de vastgezette MSO-mockbron. |
| downloadketen | arr-sim en sabnzbd-sim gebruiken dezelfde gepinde MSO-testimage en de lokale simulatorcode. |
| media-inhoud | MediaGuard-kern en herstelwerker uit `apps/core`, plus Seerr en Sonarr uit de arr-simulator. De diensten delen een projectgebonden testbibliotheek. |
| opslagmedia | Emby-protocolsimulator voor de opslagproeven. Deze proeven gebruiken ook de MediaGuard-kern en de arr-simulator uit media-inhoud. |
| media-kwaliteit | CPU-encoder uit `services/encoder` met FFmpeg en libvmaf. |
| huis | my-house en home-control-hub voor EX24–EX30 |
| huis-browser | Chromium voor de apparaatkaart in EX27 |
| appstore, onedrive, herstel | Respectievelijk appstore, onedrive en truenas-twee |
| derden | beszel-sim, kuma-sim, gotify-sim en authentik-sim delen één image |
| meldingen | node-red, uptime-kuma en gotify op de publieke digest uit elk installatiemanifest |
| zoeken, mediaserver | Versie 2. De mediaserver gebruikt de vooraf gebouwde `jelly-leon:p6` uit S-P6-30. |

Een nieuw bestand `scenarios/EXnn_naam.py` bevat een `test_*`-functie en een letterlijke dictionary `SCENARIO`. De velden `eigenaar`, `verwachting` en `groepen` zijn verplicht. In `diensten` staan de extra Compose-diensten voor dat scenario. De runner start de kern en precies die diensten met lokale images. Alleen bij een ontbrekende mediaserver slaat hij de daarvan afhankelijke proef over. Onbekende ID's, lege selecties en ontbrekende vereiste images geven een foutcode.

De fixture `kern` levert ingelogde clients en de gekoppelde simulator. Voor een extra testdienst geeft `kern.client(dienst, poort)` een interne client. `kern.simulatorfout(client, naam)` en `kern.verzoeklog(client)` bedienen de derden-simulators. Een scenario kan verwachte teksten van `pytest.skip` declareren in `overslagredenen`. Andere overslagen verwijzen naar de regel met de testvoorwaarde.

### Uitval en noodstop

Bouw `kern`, `media` en `downloadketen` onder de bouwlock. Selecteer de vier scenario’s onder de stacklock.

```bash
flock "$WERKMAP/.lokaal/stack.lock" nice -n 10 .venv/bin/python e2e/integral/run.py --scenario EX01,EX02,EX06,EX07
```

Herhaal met `INTEGRAAL_ZONDER_STORING=1` voor de tegenproeven. Dezelfde verwachtingen moeten dan falen. In elk dossier
geeft `voortgang.json` de aantallen jobs, jobhistorie, audit- en kijkregels en het aantal verdwenen oorspronkelijke
sleutels. De proef controleert ook de titel, kijker en het kijktijdstip. Bij EX07 levert een proefworker de
encoderwaarnemingen voor de MediaGuard-bewaking. De echte GPU valt onder N-P6-09.

### MediaGuard-inhoud

Bouw de profielen `kern`, `media-inhoud` en `media-kwaliteit` met de bouwopdracht hierboven. De vijf proeven draaien met één selectie.

```bash
flock "$WERKMAP/.lokaal/stack.lock" nice -n 10 .venv/bin/python e2e/integral/run.py --scenario EX03,EX04,EX05,EX09,EX37
```

Elke proef schrijft `inhoud.json` met de SHA-256 per proefbestand en de voortgang per bestaande MediaGuard-taak. EX05 leest ook de controlesommen op het moment dat Sonarr het verwijderverzoek ontvangt. De herstelwerker behandelt het terugzetverzoek. EX09 gebruikt de native vervangmodus van de CPU-encoder op twee kopieën van dezelfde clip. De storingsvrije controle levert een AV1-bestand op.

### Node-RED en meldingen

De drie proeven gebruiken de bouwprofielen `kern`, `derden` en `meldingen`. Start ze samen op de eigen teststack.

```bash
flock "$WERKMAP/.lokaal/stack.lock" nice -n 10 .venv/bin/python e2e/integral/run.py --scenario EX22,EX45,EX46
```

EX22 bewaakt de HTTP-gezondheid van lifecycle met de gepinde Uptime Kuma. De proef pauzeert lifecycle eerst met draaiende
Hub en herhaalt die onderbreking met volledig gestopte Hub. Tijdens de pauze is de dienst onbereikbaar en houdt de
container zijn netwerkadres. De executor leest de monitor en de berichten van de Gotify-simulator. Een tweede
meldkanaal loopt door een tijdelijke doorstuurder in de Hub-container. N-P6-04 toetst de ontvangst op een echte telefoon.

De negatieve proef stuurt ook het eerste opgeslagen meldkanaal via de Hub. Kuma ziet de dienst uitvallen. Beide
Gotify-kanalen ontvangen dan nul nieuwe uitvalmeldingen. EX22 geeft exitcode 1 en bewaart de tellingen in `metingen.json`.

```bash
INTEGRAAL_KUMA_VIA_HUB=1 flock "$WERKMAP/.lokaal/stack.lock" nice -n 10 .venv/bin/python e2e/integral/run.py --scenario EX22
```

In EX45 en EX46 verstuurt de officiële Node-RED-image een actieaanvraag via HTTP. Een testontvanger op loopback en een
nagebootste outbox van home-control-hub bezorgen die aanvraag aan de Hub. Alleen de outbox krijgt een eigen
diensttoken. EX45 controleert de weigering in het auditspoor. EX46 telt uitgevoerde acties en afzonderlijk ontvangen
Gotify-berichten voor vijf herleveringen en vijf verschillende events. De runner bewaart de tellingen in `metingen.json`.

### Huis en energie

Bouw de profielen `kern`, `huis` en `huis-browser` onder de bouwlock. Start daarna de zeven proeven onder de stacklock.

```bash
flock "$WERKMAP/.lokaal/stack.lock" nice -n 10 .venv/bin/python e2e/integral/run.py --scenario EX24,EX25,EX26,EX27,EX28,EX29,EX30
```

De huisproeven controleren het netwerk van de draaiende containers voordat een simulator start. Ze gebruiken de gepinde Gree-fixture uit de lokale my_house-bron en koppelen de binnenunits op naam met unicast. Bij EX27 controleert Chromium de apparaatkaart en verwerkt de schrijfconnector een ondertekend voorstel van een nagebootste assistent. De controle met een gewoon stopcontact gebruikt hetzelfde apparaat vóór het toewijzen van de boilerrol. EX28 volgt de kwartierstanden van de P1-simulator via de uur-export van my_house tot het dagrapport van B14, met een vaste UTC-ontvangsttijd. EX29 gebruikt een lokale MQTT-broker en wacht de ingestelde vijf minuten af. Het dossier bevat de meetwaarden in `huis.json`. De fysieke handelingen voor EX26 en EX30 staan in N-P6-06.

### Back-up, levenscyclus en appstore

Bouw eerst de kern en daarna de profielen voor deze zeven proeven. De herstelproef gebruikt dezelfde TrueNAS-image als de kern voor de tweede simulator.

```bash
for profiel in kern onedrive media-inhoud schema appstore upstream-git; do
  "$WERKMAP/.lokaal/stackslot" nice -n 10 .venv/bin/python e2e/integral/run.py --bouw --profiel "$profiel" || exit 1
done
"$WERKMAP/.lokaal/stackslot" nice -n 10 .venv/bin/python e2e/integral/run.py --scenario EX11,EX12,EX14,EX15,EX47,EX48,EX50
```

EX11, EX12 en EX14 gebruiken de gekoppelde Hub en executor met een geverifieerde proefexport. De OneDrive-simulator levert de taakstand die de Hub via de TrueNAS-inventaris inleest. EX14 herstelt de configuratie naar een eigen zandbakdataset en leest de actuele gegevens van de bron opnieuw uit.

De Hub-werker controleert ook de back-updekking van appgegevens. De drie proeven leveren daarvoor het synthetische MediaGuard-herstelrapport via een gekoppelde HTTPS-leesadapter en een bevestigd synthetisch Emby-archief met afzonderlijke kopie op het projectvolume. De fixtures komen uit de gebruikte Hub-bron. De Hub berekent zelf of de gate open kan.

EX15 start release 0.1.0 op de gepubliceerde digest en de lokale testimage om beurten op één projectvolume. Beide bronversies hebben schema 0003. Alleen de testimage bevat de extra migratie 0004. Het herstel selecteert de enige dump die deze upgrade toevoegde en controleert de vastgelegde SHA-256 vóór het laden en de wissel van de databasenamen. EX47 gebruikt de manifestfixtures van de appstore. EX48 voert de merge-, bundel- en overdrachtsstap uit de Forgejo-workflow van de fork uit tegen de lokale Git-simulator. De forkbron is alleen-lezen aangekoppeld.

EX14, EX15 en de voorcontrole van EX50 lezen per proefkijker de rechten, kijkgeschiedenis, favorieten, beoordelingen en spoorkeuzes via de lokale media-API. De MediaGuard-werker wacht tijdens het herstel met één bestaande bibliotheektaak op 37,5%. Na vrijgave synchroniseert dezelfde taak de twee proefafleveringen. De meetmomenten staan in `opslagmedia.json`.

EX50 registreert de HTTP-antwoorden van de verhuis- en verwijderpreview. Bij de verwachte 501 blijft de verhuizing geblokkeerd op B08-006. De meetwaarden staan per scenario in `metingen.json`.

## Bewijs en opruimen

In `e2e/integral/evidence/<run-id>/` staat per proef het resultaat met verwachting, eigenaar, datum, runnercommit, beschikbare image-ID's en broncommit of digest. Het verzoekoverzicht bevat methode, bekende route, HTTP-status en geselecteerde protocolvelden. Cookies, vrije logtekst en ruwe procesuitvoer blijven buiten het bewijs.

Voor EX-proeven staan de voorstellen in `EVIDENCE-voorstellen.json`. Een uitgevoerde `Sim` krijgt `Bezig` met `Sim-geslaagd` of `Sim-mislukt`. Geef bij `soort="Sim+"` of `"Echt"` ook `blokkade` met een toegestane `NIET GEVALIDEERD (...)`-waarde en `toelichting` met het concrete praktijkonderdeel, bijvoorbeeld een telefoon, tuner of tweede host. Het voorstel blijft dan `Geblokkeerd`. De rookproef heeft geen EX-ID.

Na succes, fouten en onderbreking ruimt de runner het project op. Bij `--bewaar` staat de eigen projectnaam in `proef.json`. Vul die naam in bij `EIGEN_PROJECT`.

```bash
flock "$WERKMAP/.lokaal/stack.lock" docker compose -f e2e/integral/compose.yaml -p "${EIGEN_PROJECT:?}" --profile '*' down -v
```

De gewone Forgejo-CI draait ook conformiteit tegen de Hub-branchekop en een eigen PostgreSQL. De workflow `integrale-kern.yml` start handmatig en bewaart het rookbewijs. Gebruik op een CI-runner met een gedeelde Docker-daemon de Forgejo-variabele `INTEGRAAL_LOCKMAP` voor de gedeelde lockmap. De volledige ronde hoort bij S-P6-22.

### Media Source Optimizer

Bouw de profielen `kern` en `mso`. Het MSO-profiel gebruikt de alles-in-één-image met een eigen PostgreSQL op het projectvolume. De runner pakt de testdiensten uit de commit in `e2e/simulators/mso-mocks.commit`.

```bash
flock "$WERKMAP/.lokaal/stack.lock" nice -n 10 .venv/bin/python e2e/integral/run.py --scenario EX39,EX40,EX41,EX42
```

Elke proef bewaart de metingen in `mso.json`. EX39 vergelijkt het SAB-verzoeklog vóór en na de goedkeuring. EX40 leest na elke storing de providerstatus terug. EX41 gebruikt een aparte qBittorrent-simulator voor de publieke torrent. EX42 telt zoekvragen en terugoproepen en begrenst het meetvenster op 25 seconden.
