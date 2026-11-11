# ADR-0021 — De manifeststandaard als byte-gelijke, gepinde kopie in de lifecycle-dienst

- Status: aanvaard · Datum: 2026-11-11 · Contract: 1.3 §2.2, §9 · Bouwplan: B08-003, B08-004
- Bron (alleen gelezen): `lvos04/appstore-en-fork` `7b3159d168b1e8b575e220068bb3df50ed0203ad`,
  `apps/api/src/appstore/__init__.py` en `apps/api/src/appstore/manifest/`

## Aanleiding

De planner (B08-003/004) moet een manifest **zelf** valideren, naar formulierwaarden vertalen en tegen de gemeten
inventaris leggen (poorten, hostpaden, appnaam, afhankelijkheden, verdwenen catalogusversie). Eigenaar van de
manifeststandaard is `appstore-en-fork` (B08-001). Die repo heeft de validator, de vertaling naar `app.create` en de
conflictanalyse al gebouwd en getest. Tot nu toe gebruikte deze repo de validator alleen in CI, als los programma
(`jl-manifest`) in een eigen venv. In de draaiende dienst bestond hij niet.

## Besluit

1. **Byte-gelijke kopie** van `appstore/__init__.py` en `appstore/manifest/` (Python en JSON-schema's) in
   `apps/api/src/appstore/`, met de herkomst in `apps/api/src/appstore/KOPIE.json` (bron-repo, volledige commit-SHA,
   sha256 per bestand). Zelfde patroon als de contract-, Peil- en I23-kopieën (ADR-0013, ADR-0019).
2. **Nooit lokaal aanpassen.** `tools/validator_kopie.py controleer` en `tests/repo/test_validator_kopie.py` falen bij
   een gewijzigd, extra of ontbrekend bestand en bij een pin die niet bij de bron op die commit past (CI haalt de bron
   op). Een nieuwere bron geeft een waarschuwing; overnemen is een bewuste stap (`bijwerken --bron … [--ref …]`),
   gevolgd door de volledige testset.
3. **Geen tweede regelset.** `lifecycle.planner` roept alleen `validate_text`, `translate` en `conflicts.analyse` aan;
   eigen regels komen er alleen bovenop (bijvoorbeeld het Hub-antwoord `onbekend` voor catalogusbeschikbaarheid), nooit
   in plaats van. De kopie valt buiten ruff (ze wordt in de bron-repo gelint); mypy --strict controleert haar wel mee
   omdat de planner haar importeert.
4. **Afhankelijkheden:** `jsonschema` en `pyyaml` worden runtime-afhankelijkheden (hash-gepind in
   `apps/api/requirements.lock`, zelfde versies als de bron-repo); de image kopieert `appstore/` naast `lifecycle/`.

## Afgewezen

- *De appstore-dienst over het netwerk aanroepen:* de planner zou dan niets kunnen plannen als die dienst ontbreekt,
  en contract 1.3 §4.4 geeft deze dienst alleen `inventaris.lezen` en `catalogus.lezen` bij de Hub.
- *Een pip-pakket uit de andere repo installeren:* er is geen gepubliceerd pakket; een git-afhankelijkheid in de image
  haalt code op tijdens de build zonder hash per bestand.
- *De regels opnieuw schrijven:* twee validators lopen uiteen; precies dat moet de standaard voorkomen.

## Gevolgen

- Wijzigt de standaard, dan loopt deze dienst achter tot iemand de kopie bijwerkt. Dat is zichtbaar (waarschuwing in
  de repotest) en bewust: een wijziging in een andere repo maakt deze CI niet rood en verandert het gedrag van de
  planner niet ongemerkt.
- De CI-stap "Installeerbare manifesten" blijft de validator uit de bron-repo zelf draaien (`jl-manifest --strict`);
  de kopie in de dienst wordt daarnaast door de eigen tests gebruikt. Beide moeten hetzelfde oordelen.

## Stand na de review van P2 (S-P2-395, ADR-0025 besluit 7)

- De validator in CI en in `make validator-venv` staat vast op appstore-en-fork `cbf4fd2`
  (`ASF_COMMIT` in `.github/workflows/ci.yml` en in de `Makefile`). Die commit vertaalt een geheim als
  `vault://app/<app_id>/<parameter>` (C1.3-ASF-14).
- De kopie in de dienst blijft **bewust** op `7b3159d`. Tussen beide commits verschillen alleen
  `manifest/translate.py` en `manifest/findings.py`: de validatieregels en schema's zijn gelijk, dus over een manifest
  oordelen CI en de planner hetzelfde. Over geheimen niet: de kopie weigert elk geheim, zodat een installatieplan met
  geheimen geblokkeerd blijft. Overnemen zou zo'n plan vrijgeven met vault-verwijzingen in `app.install`, terwijl in
  deze stap niet is vastgesteld dat de Hub en de executor die invullen (S-P2-070, ai-server-manager). Dat is
  ONBEKEND, dus de veiligste stand geldt. Overnemen blijft een eigen stap (besluit 2), samen met ADR-0022 besluit 11.
