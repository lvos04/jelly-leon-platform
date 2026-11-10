"""Contract 1.3 §5 (C1.3-JLP-12): elke muterende route (POST/PUT/PATCH/DELETE) vraagt een ``Idempotency-Key``, behalve
de uitzonderingen hieronder, elk met reden en bron (contract of ADR).

Statisch: over **alle** routes van de echte app (``create_app``; geen database nodig om de routes te lezen), op de
FastAPI-afhankelijkheden van elke route. Een nieuwe muterende route zonder ``Idempotency-Key`` (en zonder reden in
``EXEMPT``) laat deze test falen; een verouderde uitzondering ook. De dynamische tegenhanger (zonder sleutel → 400
``idempotency_key_required`` ná de authenticatie) staat in ``tests/integration/test_contract_13_api.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.routing import APIRoute

from lifecycle.app import create_app
from lifecycle.config import Settings

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

EXEMPT: dict[tuple[str, str], str] = {
    # Contract 1.3 §5 (RV10): een browserformulier kan geen header meesturen; bescherming via formuliertoken,
    # herkomstcontrole, pogingenlimiet, koppelen_bezig en de eenmaligheid van de koppelcode (§3.7).
    ("POST", "/koppelen"): "formulier-POST van de statuspagina (contract 1.3 §5, §3.7)",
    # Contract 1.3 §5: alleen zolang ze 501 capability_unavailable geven (er wordt niets uitgevoerd).
    ("POST", "/connector/v1/propose"): "501 capability_unavailable (contract 1.3 §5, §6)",
    ("POST", "/connector/v1/apply"): "501 capability_unavailable (contract 1.3 §5, §6)",
    ("POST", "/connector/v1/rollback"): "501 capability_unavailable (contract 1.3 §5, §6)",
}


def _flatten(routes: list[Any]) -> list[Any]:
    """Alle routes, ook uit ingesloten routers (FastAPI ≥ 0.16x sluit routers lui in: ``effective_route_contexts``)."""
    out: list[Any] = []
    for route in routes:
        if isinstance(route, APIRoute):
            out.append(route)
        elif hasattr(route, "effective_route_contexts"):
            out.extend(ctx for ctx in route.effective_route_contexts() if hasattr(ctx, "dependant"))
    return out


def _routes() -> list[tuple[str, str, Any]]:
    app = create_app(Settings(database_url="postgresql+psycopg://niemand@127.0.0.1:1/geen"))
    out = []
    for route in _flatten(list(app.routes)):
        for method in sorted(set(route.methods or ()) & MUTATING):
            out.append((method, route.path, route))
    return out


def _asks_idempotency_key(route: Any) -> bool:
    stack = [route.dependant]
    while stack:
        dep = stack.pop()
        if any((p.alias or p.name).lower() == "idempotency-key" for p in dep.header_params):
            return True
        stack.extend(dep.dependencies)
    return False


def test_every_mutation_asks_idempotency_key_except_the_listed_ones() -> None:
    routes = _routes()
    assert len(routes) >= 12, "te weinig muterende routes gevonden: klopt create_app nog?"
    missing = [f"{m} {p}" for m, p, r in routes if (m, p) not in EXEMPT and not _asks_idempotency_key(r)]
    assert not missing, f"muterende routes zonder Idempotency-Key (of zonder reden in EXEMPT): {missing}"


def test_exemptions_still_exist_and_have_a_reason() -> None:
    """Een verouderde uitzondering (route hernoemd of verdwenen) mag niet blijven staan; elke reden noemt de bron."""
    present = {(m, p) for m, p, _ in _routes()}
    stale = sorted(set(EXEMPT) - present)
    assert not stale, f"uitzonderingen zonder route: {stale}"
    assert all("contract 1.3" in reden or "ADR-" in reden for reden in EXEMPT.values())


@pytest.mark.parametrize("path", ["/connector/v1/propose", "/connector/v1/apply", "/connector/v1/rollback"])
def test_501_exemptions_really_never_ask_for_the_key_and_stay_501(path: str) -> None:
    """De 501-uitzondering geldt alleen zolang de route niets uitvoert: de code van de route gooit altijd
    ``capability_unavailable``. Wordt hij gebouwd, dan moet hij uit ``EXEMPT`` (en vraagt hij de sleutel)."""
    import inspect

    route = next(r for m, p, r in _routes() if p == path)
    source = inspect.getsource(route.endpoint)
    assert "raise capability_unavailable(" in source and "execute(" not in source


def test_negative_a_new_route_without_key_is_caught() -> None:
    """Negatief: de controle herkent een muterende route zonder de header werkelijk als fout."""
    from fastapi import FastAPI

    app = FastAPI()

    @app.post("/nieuw")
    def nieuw() -> None:
        return None

    route = next(r for r in app.routes if isinstance(r, APIRoute))
    assert not _asks_idempotency_key(route)
