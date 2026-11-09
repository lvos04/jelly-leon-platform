# Eerste installatie van het TrueNAS Home Platform — zonder terminal, zonder YAML

Je maakt in de TrueNAS-web-UI precies **twee** standaard Custom Apps: de Hub (`ai-server-manager`) en de executor
(`ai-server-manager-executor`). Daarna installeert de Hub alles via de TrueNAS-API (contract §2). Je hebt geen
terminal, geen SSH en geen *Install via YAML* nodig.

> **Verificatiestatus — lees dit eerst.**
> - **NIET GEVALIDEERD (echte NAS):** dit stappenplan is nog niet op een echte TrueNAS doorlopen. Menu- en veldnamen
>   komen uit de broncode van de TrueNAS-web-UI (`truenas/webui` tag TS-25.10.8) en de Custom App-definitie
>   (`truenas/apps` `ix-app` 1.4.8); in 25.04 en latere versies kunnen namen iets afwijken.
> - De images van de Hub en de executor worden gebouwd in `ai-server-manager`. Tag, setupcode, wizard en koppelpagina
>   zijn van die repo; controleer de release-notes daar. Zolang er geen release is, kun je deze stappen nog niet
>   uitvoeren (**Geblokkeerd**).
> - Vul nergens voorbeeldwaarden in. NAS-adres (D03), pool en datasets (D02) en de TrueNAS-versie (D01) zijn van
>   jouw NAS; `<…>` betekent: jouw eigen waarde.

| | Hub | Executor |
|---|---|---|
| Application Name | `ai-server-manager` | `ai-server-manager-executor` |
| Image (Repository) | `ghcr.io/lvos04/ai-server-manager` | `ghcr.io/lvos04/ai-server-manager-executor` |
| Tag | exacte versie uit de release (nooit `latest`) | dezelfde releaseversie als de Hub |
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

## Stap 1 — Images ophalen mogelijk maken (GHCR)

Een nieuw GHCR-pakket is standaard **privé**. Kies één van beide:

- **A. Pakketten openbaar maken (aanbevolen, eenmalig per pakket).** Op GitHub: profiel → *Packages* →
  `ai-server-manager` → *Package settings* → *Change visibility* → *Public*. Herhaal voor
  `ai-server-manager-executor` en later voor `appstore-en-fork`, `home-control-hub`, `jelly-leon-platform`,
  `zoeken-en-persoonlijke-ai` en `jelly-leon`. Controle in de browser:
  `https://ghcr.io/token?scope=repository:lvos04/ai-server-manager:pull` moet `{"token":"…"}` tonen. Zie je
  `DENIED` ("requested access to the resource is denied", HTTP 403), dan is het pakket nog privé of bestaat het
  (nog) niet. (Gedrag van GHCR gecontroleerd op 2026-11-09 met een openbaar en een niet-bestaand pakket.)
- **B. Privé houden met registry-login in TrueNAS.** *Apps → Configuration → Sign-in to a Docker registry* →
  *Other Registry* → URI `https://ghcr.io`, gebruikersnaam = je GitHub-gebruikersnaam, wachtwoord = een GitHub-token
  met alleen `read:packages`. Dit geheim staat dan in TrueNAS, niet in een app-formulier.
  (API: `app.registry.create`; **gedrag met GHCR-tokens NIET GEVALIDEERD**.)

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
| | Tag | exacte releaseversie (het formulier vult `latest` voor — **overschrijven**) |
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
   | Tag | dezelfde releaseversie als de Hub |
   | Ports → Add | Host Port `30410` · Container Port `30410` · TCP |
   | Storage | Host Path = dataset `ai-server-manager-executor` → Mount Path `/appdata` |

3. **TrueNAS-key met schrijfrechten** (alleen voor de executor, zo beperkt mogelijk):
   - Bij voorkeur een **eigen privilege**: *Credentials → Groups → Privileges* → *Add*: naam naar keuze, Roles
     alleen wat de executor nodig heeft (`READONLY_ADMIN`, `APPS_WRITE`, `SNAPSHOT_WRITE`, `DATASET_WRITE`;
     `POOL_WRITE` alleen als snapshot-terugzetten via de executor gewenst is), Local Groups: een nieuwe groep;
     daarna een gebruiker in die groep. Anders: rol *Full Admin* (ruimer dan nodig — afgeraden).
   - *Credentials → Users → User API Keys → Add* voor die gebruiker → key kopiëren.
   - **NIET GEVALIDEERD (echte NAS):** de minimale rollenset en de exacte privilege-schermen.
4. **Koppelen via de web-UI:** in de Hub-wizard verschijnt een eenmalige **koppelcode** (30 minuten geldig). Open
   `http://<NAS-adres>:30410` (statuspagina van de executor), vul de koppelcode **en** de TrueNAS-schrijfkey in.
   De executor versleutelt de key in zijn eigen vault en toont daarna alleen de laatste 4 tekens; de Hub ziet de
   key nooit. Ontkoppelen of roteren gaat via dezelfde pagina.
5. **Belangrijk:** de executor moet TrueNAS via **`wss://` (HTTPS)** aanspreken. TrueNAS trekt een API-key die over
   een onversleutelde verbinding wordt gebruikt automatisch in (bron: middleware `auth.py`, TS-25.04/25.10). Volg de
   aanwijzingen op de executorpagina voor het TrueNAS-certificaat.

## Stap 6 — Daarna installeert de Hub de rest

Vanaf nu installeer je niets meer met de hand. In de Hub kies je *Apps → Toevoegen*; de Hub toont per app eerst een
plan (image + exacte tag/digest, poorten met conflictcontrole, datasets via de datasetkiezer, rechten, back-up-eisen,
risico) en vraagt goedkeuring. Daarna voert de executor `app.create` uit met de standaard Custom App-waarden
(`ix-app`) of — voor apps met meerdere containers zoals Authentik en Immich — de **officiële TrueNAS-catalogusapp**
met vaste versie. Eigen apps (`appstore-en-fork`, `jelly-leon-platform`, `home-control-hub`, …) krijgen daarbij een
eenmalig enrollment-token en melden zich zelf aan.

> In fase P0 bestaat dit installatieplan nog niet: `jelly-leon-platform` antwoordt `capability_unavailable` en voert
> niets uit (B08-003/B08-004/B08-006 volgen in P2).

## Wat als het misgaat

| Probleem | Oplossing |
|---|---|
| Knop *Custom App* grijs | Stap 0.2: eerst *Apps → Configuration → Choose Pool*. |
| `denied` / `unauthorized` bij ophalen image | Stap 1 niet gedaan (pakket privé, of registry-login ontbreekt). |
| Poort bezet bij opslaan | Andere Host Port kiezen; Container Port niet wijzigen. |
| Geen setupcode in de logs | App herstarten en opnieuw *View Logs*; de code staat alleen in de logs van de eerste start(s). |
| Executor: "key ingetrokken" | De key is over een onversleutelde verbinding gebruikt; maak een nieuwe key en gebruik `wss://`. |
| App blijft *Deploying* of *Crashed* | *View Logs*; de alles-in-één images melden in het Nederlands wat er mis is (bijv. `/appdata` niet schrijfbaar). |

## Terugdraaien en verwijderen (handmatig, alleen als de Hub dat niet kan)

- **Terug naar de vorige versie:** app → *Edit* → vorige Tag → *Update*. (*Roll Back* van TrueNAS werkt bij een
  Custom App alleen tussen *ix-app*-versies, niet tussen image-tags.)
- **Verwijderen zonder dataverlies:** app → *Delete*, **Remove iXVolumes uit**. De dataset blijft staan; opnieuw
  installeren met dezelfde map herstelt de app met zijn data.
