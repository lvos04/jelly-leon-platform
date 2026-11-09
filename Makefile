# Ontwikkelhulpmiddelen (niet voor de gebruiker; installatie gaat via de TrueNAS-web-UI).
PY ?= .venv/bin/python
NICE ?= nice -n 10
IMAGE ?= jelly-leon-platform:dev

.PHONY: venv lint typecheck test test-sim test-repo test-conformance test-conformance-hub test-e2e openapi image \
	sim-image audit peil-kopie test-ui statuspagina-shots validator-venv manifesten

venv:
	python3.12 -m venv .venv
	$(PY) -m pip install -e 'apps/api[test]' -e 'e2e/simulators/truenas[test]'

lint:
	$(PY) -m ruff check .

typecheck:
	cd apps/api && ../../$(PY) -m mypy
	$(PY) -m mypy --strict --python-version 3.12 e2e/simulators/truenas/src/truenas_sim tools/peil_kopie.py \
		tools/manifesten.py tools/manifest_kopie.py tools/checklists.py

test:
	cd apps/api && ../../$(PY) -m pytest -q

test-sim:
	cd e2e/simulators/truenas && ../../../$(PY) -m pytest -q

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
