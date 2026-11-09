# Ontwikkelhulpmiddelen (niet voor de gebruiker; installatie gaat via de TrueNAS-web-UI).
PY ?= .venv/bin/python
NICE ?= nice -n 10
IMAGE ?= jelly-leon-platform:dev

.PHONY: venv lint typecheck test test-sim test-repo test-conformance test-conformance-hub test-e2e openapi image \
	sim-image audit

venv:
	python3.12 -m venv .venv
	$(PY) -m pip install -e 'apps/api[test]' -e 'e2e/simulators/truenas[test]'

lint:
	$(PY) -m ruff check .

typecheck:
	cd apps/api && ../../$(PY) -m mypy
	$(PY) -m mypy --strict --python-version 3.12 e2e/simulators/truenas/src/truenas_sim

test:
	cd apps/api && ../../$(PY) -m pytest -q

test-sim:
	cd e2e/simulators/truenas && ../../../$(PY) -m pytest -q

test-repo:
	$(PY) -m pytest -q tests/repo

# Contract 1.2: Hub ↔ dienst tegen de Hub-simulator; test-conformance-hub tegen de echte Hub uit ../ai-server-manager
test-conformance:
	$(PY) -m pytest -q tests/conformance

test-conformance-hub:
	$(NICE) $(PY) e2e/conformance/echte_hub.py

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
