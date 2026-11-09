# Ontwerpreview statuspagina jelly-leon-platform (Peil 1.1.0)

- **Wat:** `GET /` van de lifecycle-dienst (Jelly Leon Platform) in het Peil-sjabloon 1.1.0, alle toestanden.
- **Wie / wanneer:** design-agent, onafhankelijke review achteraf (contract 1.2 §13, werkwijze) · 9 november 2026.
- **Normen:** ONTWERPTAAL.md §1, §4, §7, §11, §12, §13.2; COMPONENTEN.md §13 (Foutpaneel), §25 (statuspagina);
  WCAG 2.2 AA (axe-tags `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa`); prototypes `statuspagina.html` en
  `statuspagina-gekoppeld.html` met hun schermafdrukken in `ai-server-manager/design/ontwerptaal/shots/`.
- **Besluit:** [ADR-0017](../adr/ADR-0017-ontwerpreview-statuspagina.md). **Validatie:** Sim-getest (Chromium-emulatie,
  Hub-simulator); echte telefoon/tablet, schermlezer en NAS: **NIET GEVALIDEERD**.

## 1. Uitkomst in het kort

De pagina volgt Peil goed: kop → status → koppeling → (koppelen) → dienstblok → voet zoals het prototype, groen alleen
bij gemeten gezond, "Gekoppeld" neutraal, onbekend gestippeld en nooit groen, elke status met icoon + woord + vorm, geen
JavaScript en een strikte CSP. Gemeten over 14 live toestanden × 6 breedtes × 2 thema's: **axe 0 overtredingen**,
laagste tekstcontrast **8,34:1** (licht) en **9,32:1** (donker), laagste UI-grens **4,86:1**, laagste focusring
**7,79:1**, geen overloop op 320 px, alle bedienbare elementen ≥ 44 px, focus nooit bedekt.

Wel gevonden en **gerepareerd** (alleen uiterlijk, tekst en toegankelijkheid): hetzelfde woord met twee uiterlijken op
één pagina (B-01), woorden die de woordenlijst vervangt (B-02), een te lange dienstomschrijving die de status omlaag
duwt (B-03), een foutsamenvatting zonder link naar het veld (B-04), een skiplink die op 320/360 px onder de bovenrand
uitstak (B-05) en vier meldingsteksten (B-06…B-09). Zeven punten zitten in het gepinde sjabloon zelf en gaan als
voorstel naar Peil 1.1.1 (S-01…S-07); deze repo past de kopie bewust niet aan (O-14).

Geen grafiek, meter of stat-tegel: de pagina toont toestanden, geen meetreeksen of getallen met een eenheid (dataviz-
procedure stap 1: "is het wel een grafiek?" — nee). Statuskleuren blijven daarmee gereserveerd.

## 2. Werkwijze

1. **Echte dienst, lokaal, met testdata** (`make ontwerpreview`, script `tests/ui/live/live.mjs`): per scenario een eigen
   PostgreSQL-database en datamap, `python -m lifecycle init/migrate`, daarna `python -m lifecycle api` en
   `python -m lifecycle worker` als eigen processen, naast de Hub-simulator (`tests/ui/live/hub_review.py`, alleen
   test). Chromium vult het formulier zelf in en verstuurt het, met de echte CSRF-cookie, `Origin`-controle,
   rate-limit en 303-redirect. Testdata: adressen en vingerafdrukken van de simulator (127.0.0.1, willekeurige
   sleutels); geen NAS-gegevens.
2. **Toestanden** (14; HTTP-status en route in [`live.json`](live.json)):

   | Toestand | Hoe bereikt |
   |---|---|
   | `niet-gekoppeld` | verse dienst, worker draait |
   | `koppelen-invoer-ongeldig` | formulier met `ftp://nas`, `ABC`, `1234` |
   | `koppelen-code-geweigerd` | geldige vorm, code die de Hub niet kent |
   | `koppelen-vingerafdruk-onjuist` | echte code, verkeerde vingerafdruk |
   | `laatste-poging-mislukt` | pagina opnieuw laden na de mislukte poging |
   | `gekoppeld-bevestigd` | echte code + vingerafdruk → 303 → `/?gekoppeld=1` |
   | `gekoppeld` | pagina opnieuw laden |
   | `ontkoppeld` | de Hub ontkoppelt via `POST /connector/v1/hub-link/unlink` (dienst-JWT) |
   | `formulier-verlopen` | cookie gewist, dan versturen (CSRF) |
   | `te-veel-pogingen` | zesde poging binnen 15 minuten (429) |
   | `koppelbestand-onleesbaar` | `hub-link.json` beschadigd |
   | `koppelen-ingetrokken` | de Hub trekt direct na het koppelen in (antwoord op de POST) |
   | `ingetrokken` | pagina opnieuw laden |
   | `niet-gereed` | eigen database onbereikbaar (geen worker) |

   Daarnaast de 10 momentopnamen uit `apps/api/tests/integration/test_statuspage_peil.py` (o.a. `beperkt` = gekoppeld
   maar nog niet bevestigd, `koppelen-bezig`, leesfout van de koppelstatus) via `make statuspagina-shots`.
3. **Metingen** (`tests/ui/controle.mjs`, Chromium 158, Playwright 1.65.0, axe-core 4.14.0) per toestand × 320, 360,
   390, 768, 1024, 1440 px × licht/donker: axe; gemeten contrast in de gerenderde pagina (tekst ≥ 4,5:1; veldrand,
   knopgrens, betekenisdragende randen, iconen en trace-ID-kader ≥ 3:1); overloop en elementen buiten beeld; doelen
   ≥ 44 px; tekst ≥ 14 px; één h1; statusregels (icoon + woord per pil, icoon past bij de vorm, groen alleen bij Gezond,
   totaal nooit Gezond bij niet gereed of een niet-gezonde controle, één woord = één uiterlijk); microcopy-lint
   (§11.1); skiplink zonder focus buiten beeld; CSP zonder overtreding en zonder extra verzoek. Op 390 en 1440 een
   volledige Tab-doorloop (ring ≥ 3 px en ≥ 3:1 tegen de achtergrond, niet bedekt). Op 320 px tekstafstand volgens
   WCAG 1.4.12 (niets afgekapt of overlopend) en `forced-colors: active` (stippelrand van Onbekend en de randen
   blijven).
4. **Controle van de controle:** een opzettelijk beschadigde pagina (veldrand `#C9D0D9`, focusring `#E3ECFA`, groene
   tekst in een Onbekend-pil, een pil zonder icoon, totaal groen terwijl niet gereed, "Actueel!", vaste hoogte op een
   naam, stippelrand weg in forced colors) gaf exit 1 met precies die bevindingen: veldrand 1,55:1, focusring
   1,05–1,19:1, "groen buiten Gezond", "status alleen kleur", "totaal groen terwijl Gereed = Nee …",
   "microcopy: Actueel" en "!", "tekstafstand: afgekapt", "forced colors: stippelrand van Onbekend weg". Axe meldde
   op dezelfde pagina 0 overtredingen — de eigen metingen zijn dus nodig. De skiplink-controle ving de oude tekst
   (B-05) op 320 en 360 px.
5. **Kijken:** alle schermafdrukken bekeken en vergeleken met de prototypes (zelfde opbouw, tokens, iconen, vormen).

## 3. Metingen (live, na de reparaties)

Contrast: laagste gemeten waarde over 6 breedtes, licht / donker. Tab = aantal tabstops op 390 px (incl. skiplink).

| Toestand | HTTP | axe | Tekst | UI-grens | Focusring | Overloop 320 | Tab |
|---|---|---|---|---|---|---|---|
| `niet-gekoppeld` | 200 | 0 | 8,34 / 9,32 | 5,89 / 6,11 | 8,34 / 8,63 | 0 px | 7 |
| `koppelen-invoer-ongeldig` | 400 | 0 | 8,34 / 9,32 | 4,86 / 5,69 | 7,79 / 8,04 | 0 px | 8 |
| `koppelen-code-geweigerd` | 400 | 0 | 8,34 / 9,32 | 4,86 / 5,69 | 7,79 / 8,04 | 0 px | 8 |
| `koppelen-vingerafdruk-onjuist` | 400 | 0 | 8,34 / 9,32 | 4,86 / 5,69 | 7,79 / 8,04 | 0 px | 8 |
| `laatste-poging-mislukt` | 200 | 0 | 8,34 / 9,32 | 5,71 / 6,11 | 8,34 / 8,63 | 0 px | 7 |
| `gekoppeld-bevestigd` | 200 | 0 | 8,34 / 9,60 | 5,89 / 6,11 | 8,34 / 8,63 | 0 px | 4 |
| `gekoppeld` | 200 | 0 | 8,34 / 9,60 | 5,89 / 6,11 | 8,34 / 8,63 | 0 px | 4 |
| `ontkoppeld` | 200 | 0 | 8,34 / 10,58 | 6,36 / 7,78 | 8,34 / 9,51 | 0 px | 3 |
| `formulier-verlopen` | 403 | 0 | 8,34 / 9,32 | 4,86 / 5,69 | 8,34 / 8,63 | 0 px | 7 |
| `te-veel-pogingen` | 429 | 0 | 8,34 / 9,32 | 4,86 / 5,69 | 8,34 / 8,63 | 0 px | 7 |
| `koppelbestand-onleesbaar` | 200 | 0 | 8,34 / 10,58 | 5,71 / 8,15 | 8,34 / 9,51 | 0 px | 3 |
| `koppelen-ingetrokken` | 400 | 0 | 8,34 / 10,58 | 4,86 / 5,69 | 8,34 / 9,51 | 0 px | 3 |
| `ingetrokken` | 200 | 0 | 8,34 / 10,58 | 6,36 / 7,78 | 8,34 / 9,51 | 0 px | 3 |
| `niet-gereed` | 200 | 0 | 8,34 / 9,32 | 5,89 / 6,11 | 8,34 / 8,63 | 0 px | 7 |

De laagste tekstwaarde (8,34:1) is de link in de voet op het paginavlak; de laagste UI-grens (4,86:1) is het
stippelkader van de trace-ID (`--border-strong`) op het rode meldingsvlak; de laagste focusring (7,79:1) is de ring
rond de link in de rode melding. Alle tekst haalt ook de Peil-eis AAA
(≥ 7:1). Volledige uitkomst: [`rapport-live.json`](rapport-live.json); release-set:
[`../screenshots/peil/statuspagina/rapport.json`](../screenshots/peil/statuspagina/rapport.json).

Tokenparen uit `statuspagina.css` (berekend, licht / donker):

| Soort | Paar | Licht | Donker |
|---|---|---|---|
| tekst | `--foreground` op `--card` | 19,22:1 | 16,13:1 |
| tekst | `--muted-foreground` op `--background` | 9,56:1 | 11,77:1 |
| tekst | `--link` op `--background` | 8,34:1 | 10,58:1 |
| tekst | `--healthy-ink` op `--card` (Gezond, zonder vlak) | 10,42:1 | 12,82:1 |
| tekst | `--degraded-ink` op `--degraded-bg` | 9,47:1 | 10,87:1 |
| tekst | `--down-ink` op `--down-bg` | 9,39:1 | 10,76:1 |
| tekst | `--unknown-ink` op `--unknown-bg` | 11,16:1 | 11,26:1 |
| tekst | `--gate-ink` op `--gate-bg` | 17,29:1 | 14,98:1 |
| tekst | `--info-ink` op `--info-bg` | 9,42:1 | 10,94:1 |
| tekst | `--primary-foreground` op `--primary` (knop) | 9,45:1 | 9,32:1 |
| UI | `--input` op `--card` (veldrand) | 5,89:1 | 6,11:1 |
| UI | `--unknown` op `--card` (stippelrand Onbekend) | 6,36:1 | 7,78:1 |
| UI | `--down` op `--background` (rand foutmelding) | 6,11:1 | 9,65:1 |
| UI | `--ring` op `--card` / `--background` (focus) | 9,45 / 8,34:1 | 8,63 / 9,51:1 |

Toetsenbord (alle toestanden): eerste tabstop is de skiplink ("Naar inhoud", of bij een veldfout "Naar het veld met
de fout"), daarna de link in de melding (alleen bij een veldfout), de drie velden, "Koppelen met de Hub", "Hub openen"
(gekoppeld) en de twee links in de voet. Geen focusval, geen tabindex, volgorde = leesvolgorde. Enter verstuurt het
formulier; er zijn geen andere sneltoetsen. Schermlezer (verwacht, **niet getest** met NVDA/TalkBack): landmarks
`header`/`main`/`footer`, één h1, secties met `aria-labelledby`, `ul aria-label="Controles"`, iconen `aria-hidden`,
`<time datetime>`, velden met label + `aria-describedby` (hulp + fout) + `aria-invalid`.

## 4. Bevindingen

Ernst: **Hoog** = verkeerde of tegenstrijdige statusinformatie of een blokkade; **Middel** = wijkt af van Peil of
maakt de taak moeilijker; **Laag** = afwerking.

### Gerepareerd in deze repo

| # | Bevinding | Regel | Ernst | Reparatie |
|---|---|---|---|---|
| B-01 | Hetzelfde woord met twee uiterlijken op één pagina: "Niet gekoppeld" en "Ontkoppeld" met `circle-help` in de controles en met `link-2-off` in het koppelblok; "Ingetrokken" als rode Storing-pil én als grijze stippelpil. Daarnaast woorden buiten de vaste statuswoorden in de controles ("Onleesbaar", "Nog niet bevestigd", "Koppelen bezig"). | §1.8, §7 (één icoon per betekenis), §11.1; WCAG 3.2.4 | Hoog | Controlerijen tonen alleen Gezond · Beperkt · Storing · Onbekend, de koppeltoestand staat in de reden ("Onbekend — Nog niet gekoppeld: …", "Storing — Ingetrokken door de Hub: …"). Het woord van de koppeltoestand komt alleen nog in het koppelblok voor (met `link-2-off`). De toon volgt de connectorstatus (`/connector/v1/health`). Nieuwe controle "één woord, verschillend uiterlijk". |
| B-02 | "(de eigen afhankelijkheden zijn in orde)", "… zijn niet in orde", "eigen afhankelijkheden niet in orde": de woordenlijst vervangt "In orde" door Gezond en "Niet gezond" door Storing. | §11.1 | Middel | "(eigen database, schema en geheimen zijn gezond)"; totaal per toon: "Storing — de eigen database, het schema of de geheimen werken niet; zie de controles hieronder." / "Deels onbekend — niet alle eigen afhankelijkheden zijn te controleren; …"; Gereed: "Nee — /readyz meldt not_ready (eigen database, schema of geheimen)". Microcopy-lint is nu hoofdletterongevoelig voor deze twee. |
| B-03 | Dienstomschrijving van 7 regels op 320 px met vaktermen ("Lifecycle-dienst", "connectorcontract") en een herhaling van "TrueNAS Home Platform" uit de kop; de status staat daardoor lager dan nodig. | §1.2 (status eerst), §11 (toon) | Middel | "Houdt de levenscyclus van apps bij: installeren, bijwerken, terugzetten, verwijderen en verhuizen. Deze pagina toont alleen de status; alle schermen staan in de Hub." Gemeten: 5 regels i.p.v. 7 op 320 px, onderkant van de totaalstatus op 387 px i.p.v. 435 px (op 390 px: 363 i.p.v. 411). |
| B-04 | Foutsamenvatting "Controleer de invoer" (drie veldfouten) had geen link naar het veld, terwijl de Hub-fouten die wel hebben. | COMPONENTEN §13, §25; ADR-0013 punt 5; WCAG 3.3.1 | Middel | De melding linkt naar het eerste foute veld ("Naar het veld Adres van de Hub"); tekst: "Niet alle velden kloppen; bij elk veld met een fout staat wat er mis is. Er is niets naar de Hub gestuurd." |
| B-05 | Skiplink "Naar het veld Vingerafdruk van de Hub (fout)" liep op 320 en 360 px over twee regels; omdat het sjabloon hem met `top:-4rem` verbergt, stak de onderkant (± 4 px) zonder focus boven in beeld. | §12 (A-11), WCAG 2.4.1 | Laag | Korte vaste tekst "Naar het veld met de fout" (past op 320 px op één regel); nieuwe controle "skiplink zonder focus deels in beeld". Oorzaak in het sjabloon: S-05. |
| B-06 | "Te veel koppelpogingen" zei niet waarom. | §11 (wat · waarom · wat nu) | Laag | "Ter bescherming tegen raden neemt deze pagina tijdelijk geen pogingen aan. Wacht 15 minuten en probeer het dan opnieuw. Er is niets naar de Hub gestuurd." |
| B-07 | "Koppelbestand onleesbaar": "geen enkele Hub-aanmelding" en "(nieuw token in het TrueNAS-formulier)" waren vaag over gevolg en weg terug. | §11 | Laag | "Het koppelbestand van deze dienst is onleesbaar. Daarom vertrouwt de dienst geen enkele aanmelding van de Hub en voert hij niets uit. Opnieuw koppelen gaat via de Hub, met een nieuw koppeltoken in het TrueNAS-formulier van deze app." |
| B-08 | Foutmelding vingerafdruk: "Hub-sleutels" en "Hub-UI" (geen Peil-termen; elders heet het "de Hub" en "vingerafdruk van de Hub"). | §11.1 (vingerafdruk) | Laag | "De vingerafdruk van de Hub klopt niet; er is niets opgeslagen. Controleer het adres van de Hub en vergelijk de vingerafdruk met die in de Hub." (ook in de connectorweergave). |
| B-09 | Dienstblok: "antwoordt de API **eerlijk** capability_unavailable" (zelfoordeel) en de gate-zin "er wacht een voorwaarde" bij een fase; "vanaf P2" zonder "fase". | §11, §11.2 (Nog niet beschikbaar) | Laag | "Volgt in fase P2. Tot dan antwoordt de API met capability_unavailable en voert deze dienst niets uit op de NAS. Dit is geen storing." en "Vanaf fase P2 loopt uitvoering uitsluitend via de executor van de Hub, …". |

### In het sjabloon (voorstel voor Peil 1.1.1, eigenaar ai-server-manager)

| # | Bevinding | Regel | Ernst | Voorstel |
|---|---|---|---|---|
| S-01 | Native browservalidatie gaat vóór de NL-veldfout: `type="url"` + `required` blokkeren "192.168.1.10:30400" (een gangbare invoer) met de browsermelding "Please enter a URL." / "Voer een URL in." zonder vormhint en zonder Peil-stijl; "nas.local:30400" gaat wél door en krijgt de NL-fout. Gemeten in Chromium. | §9 (formulier: veldfout bij het veld + samenvatting), WCAG 3.3.1/3.3.3 | Middel | `novalidate` op alle formulieren van de statuspagina; de diensten valideren al alles server-side met vaste NL-teksten. |
| S-02 | Een controlerij kan alleen het icoon van zijn toon tonen. "Niet gekoppeld" (`link-2-off`), "Nog niet beschikbaar" (`hourglass`) en beleid (`ban`) kunnen daardoor niet met hun eigen icoon; deze dienst toont "Nog niet beschikbaar" nu met het slot van de gate. | §4.3, §7 | Middel | Tonen `nog-niet` (hourglass, neutraal op `--muted`) en `beleid` (ban), of een optionele plek `{{icoon}}` per rij uit een vaste lijst. |
| S-03 | De melding heeft altijd `role="alert"`, ook bij een blijvende toestand die bij het laden van de pagina al bestaat (laatste mislukte poging, onleesbaar bestand). | COMPONENTEN §13 ("geen alert-spam bij laden") | Laag | Plek voor de rol: `alert` alleen bij het antwoord op een verzoek, anders geen live-rol. |
| S-04 | De code staat alleen in een opmaak met trace-ID; blijvende toestanden zetten de code daarom in de lopende tekst (niet in mono). | §5 (codes in mono) | Laag | `melding_code` los van `trace_id` (al gemeld in ADR-0013). |
| S-05 | `.spring` wordt verborgen met `top:-4rem`; elke skiplinktekst die omloopt (smal scherm, grote letter, tekstafstand) steekt uit. | §12 (A-11) | Laag | Verbergen met `transform: translateY(-100%)` vanaf `top:0` (of `clip-path` tot `:focus`), zodat de hoogte niet uitmaakt. |
| S-06 | Het prototype `statuspagina.html` toont zelf "Niet gekoppeld" met `circle-help` in de controles en met `link-2-off` in het koppelblok (zelfde punt als B-01). | §7 | Laag | Prototype en COMPONENTEN §25 aanpassen naar de vaste statuswoorden in de controles. |
| S-07 | `bouw/sjabloon.mjs` vult `{{x}}` na de blokken nog eens over de hele tekst in (dubbele vervanging; al gemeld in ADR-0013). | §13.2 | Laag | Eén doorgang, zoals de Python-renderer van deze dienst. |

### Bewust zo gelaten

- **"Nog niet beschikbaar" met het slot (gate-toon):** het woord is het vaste woord van §11.1 (zelfde als in de Hub);
  het icoon volgt pas na S-02. Het Peil-prototype van de executor gebruikt de gate-toon op dezelfde manier.
- **Tijden in UTC met "UTC" erbij:** zonder JavaScript kent de pagina de tijdzone van de browser niet; de dienst draait
  in UTC (ADR-0013 punt 8). Eerlijker dan een tijd zonder zone.
- **"Koppeling met de Hub" als controle én als blok:** twee assen (gezondheid van de koppeling vs. koppeltoestand),
  zoals in het prototype; na B-01 hebben ze ook verschillende woorden.
- **Herhaling bij ingetrokken** (melding, totaal, controle, koppelblok): elk blok heeft een eigen taak en wordt ook
  los gelezen (schermlezer springt per kop); ingekort waar het kon ("Ingetrokken door de Hub: de API weigert elke
  aanvraag.").

## 5. Niet gevalideerd / open

- Echte Android-telefoon en tablet (ook in fel licht), echte schermlezer (NVDA, TalkBack), echte browserzoom 200 %:
  **NIET GEVALIDEERD (hardware)**. Reflow op 320 px (WCAG 1.4.10) en tekstafstand (1.4.12) zijn in Chromium gemeten;
  forced colors alleen geëmuleerd (`forced-colors: active`).
- Op een echte TrueNAS (o.a. bron-IP achter de poortpublicatie voor de rate-limit): **NIET GEVALIDEERD (echte NAS)**.
- Sjabloonpunten S-01…S-07: wachten op Peil 1.1.1 in ai-server-manager; daarna kopie bijwerken
  (`tools/peil_kopie.py bijwerken`) en `make test-ui test-ui-live statuspagina-shots`.

## 6. Schermafdrukken en reproduceren

- Release-set (pytest-momentopnamen van de echte dienstcode): `docs/screenshots/peil/statuspagina/` — 5 hoofdtoestanden
  × 320/360/390/768/1440 × licht/donker (`make statuspagina-shots`).
- Live review: `docs/ontwerp/shots/` — alle 14 toestanden × 320 en 1440 × licht/donker (`make ontwerpreview`). De
  afdrukken op 360/390/768 zijn gemaakt en bekeken maar niet gecommit; maak ze met
  `make ontwerpreview REVIEW_BREEDTES=320,360,390,768,1440`.
- PNG's zijn met pngquant (palet, kwaliteit 90–100) verkleind; contrast is in de DOM gemeten, niet op de PNG's.
- Alleen de controles, zonder afdrukken: `make test-ui` (pytest-momentopnamen) en `make test-ui-live` (live dienst).
  In CI draaien beide in de job `statuspagina`.
