# ADR-0017 — Ontwerpreview statuspagina: één woord één uiterlijk, live review en strengere browsercontrole

- Status: aanvaard · Datum: 2026-11-09 · Contract: 1.2 §3.7, §13 (werkwijze: ontwerpreview achteraf) · Ontwerptaal:
  Peil 1.1.0 (ONTWERPTAAL §1, §4, §7, §11, §12, §13.2; COMPONENTEN §13, §25)
- Vult aan: ADR-0013 (punt 4, 5 en 9). Bevindingen en metingen: [`docs/ontwerp/PEIL_REVIEW.md`](../ontwerp/PEIL_REVIEW.md).

## Aanleiding

Contract §13 vraagt na elke UI-bouwstap een onafhankelijke ontwerpreview door de design-agent. De review van de
statuspagina (`GET /`, alle toestanden, 320–1440 px, licht/donker) vond onder meer: hetzelfde woord met twee
uiterlijken op één pagina ("Niet gekoppeld" met `circle-help` in de controles en met `link-2-off` in het koppelblok;
"Ingetrokken" rood én grijs-gestippeld), woorden die de Peil-woordenlijst vervangt ("in orde"), een skiplink die op
320/360 px onder de bovenrand uitstak en een foutsamenvatting zonder link naar het veld. De bestaande browsercontrole
mat geen contrast van UI-grenzen en focusring en geen statusregels; axe-core vangt die niet.

## Besluit

1. **Controlerijen gebruiken alleen de vaste gezondheidswoorden** (Gezond · Beperkt · Storing · Onbekend), met de
   koppeltoestand in de reden: "Onbekend — Nog niet gekoppeld: …", "Storing — Ingetrokken door de Hub: …". Het woord
   van de koppeltoestand ("Niet gekoppeld", "Ontkoppeld", "Ingetrokken") staat alleen in het koppelblok, met
   `link-2-off`. Zo heeft één woord op de pagina één uiterlijk (Peil §1.8, §7; WCAG 3.2.4) en volgt de rij de
   connectorstatus van `/connector/v1/health` (`unknown`/`down`). Dit wijkt bewust af van het Peil-prototype
   (`statuspagina.html` toont dezelfde dubbele "Niet gekoppeld"); gemeld als sjabloonpunt voor Peil 1.1.1.
2. **Teksten** volgens de woordenlijst en het patroon wat · waarom · wat nu: geen "in orde"/"niet in orde", geen
   "eerlijk", geen gate-zin bij een fase, "de Hub" in plaats van "Hub-UI"; kortere dienstomschrijving zonder vaktermen
   (op 320 px staat de totaalstatus 48 px hoger); "Te veel koppelpogingen" zegt waarom; de foutsamenvatting
   "Controleer de invoer" linkt naar het eerste foute veld; de skiplink heeft een korte vaste tekst
   ("Naar het veld met de fout").
3. **Het gepinde sjabloon blijft byte-gelijk.** Wat alleen in het sjabloon op te lossen is (o.a. `novalidate`, een
   icoon per controlerij, `role=alert` alleen bij een nieuwe fout, robuuster verbergen van de skiplink) gaat als
   voorstel naar de eigenaar van Peil (ai-server-manager); deze repo past de kopie niet aan (O-14, drift-controle).
4. **Live review als test** (`tests/ui/live/live.mjs`, `make test-ui-live` / `make ontwerpreview`, CI-job
   `statuspagina`): de dienst draait als echte processen (`python -m lifecycle api` + `worker`, eigen database) naast de
   Hub-simulator (`tests/ui/live/hub_review.py`, alleen test). Chromium vult het formulier zelf in (echte CSRF-cookie,
   `Origin`, 303-redirect) en bereikt 14 toestanden, waaronder "formulier verlopen", "te veel pogingen" en "direct
   ingetrokken" die de momentopnamen uit pytest niet hadden.
5. **Strengere browsercontrole** (`tests/ui/controle.mjs`, ook op de pytest-momentopnamen): gemeten contrast in de
   gerenderde pagina (tekst ≥ 4,5:1; veldrand, knopgrens, betekenisdragende randen en iconen ≥ 3:1; focusring ≥ 3:1
   tegen de achtergrond), statusregels (icoon + woord per pil, icoon past bij de pilvorm, groen alleen bij Gezond,
   totaal nooit Gezond bij niet gereed of een niet-gezonde controle, één woord = één uiterlijk), microcopy-lint op de
   vervangen woorden van §11.1, skiplink zonder focus buiten beeld, tekstafstand (WCAG 1.4.12) en forced colors op
   320 px. Een opzettelijk beschadigde pagina laat elke nieuwe controle falen (zie PEIL_REVIEW.md).
6. **Schermafdrukken**: release-set (pytest) 5 hoofdtoestanden × 320/360/390/768/1440 × licht/donker; live set alle 14
   toestanden × 320/1440 × licht/donker in `docs/ontwerp/shots/` (360/390/768 gemaakt en bekeken, niet gecommit:
   `make ontwerpreview REVIEW_BREEDTES=320,360,390,768,1440`). PNG's worden met pngquant (palet, kwaliteit 90–100)
   verkleind; contrast wordt in de DOM gemeten, nooit op de PNG's.

## Gevolgen

- Tests die op de oude labels ("Nog niet bevestigd", "Onleesbaar") of teksten letten zijn bijgewerkt; de API en
  `/readyz` veranderen niet. `ERROR_MESSAGES_NL["hub_vingerafdruk_onjuist"]` (ook in de connectorweergave) heeft een
  nieuwe tekst.
- De CI-job `statuspagina` duurt ± 3 minuten langer (live run + 14 extra toestanden).
- Niet gevalideerd: echte telefoon/tablet, schermlezer (NVDA/TalkBack), echte browserzoom; op een echte NAS niets.
