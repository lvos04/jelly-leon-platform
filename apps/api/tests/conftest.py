"""Gedeelde fixtures. Integratietests gebruiken een echte PostgreSQL (CI: service-container; lokaal
``LIFECYCLE_TEST_PG`` of de standaard ``postgresql://postgres@127.0.0.1:5432``). Per testsessie een eigen database
met het repo-voorvoegsel ``jlp_``."""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "e2e" / "simulators" / "hub"))

PG_ADMIN = os.environ.get("LIFECYCLE_TEST_PG", "postgresql+psycopg://postgres@127.0.0.1:5432/postgres")


def _pg_available() -> bool:
    try:
        engine = create_engine(PG_ADMIN, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def pg_url() -> Iterator[str]:
    if not _pg_available():
        pytest.skip("PostgreSQL niet bereikbaar (zet LIFECYCLE_TEST_PG)")
    name = f"jlp_test_{uuid.uuid4().hex[:10]}"
    admin = create_engine(PG_ADMIN, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    base = PG_ADMIN.rsplit("/", 1)[0]
    url = f"{base}/{name}"
    from lifecycle.migrations_util import upgrade

    upgrade(url)
    yield url
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def clean_db(pg_url: str) -> Iterator[str]:
    yield pg_url
    engine = create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE lifecycle_plans, lifecycle_transitions, lifecycle_runs, idempotency_records, jwt_replay, "
                "outbox_events, audit_log, worker_heartbeats RESTART IDENTITY CASCADE"
            )
        )
    engine.dispose()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    from lifecycle.secrets_store import ensure_secrets

    ensure_secrets(tmp_path / "secrets")
    return tmp_path


@pytest.fixture
def hub() -> Any:
    from hub_sim import HubState, create_hub_app

    state = HubState()
    return create_hub_app(state)


HUB_TEST_URL = "http://hub.test"


def link_with_hub(data_dir: Path, sim: Any, hub_url: str = HUB_TEST_URL, *, confirmed: bool = True) -> str:
    """Gekoppelde toestand zoals na een geslaagde enrollment bij de Hub-simulator: ``hub-link.json`` met de
    gepinde sleutels van de simulator en een geregistreerde dienst. Geeft het service-token terug."""
    from lifecycle import APP_ID
    from lifecycle.hublink import HubLinkStore, now_iso
    from lifecycle.keys import hub_jwk_to_pin, keyset_fingerprint_of_thumbprints

    service_id, token = sim.register_service(APP_ID)
    jwt_pins = [hub_jwk_to_pin(k) for k in sim.jwks()["keys"]]
    policy = hub_jwk_to_pin(sim.policy_jwk())
    at = now_iso()

    def change(doc: dict[str, Any]) -> None:
        doc.update(
            state="gekoppeld", hub_url=hub_url, service_id=service_id, via="koppelcode", first_linked_at=at,
            linked_at=at, confirmed_at=at if confirmed else None, service_token=token, service_token_last4=token[-4:],
            pins={"jwt": [p.as_entry(at, "enroll") for p in jwt_pins], "policy": [policy.as_entry(at, "enroll")],
                  "keyset_fingerprint": keyset_fingerprint_of_thumbprints([p.thumbprint for p in jwt_pins],
                                                                          [policy.thumbprint]),
                  "rotation_sequence": 0},
            pending=None, last_error=None,
        )

    HubLinkStore(data_dir / "secrets").update(change)
    return str(token)


@pytest.fixture
def make_app(clean_db: str, data_dir: Path, hub: Any) -> Any:
    """Fabriek voor de app. Standaard gekoppeld met de Hub-simulator (``linked=False`` voor de niet-gekoppelde toestand:
    dan wordt elk JWT geweigerd)."""
    from starlette.testclient import TestClient

    from lifecycle.app import create_app
    from lifecycle.config import Settings

    def factory(**overrides: Any) -> Any:
        from lifecycle.hublink import HubLinkStore

        if overrides.pop("linked", True) and not HubLinkStore(data_dir / "secrets").read().linked:
            link_with_hub(data_dir, hub.state.sim)
        settings = Settings(
            data_dir=data_dir,
            database_url=clean_db,
            hub_url=overrides.pop("hub_url", HUB_TEST_URL),
            enrollment_token=overrides.pop("enrollment_token", None),
            **overrides,
        )
        return create_app(settings, http_client_factory=lambda: TestClient(hub, base_url=HUB_TEST_URL,
                                                                           follow_redirects=False))

    return factory


@pytest.fixture
def client(make_app: Any) -> Iterator[Any]:
    from starlette.testclient import TestClient

    with TestClient(make_app(), base_url="http://lifecycle.test") as c:
        yield c


@pytest.fixture
def token(hub: Any) -> Any:
    def mint(*roles: str, **kw: Any) -> str:
        kw.setdefault("aud", "jelly-leon-platform")
        return str(hub.state.sim.mint(kw.pop("sub", "gebruiker-1"), list(roles), **kw))

    return mint


@pytest.fixture
def auth(token: Any) -> Any:
    def headers(*roles: str, idem: bool = False, **extra: str) -> dict[str, str]:
        h = {"Authorization": f"Bearer {token(*roles)}", **extra}
        if idem:
            h["Idempotency-Key"] = str(uuid.uuid4())
        return h

    return headers


@pytest.fixture
def gebonden_updateroute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Doet alsof de gebonden update-catalogusroute van de Hub er al is (VERSIE 2), zodat de updateketen achter de
    §8.2-blokkade (schaduwtest, digest-readback, terugzetten) getest blijft. De blokkade zelf: test_plans_update_los."""
    from lifecycle import api_plans

    monkeypatch.setattr(api_plans, "GEBONDEN_UPDATEROUTE", True)


@pytest.fixture
def acl_dataset(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Bootst een ZFS-dataset met NFSv4-ACL na (TrueNAS-preset Apps; ZFS met NFSv4-ACL is lokaal niet beschikbaar):
    elk nieuw bestand krijgt 0770 uit de overgeërfde ACE's (``aclinherit=passthrough`` negeert de 0600 van open()).
    ``chmod``: ``werkt`` (aclmode=passthrough), ``geweigerd`` (aclmode=restricted) of ``genegeerd``. ``herkend``: of
    de NFSv4-ACL zichtbaar is als xattr. De ACE verleent de eigenaar of de groep lees- en schrijfrecht."""
    import errno
    import stat
    import struct

    echte_open, echte_fchmod, echte_chmod = os.open, os.fchmod, os.chmod
    echte_getxattr = os.getxattr

    def zet(chmod: str, herkend: bool, *, toegang: str = "eigenaar", alleen_bestanden: bool = False) -> None:
        def getxattr(doel: Any, naam: str, **kwargs: Any) -> bytes:
            if naam == "system.nfs4_acl_xdr" and herkend:
                st = os.fstat(doel) if isinstance(doel, int) else os.lstat(doel)
                if not alleen_bestanden or stat.S_ISREG(st.st_mode):
                    # XDR-kop (vlaggen, aantal), één overgeërfde ALLOW voor OWNER@ (1) of GROUP@ (2).
                    who = 1 if toegang == "eigenaar" else 2
                    return struct.pack(">7I", 0, 1, 0, 0x80, 1, 0x1F01FF, who)
            return echte_getxattr(doel, naam, **kwargs)

        def open_acl(path: Any, flags: int, mode: int = 0o777, *args: Any, **kwargs: Any) -> int:
            fd = echte_open(path, flags, mode, *args, **kwargs)
            if flags & os.O_CREAT and stat.S_ISREG(os.fstat(fd).st_mode):
                echte_fchmod(fd, 0o770)
            return fd

        def wijzig(echt: Any) -> Any:
            def chmod_acl(doel: Any, mode: int, *args: Any, **kwargs: Any) -> None:
                if chmod == "geweigerd":
                    raise PermissionError(errno.EPERM, "Operation not permitted")
                if chmod == "werkt":
                    echt(doel, mode, *args, **kwargs)

            return chmod_acl

        monkeypatch.setattr(os, "open", open_acl)
        monkeypatch.setattr(os, "fchmod", wijzig(echte_fchmod))
        monkeypatch.setattr(os, "chmod", wijzig(echte_chmod))
        monkeypatch.setattr(os, "getxattr", getxattr)

    return zet
