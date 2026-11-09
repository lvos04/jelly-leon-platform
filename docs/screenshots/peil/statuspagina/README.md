# Schermafdrukken statuspagina (Peil 1.1.0)

`GET /` van jelly-leon-platform in het Peil-sjabloon, gemaakt met `make statuspagina-shots`: de echte dienst (met
PostgreSQL en de Hub-simulator) rendert elke toestand, daarna maakt Chromium (Playwright 1.65.0) volledige
schermafdrukken op 320, 360, 390, 768 en 1440 px in licht en donker (`<toestand>-<thema>-<breedte>.png`). Bijgewerkt na
de ontwerpreview ([`docs/ontwerp/PEIL_REVIEW.md`](../../../ontwerp/PEIL_REVIEW.md), ADR-0017).

| Toestand | Wat je ziet |
|---|---|
| `niet-gekoppeld` | nog nooit gekoppeld: koppelformulier (Hub-adres, koppelcode, vingerafdruk), totaal "Niet gereed" |
| `koppelen-mislukt` | antwoord op een koppelpoging met een verkeerde vingerafdruk: melding met code + trace-ID, veldfout |
| `gekoppeld` | gekoppeld en bevestigd, worker actief: "Alles gezond", koppeling als neutrale pil, geen formulier |
| `ingetrokken` | de Hub trok de koppeling in: "Niet gereed" (Storing), geen formulier, opnieuw koppelen via de Hub |
| `niet-gereed` | eigen database onbereikbaar: "Niet gereed" (Storing), eerste signaal nooit groen |

`rapport.json` is de uitkomst van dezelfde run van `tests/ui/controle.mjs` over alle toestanden (ook ontkoppeld,
koppelen bezig, invoer ongeldig, onleesbaar en beperkt) op 320/360/390/768/1024/1440 px × licht/donker: axe-core,
gemeten contrast (tekst, UI-grenzen, focusring), statusregels, microcopy, overloop, aanraakdoelen, tekstgrootte,
focusring, tekstafstand, forced colors en CSP. Adressen en vingerafdrukken zijn testwaarden (`hub.test`, sleutels van
de simulator), geen NAS-gegevens. Geen pixel-baselines (ONTWERPTAAL §12). De PNG's zijn met pngquant (palet, kwaliteit
90–100) verkleind; contrast is in de DOM gemeten, niet op de PNG's.

Alle 14 toestanden van de **live** dienst (eigen processen, formulier ingevuld door Chromium) staan in
[`docs/ontwerp/shots/`](../../../ontwerp/shots/) (`make ontwerpreview`).
