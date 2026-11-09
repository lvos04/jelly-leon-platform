from __future__ import annotations

import itertools

import pytest

from lifecycle.statemachine import (
    FORWARD,
    GUARDS,
    MANUAL_ALLOWED_P0,
    PRE_DEPLOY,
    TERMINAL,
    TRANSITIONS,
    LifecycleState,
    TransitionContext,
    TransitionError,
    check_transition,
    describe,
)

S = LifecycleState


def test_forward_chain_matches_operations_doc() -> None:
    # 01_ARCHITECTUUR/05: discovered → inventory → risk-evaluated → backed-up → staged → tested → approved →
    # deployed → observed → accepted | rolled_back
    assert [s.value for s in FORWARD] == [
        "discovered", "inventory", "risk_evaluated", "backed_up", "staged", "tested", "approved", "deployed",
        "observed", "accepted",
    ]
    for current, nxt in itertools.pairwise(FORWARD):
        assert nxt in TRANSITIONS[current]


def test_no_skipping_backup_or_approval() -> None:
    with pytest.raises(TransitionError) as exc:
        check_transition(S.RISK_EVALUATED, S.STAGED, TransitionContext({"staging_ref": "x"}))
    assert exc.value.code == "invalid_transition"
    with pytest.raises(TransitionError):
        check_transition(S.TESTED, S.DEPLOYED, TransitionContext({"execution_evidence_ref": "x"}))


def test_rolled_back_only_after_deploy() -> None:
    for state in PRE_DEPLOY:
        assert S.ROLLED_BACK not in TRANSITIONS[state], state
        assert S.ABORTED in TRANSITIONS[state]
    assert S.ROLLED_BACK in TRANSITIONS[S.DEPLOYED]
    assert S.ROLLED_BACK in TRANSITIONS[S.OBSERVED]
    assert S.ABORTED not in TRANSITIONS[S.DEPLOYED]


def test_terminal_states_are_final() -> None:
    for state in TERMINAL:
        with pytest.raises(TransitionError) as exc:
            check_transition(state, S.DISCOVERED, TransitionContext())
        assert exc.value.code == "run_terminal"


def test_guard_requires_evidence_and_rejects_false() -> None:
    with pytest.raises(TransitionError) as exc:
        check_transition(S.INVENTORY, S.RISK_EVALUATED, TransitionContext({}))
    assert exc.value.code == "transition_evidence_missing"
    with pytest.raises(TransitionError) as exc:
        check_transition(S.RISK_EVALUATED, S.BACKED_UP, TransitionContext({"backup_checkpoint_id": "c1",
                                                                         "backup_verified": False}))
    assert exc.value.details["missing"] == ["backup_verified"]
    check_transition(S.RISK_EVALUATED, S.BACKED_UP, TransitionContext({"backup_checkpoint_id": "c1",
                                                                     "backup_verified": True}))


def test_every_non_initial_state_has_a_guard() -> None:
    for state in LifecycleState:
        if state is not S.DISCOVERED:
            assert GUARDS.get(state), state


def test_manual_p0_only_abort() -> None:
    assert frozenset({S.ABORTED}) == MANUAL_ALLOWED_P0


def test_describe_is_machine_readable() -> None:
    d = describe()
    assert d["initial"] == "discovered"
    ids = [s["id"] for s in d["states"]]  # type: ignore[index]
    assert set(ids) == {s.value for s in LifecycleState}
    assert d["manual_transitions_p0"] == ["aborted"]
