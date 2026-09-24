"""Node status machine (plan section 3).

pending -> ready -> in_progress -> review -> done
Side states: awaiting_approval, blocked(reason), failed.

Only the lease owner (nodes.owner) may transition a node out of a state
that requires ownership. This module is the single source of truth for
which transitions are legal; muvue.core.nodes calls into it.
"""

from __future__ import annotations

STATUSES = frozenset(
    {
        "pending",
        "ready",
        "in_progress",
        "review",
        "done",
        "awaiting_approval",
        "blocked",
        "failed",
    }
)

BLOCK_REASONS = frozenset({"conflict", "rate_limit", "question", "external"})


class InvalidTransition(Exception):
    """Raised when (from_status -> to_status) is not a legal edge."""


class NotLeaseOwner(Exception):
    """Raised when actor != node.owner for a transition that requires it."""


# (from_status, to_status) -> requires_owner
# requires_owner True means: the node must already have an owner and the
# actor performing the transition must match it.
TRANSITIONS: dict[tuple[str, str], bool] = {
    ("pending", "ready"): False,
    ("ready", "pending"): False,
    ("ready", "in_progress"): False,  # start() sets the owner as part of this edge
    ("in_progress", "review"): True,
    ("review", "done"): True,
    ("review", "in_progress"): True,  # rejected / reopened
    ("in_progress", "awaiting_approval"): True,
    ("awaiting_approval", "in_progress"): True,
    ("in_progress", "blocked"): True,
    ("awaiting_approval", "blocked"): True,
    ("blocked", "ready"): False,  # lease released on unblock
    ("blocked", "in_progress"): True,
    ("in_progress", "failed"): True,
    ("blocked", "failed"): True,
    ("in_progress", "ready"): True,  # retry after a fail() below max_attempts
    # P5 "Merging" (plan section 6): a merge conflict discovered after a
    # node is `done` blocks it for a human/rebase subtask to resolve. No
    # owner check -- `done` already cleared `lease_until`, and the daemon
    # (not any single agent) is what attempts the merge.
    ("done", "blocked"): False,
}


def can_transition(from_status: str, to_status: str) -> bool:
    return (from_status, to_status) in TRANSITIONS


def requires_owner(from_status: str, to_status: str) -> bool:
    return TRANSITIONS.get((from_status, to_status), False)


def validate_transition(
    from_status: str,
    to_status: str,
    *,
    owner: str | None,
    actor: str | None,
) -> None:
    """Raise InvalidTransition or NotLeaseOwner if the edge is illegal.

    `owner` is the node's current lease owner (nodes.owner column).
    `actor` is who is attempting the transition.
    """
    if from_status not in STATUSES or to_status not in STATUSES:
        raise InvalidTransition(f"unknown status in ({from_status!r} -> {to_status!r})")
    if not can_transition(from_status, to_status):
        raise InvalidTransition(f"illegal transition {from_status!r} -> {to_status!r}")
    if requires_owner(from_status, to_status):
        if owner is None or actor != owner:
            raise NotLeaseOwner(
                f"only the lease owner ({owner!r}) may transition this node "
                f"(actor was {actor!r})"
            )
