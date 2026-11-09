from __future__ import annotations

import uuid

import pytest

from lifecycle.errors import ApiError
from lifecycle.idempotency import parse_key, request_hash
from lifecycle.outbox import backoff_seconds
from lifecycle.pagination import MAX_LIMIT, clamp_limit, decode_cursor, encode_cursor


def test_idempotency_key_and_hash() -> None:
    with pytest.raises(ApiError) as exc:
        parse_key(None)
    assert exc.value.code == "idempotency_key_required"
    with pytest.raises(ApiError):
        parse_key("geen-uuid")
    k = uuid.uuid4()
    assert parse_key(str(k)) == k
    assert request_hash("post", "/x", {"a": 1, "b": 2}) == request_hash("POST", "/x", {"b": 2, "a": 1})
    assert request_hash("POST", "/x", {"a": 1}) != request_hash("POST", "/x", {"a": 2})


def test_pagination() -> None:
    assert clamp_limit(None) == 50 and clamp_limit(MAX_LIMIT) == 200
    for bad in (0, 201):
        with pytest.raises(ApiError):
            clamp_limit(bad)
    assert decode_cursor(encode_cursor({"after": 5})) == {"after": 5}
    with pytest.raises(ApiError):
        decode_cursor("!!!")


def test_backoff_grows_and_caps() -> None:
    assert 1 <= backoff_seconds(0) <= 1.25
    assert backoff_seconds(5) >= 32
    assert backoff_seconds(30) <= 3600 * 1.25
