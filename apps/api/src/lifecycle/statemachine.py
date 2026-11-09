"""Lifecycle-statusmachine (01_ARCHITECTUUR/05_OPERATIES_EN_UPDATES).

``discovered → inventory → risk_evaluated → backed_up → staged → tested → approved → deployed → observed →
accepted`` of ``rolled_back``. De pakketdocs schrijven ``risk-evaluated``/``backed-up``; in de API gebruiken we
snake_case (contract §5). Extra eindtoestand ``aborted`` (ADR-0003): een run die vóór ``deployed`` stopt heeft
productie niet aangeraakt en mag dus níet ``rolled_back`` heten — dat zou een terugzetting claimen die nooit
plaatsvond.

Fase P0 levert alleen het datamodel en de regels; de stappen zelf (back-up, staging, test, uitvoering via de
executor, readback) volgen in P2. Daarom mag een client in P0 alleen ``aborted`` handmatig zetten; alle andere
overgangen vereisen bewijs (guards) dat pas de P2-engine kan leveren.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import StrEnum


class LifecycleState(StrEnum):
    DISCOVERED = "discovered"
    INVENTORY = "inventory"
    RISK_EVALUATED = "risk_evaluated"
    BACKED_UP = "backed_up"
    STAGED = "staged"
    TESTED = "tested"
    APPROVED = "approved"
    DEPLOYED = "deployed"
    OBSERVED = "observed"
    ACCEPTED = "accepted"
    ROLLED_BACK = "rolled_back"
    ABORTED = "aborted"


class RunKind(StrEnum):
    INSTALL = "install"
    UPDATE = "update"
    ROLLBACK = "rollback"
    UNINSTALL = "uninstall"
    MOVE = "move"


S = LifecycleState

FORWARD: tuple[LifecycleState, ...] = (
    S.DISCOVERED,
    S.INVENTORY,
    S.RISK_EVALUATED,
    S.BACKED_UP,
    S.STAGED,
    S.TESTED,
    S.APPROVED,
    S.DEPLOYED,
    S.OBSERVED,
    S.ACCEPTED,
)

TERMINAL: frozenset[LifecycleState] = frozenset({S.ACCEPTED, S.ROLLED_BACK, S.ABORTED})

# Toestanden waarin productie nog niet is gewijzigd: stoppen = afbreken, niet terugzetten.
PRE_DEPLOY: frozenset[LifecycleState] = frozenset(FORWARD[: FORWARD.index(S.DEPLOYED)])


def _build_transitions() -> dict[LifecycleState, frozenset[LifecycleState]]:
    result: dict[LifecycleState, set[LifecycleState]] = {state: set() for state in LifecycleState}
    for current, nxt in itertools.pairwise(FORWARD):
        result[current].add(nxt)
    for state in PRE_DEPLOY:
        result[state].add(S.ABORTED)
    # na uitvoering kan alleen terugzetten (met bewijs) of doorgaan
    result[S.DEPLOYED].add(S.ROLLED_BACK)
    result[S.OBSERVED].add(S.ROLLED_BACK)
    return {state: frozenset(targets) for state, targets in result.items()}


TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = _build_transitions()

LABELS_NL: dict[LifecycleState, str] = {
    S.DISCOVERED: "Ontdekt",
    S.INVENTORY: "Geïnventariseerd",
    S.RISK_EVALUATED: "Risico beoordeeld",
    S.BACKED_UP: "Back-up gemaakt en gecontroleerd",
    S.STAGED: "Klaargezet (staging)",
    S.TESTED: "Getest",
    S.APPROVED: "Goedgekeurd",
    S.DEPLOYED: "Uitgevoerd",
    S.OBSERVED: "Onder observatie",
    S.ACCEPTED: "Geaccepteerd",
    S.ROLLED_BACK: "Teruggezet",
    S.ABORTED: "Afgebroken (productie ongewijzigd)",
}

# Bewijs dat verplicht is om een toestand te bereiken. Ontbreekt het, dan wordt de overgang geweigerd:
# een status mag nooit iets claimen dat niet is aangetoond.
GUARDS: dict[LifecycleState, tuple[str, ...]] = {
    S.INVENTORY: ("inventory_ref",),
    S.RISK_EVALUATED: ("policy_decision_ref",),
    S.BACKED_UP: ("backup_checkpoint_id", "backup_verified"),
    S.STAGED: ("staging_ref",),
    S.TESTED: ("test_evidence_ref",),
    S.APPROVED: ("approval_id",),
    S.DEPLOYED: ("execution_evidence_ref",),
    S.OBSERVED: ("readback_ref",),
    S.ACCEPTED: ("acceptance_ref",),
    S.ROLLED_BACK: ("rollback_evidence_ref",),
    S.ABORTED: ("reason",),
}

# Overgangen die in fase P0 via de API mogen (handmatig): alleen afbreken.
MANUAL_ALLOWED_P0: frozenset[LifecycleState] = frozenset({S.ABORTED})


class TransitionError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class TransitionContext:
    evidence: dict[str, object] = field(default_factory=dict)

    def missing(self, required: tuple[str, ...]) -> list[str]:
        missing: list[str] = []
        for key in required:
            value = self.evidence.get(key)
            if value is None or value == "" or value is False:
                missing.append(key)
        return missing


def check_transition(current: LifecycleState, target: LifecycleState, context: TransitionContext) -> None:
    """Gooit ``TransitionError`` als ``current → target`` niet mag of bewijs ontbreekt."""
    if current in TERMINAL:
        raise TransitionError(
            "run_terminal",
            f"Deze run is al afgesloten ({LABELS_NL[current]}); maak een nieuwe run.",
            {"state": current.value},
        )
    if target not in TRANSITIONS[current]:
        raise TransitionError(
            "invalid_transition",
            f"Overgang van '{LABELS_NL[current]}' naar '{LABELS_NL[target]}' is niet toegestaan.",
            {"from": current.value, "to": target.value, "allowed": sorted(t.value for t in TRANSITIONS[current])},
        )
    missing = context.missing(GUARDS.get(target, ()))
    if missing:
        raise TransitionError(
            "transition_evidence_missing",
            f"Voor '{LABELS_NL[target]}' ontbreekt verplicht bewijs: {', '.join(missing)}.",
            {"from": current.value, "to": target.value, "missing": missing},
        )


def describe() -> dict[str, object]:
    """Machineleesbare beschrijving (voor de Hub-UI en de OpenAPI-documentatie)."""
    return {
        "states": [
            {
                "id": state.value,
                "label_nl": LABELS_NL[state],
                "terminal": state in TERMINAL,
                "pre_deploy": state in PRE_DEPLOY,
                "requires": list(GUARDS.get(state, ())),
                "next": sorted(t.value for t in TRANSITIONS[state]),
            }
            for state in LifecycleState
        ],
        "initial": S.DISCOVERED.value,
        "kinds": [kind.value for kind in RunKind],
        "manual_transitions_p0": sorted(s.value for s in MANUAL_ALLOWED_P0),
    }
