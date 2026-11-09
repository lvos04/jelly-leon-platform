"""Opdrachtregel binnen de container (niet voor de gebruiker): ``python -m lifecycle <opdracht>``.

- ``init``            eerste-start: ontbrekende geheimen aanmaken (nooit overschrijven)
- ``migrate``         databaseschema bijwerken (alles in één transactie); ``--down REV`` voor terugzetten
- ``pending``         exitcode 10 als er migraties openstaan (supervisor maakt dan eerst een back-up)
- ``api``             API-server (uvicorn)
- ``worker``          achtergrondproces
- ``export-openapi``  OpenAPI naar bestand (CI controleert drift)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from lifecycle.config import ConfigError, load_settings


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)


def cmd_init() -> int:
    from lifecycle.secrets_store import SecretsError, ensure_secrets

    settings = load_settings()
    try:
        created = ensure_secrets(settings.secrets_dir)
    except SecretsError as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 2
    print("geheimen aangemaakt: " + (", ".join(created) if created else "geen (al aanwezig)"))
    return 0


def cmd_migrate(down: str | None) -> int:
    from lifecycle.migrations_util import downgrade, upgrade

    settings = load_settings()
    if down:
        downgrade(settings.database_url, down)
    else:
        upgrade(settings.database_url)
    return 0


def cmd_pending() -> int:
    from lifecycle.db import make_engine
    from lifecycle.migrations_util import migration_status

    engine = make_engine(load_settings().database_url)
    try:
        current, head = migration_status(engine)
    finally:
        engine.dispose()
    print(json.dumps({"current": current, "head": head}))
    return 0 if current == head else 10


def cmd_api() -> int:
    import uvicorn

    from lifecycle.app import create_app

    settings = load_settings()
    # /readyz (Docker-healthcheck elke 30 s) leest de schemaversie; zonder dit twee INFO-regels per controle in de log
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        # Nooit X-Forwarded-For/-Proto vertrouwen: het bron-IP van de TCP-verbinding bepaalt de rate-limit van de
        # statuspagina (contract 1.2 §3.7). Bewust geen optie om dit aan te zetten.
        proxy_headers=False,
        server_header=False,
        log_level="info",
    )
    return 0


def cmd_worker() -> int:
    from lifecycle.worker import Worker

    Worker(load_settings()).run()
    return 0


def cmd_export_openapi(path: str) -> int:
    from lifecycle.app import create_app
    from lifecycle.config import Settings
    from lifecycle.db import make_engine

    app = create_app(Settings(), engine=make_engine("postgresql+psycopg://openapi@/openapi?host=/nonexistent"))
    spec = app.openapi()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(spec, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"OpenAPI geschreven naar {path}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lifecycle", description="jelly-leon-platform lifecycle-dienst")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    migrate = sub.add_parser("migrate")
    migrate.add_argument("--down", default=None, help="terug naar deze revisie")
    sub.add_parser("pending")
    sub.add_parser("api")
    sub.add_parser("worker")
    export = sub.add_parser("export-openapi")
    export.add_argument("path")
    args = parser.parse_args(argv)
    _setup_logging()
    try:
        if args.cmd == "init":
            code = cmd_init()
        elif args.cmd == "migrate":
            code = cmd_migrate(args.down)
        elif args.cmd == "pending":
            code = cmd_pending()
        elif args.cmd == "api":
            code = cmd_api()
        elif args.cmd == "worker":
            code = cmd_worker()
        else:
            code = cmd_export_openapi(args.path)
    except ConfigError as exc:
        print(f"FOUT in configuratie: {exc}", file=sys.stderr)
        code = 78
    sys.exit(code)
