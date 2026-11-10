# Herkomst van `design/ontwerptaal/` (O-14)

Versie-gepinde kopie van het **statuspaginasjabloon van de ontwerptaal Peil 1.1.1** (ONTWERPTAAL §13.2 en §14).
Bron van waarheid: `lvos04/ai-server-manager` → `design/ontwerptaal/` (contract 1.3 §13), commit
`0a7143bc0a94ed5317a9655fa785e73a1fb07ba1` (werkbranch `ccr-c133d7a0-kuz1cv`; Peil 1.1.1 staat in `3e559e7`).
Vorige pin: Peil 1.1.0 @ `172ee87f15406edbe1a72dda30908abde7488e94`.

| Bestand | Gebruik in jelly-leon-platform | sha256 van het bronbestand (zonder herkomstkop) |
|---|---|---|
| `statuspagina.html` | sjabloon van `GET /` (CSS en lucide-symbolen inline, geen JS) | `d352028f798934d5efb96610c42a61712e3948b8c9eec8a87f34035b89f8c80b` |
| `statuspagina.css` | dezelfde CSS los (referentie; het sjabloon bevat hem al inline) | `0f9963583a32d91232e7cfb1b7fe8a8b7612cf8349d4fcc8edc3ca545e32e91b` |

- Beide bestanden hebben een **herkomstkop van 2 regels** (versie, bronbestand met commit, sha256); vanaf regel 3 zijn
  ze byte-gelijk aan de bron.
- De dienst serveert `apps/api/src/lifecycle/peil/statuspagina.html`: byte-gelijk aan `statuspagina.html` hier (zelfde
  kop; pakketdata, zodat het sjabloon in de image zit). De renderer (`lifecycle.peil`) haalt bij het laden de
  herkomstkop en de bouwersopmerking weg en weigert een ander sjabloon dan Peil 1.1.1. Hij vult in **één doorgang**
  in (E07), zoals `bouw/sjabloon.py` van de bron; `tests/repo/test_peil_kopie.py` vergelijkt de bytes met die
  referentie op de gepinde commit.
- Niet gekopieerd: de rest van de ontwerptaal (`ontwerptaal.css`, tokens, componenten, prototypes, schermafdrukken,
  bouwscripts). Deze dienst heeft alleen een statuspagina; alle andere schermen staan in de Hub.

## Controle (CI)

`tests/repo/test_peil_kopie.py` (stap "Manifest-, documentatie- en Peil-kopiecontroles" in `.github/workflows/ci.yml`):

1. de kopie is intact (sha256 van de inhoud = de kop), HTML en CSS komen uit dezelfde versie en commit, de CSS in het
   sjabloon is gelijk aan `statuspagina.css`, en de runtime-kopie van de dienst is byte-gelijk;
2. het bronbestand op de gepinde commit heeft precies de gepinde sha256;
3. **pin- en driftregel (Peil 1.1.1, E08):** dezelfde Peil-versie met andere inhoud in de bron (kop van
   `ccr-c133d7a0-kuz1cv`) laat CI falen; een **nieuwere** Peil-versie geeft alleen een waarschuwing (overstappen is een
   bewuste stap). Tot 1.1.0 faalde CI bij elke nieuwe versie; dat is vervallen.

In CI haalt de workflow ai-server-manager zelf op (publiek, alleen lezen) en zet `PEIL_BRON_VERPLICHT=1`; lokaal
gebruikt de test de naburige map `../ai-server-manager` en slaat hij 2 en 3 over als die ontbreekt.

## Bijwerken naar een nieuwe Peil-versie

```sh
python tools/peil_kopie.py bijwerken --bron ../ai-server-manager   # leest gecommitte inhoud (git show), schrijft kop
python tools/peil_kopie.py controleer
```

Daarna: `PEIL_VERSIE` in `apps/api/src/lifecycle/peil/__init__.py`, de statuspagina en de tests aanpassen aan nieuwe
plekken, `make test-ui`, `make test-ui-live`, `make statuspagina-shots` en `make ontwerpreview`, deze tabel en
`CHANGELOG.md` bijwerken. Nooit met de hand in de kopie wijzigen.

## Overstap 1.1.0 → 1.1.1 (2026-11-10, ADR-0019)

Gedaan volgens ONTWERPTAAL §13.2 "Overstap 1.1.0 → 1.1.1": kopie bijgewerkt; renderer op één doorgang; `melding_rol`
(alert voor de fout van dít verzoek, status voor de bevestiging na een geslaagde koppeling, leeg voor een bestaande
toestand); de code van een melding zonder trace-ID via `melding_code`, het pad via `melding_pad`; rijen met de juiste
toon (`niet-gekoppeld`, `nog-niet`, `beleid`, `verouderd`) in plaats van een omweg via `onbekend` of `gate`; de
toestanden `koppelstatus_onleesbaar` en `koppelen_bezig`; `{{bron}}`. Gemeten in Chromium (Sim-getest): 12
momentopnamen en 17 live toestanden × 320–1440 px × licht/donker, axe 0 en contrast AA, ook met DejaVu Sans.
