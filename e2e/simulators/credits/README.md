# Inbegrepen testtegoed voor EX21

## Bron

De Hub-runner verstuurt een diagnose naar het ingestelde proefadres. Het protocol is vastgelegd in
`apps/api/src/hub/ai/runner.py` en `tests/fakes/fake_kredietdienst.py` in ai-server-manager.

## Starten

`credits-sim --eenheden 2` start een proef met twee inbegrepen eenheden. De standaard is nul.
Het Compose-profiel `credits` gebruikt poort 30224 op het interne testnet.

## Protocol

`POST /diagnose` verwacht een JSON-object met `alleen_inbegrepen` op `true` en een positief geheel aantal
`max_eenheden`. Een toekenning boekt dat aantal atomair af en geeft HTTP 200 met een vast analysebericht.
Bij onvoldoende tegoed geeft de dienst HTTP 402. Betaalde uitwijk geeft HTTP 422.

## Foutinjectie en bediening

Activeer `tegoed_op` via `POST /_sim/fouten`. Vanaf de volgende diagnose blijft het tegoed op tot een
expliciete proefreset via `POST /_sim/reset`. `GET /_sim/stand` geeft het saldo, het aantal adviezen en het aantal
weigeringen bij uitputting.

## Afwijkingen mogelijk

De simulator gebruikt geheugen en een vast advies. De praktijkproef met een account blijft open.
