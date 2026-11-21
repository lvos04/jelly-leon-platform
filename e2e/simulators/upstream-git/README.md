# upstream-git (Jellyfin-upstream met een onverenigbare tag) voor EX48

## Bron

De sync-hulp van de fork: `jelly-leon:tools/jelly-leon/upstream_sync.py` (subcommando's `kies` en `merge`, optie
`--bron`). `kies` leest de tags met `git ls-remote --tags <bron>`, `merge` haalt een tag op met
`git fetch <bron> refs/tags/<tag>` en past binnen dezelfde reeks een merge toe, daarbuiten een herbasis met de
core-patches uit `docs/platform-v2/PATCHES.md`. Een conflict betekent onverenigbaar: exitcode 2, alles teruggedraaid.
De opzet volgt de testfixture `jelly-leon:tools/jelly-leon/tests/conftest.py` (`repos`). Er staat geen Jellyfin-
broncode in: alleen vier kleine bestanden.

## Starten

Een los bestand, geen dienst of poort. `python e2e/simulators/upstream-git/upstream_git_sim.py --doel <lege map>`
maakt in die map:

- `upstream.git`: een bare repository, de bron voor `--bron`;
- `fork`: een werkkopie op `v1.0.0` met `tools/jelly-leon/baseline.json`, `fork_paths.txt`,
  `plugins/Directory.Build.props`, het patchregister en core-patch JL-0001; remote `upstream` wijst naar de bron.

De uitvoer is JSON met de absolute paden (`bron`, `fork`) en de commit per tag. In Python: `bouw(Path(...))`.

## Protocol

| Tag | Inhoud | Verwacht bij `upstream_sync.py --repo <fork> merge --tag <tag> --bron <bron>` |
|---|---|---|
| `v1.0.0` | basis van de fork | geen downgrade (exit 1) |
| `v1.0.1` | raakt alleen `src/Server.cs` en de versie | merge, branch `sync/v1.0.1` (exit 0) |
| `v1.1` | wijzigt `.github/workflows/bot.yml`, waar de fork core-patch JL-0001 op heeft | herbasis met conflict: `INCOMPATIBEL`, exit 2, fork ongewijzigd |

Alle tags zijn geannoteerd; `kies` geeft `v1.0.1` als patchkandidaat en `v1.1` als nieuwste.

## Foutinjectie en bediening

De onverenigbare wijziging is de foutinjectie: tag `v1.1`. Er is geen bediening; een nieuwe stand maak je met een
nieuwe lege map. `bouw` weigert een map die niet leeg is (`FileExistsError`) en laat die ongemoeid.

## Niet nagebootst

- De echte Jellyfin-repository, haar tags en een netwerkbron (alleen een lokaal pad).
- Release-candidates, verplaatste tags en de manifest- en SDK-controles van de fork.
- De echte sync tegen de Jellyfin-upstream en een uitrol blijven NAS-werk (N-P6-09).
