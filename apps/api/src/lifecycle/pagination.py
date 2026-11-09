"""Cursor-paginering (contract §5): ``cursor`` + ``limit`` (max 200). De cursor is opaak (base64url)."""

from __future__ import annotations

import base64
import json
from typing import Any

from lifecycle.errors import ApiError

MAX_LIMIT = 200
DEFAULT_LIMIT = 50


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    if limit < 1 or limit > MAX_LIMIT:
        raise ApiError(400, "invalid_limit", f"limit moet tussen 1 en {MAX_LIMIT} liggen.", {"max": MAX_LIMIT})
    return limit


def encode_cursor(data: dict[str, Any]) -> str:
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApiError(400, "invalid_cursor", "Ongeldige cursor.") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "invalid_cursor", "Ongeldige cursor.")
    return data
