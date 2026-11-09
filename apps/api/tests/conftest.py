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
                "TRUNCATE lifecycle_transitions, lifecycle_runs, idempotency_records, hub_link, outbox_events, "
                "audit_log, worker_heartbeats RESTART IDENTITY CASCADE"
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
TEST_SERVICE_TOKEN = "svc_test_service_token_0123456789"


def link_with_hub(db_url: str, data_dir: Path, hub_url: str = HUB_TEST_URL) -> None:
    """Gekoppelde toestand zoals na een geslaagde enrollment (zonder meegegeven JWKS: de app haalt ze bij de Hub).

    Een bestaande koppeling (bijv. door de worker) blijft ongemoeid.
    """
    from sqlalchemy.orm import sessionmaker

    from lifecycle.auth import sha256_hex
    from lifecycle.hublink import get_or_create, store_link
    from lifecycle.secrets_store import Vault

    engine = create_engine(db_url)
    try:
        with sessionmaker(bind=engine).begin() as session:
            if get_or_create(session).state == "gekoppeld":
                return
            store_link(
                session,
                Vault.load(data_dir / "secrets"),
                hub_url=hub_url,
                service_id="test-service",
                service_token=TEST_SERVICE_TOKEN,
                service_token_sha256=sha256_hex(TEST_SERVICE_TOKEN),
                jwks=None,
                policy_public_key=None,
                linked_via="koppelcode",
                used_enrollment_token_sha256=None,
            )
    finally:
        engine.dispose()


@pytest.fixture
def make_app(clean_db: str, data_dir: Path, hub: Any) -> Any:
    """Fabriek voor de app. Standaard gekoppeld met de Hub-simulator (``linked=False`` voor de niet-gekoppelde toestand:
    dan wordt elk JWT geweigerd)."""
    from starlette.testclient import TestClient

    from lifecycle.app import create_app
    from lifecycle.config import Settings

    def factory(**overrides: Any) -> Any:
        if overrides.pop("linked", True):
            link_with_hub(clean_db, data_dir)
        settings = Settings(
            data_dir=data_dir,
            database_url=clean_db,
            hub_url=overrides.pop("hub_url", HUB_TEST_URL),
            enrollment_token=overrides.pop("enrollment_token", None),
            jwks_min_refresh_seconds=overrides.pop("jwks_min_refresh_seconds", 0),
            **overrides,
        )
        return create_app(settings, http_client_factory=lambda: TestClient(hub, base_url=HUB_TEST_URL))

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
