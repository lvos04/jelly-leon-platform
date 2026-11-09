"""JSON-RPC-fouten, filters en de methoderegistratie van de simulator.

Foutcodes volgen truenas/api_client ``truenas_api_client/jsonrpc.py`` (TS-25.10.8): -32700 INVALID_JSON,
-32600 INVALID_REQUEST, -32601 METHOD_NOT_FOUND, -32602 INVALID_PARAMS, -32603 INTERNAL_ERROR,
-32000 TRUENAS_TOO_MANY_CONCURRENT_CALLS, -32001 TRUENAS_CALL_ERROR. ``data`` heeft de vorm uit middlewared
``api/base/server/ws_handler/rpc.py`` (``error``, ``errname``, ``reason``, ``trace``, ``extra``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

INVALID_JSON = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TOO_MANY_CALLS = -32000
CALL_ERROR = -32001

ERRNAMES = {2: "ENOENT", 13: "EACCES", 16: "EBUSY", 17: "EEXIST", 22: "EINVAL", 110: "ETIMEDOUT",
            207: "ENOTAUTHENTICATED"}
ENOENT, EACCES, EBUSY, EEXIST, EINVAL, ETIMEDOUT, ENOTAUTHENTICATED = 2, 13, 16, 17, 22, 110, 207


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_json(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return err

    @property
    def reason(self) -> str:
        return str((self.data or {}).get("reason", self.message))


def call_error(errno: int, reason: str, extra: Any = None) -> RpcError:
    return RpcError(CALL_ERROR, "Method call error", {"error": errno, "errname": ERRNAMES.get(errno, "EFAULT"),
                                                      "reason": reason, "trace": None, "extra": extra})


def validation_error(errors: list[tuple[str, str]]) -> RpcError:
    extra = [[attr, msg, EINVAL] for attr, msg in errors]
    reason = "\n".join(f"[EINVAL] {attr}: {msg}" for attr, msg in errors)
    return RpcError(INVALID_PARAMS, "Invalid params", {"error": EINVAL, "errname": "EINVAL", "reason": reason,
                                                       "trace": None, "extra": extra})


# ------------------------------------------------------------------ filters (vereenvoudigde filter_list)
def _get(item: dict[str, Any], path: str) -> Any:
    cur: Any = item
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _match(item: dict[str, Any], flt: list[Any]) -> bool:
    if len(flt) == 2 and flt[0] == "OR":
        return any(_match(item, f) for f in flt[1])
    if len(flt) != 3:
        raise validation_error([("filters", f"Ongeldig filter: {flt!r}")])
    name, op, value = flt
    current = _get(item, name)
    ops: dict[str, Callable[[], bool]] = {
        "=": lambda: current == value,
        "!=": lambda: current != value,
        ">": lambda: current is not None and current > value,
        "<": lambda: current is not None and current < value,
        ">=": lambda: current is not None and current >= value,
        "<=": lambda: current is not None and current <= value,
        "in": lambda: current in value,
        "nin": lambda: current not in value,
        "^": lambda: isinstance(current, str) and current.startswith(value),
        "$": lambda: isinstance(current, str) and current.endswith(value),
        "~": lambda: isinstance(current, str) and re.search(value, current) is not None,
    }
    if op not in ops:
        raise validation_error([("filters", f"Onbekende operator {op!r}")])
    return ops[op]()


def filter_list(items: list[dict[str, Any]], filters: list[Any] | None, options: dict[str, Any] | None) -> Any:
    filters = filters or []
    options = options or {}
    result = [i for i in items if all(_match(i, f) for f in filters)]
    for key in reversed(options.get("order_by", [])):
        desc = key.startswith("-")
        field_name = key.lstrip("-")

        def sort_key(item: dict[str, Any], name: str = field_name) -> tuple[bool, Any]:
            value = _get(item, name)
            return (value is None, value)

        result.sort(key=sort_key, reverse=desc)
    offset = int(options.get("offset", 0) or 0)
    limit = int(options.get("limit", 0) or 0)
    result = result[offset:]
    if limit:
        result = result[:limit]
    if select := options.get("select"):
        result = [{k: _get(i, k) for k in select} for i in result]
    if options.get("count"):
        return len(result)
    if options.get("get"):
        if not result:
            raise call_error(ENOENT, "MatchNotFound")
        return result[0]
    return result


# ------------------------------------------------------------------ registratie
@dataclass
class Method:
    name: str
    fn: Callable[..., Any]
    roles: frozenset[str] = frozenset()
    job: bool = False
    no_auth: bool = False
    families: frozenset[str] = frozenset({"25.04", "25.10"})
    description: str = ""
    filterable: bool = False
    pre: Callable[..., None] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


REGISTRY: dict[str, Method] = {}
F = TypeVar("F", bound=Callable[..., Any])


def register(
    name: str,
    *,
    roles: tuple[str, ...] = (),
    job: bool = False,
    no_auth: bool = False,
    families: tuple[str, ...] = ("25.04", "25.10"),
    description: str = "",
    filterable: bool = False,
    pre: Callable[..., None] | None = None,
) -> Callable[[F], F]:
    def deco(fn: F) -> F:
        doc_lines = (fn.__doc__ or "").strip().splitlines()
        desc = description or (doc_lines[0] if doc_lines else "")
        REGISTRY[name] = Method(name, fn, frozenset(roles), job, no_auth, frozenset(families), desc, filterable, pre)
        return fn

    return deco


def methods_for(family: str) -> dict[str, Method]:
    return {name: m for name, m in REGISTRY.items() if family in m.families}
