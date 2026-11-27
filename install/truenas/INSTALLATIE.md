# Eerste installatie van het TrueNAS Home Platform — zonder terminal, zonder YAML

Je maakt in de TrueNAS-web-UI precies **twee** standaard Custom Apps: de Hub (`ai-server-manager`) en de executor
(`ai-server-manager-executor`). Daarna installeert de Hub alles via de TrueNAS-API (contract §2). Je hebt geen
terminal, geen SSH en geen *Install via YAML* nodig.

> **Begrippen (contract 1.3 §2):** een *standaard Custom App* is het formulier *Apps → Discover Apps → Custom App*
> (technisch de catalogusapp `ix-app`; in TrueNAS staat dan `custom_app: false`).
> Nooit toegestaan: *Install via YAML* (dat maakt een *compose-app*, `custom_app: true`). Een andere app uit de officiële
> catalogus heet hier *catalogusapp*; die gebruikt het platform voor apps met meerdere containers (zoals Authentik), en
> voor één container alleen met een reden in het manifest (`install.reason`).

> **Verificatiestatus — lees dit eerst.**
> - **NIET GEVALIDEERD (echte NAS):** dit stappenplan is nog niet op een echte TrueNAS doorlopen. Menu- en veldnamen
>   komen uit de broncode van de TrueNAS-web-UI (`truenas/webui` tag TS-25.10.8) en de Custom App-definitie
>   (`truenas/apps` `ix-app` 1.4.8); in 25.04 en latere versies kunnen namen iets afwijken.
> - De images van de Hub en de executor worden gebouwd in `ai-server-manager`. Tag, setupcode, wizard en koppelpagina
>   zijn van die repo. Gebruik voor de Hub versie `0.1.0` en voor de executor versie `0.1.1`.
> - Vul nergens voorbeeldwaarden in. NAS-adres (D03), pool en datasets (D02) en de TrueNAS-versie (D01) zijn van
>   jouw NAS; `<…>` betekent: jouw eigen waarde.

| | Hub | Executor |
|---|---|---|
| Application Name | `ai-server-manager` | `ai-server-manager-executor` |
| Image (Repository) | `ghcr.io/lvos04/ai-server-manager` | `ghcr.io/lvos04/ai-server-manager-executor` |
| Tag | `0.1.0` met de Hub-digest uit stap 1 | `0.1.1` met de executordigest uit stap 1 |
| Poort (container → host) | `30400` → `30400` | `30410` → `30410` |
| Opslag | eigen map → `/appdata` | **eigen, aparte** map → `/appdata` |
| Geheimen in het formulier | geen | geen (de schrijfkey voer je alleen op de executorpagina in) |

## Stap 0 — Controleren vóór je begint

1. **Systeemversie noteren:** *System → General* (of het dashboard) → noteer de TrueNAS-versie (besluit D01).
   Het platform ondersteunt 25.04 en 25.10 (bron: `docs/platform-v2/TRUENAS_APP_API.md`).
2. **Apps ingeschakeld:** *Apps* → als TrueNAS om een pool vraagt: *Configuration → Choose Pool* en kies de pool
   waar apps mogen staan (D02). Zonder deze stap is de knop *Custom App* uitgeschakeld ("Setup Pool To Create
   Custom App").
3. **Back-up-gate (I01-01-02):** zorg vóór nieuwe installaties voor een recente snapshot/back-up van bestaande
   appdata. De Hub controleert dit later zelf; nu alleen bewust nagaan.

## Stap 1 — Images ophalen (GHCR)

Op 27 november 2026 zijn de onderstaande manifestdigests anoniem gecontroleerd bij GHCR. Gebruik de eerste twee
rijen voor het Hub- en executorformulier. De overige vier horen bij de installatieplannen in stap 6.

| Repository | Tag voor het formulier |
|---|---|
| `ghcr.io/lvos04/ai-server-manager` | `0.1.0@sha256:4accffc49b8d4180a302c974f4acf6e2c0d36b2a4fe458e80741b8ffc94546fa` |
| `ghcr.io/lvos04/ai-server-manager-executor` | `0.1.1@sha256:eb98c8fab8121f819582325dbd9582256e3dd9c77912b2c8a67c7d5619aee27e` |
| `ghcr.io/lvos04/jelly-leon-platform` | `0.1.1@sha256:df07b0ff8204b0f38c50fab430f43424f8cc5755b5e06c38827305c90bb3cb89` |
| `ghcr.io/lvos04/home-control-hub` | `0.1.1@sha256:52dc80ad3b038a8a34b12096bd18791a4f299339629c955ae81c3d8776c40929` |
| `ghcr.io/lvos04/appstore-en-fork` | `0.1.1@sha256:79e642d2b2a0736282f7c8022ee1b6f178783a19f6fcaa5baa5a23be9e6789b3` |
| `ghcr.io/lvos04/zoeken-en-persoonlijke-ai` | `0.1.1@sha256:3c385eed778a0d09bdfab1f42ff4b9bca0bd0a4edf885f98f9789ea33f347892` |

- **A. Publieke pakketten.** Ga door naar stap 2. De eerste twee waarden staan ook in de releasebijlagen van de
  [Hub 0.1.0](https://github.com/lvos04/ai-server-manager/releases/tag/v0.1.0) en de
  [executor 0.1.1](https://github.com/lvos04/ai-server-manager/releases/tag/v0.1.1).
- **B. Alleen als terugvaloptie: registry-login in TrueNAS.** *Apps → Configuration → Sign-in to a Docker registry*
  → *Other Registry* → URI `https://ghcr.io`, gebruikersnaam = je GitHub-gebruikersnaam, wachtwoord = een
  GitHub-token met alleen `read:packages`. Dit geheim staat dan in TrueNAS, nooit in een app-formulier, manifest,
  repo of log. (API: `app.registry.create`; **gedrag met GHCR-tokens NIET GEVALIDEERD**.)

## Stap 2 — Opslag maken (dataset of ixVolume)

Per app één eigen map. Twee mogelijkheden:

- **Dataset (aanbevolen: eenvoudig te snapshotten, back-uppen en verhuizen).** *Datasets* → kies de pool →
  (eventueel eerst een bovenliggende dataset zoals `apps`) → *Add Dataset* → Name `ai-server-manager`, **Dataset
  Preset: Apps** → *Save*. Herhaal voor `ai-server-manager-executor`. Rechten niet aanpassen: elke app maakt bij de
  eerste start gebruiker 568 (*apps*) eigenaar van alléén zijn eigen submappen.
- **ixVolume.** Kies in stap 3/5 bij *Storage* het type *ixVolume*; TrueNAS maakt dan zelf een dataset.
  Let op: bij *Delete* van de app nooit **Remove iXVolumes** aanvinken, anders is de data weg.

## Stap 3 — Custom App `ai-server-manager` (de Hub)

*Apps → Discover Apps* → knop **Custom App** (niet *Install via YAML*). Vul in:

| Sectie | Veld | Waarde |
|---|---|---|
| — | Application Name | `ai-server-manager` |
| Image Configuration | Repository | `ghcr.io/lvos04/ai-server-manager` |
| | Tag | Overschrijf `latest` met de volledige tag voor `ai-server-manager` uit stap 1. Weigert het formulier het `@`-teken, vul dan `0.1.0` in. De Hub controleert de digest na installatie (`app.image.query`). **NIET GEVALIDEERD (echte NAS):** of het formulier `@` accepteert en of TrueNAS daarmee updates detecteert. |
| | Pull Policy | *Pull the image if it is not already present on the host* |
| Container Configuration | Timezone | jouw tijdzone |
| | Environment Variables | leeg laten |
| | Restart Policy | **Unless Stopped** (standaard staat er *No*) |
| | Disable Builtin Healthcheck | uit |
| Security Context Configuration | Privileged | **uit** |
| | Capabilities | niets toevoegen |
| | Custom User | **uit** |
| Network Configuration | Host Network | **uit** |
| | Ports → Add | Port Bind Mode `published` · Host Port `30400` · Container Port `30400` · Protocol `TCP` |
| Portal Configuration (optioneel) | Portal → Add | Name `Hub` · Protocol `http` · Use Node IP aan · Port `30400` · Path `/` |
| Storage Configuration | Storage → Add | Type `Host Path` · Mount Path `/appdata` · Host Path = dataset `ai-server-manager` uit stap 2 *(of Type ixVolume, Dataset Name `appdata`)* |
| Resources Configuration | Enable Resource Limits | volgens de release-notes van `ai-server-manager` |
| | GPU Configuration | geen |

**Install.** Wacht tot de app *Running* (en daarna *Healthy*) is. Is de hostpoort al bezet, kies dan een andere
Host Port en onthoud die.

## Stap 4 — Setupcode uit de logs en de wizard

1. *Apps* → klik `ai-server-manager` → kaart **Workloads** → bij de container het pictogram **View Logs**.
2. Zoek de regel met de eenmalige **setupcode** (alleen in de logs, nergens anders). Deel hem met niemand.
3. Open `http://<NAS-adres>:30400` in de browser. De wizard vraagt eerst de setupcode, daarna maak je het eerste
   beheerdersaccount aan en vul je (of meet de wizard) de NAS-gegevens in (D01–D03 …).
4. In de Hub voer je de **TrueNAS-key met alleen-lezen-rechten** in (Hub-UI, versleuteld opgeslagen). Zo maak je die:
   - *Credentials → Users* → *Add*: een aparte gebruiker (naam naar keuze), **TrueNAS Access** aan met rol
     **Read-Only Admin**, SMB/SSH/Shell uit → *Save*.
   - *Credentials → Users → User API Keys* → *Add*: Name naar keuze, Username = die gebruiker, verloopdatum naar
     keuze → *Save* → **"This is the only time the key is shown."** → kopieer en plak direct in de Hub.
   - **NIET GEVALIDEERD (echte NAS):** of *Read-Only Admin* alle benodigde leesmethoden toestaat.

## Stap 5 — Custom App `ai-server-manager-executor` en koppelen

1. Opslag: aparte dataset `ai-server-manager-executor` (stap 2) — **niet** dezelfde map als de Hub.
2. *Apps → Discover Apps → Custom App* met dezelfde velden als stap 3, behalve:

   | Veld | Waarde |
   |---|---|
   | Application Name | `ai-server-manager-executor` |
   | Repository | `ghcr.io/lvos04/ai-server-manager-executor` |
   | Tag | De volledige tag voor `ai-server-manager-executor` uit stap 1. Weigert het formulier het `@`-teken, vul dan `0.1.1` in. |
   | Ports → Add | Host Port `30410` · Container Port `30410` · TCP |
   | Storage | Host Path = dataset `ai-server-manager-executor` → Mount Path `/appdata` |

3. **TrueNAS-key met schrijfrechten** (alleen voor de executor, zo beperkt mogelijk):
   - Bij voorkeur een **eigen privilege**: *Credentials → Groups → Privileges* → *Add*: naam naar keuze, Roles
     alleen wat de executor nodig heeft (`READONLY_ADMIN`, `APPS_WRITE`, `SNAPSHOT_WRITE`, `DATASET_WRITE`;
     `POOL_WRITE` alleen als snapshot-terugzetten via de executor gewenst is), Local Groups: een nieuwe groep;
     daarna een gebruiker in die groep. Anders: rol *Full Admin* (ruimer dan nodig — afgeraden).
   - *Credentials → Users → User API Keys → Add* voor die gebruiker → key kopiëren.
   - **NIET GEVALIDEERD (echte NAS):** de minimale rollenset en de exacte privilege-schermen.
4. **Koppelen via de web-UI (contract 1.3 §3.9):** in de Hub-wizard verschijnen een eenmalige **koppelcode**
   (`XXXX-XXXX-XXXX`, 30 minuten geldig) en de **vingerafdruk** van de Hub-sleutels (`XXXX-XXXX-XXXX-XXXX`), met een
   kopieerknop. Open `http://<NAS-adres>:30410` (statuspagina van de executor) en vul het Hub-adres, de koppelcode
   **en** de vingerafdruk in; de executor weigert een Hub met andere sleutels en pint vooral de policy-sleutel.
   Open de pagina via het **IP-adres van de NAS** (of een lokale naam zonder punt of op `.local`, `.lan`, `.internal`,
   `.home.arpa`): via een eigen domeinnaam weigeren de koppel- en sleutelformulieren elk verzoek (bescherming tegen
   DNS-rebinding, contract 1.3 §3.7).
   Daarna vul je op dezelfde pagina de TrueNAS-schrijfkey in. De executor test die live (`auth.login_with_api_key` +
   rolcontrole) tegen het NAS-adres en TLS-certificaat die je bij deze eerste koppeling vastlegt, versleutelt de key in
   zijn eigen vault en toont daarna alleen de laatste 4 tekens; de Hub ziet de key nooit. Ontkoppelen, opnieuw
   koppelen of de key roteren kan op deze pagina **alleen** na het opnieuw invoeren van een schrijfkey die de executor
   live verifieert tegen dat vastgelegde adres en certificaat (een ander adres opgeven telt niet).
5. **Belangrijk:** de executor moet TrueNAS via **`wss://` (HTTPS)** aanspreken. TrueNAS trekt een API-key die over
   een onversleutelde verbinding wordt gebruikt automatisch in (bron: middleware `auth.py`, TS-25.04/25.10). Volg de
   aanwijzingen op de executorpagina voor het TrueNAS-certificaat.

## Stap 6 — Daarna installeert de Hub de rest

Vanaf nu installeer je niets meer met de hand. In de Hub kies je *Apps → Toevoegen*; de Hub toont per app eerst een
plan (image + exacte tag/digest, poorten met conflictcontrole, datasets via de datasetkiezer, rechten, back-up-eisen,
risico) en vraagt goedkeuring. Daarna voert de executor `app.create` uit met de standaard Custom App-waarden
(`ix-app`) of — voor apps met meerdere containers zoals Authentik en Immich — de **officiële TrueNAS-catalogusapp**
met vaste versie. Eigen apps (`appstore-en-fork`, `jelly-leon-platform`, `home-control-hub`, …) krijgen daarbij alleen
`HUB_URL`, `HUB_KEY_FINGERPRINT` (niet geheim) en een eenmalig `HUB_ENROLLMENT_TOKEN` (60 minuten) mee en melden zich
zelf aan: ze controleren de vingerafdruk, pinnen de Hub-sleutels en bevestigen de koppeling. Installeer je zo'n app
toch met de hand, vul dan diezelfde drie waarden uit de Hub-UI in het formulier in. **Dit is de aanbevolen weg**
(env-enrollment, contract 1.3 §3.2): er is geen wedloop met iemand anders op het LAN. Een ongeldige `HUB_URL` of
vingerafdruk laat de app gewoon starten; de statuspagina meldt `hub_adres_ongeldig` of `vingerafdruk_ongeldig` en het
token blijft bruikbaar tot je het formulier verbetert.

De koppelcode op de statuspagina van de app (bijv. `http://<NAS-adres>:30440` voor `jelly-leon-platform`) is de
tweede weg en werkt alleen zolang die app nog nooit gekoppeld is:

- Open de statuspagina via het **IP-adres van de NAS** of een **lokale naam** (een naam zonder punt, of een naam op
  `.local`, `.lan`, `.internal` of `.home.arpa`). Via een eigen domeinnaam (bijv. een naam achter een reverse proxy)
  weigert de pagina het formulier met "Open deze pagina via het IP-adres van de NAS, of koppel via de Hub"
  (`host_niet_toegestaan`): zo kan een website die je bezoekt de app niet via DNS-rebinding aan een vreemde "Hub"
  koppelen. De pagina zegt dat ook al vóór het versturen.
- Vul alle drie de velden in (Hub-adres, koppelcode **en** vingerafdruk uit de Hub). Hooguit 5 pogingen per
  15 minuten per apparaat en 20 voor de hele app; ook een geweigerde poging telt mee.
- Na de eerste koppeling verdwijnt het formulier voorgoed; opnieuw koppelen of ontkoppelen gaat via de Hub.

> **Stand P2:** de manifesten per app staan in `install/manifests/` (Beszel, Uptime Kuma, Gotify, Authentik, Node-RED;
> overzicht en verplichte stappen na installatie in `install/manifests/README.md`). `jelly-leon-platform` maakt voor
> installatie, update en terugzetten een **plan** (formulierwaarden met tag `<versie>@sha256:…`, conflictcontrole,
> afhankelijkheden, volgorde van stappen) en voert zelf niets uit. Uitvoeren doet de executor na beleid en goedkeuring
> in de Hub. Die keten is alleen met stand-ins en de TrueNAS-simulator getest (**Sim-getest**); de echte Hub heeft de
> route voor voorstellen nog niet, dus installeren van deze apps via de Hub is op een NAS nog **Geblokkeerd**.

## Rooktest van deze stappen (zonder NAS)

`tools/rooktest.py` (`make rooktest`, CI-job `rooktest`) speelt stap 3–5 na met lokaal gebouwde images en de
TrueNAS-simulator: elke app als één container met één map → `/appdata` en één poort, zonder extra rechten (zoals het
formulier); setupcode uit de log; eerste beheerder; wizard met NAS-adres, alleen-lezen-key en het **bevestigen van de
TLS-vingerafdruk** (een verkeerde wordt geweigerd); metingen; executor koppelen met koppelcode + vingerafdruk; een
eigen dienst koppelen met `HUB_URL` + `HUB_ENROLLMENT_TOKEN` + `HUB_KEY_FINGERPRINT`. Uitkomst:
`docs/platform-v2/bewijs/rooktest-p1.json` — **Sim-getest**; de echte TrueNAS-web-UI is daarbij niet gebruikt
(**NIET GEVALIDEERD (echte NAS)**).

## Wat als het misgaat

| Probleem | Oplossing |
|---|---|
| Knop *Custom App* grijs | Stap 0.2: eerst *Apps → Configuration → Choose Pool*. |
| `denied` / `unauthorized` bij ophalen image | Vergelijk de repository en tag met de rij van de app in stap 1. Vraagt GHCR om aanmelding, gebruik dan de terugvaloptie in die stap. |
| Poort bezet bij opslaan | Andere Host Port kiezen; Container Port niet wijzigen. |
| Geen setupcode in de logs | App herstarten en opnieuw *View Logs*; de code staat alleen in de logs van de eerste start(s). |
| Executor: "key ingetrokken" | De key is over een onversleutelde verbinding gebruikt; maak een nieuwe key en gebruik `wss://`. |
| Statuspagina: "Koppelen via dit adres kan niet" | Je opende de pagina via een domeinnaam. Open hem via het IP-adres van de NAS, of koppel via de Hub (eenmalig token in het formulier van de app). |
| Statuspagina: "De koppelstatus is onleesbaar" | Het koppelbestand van de app is beschadigd; de app vertrouwt dan niets. Maak in de Hub een nieuw koppeltoken en zet het in het formulier van de app (*Edit* → omgevingsvariabelen); het oude bestand blijft bewaard als `hub-link.json.onleesbaar-<tijd>`. |
| App blijft *Deploying* of *Crashed* | *View Logs*; de alles-in-één images melden in het Nederlands wat er mis is (bijv. `/appdata` niet schrijfbaar). |

## Terugdraaien en verwijderen (handmatig, alleen als de Hub dat niet kan)

- **Terug naar de vorige versie:** app → *Edit* → vorige Tag (met digest) → *Update*. (*Roll Back* van TrueNAS werkt bij een
  Custom App alleen tussen *ix-app*-versies, niet tussen image-tags.)
- **Verwijderen zonder dataverlies:** app → *Delete*, **Remove iXVolumes uit**. De dataset blijft staan; opnieuw
  installeren met dezelfde map herstelt de app met zijn data.
