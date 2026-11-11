# Ontwikkelhulpmiddelen (niet voor de gebruiker; installatie gaat via de TrueNAS-web-UI).
PY ?= .venv/bin/python
NICE ?= nice -n 10
IMAGE ?= jelly-leon-platform:dev

.PHONY: venv lint typecheck test test-sim test-repo test-conformance test-conformance-hub test-e2e openapi image \
	sim-image audit peil-kopie test-ui test-ui-live statuspagina-shots ontwerpreview validator-venv manifesten rooktest upstream-rooktest \
	nodered nodered-rooktest

venv:
	python3.12 -m venv .venv
	$(PY) -m pip install -e 'apps/api[test]' -e 'e2e/simulators/truenas[test]' -e 'e2e/simulators[test]'

lint:
	$(PY) -m ruff check .

typecheck:
	cd apps/api && ../../$(PY) -m mypy
	$(PY) -m mypy --strict --python-version 3.12 e2e/simulators/truenas/src/truenas_sim tools/peil_kopie.py tools/contract_kopie.py \
		tools/manifesten.py tools/manifest_kopie.py tools/checklists.py tools/rooktest.py tools/upstream_rooktest.py \
		tools/validator_kopie.py tools/nodered_flows.py
	cd e2e/simulators && ../../$(PY) -m mypy --strict --python-version 3.12 simkit beszel gotify authentik uptime_kuma \
		--exclude '/tests/'
	MYPYPATH=e2e/simulators/hub $(PY) -m mypy --strict --python-version 3.12 tests/ui/live/hub_review.py e2e/simulators/hub/executor_sim.py
	MYPYPATH=e2e/simulators/hub:tools $(PY) -m mypy --strict --python-version 3.12 tools/nodered_rooktest.py

test:
	cd apps/api && ../../$(PY) -m pytest -q

test-sim:
	cd e2e/simulators/truenas && ../../../$(PY) -m pytest -q
	cd e2e/simulators && ../../$(PY) -m pytest -q

test-repo:
	$(PY) -m pytest -q tests/repo

# Installeerbare manifesten (install/manifests, ADR-0014): validator van appstore-en-fork in een eigen venv
# (gecommitte inhoud via git archive, alleen lezen; APPSTORE=<checkout> om een andere bron te kiezen).
APPSTORE ?= ../appstore-en-fork
VALIDATOR_SRC ?= /tmp/jlp-appstore-validator
validator-venv:
	rm -rf $(VALIDATOR_SRC) && mkdir -p $(VALIDATOR_SRC)
	git -C $(APPSTORE) archive HEAD apps/api | tar -x -C $(VALIDATOR_SRC)
	python3.12 -m venv .venv-validator
	.venv-validator/bin/pip install -q --require-hashes -r $(VALIDATOR_SRC)/apps/api/requirements.lock
	.venv-validator/bin/pip install -q --no-deps $(VALIDATOR_SRC)/apps/api
	@echo "validator: appstore-en-fork@$$(git -C $(APPSTORE) rev-parse HEAD)"

manifesten:
	$(PY) tools/manifesten.py
	$(PY) tools/manifest_kopie.py controleer
	$(PY) tools/validator_kopie.py controleer
	$(PY) tools/checklists.py

# Contract 1.2: Hub ↔ dienst tegen de Hub-simulator; test-conformance-hub tegen de echte Hub uit ../ai-server-manager
test-conformance:
	$(PY) -m pytest -q tests/conformance

test-conformance-hub:
	$(NICE) $(PY) e2e/conformance/echte_hub.py

# Peil-statuspagina (JP11). peil-kopie: versie-gepinde kopie intact (drift met ai-server-manager: make test-repo).
# test-ui: HTML per toestand uit de echte dienst → Chromium (axe-core, gemeten contrast tekst/UI/focus, statusregels,
#   microcopy, 320–1440 px, licht/donker, focus, tekstafstand, forced colors, CSP).
# test-ui-live: idem, maar de dienst draait als echte processen (api + worker + Hub-simulator) en Chromium vult het
#   formulier zelf in (docs/ontwerp/PEIL_REVIEW.md).
# statuspagina-shots: test-ui + schermafdrukken naar docs/screenshots/peil/statuspagina/ (bij elke release).
# ontwerpreview: test-ui-live + schermafdrukken van alle live toestanden naar docs/ontwerp/shots/.
# PNG's worden met pngquant (palet, --quality 90-100) verkleind als dat er is; contrast meet de controle in de DOM.
SNAP ?= /tmp/jlp-statuspagina
SHOTS ?= docs/screenshots/peil/statuspagina
LIVE ?= /tmp/jlp-statuspagina-live
REVIEW ?= docs/ontwerp
REVIEW_BREEDTES ?= 320,1440
PNGQUANT = if command -v pngquant >/dev/null; then pngquant --quality=90-100 --speed 1 --strip --force --ext .png $(1)/*.png; fi

peil-kopie:
	$(PY) tools/peil_kopie.py controleer

test-ui:
	rm -rf $(SNAP)
	cd apps/api && STATUSPAGINA_SNAPSHOTS=$(SNAP) ../../$(PY) -m pytest -q tests/integration/test_statuspage_peil.py
	test -d tests/ui/node_modules || npm ci --prefix tests/ui --ignore-scripts --no-audit --no-fund
	$(NICE) node tests/ui/controle.mjs --map $(SNAP)

statuspagina-shots:
	rm -rf $(SNAP) && rm -f $(SHOTS)/*.png
	cd apps/api && STATUSPAGINA_SNAPSHOTS=$(SNAP) ../../$(PY) -m pytest -q tests/integration/test_statuspage_peil.py
	test -d tests/ui/node_modules || npm ci --prefix tests/ui --ignore-scripts --no-audit --no-fund
	$(NICE) node tests/ui/controle.mjs --map $(SNAP) --shots $(SHOTS) --rapport $(SHOTS)/rapport.json
	$(call PNGQUANT,$(SHOTS))

test-ui-live:
	test -d tests/ui/node_modules || npm ci --prefix tests/ui --ignore-scripts --no-audit --no-fund
	$(NICE) node tests/ui/live/live.mjs --map $(LIVE) --python $(abspath $(PY))
	$(NICE) node tests/ui/controle.mjs --map $(LIVE)

ontwerpreview:
	test -d tests/ui/node_modules || npm ci --prefix tests/ui --ignore-scripts --no-audit --no-fund
	rm -rf $(REVIEW)/shots $(LIVE)
	$(NICE) node tests/ui/live/live.mjs --map $(LIVE) --python $(abspath $(PY))
	cp $(LIVE)/live.json $(REVIEW)/live.json
	$(NICE) node tests/ui/controle.mjs --map $(LIVE) --shots $(REVIEW)/shots --shot-toestanden alle \
		--shot-breedtes $(REVIEW_BREEDTES) --rapport $(REVIEW)/rapport-live.json
	$(call PNGQUANT,$(REVIEW)/shots)

# Achter een TLS-proxy: LIFECYCLE_BUILD_CA=<ca.crt> LIFECYCLE_BUILD_NETWORK=host make test-e2e
test-e2e: image
	LIFECYCLE_IMAGE=$(IMAGE) $(NICE) $(PY) -m pytest -q tests/e2e

openapi:
	$(PY) -m lifecycle export-openapi docs/openapi/jelly-leon-platform.openapi.json

image:
	$(NICE) docker build -f deploy/allinone/Dockerfile --build-arg VERSION=$$(cat VERSION) -t $(IMAGE) .

sim-image:
	$(NICE) docker build -t jlp-truenas-sim:dev e2e/simulators/truenas

audit:
	$(PY) -m pip_audit --strict --require-hashes --disable-pip -r apps/api/requirements.lock

# Installatie-rooktest zonder YAML (P1, I23): Hub + executor uit ../ai-server-manager (gecommitte inhoud via git archive,
# alleen lezen; ASM_REF kiest de commit) en de eigen image; draait ze als twee Custom Apps (docker run: één map, één
# poort) tegen de TrueNAS-simulator. Image-builds machinebreed geserialiseerd met flock.
ASM ?= ../ai-server-manager
ASM_REF ?= HEAD
LOCK ?= $(abspath ..)/.lokaal/docker-build.lock
ROOKTEST_SRC ?= /tmp/jlp-rooktest-asm
rooktest: validator-venv
	rm -rf $(ROOKTEST_SRC) && mkdir -p $(ROOKTEST_SRC)
	git -C $(ASM) archive $(ASM_REF) | tar -x -C $(ROOKTEST_SRC)
	c=$$(git -C $(ASM) rev-parse $(ASM_REF)); cs=$$(git -C $(ASM) rev-parse --short=12 $(ASM_REF)); \
	e=$$(git rev-parse HEAD); es=$$(git rev-parse --short=12 HEAD); \
	flock $(LOCK) $(NICE) docker buildx build --load -f $(ROOKTEST_SRC)/deploy/allinone/Dockerfile \
		--build-arg REVISION=$$c -t jlp-rooktest/ai-server-manager:$$cs $(ROOKTEST_SRC) && \
	flock $(LOCK) $(NICE) docker buildx build --load -f $(ROOKTEST_SRC)/deploy/executor/Dockerfile \
		--build-arg REVISION=$$c -t jlp-rooktest/ai-server-manager-executor:$$cs $(ROOKTEST_SRC) && \
	flock $(LOCK) $(NICE) docker buildx build --load -f deploy/allinone/Dockerfile --build-arg VERSION=$$(cat VERSION) \
		--build-arg REVISION=$$e -t jlp-rooktest/jelly-leon-platform:$$es . && \
	$(PY) tools/rooktest.py --hub-image jlp-rooktest/ai-server-manager:$$cs \
		--executor-image jlp-rooktest/ai-server-manager-executor:$$cs \
		--lifecycle-image jlp-rooktest/jelly-leon-platform:$$es --hub-commit $$c --eigen-commit $$e \
		--validator-commit $$(git -C $(APPSTORE) rev-parse HEAD) --rapport /tmp/jlp-rooktest-rapport.json

# Upstream-images uit install/manifests (Beszel hub + agent, Gotify, Uptime Kuma) op digest, als 568:568 met een lege
# map zoals een standaard Custom App: digest-readback, health, kernroutes zoals de simulators, herstart zonder
# dataverlies, Beszel-agent in SSH-modus met alleen de publieke hubsleutel. Docker, geen TrueNAS (Sim-getest).
upstream-rooktest:
	$(NICE) $(PY) tools/upstream_rooktest.py --rapport /tmp/jlp-upstream-rooktest.json

# Node-RED (I10, ADR-0023). nodered: manifest-FLOWS gelijk aan install/node-red/settings-overrides.json, flowbundels
#   door de eigen controle en het lock (checksums) actueel — zonder Docker.
# nodered-rooktest: de gepinde image als 568 met het manifest tegen de Hub-simulator (beveiliging, flows importeren,
#   melding, aanvraag via de Hub, hartslag, herstel, stop zichtbaar). Alleen Docker en de simulator; geen NAS.
nodered:
	$(PY) tools/nodered_flows.py render --check
	$(PY) tools/nodered_flows.py lint
	$(PY) tools/nodered_flows.py lock --check

nodered-rooktest:
	$(NICE) $(PY) tools/nodered_rooktest.py --rapport /tmp/jlp-nodered-rooktest.json
