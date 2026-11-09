"""koppeling naar /appdata/secrets/hub-link.json, replaycache voor jti (contract 1.2 §3.5, §4.6)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-09

Eenmalige overzetting: een bestaande rij in ``hub_link`` (P0) gaat naar ``hub-link.json`` en de tabel verdwijnt.
- ``gekoppeld`` met een te ontsleutelen service-token, minstens één Hub-sleutel en de policy-sleutel → ``gekoppeld``
  met die sleutels gepind (zoals ze bij de toen geslaagde enrollment zijn ontvangen; ``confirmed_at`` leeg, zodat de
  worker de koppeling met ``GET /api/v1/services/self`` bevestigt);
- ``gekoppeld`` maar onvolledig (sleutels ontbreken, token niet te ontsleutelen) → ``ontkoppeld`` met
  ``last_error: overzetten_onvolledig`` (fail-closed; opnieuw koppelen via de Hub);
- anders → ``nooit_gekoppeld``; de hash van een afgehandeld env-token blijft bewaard.
Een al bestaand ``hub-link.json`` wordt nooit overschreven. Het bestand valt buiten de databasetransactie: mislukt een
latere migratie, dan herhaalt deze stap bij de volgende start en ziet het bestand al staan.
Terugzetten (downgrade) maakt een lege tabel ``hub_link``; de koppeling blijft in het bestand (zie ADR-0011).
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SVC_RE = re.compile(r"^svc_[A-Za-z0-9_-]{43}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _old_jwt_pins(jwks: Any) -> list[Any]:
    from lifecycle.keys import KeyFormatError, Pin, thumbprint_of_x

    pins: list[Pin] = []
    for jwk in (jwks or {}).get("keys", []) if isinstance(jwks, dict) else []:
        if not isinstance(jwk, dict) or jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or "d" in jwk:
            continue
        kid = jwk.get("kid")
        try:
            tp = thumbprint_of_x(str(jwk.get("x")))
        except KeyFormatError:
            continue
        if isinstance(kid, str) and kid and kid not in {p.kid for p in pins}:
            pins.append(Pin(kid=kid, x=str(jwk["x"]), thumbprint=tp))
    return pins


def _transfer(bind: sa.Connection) -> None:
    inspector = sa.inspect(bind)
    if "hub_link" not in inspector.get_table_names():
        return
    row = bind.execute(sa.text(
        "SELECT state, hub_url, service_id, service_token_enc, policy_public_key, jwks, linked_via, enrolled_at, "
        "used_enrollment_token_sha256 FROM hub_link WHERE id = 1"
    )).mappings().first()
    if row is None:
        return
    from lifecycle.hublink import HubLinkStore, empty_doc, now_iso, set_error
    from lifecycle.keys import Pin, keyset_fingerprint_of_thumbprints, thumbprint_of_x
    from lifecycle.secrets_store import Vault

    secrets_dir = Path(os.environ.get("LIFECYCLE_DATA_DIR", "/appdata")) / "secrets"
    store = HubLinkStore(secrets_dir)
    if store.path.exists():
        return  # al overgezet (of door 1.2 zelf geschreven): nooit overschrijven
    doc = empty_doc()
    used = row["used_enrollment_token_sha256"]
    if isinstance(used, str) and HEX64_RE.fullmatch(used):
        doc["used_enrollment_token_sha256"] = [used]
    if row["state"] == "gekoppeld" and row["service_token_enc"]:
        if not secrets_dir.is_dir():
            raise RuntimeError(f"{secrets_dir} ontbreekt: de koppeling kan niet worden overgezet (migratie gestopt)")
        enrolled = now_iso(row["enrolled_at"]) if row["enrolled_at"] else now_iso()
        doc.update(first_linked_at=enrolled, linked_at=enrolled, hub_url=row["hub_url"], service_id=row["service_id"],
                   via=row["linked_via"] if row["linked_via"] in {"token", "koppelcode"} else None)
        token: str | None = None
        jwt_pins: list[Pin] = []
        policy: Pin | None = None
        try:
            token = Vault.load(secrets_dir).decrypt(bytes(row["service_token_enc"]), "hub_link.service_token")
            jwt_pins = _old_jwt_pins(row["jwks"])
            if isinstance(row["policy_public_key"], str):
                tp = thumbprint_of_x(row["policy_public_key"])
                policy = Pin(kid=tp, x=row["policy_public_key"], thumbprint=tp)
        except Exception:  # elk probleem (sleutel, formaat, ontsleutelen) = onvolledig, nooit half vertrouwen
            token = None
        if token and SVC_RE.fullmatch(token) and jwt_pins and policy and row["hub_url"] and row["service_id"]:
            at = now_iso()
            doc.update(
                state="gekoppeld", service_token=token, service_token_last4=token[-4:],
                pins={"jwt": [p.as_entry(at, "overgezet") for p in jwt_pins],
                      "policy": [policy.as_entry(at, "overgezet")],
                      "keyset_fingerprint": keyset_fingerprint_of_thumbprints(
                          [p.thumbprint for p in jwt_pins], policy.thumbprint),
                      "rotation_sequence": 0},
            )
        else:
            doc["state"] = "ontkoppeld"
            set_error(doc, "overzetten_onvolledig")
    elif not secrets_dir.is_dir():
        return  # niets te bewaren en geen map om in te schrijven (bijv. lege testdatabase)
    store.update(lambda current: current.update(doc))


def upgrade() -> None:
    _transfer(op.get_bind())
    op.drop_table("hub_link")
    op.create_table(
        "jwt_replay",
        sa.Column("iss", sa.String(length=32), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("iss", "jti", name=op.f("pk_jwt_replay")),
    )
    op.create_index(op.f("ix_jwt_replay_expires_at"), "jwt_replay", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_jwt_replay_expires_at"), table_name="jwt_replay")
    op.drop_table("jwt_replay")
    op.create_table(
        "hub_link",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("hub_url", sa.String(length=512), nullable=True),
        sa.Column("service_id", sa.String(length=128), nullable=True),
        sa.Column("service_token_enc", sa.LargeBinary(), nullable=True),
        sa.Column("service_token_last4", sa.String(length=4), nullable=True),
        sa.Column("service_token_sha256", sa.String(length=64), nullable=True),
        sa.Column("policy_public_key", sa.Text(), nullable=True),
        sa.Column("jwks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("jwks_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_via", sa.String(length=16), nullable=True),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("used_enrollment_token_sha256", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('niet_gekoppeld', 'gekoppeld', 'fout')", name=op.f("ck_hub_link_state_valid")),
        sa.CheckConstraint("id = 1", name=op.f("ck_hub_link_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hub_link")),
    )
