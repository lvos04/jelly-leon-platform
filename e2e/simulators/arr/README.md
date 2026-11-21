# Seerr, Sonarr, Radarr en Prowlarr voor de downloadketen

## Starten

Installeer `e2e/simulators[test]` en start `python -m arr_sim`. Seerr luistert op de basis-URL van poort
30216. Sonarr en Radarr hebben elk een eigen URL-basis, sleutel en bibliotheek in hetzelfde proces.
`GET /_sim/sleutels` geeft de testsleutels per instantienaam. Gebruik een vrije `--port` bij parallelle proeven.

Voor EX02 accepteert `--sab-url` loopback en de interne dienstnamen `sabnzbd` en `sabnzbd-sim`. Geef de
huidige testsleutel uit de SAB-bediening door via `SIM_SAB_APIKEY`. Na een SAB-reset is een nieuwe sleutel nodig.
Prowlarr draait apart op 30219 met `python -m arr_sim --prowlarr`. De gedeelde README beschrijft de MSO-container.

## Protocol

De vier URL-basissen zijn `/sonarr-series`, `/sonarr-anime`, `/radarr-films` en `/radarr-animefilms`.
Daarachter ligt hun eigen `/api/v3`. De keuze voor een anime-server is expliciet, ook bij een anime-titel.

| Aanroep | Waarneembaar resultaat |
|---|---|
| Seerr `GET /api/v1/settings/{sonarr,radarr}` en `POST /api/v1/settings/{sonarr,radarr}/test` | Server 0 is de standaard, server 1 de anime-instance. De verbindingstest leest profiel, rootmap en tags van het gekozen testdoel. |
| Seerr `POST /api/v1/request` | Gebruik `{"mediaType":"tv","mediaId":101,"seasons":[1]}` voor de gewone serie. De anime heeft media-ID 102 en voor de anime-instance ook `"serverId":1`. Testfilm 103 gebruikt `"mediaType":"movie"`. Het verzoeklog toont de POST op de gekozen arr-instance. |
| Seerr `GET /api/v1/request` en `GET /api/v1/request/{id}` | Aanvragen bevatten de service- en bibliotheek-ID. Paginering via `take`, `skip`, `filter=all`, `sort=added`. Herhaalde serieaanvraag voor hetzelfde seizoen geeft 202, herhaalde filmaanvraag 409. |
| Sonarr/Radarr `series` of `movie` | Zoeken in de testcatalogus met `/lookup`, toevoegen, lijst en detail. Seerr gebruikt dezelfde schrijfroutes en eigen API-sleutels als een externe testclient. |
| `episode`, `episodefile`, `qualityprofile`, `customformat` | De anime heeft twee bestanden met twee audiosporen. TVDB geeft absolute nummers 13 en 14, de scene-bron 14 en 13. De anime-instance heeft een kwaliteitsprofiel met een positief gewicht voor Dual Audio. |
| `POST /command`, `GET /command/{id}` | `RefreshSeries` wisselt de leesvolgorde, met dezelfde aflevering- en bestands-ID’s. `EpisodeSearch`, `SeasonSearch`, `SeriesSearch` en `MoviesSearch` verwerken de toepasselijke testreleases. |
| `GET /release`, `POST /release`, `GET /queue` | Zoek op aflevering, serie plus seizoen, of film. Alleen een gevonden GUID en indexer-ID kunnen naar SAB. Een taak verschijnt in de arr-wachtrij na een bevestigde SAB-taak met download-ID. |
| Prowlarr `/api/v1/indexer/schema`, `/api/v1/indexer`, `/api/v1/search` | Registreer MSO als Newznab-indexer met zijn **interne** basis-URL. De MSO-mock doet de terugoproep met Prowlarr als afzender. `GET /_sim/zoekstatistiek` toont zoekopdrachten, terugoproepen, mislukte terugoproepen en maximale zoekdiepte. |

Seerr gebruikt `X-Api-Key` op de beveiligde routes en geeft 403 bij weigering. De arr- en Prowlarr-API gebruiken
`X-Api-Key` of `apikey` en geven 401. Bij een onbereikbare doelinstantie wordt geen geslaagde aanvraag geregistreerd.

De API-bronnen zijn Sonarr/Sonarr `v4.0.20.3014` (`cab419ade8ac7fcab5bf80394ee492abd35d5f5a`, forkbasis
`bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a`), Radarr/Radarr `v6.4.4.10685`
(`a96bf7c3ecb23cb76993770727285dc17a613fbc`) en seerr-team/seerr `v3.5.1`
(`564bfb08b54caa374bddaf820abfd226e5e69151`). De Prowlarr-mock volgt Prowlarr/Prowlarr
`v1.24.3.4754` (`17e1a72baf2b547e5944e04434af1c1895391f4f`). De afnemers en de vastgelegde
routeringsproeven staan in `media-guard-ultimate@9d2b1cf35c5daf0c96a4d8d71eace8faf5871453`,
`apps/core/mediaguard/{arr,seerr,routing,library}` en `tests/contract/seerr/RESULTS.md`.
[Seerr-aanvragen](https://github.com/seerr-team/seerr/blob/564bfb08b54caa374bddaf820abfd226e5e69151/server/routes/request.ts)
· [Sonarr-releases](https://github.com/Sonarr/Sonarr/blob/bc54afbdd0e38bdb0fca4785fcf7d7a6af77bf9a/src/Sonarr.Api.V3/Indexers/ReleaseController.cs)
· [Radarr-films](https://github.com/Radarr/Radarr/blob/a96bf7c3ecb23cb76993770727285dc17a613fbc/src/Radarr.Api.V3/Movies/MovieController.cs).

## Foutinjectie

De gemeenschappelijke `/_sim/fouten` werkt ook op de interne POST van Seerr naar de gekozen instantie.
Beperk bijvoorbeeld `http_503` tot `/sonarr-anime/api/v3/series$` met `aantal: 1`. In `/_sim/verzoeken`
staat bij het betrokken verzoek de toegepaste fout. `POST /_sim/reset` wist aanvragen, bibliotheken,
wachtrijen en log en roteert de testsleutels. Prowlarr heeft dezelfde bediening op zijn eigen poort.

## Niet nagebootst (afwijkingen)

De catalogus bevat drie synthetische titels en één seizoen per serie. Alleen de genoemde beheer- en
zoekroutes zijn uitgewerkt. Seerr voert de aanvraag direct uit en meldt een downstreamfout aan de
aanroeper. Authenticatie via gebruikerssessies, goedkeuringswachtrijen, cutover, externe metadata,
fysieke mediabestanden, indexerkwaliteit en echte download- of importvoortgang vallen buiten de proef.
De MSO-Radarr-fixture blijft beschikbaar voor zijn bestaande proef met een vaste film en wachtrij.
Seerr-routering, twee afzonderlijke bibliotheken en afleveringsbestanden vereisen deze arr-fixture.
