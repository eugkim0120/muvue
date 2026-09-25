"""The status machine checked against a table written from the plan, not
read from `state_machine.TRANSITIONS` (v4 section 10: "exhaustive
state-machine table tests"). `test_state_machine.py` walks every pair but
takes the expected answer from the module under test, so it can't catch a
wrong edge. This table is the independent statement: changing an edge
means changing it here too, on purpose.

Owner column: True when the node has a lease at that moment, so only the
lease owner may move it (v4 section 3: "Only the lease owner may
transition a node").
"""

from __future__ import annotations

import itertools

import pytest

from muvue.core import state_machine as sm

STATUSES = [
    "pending", "ready", "in_progress", "review", "done",
    "awaiting_approval", "blocked", "failed",
]

# (from, to): (owner required, where it comes from)
EXPECTED = {
    # Main chain, v4 section 3.
    ("pending", "ready"): (False, "Gate 2 approval; nothing is leased yet"),
    ("ready", "in_progress"): (False, "start takes the lease"),
    ("in_progress", "review"): (True, "the owning agent calls done"),
    ("review", "done"): (True, "approve; the lease is held through review"),
    ("review", "in_progress"): (True, "reject sends it back to the same owner"),
    # A plan revision re-gates a changed node that hasn't started (section 3,
    # plan_revisions: "approval covers the diff").
    ("ready", "pending"): (False, "criteria edited before start"),
    # awaiting_approval: a criteria edit after Gate 2 on a running node (section 5).
    ("in_progress", "awaiting_approval"): (True, "criteria edited after start"),
    ("awaiting_approval", "in_progress"): (True, "the edit is approved"),
    # blocked(reason), section 3.
    ("in_progress", "blocked"): (True, "question, rate_limit or external"),
    ("awaiting_approval", "blocked"): (True, "blocked while waiting on the edit"),
    ("blocked", "in_progress"): (True, "unblocked with the lease still held"),
    ("blocked", "ready"): (False, "unblocked after the lease was released"),
    ("done", "blocked"): (False, "merge conflict found after done (section 6)"),
    # failed means attempts >= max_attempts; below that, fail retries.
    ("in_progress", "failed"): (True, "fail at max_attempts"),
    ("blocked", "failed"): (True, "fail while blocked, at max_attempts"),
    ("in_progress", "ready"): (True, "fail below max_attempts, or a reclaimed lease"),
}

TERMINAL = {"failed"}


def test_the_table_uses_exactly_the_spec_statuses():
    assert set(STATUSES) == set(sm.STATUSES)
    assert sm.BLOCK_REASONS == {"conflict", "rate_limit", "question", "external"}


@pytest.mark.parametrize("edge", list(itertools.product(STATUSES, STATUSES)), ids="->".join)
def test_edge_matches_the_spec(edge):
    src, dst = edge
    if edge not in EXPECTED:
        assert not sm.can_transition(src, dst), f"{src} -> {dst} is legal but the spec has no such edge"
        return
    needs_owner, why = EXPECTED[edge]
    assert sm.can_transition(src, dst), f"{src} -> {dst} ({why}) is missing"
    assert sm.requires_owner(src, dst) == needs_owner, why


def test_failed_is_terminal_and_done_only_reopens_on_conflict():
    for dst in STATUSES:
        assert not sm.can_transition("failed", dst)
        assert sm.can_transition("done", dst) == (dst == "blocked")


def test_nothing_skips_the_gate_or_review():
    assert not sm.can_transition("pending", "in_progress")
    assert not sm.can_transition("ready", "review")
    assert not sm.can_transition("in_progress", "done")
    assert not sm.can_transition("ready", "done")
