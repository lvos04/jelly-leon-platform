# Schermafdrukken statuspagina (Peil 1.1.1, contract 1.3)

`GET /` van jelly-leon-platform in het Peil-sjabloon, gemaakt met `make statuspagina-shots`: de echte dienst (met
PostgreSQL en de Hub-simulator) rendert elke toestand, daarna maakt Chromium (Playwright 1.65.0) volledige
schermafdrukken op 320, 360, 390, 768 en 1440 px in licht en donker (`<toestand>-<thema>-<breedte>.png`). Bijgewerkt na
de ontwerpreview ([`docs/ontwerp/PEIL_REVIEW.md`](../../../ontwerp/PEIL_REVIEW.md), ADR-0017) en opnieuw gemaakt bij de
overstap naar Peil 1.1.1 en contract 1.3 (ADR-0019).

| Toestand | Wat je ziet |
|---|---|
| `niet-gekoppeld` | nog nooit gekoppeld: koppelformulier (Hub-adres, koppelcode, vingerafdruk), totaal "Niet gereed" |
| `koppelen-mislukt` | antwoord op een koppelpoging met een verkeerde vingerafdruk: melding (`role="alert"`) met code + trace-ID, veldfout |
| `gekoppeld` | gekoppeld en bevestigd, worker actief: "Alles gezond", koppeling als neutrale pil, "Hub openen" als knop, geen formulier |
| `ingetrokken` | de Hub trok de koppeling in: "Niet gereed" (Storing), geen formulier, opnieuw koppelen via de Hub |
| `niet-gereed` | eigen database onbereikbaar: "Niet gereed" (Storing), eerste signaal nooit groen |
| `koppelen-bezig` | er loopt een poging (env-token): pil "Koppelen bezig", geen formulier en geen stappen (1.1.1, E02) |
| `onleesbaar` | koppelbestand onleesbaar: pil Storing "Onleesbaar", geen formulier, herstel met een nieuw koppeltoken; melding zonder live-rol, met code en pad (1.1.1, E01; contract 1.3 §3.5) |

`rapport.json` is de uitkomst van dezelfde run van `tests/ui/controle.mjs` over alle 12 toestanden (ook ontkoppeld,
invoer ongeldig, beperkt (niet bevestigd), verouderde hartslag en een adres dat geen IP-adres of lokale naam is) op
320/360/390/768/1024/1440 px × licht/donker: axe-core, inhoud buiten zijn element (1.1.1),
gemeten contrast (tekst, UI-grenzen, focusring), statusregels, microcopy, overloop, aanraakdoelen, tekstgrootte,
focusring, tekstafstand, forced colors en CSP. Adressen en vingerafdrukken zijn testwaarden (`hub.test`, sleutels van
de simulator), geen NAS-gegevens. Geen pixel-baselines (ONTWERPTAAL §12). De PNG's zijn met pngquant (palet, kwaliteit
90–100) verkleind; contrast is in de DOM gemeten, niet op de PNG's.

Alle 17 toestanden van de **live** dienst (eigen processen, formulier ingevuld door Chromium, ook de negatieve
herkomstgevallen van contract 1.3 §3.7) staan in [`docs/ontwerp/shots/`](../../../ontwerp/shots/) (`make ontwerpreview`).
Het pad van het koppelbestand in de melding "onleesbaar" is in de momentopnamen vervangen door het pad in de image
(`/appdata/secrets/hub-link.json`); het tijdelijke testpad hoort niet in de repo.
