# Ontwikkelhulpmiddelen (niet voor de gebruiker; installatie gaat via de TrueNAS-web-UI).
PY ?= .venv/bin/python
NICE ?= nice -n 10
IMAGE ?= jelly-leon-platform:dev

.PHONY: venv lint typecheck test test-sim test-repo test-conformance test-conformance-hub test-e2e openapi image \
	sim-image audit peil-kopie test-ui statuspagina-shots validator-venv manifesten rooktest

venv:
	python3.12 -m venv .venv
	$(PY) -m pip install -e 'apps/api[test]' -e 'e2e/simulators/truenas[test]' -e 'e2e/simulators[test]'

lint:
	$(PY) -m ruff check .

typecheck:
	cd apps/api && ../../$(PY) -m mypy
	$(PY) -m mypy --strict --python-version 3.12 e2e/simulators/truenas/src/truenas_sim tools/peil_kopie.py \
		tools/manifesten.py tools/manifest_kopie.py tools/checklists.py tools/rooktest.py
	cd e2e/simulators && ../../$(PY) -m mypy --strict --python-version 3.12 simkit beszel gotify authentik uptime_kuma \
		--exclude '/tests/'

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
	$(PY) tools/checklists.py

# Contract 1.2: Hub ↔ dienst tegen de Hub-simulator; test-conformance-hub tegen de echte Hub uit ../ai-server-manager
test-conformance:
	$(PY) -m pytest -q tests/conformance

test-conformance-hub:
	$(NICE) $(PY) e2e/conformance/echte_hub.py

# Peil-statuspagina (JP11). peil-kopie: versie-gepinde kopie intact (drift met ai-server-manager: make test-repo).
# test-ui: HTML per toestand uit de echte dienst → Chromium (axe-core, 320–1440 px, licht/donker, focus, CSP).
# statuspagina-shots: idem + schermafdrukken naar docs/screenshots/peil/statuspagina/ (bij elke release).
SNAP ?= /tmp/jlp-statuspagina
SHOTS ?= docs/screenshots/peil/statuspagina

peil-kopie:
	$(PY) tools/peil_kopie.py controleer

test-ui:
	rm -rf $(SNAP)
	cd apps/api && STATUSPAGINA_SNAPSHOTS=$(SNAP) ../../$(PY) -m pytest -q tests/integration/test_statuspage_peil.py
	test -d tests/ui/node_modules || npm ci --prefix tests/ui --ignore-scripts --no-audit --no-fund
	$(NICE) node tests/ui/controle.mjs --map $(SNAP)

statuspagina-shots:
	rm -rf $(SNAP) $(SHOTS)
	cd apps/api && STATUSPAGINA_SNAPSHOTS=$(SNAP) ../../$(PY) -m pytest -q tests/integration/test_statuspage_peil.py
	test -d tests/ui/node_modules || npm ci --prefix tests/ui --ignore-scripts --no-audit --no-fund
	$(NICE) node tests/ui/controle.mjs --map $(SNAP) --shots $(SHOTS) --rapport $(SHOTS)/rapport.json

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
