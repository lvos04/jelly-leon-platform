# Herkomst van `design/ontwerptaal/` (O-14)

Versie-gepinde kopie van het **statuspaginasjabloon van de ontwerptaal Peil 1.1.0** (ONTWERPTAAL §13.2 en §14).
Bron van waarheid: `lvos04/ai-server-manager` → `design/ontwerptaal/` (contract 1.2 §13), commit
`172ee87f15406edbe1a72dda30908abde7488e94` ("Ontwerptaal Peil 1.1.0 als bron van waarheid").

| Bestand | Gebruik in jelly-leon-platform | sha256 van het bronbestand (zonder herkomstkop) |
|---|---|---|
| `statuspagina.html` | sjabloon van `GET /` (CSS en lucide-symbolen inline, geen JS) | `be766cc4bf6962772a8dfc77996da34040fa94698403a9625e33545fa9ee62c8` |
| `statuspagina.css` | dezelfde CSS los (referentie; het sjabloon bevat hem al inline) | `b1e3f789cbeaa2c928088cdac482cc8d6d11c7a5007c5d9b79005df66dd1b5af` |

- Beide bestanden hebben een **herkomstkop van 2 regels** (versie, bronbestand met commit, sha256); vanaf regel 3 zijn
  ze byte-gelijk aan de bron.
- De dienst serveert `apps/api/src/lifecycle/peil/statuspagina.html`: byte-gelijk aan `statuspagina.html` hier (zelfde
  kop; pakketdata, zodat het sjabloon in de image zit). De renderer (`lifecycle.peil`) haalt bij het laden de
  herkomstkop en de bouwersopmerking weg en weigert een ander sjabloon dan Peil 1.1.0.
- Niet gekopieerd: de rest van de ontwerptaal (`ontwerptaal.css`, tokens, componenten, prototypes, schermafdrukken,
  bouwscripts). Deze dienst heeft alleen een statuspagina; alle andere schermen staan in de Hub.

## Controle (CI)

`tests/repo/test_peil_kopie.py` (stap "Manifest-, documentatie- en Peil-kopiecontroles" in `.github/workflows/ci.yml`):

1. de kopie is intact (sha256 van de inhoud = de kop), HTML en CSS komen uit dezelfde versie en commit, de CSS in het
   sjabloon is gelijk aan `statuspagina.css`, en de runtime-kopie van de dienst is byte-gelijk;
2. het bronbestand op de gepinde commit heeft precies de gepinde sha256;
3. **geen drift**: het bronbestand op de kop van `ccr-c133d7a0-kuz1cv` in ai-server-manager heeft dezelfde sha256.
   Wijzigt de design-agent het sjabloon, dan faalt CI hier tot de kopie is bijgewerkt.

In CI haalt de workflow ai-server-manager zelf op (publiek, alleen lezen) en zet `PEIL_BRON_VERPLICHT=1`; lokaal
gebruikt de test de naburige map `../ai-server-manager` en slaat hij 2 en 3 over als die ontbreekt.

## Bijwerken naar een nieuwe Peil-versie

```sh
python tools/peil_kopie.py bijwerken --bron ../ai-server-manager   # leest gecommitte inhoud (git show), schrijft kop
python tools/peil_kopie.py controleer
```

Daarna: `PEIL_VERSIE` in `apps/api/src/lifecycle/peil/__init__.py`, de statuspagina en de tests aanpassen aan nieuwe
plekken, `make test-ui` en `make statuspagina-shots`, deze tabel en `CHANGELOG.md` bijwerken. Nooit met de hand in de
kopie wijzigen.
