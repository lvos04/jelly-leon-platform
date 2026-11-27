"""Start release, migratie, geweigerde release en herstelde release na elkaar op hetzelfde eigen volume."""

import httpx
import pytest

from e2e.integral.project import Proeffout
from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import meet, schrijfacties
from e2e.integral.scenarios.opslag import plaats_proefmedia

SCENARIO = {
    "groepen": ("opslag",), "eigenaar": "jelly-leon-platform",
    "diensten": ("schema-release", "schema-nieuw", "emby-opslag", "mg-inhoud", "arr-inhoud"),
    "later_starten": ("schema-release", "schema-nieuw"),
    "verwachting": "Release 0.1.0 weigert het nieuwe schema en start met de herstelde database en proefrij.",
}

OUD = "schema-release"
NIEUW = "schema-nieuw"
REVISIE = "SELECT version_num FROM alembic_version"
KOLOM = ("SELECT count(*) FROM information_schema.columns WHERE table_name='lifecycle_runs' "
         "AND column_name='e2e_extra'")

# Tijdens beide fasen zijn de schema-apps gestopt. De actieve database blijft beschikbaar tot de atomaire wissel.
HERSTEL = """
set -eu
fase=$1
archief=$2
verwachte_sha=$3
controleer_archief() {
    test -s "$archief"
    test "$(sha256sum "$archief" | cut -d ' ' -f 1)" = "$verwachte_sha"
}
controleer_archief
export PGHOST=/appdata/backups/herstelsocket PGUSER=lifecycle
mkdir -p -m 700 "$PGHOST"
pg_ctl -D /appdata/postgres -l "$PGHOST/postgres.log" -w start \
    -o "-c listen_addresses='' -c unix_socket_directories=$PGHOST -c jit=off"
trap 'pg_ctl -D /appdata/postgres -m fast -w stop' EXIT
case "$fase" in
    laden)
        psql -X -v ON_ERROR_STOP=1 -d postgres -c 'CREATE DATABASE ex15_hersteld TEMPLATE template0'
        pg_restore --exit-on-error --single-transaction -d ex15_hersteld "$archief"
        controleer_archief
        psql -X -v ON_ERROR_STOP=1 -v sha="$verwachte_sha" -d postgres <<'SQL'
ALTER DATABASE ex15_hersteld SET ex15.archief_sha256 TO :'sha';
SQL
        ;;
    activeren)
        geladen_sha=$(psql -XAt -v ON_ERROR_STOP=1 -d ex15_hersteld \
            -c "SELECT current_setting('ex15.archief_sha256', true)")
        [ "$geladen_sha" = "$verwachte_sha" ] || exit 42
        psql -X -v ON_ERROR_STOP=1 --single-transaction -d postgres \
            -c 'ALTER DATABASE ex15_hersteld RESET ex15.archief_sha256' \
            -c 'ALTER DATABASE lifecycle RENAME TO ex15_nieuw' \
            -c 'ALTER DATABASE ex15_hersteld RENAME TO lifecycle'
        ;;
    *) exit 2 ;;
esac
"""


def upgrade_archief(voor: set[str], na: set[str]) -> str:
    nieuw = na - voor
    assert len(nieuw) == 1, "De upgrade heeft geen eenduidig nieuw herstelarchief"
    return nieuw.pop()


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

    def archieven(dienst: str) -> set[str]:
        namen = p.compose("exec", "-T", "--user", "568:568", dienst, "find", "/appdata/backups", "-maxdepth", "1",
                          "-type", "f", "-name", "voor-upgrade-0003-*.dump", "-print")
        return set(namen.splitlines())

    def start(dienst: str, client: httpx.Client) -> None:
        p.start((dienst,))
        wacht("schema-app gereed", lambda: client.get("/readyz").json()["status"] == "ready")

    media = plaats_proefmedia(kern)
    start(OUD, oud)
    assert sql(OUD, REVISIE) == "0003" and sql(OUD, KOLOM) == "0"
    sql(OUD, "INSERT INTO lifecycle_runs (id, app_id, kind, state, requested_by_type, requested_by_id, trace_id, "
        "evidence, version, created_at, updated_at) VALUES (gen_random_uuid(), 'proef-config', 'update', 'discovered', "
        "'user', 'proef', gen_random_uuid(), '{}', 1, now(), now())")
    marker = "SELECT version FROM lifecycle_runs WHERE app_id='proef-config'"
    assert sql(OUD, marker) == "1"
    eerder = "/appdata/backups/ex15-eerdere-versie.dump"
    p.compose("exec", "-T", "--user", "568:568", OUD, "pg_dump", "-h", socketmap, "-U", "lifecycle",
              "-Fc", "-f", eerder, "lifecycle")
    eerdere_sha = p.compose("exec", "-T", "--user", "568:568", OUD, "sha256sum", eerder).split()[0]
    sql(OUD, "UPDATE lifecycle_runs SET version=2 WHERE app_id='proef-config'")
    vooraf = archieven(OUD)
    p.compose("stop", OUD)
    start(NIEUW, nieuw)
    archief = upgrade_archief(vooraf, archieven(NIEUW))
    sha = p.compose("exec", "-T", "--user", "568:568", NIEUW, "sha256sum", archief).split()[0]
    assert sql(NIEUW, REVISIE) == "0005" and sql(NIEUW, KOLOM) == "1" and sql(NIEUW, marker) == "2"
    media.meet("na_upgrade")
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
    media.meet("oude_release_geweigerd")
    p.compose("stop", OUD)

    def herstel(fase: str, bestand: str, digest: str) -> None:
        p.compose("run", "--rm", "--no-deps", "-T", "--user", "568:568", "--entrypoint", "sh", OUD,
                  "-ec", HERSTEL, "herstel", fase, bestand, digest)

    herstel("laden", archief, sha)
    media.meet("na_laden")
    with pytest.raises(Proeffout) as weigering:
        herstel("activeren", eerder, eerdere_sha)
    assert weigering.value.code == 42
    herstel("activeren", archief, sha)
    media.meet("na_activeren")
    start(OUD, oud)
    assert sql(OUD, REVISIE) == "0003" and sql(OUD, KOLOM) == "0" and sql(OUD, marker) == "2"
    assert sql(OUD, REVISIE, "ex15_nieuw") == "0005" and sql(OUD, marker, "ex15_nieuw") == "2"
    media.vervolg()
    assert schrijfacties(kern) == []
    meet(kern, verschillende_images=2, revisie_voor=3, proefrevisie_na=5, tag_alleen_geweigerd=True,
         revisie_na_herstel=3, proefrij_behouden=True, gemigreerde_database_bewaard=True,
         archief_sha256_gecontroleerd=True, mediacontroles=len(media.metingen), taak_vervolgd=True)
