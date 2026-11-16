# ADR-0027 — Controlelijsten voor de bestaande apps (I03, I04, I05, I15 tot en met I20)

- Status: aanvaard · Datum: 2026-11-16 · Contract: 1.3 §1 (geen terminal, geen YAML), §9 (`kind: checklist`), §11
- Excel: voorwaarde voor I03-03-01…08, I04-04-01…08, I05-05-01…08, I15-15-01…06, I16-16-01…06, I17-17-01…06,
  I18-18-01…06, I19-19-01…06, I20-20-01…05 (59 taken; geen statuswijziging door dit ADR)
- Bouwt voort op: ADR-0015 (formaat `checklist v1` voor I01 en I02); ADR-0019 (contract 1.3, `kind: checklist`)

## Aanleiding

ADR-0015 legde het formaat `checklist v1` vast voor de twee I-documenten zonder installatie (I01 en I02). De documenten
van de apps die al op de NAS draaien of die het platform niet zelf installeert (MediaGuard, Media Source Optimizer,
my_house, Immich, Prowlarr, SABnzbd, qBittorrent, Seerr en Bazarr) vragen hetzelfde: per Excel-taak vastleggen wat de
gebruiker of de Hub controleert, zonder dat de lijst iets installeert. Het gereedschap en de test waren tot nu toe
geschreven voor precies de twee bestanden van I01 en I02 (de test eiste die twee namen en acht stappen per lijst).

## Besluit

1. **Zelfde formaat.** De documenten I03, I04, I05 en I15 tot en met I20 krijgen elk een controlelijst in het formaat
   `checklist v1` (`schemas/checklist-v1.schema.json`, regels in `tools/checklists.py`), documentsoort
   `kind: checklist` met `install.method: none` (contract 1.3 §9). Alle regels van ADR-0015 gelden ongewijzigd voor
   elke lijst: dekking van precies de Excel-taken (ID, titel, acceptatie en risico uit de Excel), alleen-lezen
   TrueNAS-methoden per versie, geen terminal, geen voorbeeldwaarden en eerlijke validatie per stap (nooit alleen
   `Sim-getest`).
2. **Aanpak.** `aanpak` is `behouden_en_valideren` voor apps die al draaien; `inrichten_en_testen` alleen waar het
   document een nieuwe inrichting beschrijft. Volgens de regel "Aanpak" van de documenten: I03 ("Upgrade alleen na
   versiecontrole"), I04 ("Bestaande app behouden"), I15 ("Behouden en beschermen") en I16 tot en met I20
   ("Behouden", "Behouden, via MediaGuard", "Behouden totdat MediaGuard parity aangetoond") beschrijven een app die al
   draait; I05 ("Installeer met echte apparaatgate") beschrijft een nieuwe inrichting. De stap die de lijst maakt, legt
   de keuze per lijst vast en toetst haar aan het document.
3. **De lijsten installeren niets.** `installatie: geen` en `mutaties_door_platform: false` blijven constanten in het
   schema. Een lijst vervangt geen manifest: installeren of importeren (bijvoorbeeld de adoptie van een bestaande app,
   contract 1.3 §9 `kind: adoption`, eigenaar appstore-en-fork) blijft een eigen document. Wijzigingen doet de gebruiker
   in de TrueNAS-webinterface (menupad verplicht), of ze volgen later als goedgekeurde actie via de executor
   (ADR-0015 besluit 2).
4. **Geen terminal en geen YAML als weg.** Noemt een document een terminal, een shell, YAML of compose als weg (gemeten
   op 2026-11-16: alleen I05, met "Compose stack", "Eerste YAML" en een installatiescript), dan beschrijft de lijst de
   weg via de TrueNAS-webinterface of via het formulier van de standaard Custom App, en legt zij de afwijking van het
   document vast in `afwijking_van_document_nl` (ADR-0015 besluit 3). Bestaat die weg nog niet, dan zegt de stap dat
   eerlijk in zijn validatie en blokkade; een terminal of YAML is nooit een tussenoplossing.
5. **Een lijst per document, met de naam van het document.** Elke lijst in `install/checklists/` hoort bij precies één
   bestaand document onder `docs/platform-v2/03_INSTALLATIE/`; de bestandsnaam is de documentnaam met
   `.checklist.yaml` in plaats van `.md` (`I03_MEDIAGUARD.md` → `I03_MEDIAGUARD.checklist.yaml`), en geen document
   heeft twee lijsten. `tools/checklists.py` (functies `controleer_lijst` en `controleer_alle`, dus ook
   `python tools/checklists.py` en `make manifesten`) faalt bij een tweede lijst, een afwijkende bestandsnaam of een
   document dat niet bestaat; `main` faalt ook bij een map zonder lijsten.
6. **Gereedschap en test zonder vaste namen of aantallen.** De test leest de lijsten uit de map (`cl.lijsten()`) en
   vergelijkt het aantal stappen met het aantal Excel-taken van het eigen document (5, 6 of 8 voor deze documenten).
   De ondergrens blijft: I01 en I02 moeten er altijd zijn, zodat de test nooit op nul lijsten slaagt. De negatieve
   tests (`_mutate`) gelden voor elke lijst; de simulatortest roept de methoden van elke lijst aan en eist nog steeds
   dat er minstens één versie-afhankelijke methode is getest. De test voor account-stappen blijft specifiek voor I02
   (OneDrive).

## Schema (bouwpunt 4): geen wijziging

Gemeten op 2026-11-16 in `docs/platform-v2/EXCEL_TAKEN.json`: de 59 taken van deze documenten hebben risico `Hoog` of
`Middel` (enum `Hoog`, `Middel`, `Laag`), een titel van hooguit 43 tekens (schema: 120) en een acceptatie van hooguit
82 tekens (schema: 500); elk ID past op `^I[0-9]{2}-[0-9]{2}-[0-9]{2}$` en begint met het documentnummer; elk document
past op het patroon van `document`; `id` (`^I[0-9]{2}$`) laat I03 tot en met I20 toe. Geen verplicht veld mist een
zinnige waarde voor een bestaande app (`besluiten` mag leeg zijn; `eigenaar_repo` is de repo waarin de lijst staat).
Alleen de beschrijvende tekst (`description`) van het schema is bijgewerkt; dat is geen formaatwijziging.

## Gevolgen

- De lijsten zelf volgen in de stappen S-P3-410 tot en met S-P3-440. Elke nieuwe lijst krijgt daar een rij in
  `install/manifests/README.md` (naam en document); in deze stap staat er alleen de algemene regel.
- Besluiten in de documenten buiten D01 tot en met D18 passen niet in `besluiten` van het schema; of zo'n verwijzing
  nodig is, stelt de stap van de lijst vast. Een nieuw veld of een ruimer patroon is een formaatwijziging en vraagt
  een besluit (niet in dit ADR).
- Validatie: niets hiervan is op een echte NAS getest. Een lijst zegt per stap wat er nog nodig is
  (`NIET GEVALIDEERD (echte NAS)`, `(hardware)` of `(account)`).
