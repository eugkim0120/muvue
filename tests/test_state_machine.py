"""P0 acceptance #1: exhaustive state-machine table test.

Covers every (from_status, to_status) pair over the full status set,
asserting the transition table matches the spec, and that lease-owner
enforcement holds for every edge that requires it.
"""

import itertools

import pytest

from muvue.core import state_machine as sm

ALL_STATUSES = sorted(sm.STATUSES)


@pytest.mark.parametrize(
    "from_status,to_status", list(itertools.product(ALL_STATUSES, ALL_STATUSES))
)
def test_exhaustive_transition_table(from_status, to_status):
    expected_legal = (from_status, to_status) in sm.TRANSITIONS
    assert sm.can_transition(from_status, to_status) == expected_legal

    if not expected_legal:
        with pytest.raises(sm.InvalidTransition):
            sm.validate_transition(
                from_status, to_status, owner="alice", actor="alice"
            )
        with pytest.raises(sm.InvalidTransition):
            sm.validate_transition(
                from_status, to_status, owner=None, actor=None
            )
        return

    needs_owner = sm.requires_owner(from_status, to_status)
    if needs_owner:
        # Correct owner: succeeds.
        sm.validate_transition(from_status, to_status, owner="alice", actor="alice")
        # Wrong owner: rejected.
        with pytest.raises(sm.NotLeaseOwner):
            sm.validate_transition(from_status, to_status, owner="alice", actor="bob")
        # No owner recorded at all: rejected.
        with pytest.raises(sm.NotLeaseOwner):
            sm.validate_transition(from_status, to_status, owner=None, actor="alice")
    else:
        # Ownership is irrelevant for this edge; any actor/owner combo is fine.
        sm.validate_transition(from_status, to_status, owner=None, actor="anyone")
        sm.validate_transition(from_status, to_status, owner="alice", actor="bob")


def test_documented_spec_statuses_are_covered():
    # plan section 3: pending -> ready -> in_progress -> review -> done,
    # side states awaiting_approval, blocked, failed.
    for status in [
        "pending",
        "ready",
        "in_progress",
        "review",
        "done",
        "awaiting_approval",
        "blocked",
        "failed",
    ]:
        assert status in sm.STATUSES


def test_main_happy_path_is_legal():
    assert sm.can_transition("pending", "ready")
    assert sm.can_transition("ready", "in_progress")
    assert sm.can_transition("in_progress", "review")
    assert sm.can_transition("review", "done")


def test_done_is_terminal():
    for status in ALL_STATUSES:
        assert not sm.can_transition("done", status)
