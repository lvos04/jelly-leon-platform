# jelly-leon-platform als standaard TrueNAS Custom App

> **Normaal installeert de Hub deze app automatisch** (via de executor en de TrueNAS-API, `app.create` met de
> catalogusapp `ix-app` — exact dezelfde velden als hieronder). Deze pagina is voor controle en voor handmatig
> installeren als de Hub dat (nog) niet kan. **Geen YAML, geen "Install via YAML", geen terminal.**

| Wat | Waarde |
|---|---|
| Image | `ghcr.io/lvos04/jelly-leon-platform` (publiek pakket, geen registry-login nodig; zie "Image ophalen") |
| Tag | `<versie>@sha256:<digest>` uit `release/digests.json` (bijv. `0.1.0@sha256:…`) — **nooit `latest`** (het formulier vult `latest` voor; overschrijven!) |
| Poort | container `30440` → host `30440` (voorstel; wijzigbaar als die hostpoort bezet is) |
| Opslag | één map → `/appdata` |
| Geheimen / wachtwoorden | **geen**: de app maakt ze zelf bij de eerste start in `/appdata/secrets` |
| Omgevingsvariabelen | geen verplichte; voor koppelen zonder wedloop `HUB_URL` + `HUB_ENROLLMENT_TOKEN` + `HUB_KEY_FINGERPRINT` (zie "Koppelen") |

> **Verificatiestatus: NIET GEVALIDEERD (echte NAS).** De image is in Docker getest zoals TrueNAS hem start
> (één map op `/appdata`, één poort, als root én als 568, herstart, harde kill, enrollment bij een Hub-simulator; zie
> `tests/e2e/test_allinone_image.py`; daar ook: **elk** proces, ook PID 1, draait als 568 zonder capabilities en met
> *no_new_privs*, zonder dat het formulier daar iets voor hoeft te zetten). De veldnamen hieronder komen uit de broncode van het TrueNAS-formulier
> (`ix-app` 1.4.8 in `truenas/apps`, web-UI `truenas/webui` TS-25.10.x); het formulier is **niet** op een echte
> TrueNAS doorlopen en kan per versie iets afwijken. Bron en onzekerheden: `docs/platform-v2/TRUENAS_APP_API.md`.

## 0. Image ophalen (publiek; registry-login alleen als terugvaloptie)

- **Standaard (route A):** het GHCR-pakket is publiek (besluit 3, na een scan van de volledige historie). TrueNAS
  haalt de image dan zonder registry-login op. **Let op:** zolang de pakketten nog niet publiek zijn gemaakt, lukt
  dit niet (`denied`); dat is op de peildatum nog zo — installeren op een echte NAS is daarom **Geblokkeerd /
  NIET GEVALIDEERD**.
- **Terugvaloptie (route B):** *Apps → Configuration → Sign-in to a Docker registry* → *Other Registry*, URI
  `https://ghcr.io`, een GitHub-token met **alleen** `read:packages`. Dat token is een geheim van jou in TrueNAS —
  nooit in een app-formulier, manifest, repo of log. Gedrag met GHCR-tokens: **NIET GEVALIDEERD (echte NAS)**.

## 1. Opslag kiezen

Kies één van beide (de Hub-wizard stelt dit voor via de datasetkiezer; nooit een voorbeeldpad overnemen):

- **Host Path (aanbevolen voor back-up en verhuizen):** maak in **Datasets** onder `<POOL>/apps` (jouw pool en
  structuur, besluit D02) een dataset `jelly-leon-platform` met **Dataset Preset: Apps**. Rechten niet aanpassen.
- **ixVolume:** TrueNAS maakt zelf een dataset onder de apps-pool. Eenvoudiger, maar verwijdert TrueNAS die bij
  *Delete app* als je "Remove ixVolumes" aanvinkt. Laat dat vakje bij verwijderen altijd **uit**.

## 2. Formulier invullen

**Apps** → **Discover Apps** → knop **Custom App** (niet het pijltje → *Install via YAML*).

| Sectie (groep) | Veld | Waarde |
|---|---|---|
| — | Application Name | `jelly-leon-platform` |
| Image Configuration | Repository | `ghcr.io/lvos04/jelly-leon-platform` |
| | Tag | `<versie>@sha256:<digest>`, bijv. `0.1.0@sha256:` + 64 hex uit `release/digests.json` (trekt op digest). Weigert het formulier het `@`-teken, vul dan alleen de exacte versie in; de Hub leest de digest na installatie terug (`app.image.query`) en stopt bij een afwijking. **Of het formulier `@` accepteert en of TrueNAS daarmee updates detecteert: NIET GEVALIDEERD (echte NAS).** |
| | Pull Policy | *Pull the image if it is not already present on the host* (`missing`) |
| Container Configuration | Hostname, Entrypoint, Command | leeg laten |
| | Timezone | jouw tijdzone (de app rekent intern in UTC) |
| | Environment Variables | leeg laten, of voor koppelen zonder wedloop: `HUB_URL`, `HUB_ENROLLMENT_TOKEN`, `HUB_KEY_FINGERPRINT` (zie stap 3) |
| | Restart Policy | **Unless Stopped** (standaard staat er *No*!) |
| | Disable Builtin Healthcheck | **uit** (de image heeft een eigen healthcheck op `/readyz`) |
| | TTY / Stdin | uit |
| | Devices | geen |
| Security Context Configuration | Privileged | **uit** |
| | Capabilities → Add | niets toevoegen |
| | Custom User | **uit** (de app start als root alléén om `/appdata` aan 568 te geven; daarna draait elk proces, ook PID 1, als 568 zonder capabilities en met *no_new_privs* — de image zet dat zelf, omdat het formulier `no-new-privileges` en `cap_drop` niet zet). Aan met 568 werkt ook (dan geen eigenaarschapsstap; is `/appdata` niet schrijfbaar, dan een Nederlandse melding in de log en healthcheck rood). |
| Network Configuration | Host Network | **uit** |
| | Ports → Add | Port Bind Mode `published` · Host Port `30440` · Container Port `30440` · Protocol `TCP` · Host IPs leeg |
| | Networks / Custom DNS Setup | leeg laten |
| Portal Configuration | Portal → Add (optioneel) | Name `Status` · Protocol `http` · Use Node IP aan · Port `30440` · Path `/` |
| Storage Configuration | Storage → Add | Type `Host Path` · Mount Path `/appdata` · Host Path: jouw dataset uit stap 1 · Read Only uit · Enable ACL uit |
| | *(of)* | Type `ixVolume` · Mount Path `/appdata` · Dataset Name `appdata` |
| Labels Configuration | — | leeg laten |
| Resources Configuration | Enable Resource Limits | aan · CPUs `1` · Memory (in MB) `512` (in Docker gemeten in rust ca. 215 MiB; op TrueNAS niet gemeten) |
| | GPU Configuration | geen GPU |

**Install**. De eerste start duurt 10–30 seconden (database aanmaken). Daarna *Running* en na de healthcheck *Healthy*.

## 3. Openen en koppelen

Open `http://<NAS-adres>:30440` (NAS-adres uit de wizard, besluit D03). De statuspagina (ontwerptaal Peil, zonder
JavaScript) toont bovenaan het totaal — "Niet gereed" zolang de app niet gekoppeld is, nooit groen als hij niet gereed
is —, daaronder de controles per onderdeel, "Gereed", de versie en "Bijgewerkt" (UTC), en de koppeling met de Hub.
Zonder koppeling met de Hub accepteert de API niets (fail-closed). Gaat koppelen mis, dan toont de pagina een melding
met een code en een trace-ID (selecteer en kopieer); dezelfde trace-ID staat in de app-log.

- **Geïnstalleerd door de Hub (normaal):** de Hub geeft `HUB_URL`, `HUB_KEY_FINGERPRINT` en een eenmalig
  `HUB_ENROLLMENT_TOKEN` mee; de app koppelt zichzelf bij de eerste start, controleert de vingerafdruk van de
  Hub-sleutels en pint die. Het token is daarna waardeloos.
- **Handmatig geïnstalleerd, aanbevolen:** maak in de Hub-UI een enrollment-token voor `jelly-leon-platform` en vul
  in het formulier (Environment Variables) `HUB_URL`, `HUB_ENROLLMENT_TOKEN` en `HUB_KEY_FINGERPRINT` in (de Hub-UI toont
  ze samen). Geen wedloop: alleen wie het TrueNAS-formulier kan wijzigen, kan zo koppelen. Klopt `HUB_URL` of de vingerafdruk niet,
  dan start de app gewoon en meldt de statuspagina `hub_adres_ongeldig` of `vingerafdruk_ongeldig`; het token blijft
  bruikbaar tot je het formulier verbetert.
- **Handmatig, met koppelcode:** maak in de Hub-UI een koppelcode en vul op de statuspagina het Hub-adres
  (`http://<NAS-adres>:30400`), de koppelcode (`XXXX-XXXX-XXXX`) **en** de vingerafdruk (`XXXX-XXXX-XXXX-XXXX`) in →
  **Koppelen**. Dit formulier bestaat alleen zolang de app **nog nooit** gekoppeld is; daarna gaan ontkoppelen en
  opnieuw koppelen alleen via de Hub (of een nieuw `HUB_ENROLLMENT_TOKEN` in dit formulier). Max. 5 pogingen per
  15 minuten per bron-IP en 20 voor de hele app; ook een geweigerde poging telt mee. Open de statuspagina daarvoor via
  het **IP-adres van de NAS** of een lokale naam (zonder punt, of op `.local`, `.lan`, `.internal`, `.home.arpa`): via
  een eigen domeinnaam weigert de app het formulier (`host_niet_toegestaan`, bescherming tegen DNS-rebinding, contract
  1.3 §3.7). Welk adres de app achter de poortpublicatie van TrueNAS als bron-IP
  ziet (jouw apparaat of het gateway-adres van Docker, dan gelden de 5 voor alle apparaten samen) is
  **NIET GEVALIDEERD (echte NAS)**. Een koppelcode-poging die klaar is nadat de app intussen al gekoppeld is (bijv.
  via het token), wordt geweigerd en overschrijft nooit de bestaande koppeling. Restrisico: vóór de eerste koppeling kan iemand op je netwerk de app aan een eigen "Hub"
  koppelen; vergelijk daarom na het koppelen de vingerafdruk op de statuspagina met die in de Hub-UI, en gebruik bij
  voorkeur de route met het token.

## Environment Variables (alle optioneel)

| Naam | Waarde | Waarom |
|---|---|---|
| `HUB_URL` | `http://<NAS-adres>:30400` | adres van de Hub (zonder pad; geen `127.0.0.1`/`localhost`) |
| `HUB_ENROLLMENT_TOKEN` | eenmalig token `enr_…` uit de Hub-UI | vervalt na 60 min of na gebruik; daarna waardeloos |
| `HUB_KEY_FINGERPRINT` | `XXXX-XXXX-XXXX-XXXX` uit de Hub-UI | niet geheim; de app weigert een Hub met andere sleutels |
| `LIFECYCLE_PUBLIC_URL` | `http://<NAS-adres>:<hostpoort>` | alleen informatief voor de Hub (die gebruikt zijn eigen register) |

## Bijwerken, terugzetten, verwijderen

- **Bijwerken:** app → **Edit** → Tag naar de nieuwe `<versie>@sha256:<digest>` → **Update**. Noteer vooraf de oude
  Tag (de Hub bewaart vanaf P2 de volledige vorige waarden als terugzetrecept). Bij openstaande databasemigraties maakt de
  app eerst een `pg_dump` in `/appdata/backups` (nieuwste 3 blijven; overgeslagen bij te weinig ruimte, dat staat in
  de log) en migreert daarna in één transactie. Mislukt de migratie, dan blijft de database op de vorige versie en
  start de app niet: zet de vorige Tag terug. Maak vooraf een ZFS-snapshot van de dataset.
- **Terugzetten:** TrueNAS **Roll Back** werkt bij een Custom App alleen tussen *ix-app*-versies, niet tussen
  image-tags. Terugzetten = **Edit** → vorige Tag (met digest) = `app.update` met de vorige waarden. Is het schema al gemigreerd, dan eerst de snapshot of de
  `voor-upgrade-*.dump` terugzetten (de Hub doet dit in P2 via de executor met goedkeuring).
- **Verwijderen:** app → **Delete**; laat **Remove ixVolumes uit**. De dataset (Host Path) blijft altijd staan.

## Problemen

| Melding in de app-log | Oplossing |
|---|---|
| `FOUT: /appdata bestaat niet` | Storage-regel ontbreekt of Mount Path is niet precies `/appdata`. |
| `FOUT: geen schrijfrechten op /appdata/...` | *Custom User* staat aan met een andere gebruiker: zet uit, of geef 568 schrijfrechten. |
| `oude postmaster.pid ... verwijderd` | Geen fout: vorige stop was onverwacht (stroom/kill). |
| `databasemigratie mislukt` | Vorige Tag terugzetten; de database is ongewijzigd. Log bewaren voor de Hub/incident. |
| Image niet op te halen (`denied`/`unauthorized`) | GHCR-pakket nog privé: zie stap 0 (route B als terugvaloptie) en `install/truenas/INSTALLATIE.md` stap 1. |
| Statuspagina: code `hub_vingerafdruk_onjuist` ("vingerafdruk klopt niet") | Ander Hub-adres of andere Hub dan bedoeld; er is niets opgeslagen. Controleer adres en vingerafdruk in de Hub-UI. |
| Statuspagina: "Ingetrokken" (code `ingetrokken_door_hub`) | De Hub heeft de koppeling ingetrokken. Opnieuw koppelen via de Hub (nieuw token). |
| Statuspagina: "De koppelstatus is onleesbaar" (code `koppelstatus_onleesbaar`) | `/appdata/secrets/hub-link.json` is beschadigd; de app vertrouwt geen Hub-aanmelding en toont geen formulier. Opnieuw koppelen via de Hub (nieuw token in dit formulier). |
| Poort bezet | Andere Host Port kiezen (Container Port blijft `30440`); de Hub controleert dit vooraf via `app.used_ports`. |
