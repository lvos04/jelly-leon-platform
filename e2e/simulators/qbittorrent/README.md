# qBittorrent voor de downloadketen

## Starten

Start `python -m qbittorrent_sim` op loopback, standaardpoort 30218. Voor de MSO-container en
de bronpin zie de gedeelde README. `GET /_sim/sleutels` levert de naam en het willekeurige wachtwoord
voor deze run. Gebruik een eigen `--port` voor een gelijktijdige proef.

## Protocol

De MSO-mock levert de WebAPI van qBittorrent/qBittorrent tag `release-5.1.2`, commit
`202ff8a099cfc1ca9421e7d30944b3c58a4a47cd`. Meld de testsessie aan met een formulier op
`POST /api/v2/auth/login` en bewaar de SID-cookie. Een ontbrekende of ongeldige sessie geeft
403 bij de beveiligde routes. Een mislukte formulierlogin levert HTTP 200 met `Fails.`.
[Bron van de WebAPI](https://github.com/qbittorrent/qBittorrent/tree/202ff8a099cfc1ca9421e7d30944b3c58a4a47cd/src/webui/api).

| Wat EX41 meet | Lezen | Schrijven in de positieve controle |
|---|---|---|
| Privéstatus | `torrents/info` geeft de hashes. `torrents/properties?hash=…` geeft `is_private`. In `torrents/trackers` staan DHT en PeX voor de private torrent op status 0. | De privéstatus komt uit de torrentmetadata. |
| Globale DHT- en PeX-voorkeuren | `app/preferences`, velden `dht` en `pex` | `POST app/setPreferences` met formulierveld `json` |
| Seedbeleid per torrent | `torrents/info?hashes=…`, velden `ratio_limit`, `seeding_time_limit`, `inactive_seeding_time_limit` | `POST torrents/setShareLimits` met `hashes`, `ratioLimit`, `seedingTimeLimit` en `inactiveSeedingTimeLimit` |

De routes in de tabel hebben het voorvoegsel `/api/v2/`. Het verzoeklog telt ook een schrijfaanroep
die een instelling later terugzet. Vergelijk voor en na de run zowel de velden als de schrijfroutes.

## Foutinjectie

De gemeenschappelijke `/_sim/fouten` ondersteunt onder meer eenmalige 503, vertraging en ongeldige JSON.
`/_sim/verzoeken` vermeldt de toegepaste fout zonder querywaarden, sessiecookie of wachtwoord.
`POST /_sim/reset` herstelt de voorkeuren en torrents, trekt sessies in en maakt een nieuw testwachtwoord.

## Niet nagebootst (afwijkingen)

De vaste torrentfixture simuleert metadata, voorkeuren en seedlimieten. Trackercontact, peers,
DHT-verkeer, verspreiding, seedingtijd en overdracht van media vinden buiten deze proef plaats.
De simulatie van WebAPI 5.1.2 gebruikt een sessiecookie voor EX41.
