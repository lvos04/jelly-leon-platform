"""Start release, migratie, geweigerde release en herstelde release na elkaar op hetzelfde eigen volume."""

import httpx

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import meet, schrijfacties
from e2e.integral.scenarios.opslag import lees_proefmedia, plaats_proefmedia

SCENARIO = {
    "groepen": ("opslag",), "eigenaar": "jelly-leon-platform",
    "diensten": ("schema-release", "schema-nieuw"), "later_starten": ("schema-release", "schema-nieuw"),
    "verwachting": "Release 0.1.0 weigert het nieuwe schema en start met de herstelde database en proefrij.",
}

OUD = "schema-release"
NIEUW = "schema-nieuw"
REVISIE = "SELECT version_num FROM alembic_version"
KOLOM = ("SELECT count(*) FROM information_schema.columns WHERE table_name='lifecycle_runs' "
         "AND column_name='e2e_extra'")

# Beide applicaties zijn gestopt. De bestaande gemigreerde database blijft als aparte database bewaard.
HERSTEL = """
set -eu
set -- /appdata/backups/voor-upgrade-0003-*.dump
test "$#" = 1 && test -s "$1"
export PGHOST=/appdata/backups/herstelsocket PGUSER=lifecycle
mkdir -m 700 "$PGHOST"
pg_ctl -D /appdata/postgres -l "$PGHOST/postgres.log" -w start \
    -o "-c listen_addresses='' -c unix_socket_directories=$PGHOST -c jit=off"
trap 'pg_ctl -D /appdata/postgres -m fast -w stop' EXIT
psql -X -v ON_ERROR_STOP=1 -d postgres -c 'CREATE DATABASE ex15_hersteld TEMPLATE template0'
pg_restore --exit-on-error --single-transaction -d ex15_hersteld "$1"
psql -X -v ON_ERROR_STOP=1 -d postgres -c 'ALTER DATABASE lifecycle RENAME TO ex15_nieuw'
psql -X -v ON_ERROR_STOP=1 -d postgres -c 'ALTER DATABASE ex15_hersteld RENAME TO lifecycle'
"""


def test_release_na_schemawijziging_en_databaseherstel(kern: Kern) -> None:
    p = kern.net.project
    images = p.images((OUD, NIEUW))
    assert images[OUD]["image_id"] != images[NIEUW]["image_id"]
    assert images[OUD]["broncommit"] == "ac213b8c0fd359986660dc35fcd96cb598c0c56f"
    assert images[OUD]["broncommit"] != images[NIEUW]["broncommit"]
    oud, nieuw = kern.client(OUD, 30440), kern.client(NIEUW, 30440)

    socketmap = "/tmp/lifecycle-pg"  # noqa: S108 -- privésocket van de procesbewaker in de container

    def sql(dienst: str, tekst: str, db: str = "lifecycle") -> str:
        return p.compose("exec", "-T", "--user", "568:568", dienst, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                         "-h", socketmap, "-U", "lifecycle", "-d", db, "-tAc", tekst).strip()

    def start(dienst: str, client: httpx.Client) -> None:
        p.start((dienst,))
        wacht("schema-app gereed", lambda: client.get("/readyz").json()["status"] == "ready")

    plaats_proefmedia(kern)
    start(OUD, oud)
    assert sql(OUD, REVISIE) == "0003" and sql(OUD, KOLOM) == "0"
    sql(OUD, "INSERT INTO lifecycle_runs (id, app_id, kind, state, requested_by_type, requested_by_id, trace_id, "
        "evidence, version, created_at, updated_at) VALUES (gen_random_uuid(), 'proef-config', 'update', 'discovered', "
        "'user', 'proef', gen_random_uuid(), '{}', 1, now(), now())")
    marker = "SELECT app_id FROM lifecycle_runs WHERE app_id='proef-config'"
    assert sql(OUD, marker) == "proef-config"
    p.compose("stop", OUD)
    start(NIEUW, nieuw)
    assert sql(NIEUW, REVISIE) == "0004" and sql(NIEUW, KOLOM) == "1" and sql(NIEUW, marker) == "proef-config"
    lees_proefmedia(kern, 41, 7)
    p.compose("stop", NIEUW)
    p.start((OUD,))
    wacht("weigering door oude procesbewaker",
          lambda: "databasemigratie mislukt" in p.compose("logs", "--no-color", OUD))
    try:
        antwoord = oud.get("/readyz")
    except httpx.ConnectError:
        pass  # De procesbewaker start de API niet na de geweigerde migratie.
    else:
        assert not antwoord.is_success
    lees_proefmedia(kern, 41, 7)
    p.compose("stop", OUD)
    p.compose("run", "--rm", "--no-deps", "-T", "--user", "568:568", "--entrypoint", "sh", OUD, "-ec", HERSTEL)
    start(OUD, oud)
    assert sql(OUD, REVISIE) == "0003" and sql(OUD, KOLOM) == "0" and sql(OUD, marker) == "proef-config"
    assert sql(OUD, REVISIE, "ex15_nieuw") == "0004" and sql(OUD, marker, "ex15_nieuw") == "proef-config"
    lees_proefmedia(kern, 41, 7)
    assert schrijfacties(kern) == []
    meet(kern, verschillende_images=2, revisie_voor=3, proefrevisie_na=4, tag_alleen_geweigerd=True,
         revisie_na_herstel=3, proefrij_behouden=True, gemigreerde_database_bewaard=True, mediacontroles=4)
