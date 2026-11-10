# syntax=docker/dockerfile:1.7
# jelly-leon-platform — ALLES-IN-ÉÉN image (PostgreSQL 16 + lifecycle-API + worker in één container).
#
#   docker build -f deploy/allinone/Dockerfile [--secret id=ca_bundle,src=<ca.crt>] -t jelly-leon-platform:dev .
#
# Installeren als STANDAARD TrueNAS Custom App (formulier, geen YAML): image ghcr.io/lvos04/jelly-leon-platform:<versie>,
# één opslag → /appdata, één poort 30440 → 30440. Geen wachtwoorden, geen verplichte omgevingsvariabelen.
# PostgreSQL 16 draait binnenin, alleen op een unix-socket (geen netwerkpoort, geen wachtwoord).
# Start als root alleen om de eigen submappen van /appdata aan 568:568 te geven; daarna draait ELK proces — ook de
# procesbewaker zelf als PID 1 (geen root-init zoals dumb-init) — als 568 zonder capabilities en met no_new_privs,
# omdat het standaard Custom App-formulier no-new-privileges en cap_drop weglaat (contract 1.3 §2.2: eerst de
# bounding set leeg). Base-images op exacte versie én @sha256-digest (contract §1). De optionele BuildKit-secret
# 'ca_bundle' is alleen voor bouwomgevingen achter een TLS-proxy; in GitHub Actions blijft hij weg.

ARG PYTHON_IMAGE=docker.io/library/python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
ARG POSTGRES_IMAGE=docker.io/library/postgres:16.15-bookworm@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9

# ---------------------------------------------------------------- PostgreSQL 16 server + client (zonder JIT)
FROM ${POSTGRES_IMAGE} AS pg
RUN set -eu; mkdir -p /out/lib /out/pg; \
    rm -f /usr/lib/postgresql/16/lib/llvmjit*.so; rm -rf /usr/lib/postgresql/16/lib/bitcode; \
    for f in /usr/lib/postgresql/16/bin/* /usr/lib/postgresql/16/lib/*.so; do ldd "$f" 2>/dev/null || true; done \
      | awk '/=>/ {print $3}' | sort -u \
      | grep -vE '/(libc|libm|libdl|libpthread|librt|libresolv|ld-linux[^/]*)\.so' \
      | while read -r lib; do cp -L "$lib" /out/lib/; done; \
    cp -rL /usr/lib/postgresql/16 /out/pg/lib; cp -rL /usr/share/postgresql/16 /out/pg/share; \
    test -f /out/pg/share/postgresql.conf.sample

# ---------------------------------------------------------------- Python-afhankelijkheden (hash-gepind)
FROM ${PYTHON_IMAGE} AS deps
WORKDIR /src
COPY apps/api/requirements.lock ./
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -s /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install --no-cache-dir --disable-pip-version-check --root-user-action=ignore --prefix=/install \
        --require-hashes --no-deps --only-binary=:all: -r requirements.lock

# ---------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE}
ARG VERSION=0.1.0
ARG REVISION=unknown
LABEL org.opencontainers.image.title="jelly-leon-platform" \
      org.opencontainers.image.description="Lifecycle-dienst TrueNAS Home Platform V2 — alles-in-één (PostgreSQL, API, worker)" \
      org.opencontainers.image.source="https://github.com/lvos04/jelly-leon-platform" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"
COPY --from=pg /out/pg/lib /usr/lib/postgresql/16
COPY --from=pg /out/pg/share /usr/share/postgresql/16
COPY --from=pg /out/lib /opt/pglibs
COPY --from=deps /install /usr/local
COPY apps/api/src/lifecycle /usr/local/lib/python3.12/site-packages/lifecycle
COPY deploy/allinone/supervisor.py deploy/allinone/healthcheck.py /usr/local/lib/lifecycle-allinone/
RUN set -eu; \
    for b in postgres initdb pg_ctl pg_isready psql pg_dump pg_restore; do \
      printf '#!/bin/sh\nLD_LIBRARY_PATH=/opt/pglibs exec /usr/lib/postgresql/16/bin/%s "$@"\n' "$b" > /usr/local/bin/$b; \
      chmod 0755 /usr/local/bin/$b; \
    done; \
    postgres --version | grep -q ' 16\.'; pg_dump --version | grep -q ' 16\.'; \
    groupadd -g 568 apps; useradd -u 568 -g 568 -M -d /tmp -s /usr/sbin/nologin apps; \
    mkdir -p /appdata; chown 568:568 /appdata; \
    find /usr/local/lib/python3.12/site-packages/lifecycle -name '__pycache__' -prune -exec rm -rf {} +; \
    python -c "import lifecycle.app, lifecycle.worker, psycopg, uvicorn, cryptography; print('imports ok')"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    LIFECYCLE_DATA_DIR=/appdata LIFECYCLE_PORT=30440 TZ=Etc/UTC
VOLUME ["/appdata"]
EXPOSE 30440
HEALTHCHECK --interval=30s --timeout=8s --start-period=90s --retries=3 \
  CMD ["python", "/usr/local/lib/lifecycle-allinone/healthcheck.py"]
# De bewaker is PID 1: ruimt zombies op, vangt SIGTERM af en wisselt zelf naar 568 (geen root-init).
ENTRYPOINT ["python", "/usr/local/lib/lifecycle-allinone/supervisor.py"]
