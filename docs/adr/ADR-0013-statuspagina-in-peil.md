# ADR-0013 — Statuspagina in het Peil-sjabloon (JP11)

- Status: aanvaard · Datum: 2026-11-09 · Contract: 1.2 §3.5, §3.7, §13 · Ontwerptaal: Peil 1.1.0 (ONTWERPTAAL §4, §11,
  §12, §13.2, §14; ADR-0015 van de Hub)
- Herziet: ADR-0004 en ADR-0011 punt 7 (alleen de weergave en de formuliervelden van de statuspagina)

## Aanleiding

`GET /` was een eigen HTML-pagina met eigen kleuren ("In orde" in groen, ook voor "gekoppeld"). Contract 1.2 §13 en
ONTWERPTAAL §13.2 schrijven voor alle nieuwe diensten één framework-vrij sjabloon voor (`statuspagina.html` + `.css` uit
`ai-server-manager/design/ontwerptaal`), als versie-gepinde kopie met een CI-controle op drift (O-14). De
koppelregels van §3.7 (formulier alleen zolang nooit gekoppeld, verplichte vingerafdruk, geen lokale ontkoppelcode)
waren al gebouwd (JP5) en blijven.

## Besluit

1. **Kopie en herkomst.** `design/ontwerptaal/statuspagina.{html,css}` = bron op commit `172ee87` met een herkomstkop
   van 2 regels (versie, bron + commit, sha256). De dienst serveert een **byte-gelijke** kopie als pakketdata
   (`lifecycle/peil/statuspagina.html`), zodat het sjabloon in de image zit zonder het Docker-bouwrecept te veranderen.
   `tools/peil_kopie.py` maakt en controleert de kopie; `tests/repo/test_peil_kopie.py` faalt bij schade aan de kopie,
   bij een runtime-kopie die afwijkt, bij een kop die niet klopt met de bron op de gepinde commit, en bij **drift**:
   een andere sha256 op de kop van de branch in ai-server-manager. In CI is de bron verplicht (de workflow haalt de
   publieke repo alleen-lezend op); lokaal wordt de naburige map gebruikt of worden die twee controles overgeslagen.
2. **Renderer** (`lifecycle.peil`): de semantiek van `bouw/sjabloon.mjs` (`{{x}}`, `ALS`, `NIET`, `ELK`), maar in
   **één doorgang**: een ingevulde waarde wordt nooit opnieuw als plek gelezen. (In de referentie-renderer loopt de
   `{{x}}`-vervanging na het invullen van blokken nog eens over de hele tekst; een waarde met `{{csrf_token}}` zou dan
   worden ingevuld — gemeld als vervolgpunt voor de design-agent.) Een ontbrekende plek is een fout, toonwaarden komen
   alleen uit `gezond|beperkt|storing|onbekend|gate`, `melding_toon` uit `fout|info`. Herkomstkop en bouwersopmerking
   gaan er bij het laden af; een ander sjabloon dan Peil 1.1.0 wordt geweigerd (tests vangen een versie-sprong).
3. **Koppeltoestand** = precies één blok: `koppelformulier` (nooit gekoppeld, ook tijdens een lopende poging — dan met
   een info-melding; een tweede poging krijgt 409), `gekoppeld` (neutrale pil, nooit groen), `ontkoppeld`,
   `ingetrokken`. **Onleesbaar koppelbestand**: het sjabloon heeft daarvoor geen blok; de veiligste keuze is géén
   blok (dus nooit het formulier, fail-closed zoals ADR-0011) plus een foutmelding met code `hub_link_onleesbaar`.
4. **Eerste signaal** (totaal) = slechtste van de controles (Storing > Beperkt > Onbekend > Gezond) én gereedheid.
   Gereed = `/readyz` in orde **én** gekoppeld (zonder koppeling weigert de API alles). Niet gereed → label
   "Niet gereed", toon minstens Onbekend, nooit groen. "Alles gezond" alleen als gereed en elke controle nu gemeten en
   gezond is. De rij "Koppeling met de Hub" staat alleen in de controles als het een uitzondering is (niet gekoppeld,
   bezig, ontkoppeld, ingetrokken, onleesbaar, nog niet bevestigd); gekoppeld en bevestigd staat alleen neutraal in
   "Koppeling met de Hub" (Peil §4.1: groen nooit voor "verbonden").
5. **Meldingen en fouten** uit een vaste lijst (`MELDINGEN`), nooit weerspiegelde invoer; invoervelden worden na een
   fout niet teruggevuld. Een fout van dit verzoek toont titel, tekst, code en de **trace-ID** van het verzoek
   (selecteerbaar blok, geen JS); dezelfde trace-ID staat in de app-log. Een opgeslagen toestand (laatste mislukte
   poging, onleesbaar bestand) toont de code in de tekst, zonder trace-ID van een ander verzoek. Veldfouten krijgen
   `aria-invalid` en een link vanuit de melding; de skiplink springt dan naar het eerste foute veld. Alle velden worden
   tegelijk gecontroleerd. "Gekoppeld met de Hub" (na `?gekoppeld=1`) verschijnt alleen als de koppeling werkelijk
   bevestigd is — de queryparameter kan iedereen intypen.
6. **Formuliervelden** volgen het sjabloon: `hub_adres`, `koppelcode`, `hub_vingerafdruk` (+ `csrf`). Te lange,
   lege of ontbrekende velden geven nu een HTML-fout (400) in plaats van een JSON-422 van FastAPI. CSRF, `Origin`,
   rate-limit (5 per bron-IP / 20 dienstbreed per 15 min) en de vergrendeling uit ADR-0012 zijn ongewijzigd.
7. **CSP** van de pagina: `default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none';
   base-uri 'none'` (het sjabloon gebruikt inline `<style>` en twee `style`-attributen; `form-action` en
   `frame-ancestors` vallen niet terug op `default-src`). Daarnaast `Cache-Control: no-store`,
   `Referrer-Policy: same-origin` (voor de `Origin`-controle) en de algemene koppen.
8. **Tijden** in UTC met "UTC" erbij (`9 nov 2026, 11:56 UTC`): de dienst draait in UTC en kent de tijdzone van de
   gebruiker niet; zonder JS kan de browser niet omrekenen.
9. **Tests:** rendering per toestand (niet gekoppeld, koppelen mislukt, invoer ongeldig, gekoppeld, beperkt,
   ingetrokken, ontkoppeld, koppelen bezig, onleesbaar, niet gereed bij database-storing en zonder geheimen) met de
   echte dienst, PostgreSQL en de Hub-simulator; daarna in Chromium (`tests/ui/controle.mjs`, Playwright 1.65.0,
   axe-core 4.14.0): axe 0 overtredingen, geen overloop van 320 tot 1440 px, doelen ≥ 44 px, tekst ≥ 14 px, focusring
   ≥ 3 px en niet bedekt, geen CSP-overtreding of extra verzoek, licht én donker. Schermafdrukken 360/768/1440 ×
   licht/donker in `docs/screenshots/peil/statuspagina/` (geen pixel-baselines, ONTWERPTAAL §12).

## Gevolgen

- De pagina oogt zoals de andere platformdiensten en de Hub; "In orde" heet nu "Gezond", gekoppeld is neutraal.
- Een nieuwe Peil-versie in ai-server-manager maakt de CI van deze repo rood tot de kopie is bijgewerkt (bewust, §14).
- Niet gedaan / niet gevalideerd: echte schermlezer (NVDA/TalkBack), echte telefoon of tablet, forced colors en 200 %
  zoom (Peil §12 noemt die voor de acceptatie, 08_UI); de bron-IP achter de TrueNAS-poortpublicatie blijft
  **NIET GEVALIDEERD (echte NAS)**.
- Ontwerpreview door de design-agent (contract §13, werkwijze) is nog niet gedaan; de schermafdrukken liggen klaar.

## Contractpunten (gemeld)

- Het sjabloon kent geen koppeltoestand voor een onleesbaar of onbekend koppelbestand (ADR-0011: fail-closed); hier
  opgelost met geen blok + foutmelding. Voorstel: een blok `koppeling_onbekend` in Peil.
- Het sjabloon toont de code alleen samen met een trace-ID; voor blijvende toestanden zonder verzoek-trace staat de
  code daarom in de tekst. Voorstel: `melding_code` los van `trace_id`.
- `bouw/sjabloon.mjs` vult `{{x}}` na de blokken nog eens over de hele tekst in (dubbele vervanging, zie punt 2).
