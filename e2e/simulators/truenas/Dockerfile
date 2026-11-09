# syntax=docker/dockerfile:1.7
# TrueNAS-simulator — ALLEEN voor tests/CI/de integrale E2E-omgeving. Geen echte TrueNAS, geen installatiemethode.
#   docker build [--secret id=ca_bundle,src=<ca.crt>] -t jlp-truenas-sim:dev e2e/simulators/truenas
ARG PYTHON_IMAGE=docker.io/library/python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
FROM ${PYTHON_IMAGE}
WORKDIR /sim
COPY requirements.lock ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore \
        --require-hashes --no-deps --only-binary=:all: -r requirements.lock
COPY src/truenas_sim /usr/local/lib/python3.12/site-packages/truenas_sim
RUN useradd -u 568 -M -d /tmp -s /usr/sbin/nologin sim && python -c "import truenas_sim.server"
USER 568
ENV PYTHONUNBUFFERED=1 TRUENAS_SIM_VERSION=25.10.8
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/versions',timeout=2).status==200 else 1)"]
CMD ["python", "-m", "truenas_sim", "--host", "0.0.0.0", "--port", "8080"]
