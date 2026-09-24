"""Git trailer parsing for the post-commit hook (plan section 5/7).

"Trailers are labels, not trusted for binding in light mode" (plan
section 5): light mode has no cryptographic or CI-enforced link between a
commit and a node, so `Muvue-Node:`/`Refs:` trailers are read best-effort
from the raw commit message text, not from a verified `git interpret-
trailers` structured parse. `nodes.id` is the schema's integer primary
key (plan section 3) -- P0-P2 never built a human-readable task-numbering
scheme (e.g. the plan's illustrative "T3.2"), so trailer values are read
as plain integers referencing `nodes.id` directly.

Squash-merge behavior (P3 acceptance #3): a squashed commit message
concatenates the original commits' messages, so it can contain several
`Muvue-Node:`/`Refs:` lines. Decision: extract every id from every
matching line, de-duplicated in first-seen order, rather than keeping
only the first or last match. The post-commit hook only uses this to
link/annotate `node_commits` bookkeeping and enqueue no-op anchor-hash/
staleness signals (nothing security- or approval-relevant), so dropping a
legitimate id because a squash concatenated messages would silently lose
real information for no safety benefit -- see docs/decisions.md.
"""

from __future__ import annotations

import re

_TRAILER_LINE_RE = re.compile(r"^(?:Muvue-Node|Refs):\s*(.+)$", re.MULTILINE)
_TOKEN_SPLIT_RE = re.compile(r"[,\s]+")


def parse_node_ids(message: str) -> list[int]:
    """Extract every `nodes.id` referenced by a `Muvue-Node:`/`Refs:`
    trailer line anywhere in `message`, de-duplicated, first-seen order.
    A trailer's value is split on commas/whitespace into tokens; only
    tokens that are plain (base-10) integers resolve. A token that isn't
    a bare integer (e.g. a future human-readable label like "T3.2") is
    silently skipped -- unresolvable, not an error, per the "labels, not
    trusted for binding" framing -- rather than having its embedded
    digits misread as an id."""
    ids: list[int] = []
    for line_match in _TRAILER_LINE_RE.finditer(message):
        for token in _TOKEN_SPLIT_RE.split(line_match.group(1).strip()):
            if token.isdigit():
                n = int(token)
                if n not in ids:
                    ids.append(n)
    return ids
