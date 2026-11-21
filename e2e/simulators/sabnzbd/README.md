# SABnzbd voor de downloadketen

## Starten

Start `python -m sabnzbd_sim` op loopback, standaardpoort 30217. De gedeelde README geeft het
containercommando en de bronpin voor `msomocks.sabnzbd`. Haal de tijdelijke `apikey` op via
`GET /_sim/sleutels`. Met `--port` kan elke proef een eigen poort krijgen.

## Protocol

Gebruik `/api` met `output=json`, `mode` en de testsleutel. Zonder sleutel geeft de beveiligde API
HTTP 403 met `API Key Required`, bij een verkeerde sleutel HTTP 403 met `API Key Incorrect`. De versie is openbaar.

| Proef | Bediening en meting |
|---|---|
| EX02, EX39 | `addurl` of `addfile` met een onbekende `cat` plaatst de taak onder `*`. Lees `queue` terug en vergelijk de `nzo_id`. Een `set_config` voor `section=categories` maakt de nieuwe categorie bruikbaar voor volgende taken. |
| EX40 | Lees de drie testservers met `get_config&section=servers`. Zet achtereenvolgens twee afzonderlijke servers op uitval en lees na iedere stap `status` of `fullstatus`. `serveractive` en `servererror` tonen de storing. De ingestelde waarde `enable` blijft onafhankelijk leesbaar. |
| Controle van schrijfacties | SAB gebruikt GET en POST voor wijzigingen. `/_sim/verzoeken` bevat daarom ook `details.mode`, en waar van toepassing `section` of `name`. Alleen bekende protocolwaarden worden opgenomen. Vergelijk de verwachte wijziging met de categorieën of serverconfiguratie die SAB daarna teruggeeft. |

De protocolbron is sabnzbd/sabnzbd tag `4.3.3`, commit `f24b3ced280e381d0aac8bd9d9d98930ad397a59`.
De categoriekeuze en de configuratie volgen `sabnzbd/misc.py` en `sabnzbd/api.py`.
[Bron van de categorie- en server-API](https://github.com/sabnzbd/sabnzbd/blob/f24b3ced280e381d0aac8bd9d9d98930ad397a59/sabnzbd/api.py).

## Foutinjectie

`POST /_sim/serveruitval` verwacht `{"server":"<naam uit get_config>","uitgevallen":true}`.
Herstel dezelfde server met `false`. Een onbekende naam geeft 404. De fout beïnvloedt de bestaande
SAB-mock, inclusief status, waarschuwingen en servertest. Ook het laatste exemplaar kan uitvallen, zodat
beleidskeuzes van MSO meetbaar blijven. `/_sim/fouten` levert daarnaast de algemene protocolstoringen.
`POST /_sim/reset` herstelt alle testservers, categorieën, taken en sleutel.

## Niet nagebootst (afwijkingen)

De taken en providerstoringen bestaan in geheugen. Er zijn geen Usenet-accounts, verbindingen met
newsservers, echte downloads of bestanden op schijf. De aparte SAB-fake van MediaGuard maakt
proefbestanden voor import. Voor deze scenario’s levert de MSO-mock de benodigde categorie- en server-API.
