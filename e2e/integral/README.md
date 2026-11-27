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
| `--scenario EX01,EX02` | De genoemde bestanden zodra die in een vervolgstap zijn toegevoegd |
| `--groep NAAM` | De bestanden die deze groep in hun metadata hebben |
| `--alles` | Alle aanwezige scenariobestanden, één voor één |
| `--bewaar` | Behoudt het project van de laatste geselecteerde proef voor onderzoek |

Geef de selectie aan Make mee als `INTEGRAAL_ARGS='--groep rook'`. Elke proef krijgt een nieuwe projectnaam, een intern netwerk, eigen volumes en verse koppelgegevens. De testclient voert zijn verzoeken uit vanuit de Hub-container. Het certificaat van de simulator ontstaat bij de start en de Hub controleert de vingerafdruk. Bij een bezette standaardpoort vraagt de runner Docker om een vrije loopbackpoort. Een expliciete `INTEGRAAL_<DIENST>_POORT` legt de hostpoort vast.

## Profielen en scenario's

| Bouwprofiel | Diensten |
|---|---|
| kern | hub, executor, truenas, lifecycle |
| media | mediaguard, mso. De invoerimages voor MediaGuard komen uit de lokale encoder-, web- en forkbronnen. |
| media-inhoud | MediaGuard-kern en herstelwerker uit `apps/core`, plus Seerr en Sonarr uit de arr-simulator. De diensten delen een projectgebonden testbibliotheek. |
| media-kwaliteit | CPU-encoder uit `services/encoder` met FFmpeg en libvmaf. |
| huis | my-house en home-control-hub voor EX24–EX30 |
| huis-browser | Chromium voor de apparaatkaart in EX27 |
| appstore, onedrive, herstel | Respectievelijk appstore, onedrive en truenas-twee |
| derden | beszel-sim, kuma-sim, gotify-sim en authentik-sim delen één image |
| meldingen | node-red, uptime-kuma en gotify op de publieke digest uit elk installatiemanifest |
| zoeken, mediaserver | Versie 2. De mediaserver gebruikt de vooraf gebouwde `jelly-leon:p6` uit S-P6-30. |

Een nieuw bestand `scenarios/EXnn_naam.py` bevat een `test_*`-functie en een letterlijke dictionary `SCENARIO`. De velden `eigenaar`, `verwachting` en `groepen` zijn verplicht. In `diensten` staan de extra Compose-diensten voor dat scenario. De runner start de kern en precies die diensten met lokale images. Alleen bij een ontbrekende mediaserver slaat hij de daarvan afhankelijke proef over. Onbekende ID's, lege selecties en ontbrekende vereiste images geven een foutcode.

De fixture `kern` levert ingelogde clients en de gekoppelde simulator. Voor een extra testdienst geeft `kern.client(dienst, poort)` een interne client. `kern.simulatorfout(client, naam)` en `kern.verzoeklog(client)` bedienen de derden-simulators. Een scenario kan verwachte teksten van `pytest.skip` declareren in `overslagredenen`. Andere overslagen verwijzen naar de regel met de testvoorwaarde.

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

EX22 bewaakt de HTTP-gezondheid van lifecycle met de gepinde Uptime Kuma. De proef schakelt eerst de Hub uit en daarna
lifecycle. De executor leest de monitor en de berichten van de Gotify-simulator tijdens de storing. Een tweede
meldkanaal loopt door een tijdelijke doorstuurder in de Hub-container. N-P6-04 toetst de ontvangst op een echte telefoon.

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

## Bewijs en opruimen

In `e2e/integral/evidence/<run-id>/` staat per proef het resultaat met verwachting, eigenaar, datum, runnercommit, beschikbare image-ID's en broncommit of digest. Het verzoekoverzicht bevat methode, bekende route, HTTP-status en geselecteerde protocolvelden. Cookies, vrije logtekst en ruwe procesuitvoer blijven buiten het bewijs.

Voor EX-proeven staan de voorstellen in `EVIDENCE-voorstellen.json`. Een uitgevoerde `Sim` krijgt `Bezig` met `Sim-geslaagd` of `Sim-mislukt`. Geef bij `soort="Sim+"` of `"Echt"` ook `blokkade` met een toegestane `NIET GEVALIDEERD (...)`-waarde en `toelichting` met het concrete praktijkonderdeel, bijvoorbeeld een telefoon, tuner of tweede host. Het voorstel blijft dan `Geblokkeerd`. De rookproef heeft geen EX-ID.

Na succes, fouten en onderbreking ruimt de runner het project op. Bij `--bewaar` staat de eigen projectnaam in `proef.json`. Vul die naam in bij `EIGEN_PROJECT`.

```bash
flock "$WERKMAP/.lokaal/stack.lock" docker compose -f e2e/integral/compose.yaml -p "${EIGEN_PROJECT:?}" --profile '*' down -v
```

De gewone Forgejo-CI draait ook conformiteit tegen de Hub-branchekop en een eigen PostgreSQL. De workflow `integrale-kern.yml` start handmatig en bewaart het rookbewijs. Gebruik op een CI-runner met een gedeelde Docker-daemon de Forgejo-variabele `INTEGRAAL_LOCKMAP` voor de gedeelde lockmap. De volledige ronde hoort bij S-P6-22.
