#!/usr/bin/env bash
set -euo pipefail
cidfile="$RUNNER_TEMP/jlp-postgres.cid"
if [[ $1 == stop ]]; then
  if [[ -s $cidfile ]]; then docker rm -fv "$(cat "$cidfile")" >/dev/null; fi
  exit 0
fi
image=docker.io/library/postgres:16.15-bookworm@sha256:5264710be20f409750ac6dc4e5706dcef929c87e241605463f0f9aefa932a8d9
loopback=$(getent ahostsv4 localhost | awk 'NR == 1 {print $1}')
# De runner gebruikt hostnetwerk. Publiceer uitsluitend op loopback, op een vrije poort per job.
cid=$(docker run -d --cidfile "$cidfile" -p "$loopback::5432" --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_HOST_AUTH_METHOD=trust "$image")
adres=$(docker port "$cid" 5432/tcp)
export LIFECYCLE_TEST_PG="postgresql+psycopg://postgres@$adres/postgres"
python - <<'PY'
import os
import time
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

engine = create_engine(os.environ["LIFECYCLE_TEST_PG"], connect_args={"connect_timeout": 2})
for poging in range(45):
    try:
        with engine.connect() as conn:
            assert conn.exec_driver_sql("show server_version_num").scalar_one().startswith("16")
        break
    except OperationalError:
        if poging == 44:
            raise
        time.sleep(1)
PY
echo "LIFECYCLE_TEST_PG=$LIFECYCLE_TEST_PG" >> "$GITHUB_ENV"
