# jelly-leon-platform als standaard TrueNAS Custom App

> **Normaal installeert de Hub deze app automatisch** (via de executor en de TrueNAS-API, `app.create` met de
> catalogusapp `ix-app` — exact dezelfde velden als hieronder). Deze pagina is voor controle en voor handmatig
> installeren als de Hub dat (nog) niet kan. **Geen YAML, geen "Install via YAML", geen terminal.**

| Wat | Waarde |
|---|---|
| Image | `ghcr.io/lvos04/jelly-leon-platform` |
| Tag | de exacte versie uit de release (bijv. `0.1.0`) — **nooit `latest`** (het formulier vult `latest` voor; overschrijven!) |
| Poort | container `30440` → host `30440` (voorstel; wijzigbaar als die hostpoort bezet is) |
| Opslag | één map → `/appdata` |
| Geheimen / wachtwoorden | **geen**: de app maakt ze zelf bij de eerste start in `/appdata/secrets` |
| Omgevingsvariabelen | geen verplichte (zie "Optioneel") |

> **Verificatiestatus: NIET GEVALIDEERD (echte NAS).** De image is in Docker getest zoals TrueNAS hem start
> (één map op `/appdata`, één poort, als root én als 568, herstart, harde kill, enrollment bij een Hub-simulator; zie
> `tests/e2e/test_allinone_image.py`). De veldnamen hieronder komen uit de broncode van het TrueNAS-formulier
> (`ix-app` 1.4.8 in `truenas/apps`, web-UI `truenas/webui` TS-25.10.x); het formulier is **niet** op een echte
> TrueNAS doorlopen en kan per versie iets afwijken. Bron en onzekerheden: `docs/platform-v2/TRUENAS_APP_API.md`.

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
| | Tag | exacte versie, bijv. `0.1.0` |
| | Pull Policy | *Pull the image if it is not already present on the host* (`missing`) |
| Container Configuration | Hostname, Entrypoint, Command | leeg laten |
| | Timezone | jouw tijdzone (de app rekent intern in UTC) |
| | Environment Variables | leeg laten |
| | Restart Policy | **Unless Stopped** (standaard staat er *No*!) |
| | Disable Builtin Healthcheck | **uit** (de image heeft een eigen healthcheck op `/readyz`) |
| | TTY / Stdin | uit |
| | Devices | geen |
| Security Context Configuration | Privileged | **uit** |
| | Capabilities → Add | niets toevoegen |
| | Custom User | **uit** (de app start als root alléén om `/appdata` aan 568 te geven en draait daarna als 568 met *no_new_privs*) |
| Network Configuration | Host Network | **uit** |
| | Ports → Add | Port Bind Mode `published` · Host Port `30440` · Container Port `30440` · Protocol `TCP` · Host IPs leeg |
| | Networks / Custom DNS Setup | leeg laten |
| Portal Configuration | Portal → Add (optioneel) | Name `Status` · Protocol `http` · Use Node IP aan · Port `30440` · Path `/` |
| Storage Configuration | Storage → Add | Type `Host Path` · Mount Path `/appdata` · Host Path: jouw dataset uit stap 1 · Read Only uit · Enable ACL uit |
| | *(of)* | Type `ixVolume` · Mount Path `/appdata` · Dataset Name `appdata` |
| Labels Configuration | — | leeg laten |
| Resources Configuration | Enable Resource Limits | aan · CPUs `1` · Memory (in MB) `512` (gemeten in rust ± 180 MiB) |
| | GPU Configuration | geen GPU |

**Install**. De eerste start duurt 10–30 seconden (database aanmaken). Daarna *Running* en na de healthcheck *Healthy*.

## 3. Openen en koppelen

Open `http://<NAS-adres>:30440` (NAS-adres uit de wizard, besluit D03). De statuspagina toont status per onderdeel en
"Laatst bijgewerkt". Zonder koppeling met de Hub accepteert de API niets (fail-closed).

- **Geïnstalleerd door de Hub:** de Hub gaf `HUB_URL` en een eenmalig `HUB_ENROLLMENT_TOKEN` mee; de app koppelt
  zichzelf bij de eerste start. Het token is daarna waardeloos.
- **Handmatig geïnstalleerd:** maak in de Hub-UI een koppelcode voor `jelly-leon-platform` en vul op de statuspagina
  het Hub-adres (`http://<NAS-adres>:30400`) en de koppelcode in → **Koppelen**.

## Optioneel (Environment Variables)

| Naam | Waarde | Waarom |
|---|---|---|
| `HUB_URL` | `http://<NAS-adres>:30400` | alleen als de Hub de app installeert (zet de Hub zelf) |
| `HUB_ENROLLMENT_TOKEN` | eenmalig token | alleen door de Hub; vervalt na 60 min of na gebruik |
| `LIFECYCLE_PUBLIC_URL` | `http://<NAS-adres>:<hostpoort>` | als de Hub de app op een ander adres moet bereiken |

## Bijwerken, terugzetten, verwijderen

- **Bijwerken:** app → **Edit** → Tag naar de nieuwe versie → **Update**. Bij openstaande databasemigraties maakt de
  app eerst een `pg_dump` in `/appdata/backups` (nieuwste 3 blijven; overgeslagen bij te weinig ruimte, dat staat in
  de log) en migreert daarna in één transactie. Mislukt de migratie, dan blijft de database op de vorige versie en
  start de app niet: zet de vorige Tag terug. Maak vooraf een ZFS-snapshot van de dataset.
- **Terugzetten:** TrueNAS **Roll Back** werkt bij een Custom App alleen tussen *ix-app*-versies, niet tussen
  image-tags. Terugzetten = **Edit** → vorige Tag. Is het schema al gemigreerd, dan eerst de snapshot of de
  `voor-upgrade-*.dump` terugzetten (de Hub doet dit in P2 via de executor met goedkeuring).
- **Verwijderen:** app → **Delete**; laat **Remove ixVolumes uit**. De dataset (Host Path) blijft altijd staan.

## Problemen

| Melding in de app-log | Oplossing |
|---|---|
| `FOUT: /appdata bestaat niet` | Storage-regel ontbreekt of Mount Path is niet precies `/appdata`. |
| `FOUT: geen schrijfrechten op /appdata/...` | *Custom User* staat aan met een andere gebruiker: zet uit, of geef 568 schrijfrechten. |
| `oude postmaster.pid ... verwijderd` | Geen fout: vorige stop was onverwacht (stroom/kill). |
| `databasemigratie mislukt` | Vorige Tag terugzetten; de database is ongewijzigd. Log bewaren voor de Hub/incident. |
| Image niet op te halen (`denied`/`unauthorized`) | GHCR-pakket nog privé: zie `install/truenas/INSTALLATIE.md` stap 1. |
| Poort bezet | Andere Host Port kiezen (Container Port blijft `30440`); de Hub controleert dit vooraf via `app.used_ports`. |
