"""Redactie: geheimen komen nooit in logs, evidence of API-antwoorden (contract §3)."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|private|credential|koppelcode|pairing_code|vault)",
    re.IGNORECASE,
)
BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
# lange willekeurige strings (tokens, sleutels) in vrije tekst; UUID's en hex-digests (sha256, git-sha) blijven leesbaar
LONG_SECRETISH = re.compile(r"\b(?![0-9a-fA-F]{8}-[0-9a-fA-F]{4}-)(?![0-9a-f]{32,}\b)[A-Za-z0-9_-]{32,}\b")

MASK = "***"


def last4(value: str | None) -> str | None:
    if not value:
        return None
    return value[-4:] if len(value) >= 8 else None


def redact_text(text: str) -> str:
    text = BEARER.sub("Bearer " + MASK, text)
    return LONG_SECRETISH.sub(MASK, text)


def redact(value: Any, _depth: int = 0) -> Any:
    """Geeft een kopie terug waarin gevoelige sleutels gemaskeerd zijn en vrije tekst is geschoond."""
    if _depth > 12:
        return MASK
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and SENSITIVE_KEYS.search(key) and not key.endswith(("_last4", "_ref", "_at")):
                out[key] = MASK if item not in (None, "") else item
            else:
                out[key] = redact(item, _depth + 1)
        return out
    if isinstance(value, list | tuple):
        return [redact(item, _depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
