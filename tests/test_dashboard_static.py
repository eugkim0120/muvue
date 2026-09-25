"""Static checks on the single-file dashboard (v4 section 8, working
rules): no CDN or external script, no inline event handlers, no
persistent storage of the token, and every endpoint it calls exists."""

from __future__ import annotations

import re
from pathlib import Path

from muvue.api import create_app

INDEX = Path(__file__).resolve().parents[1] / "src" / "muvue" / "api" / "static" / "index.html"


def test_no_external_resources():
    html = INDEX.read_text()
    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<link[^>]+href=\"https?:", html)


def test_no_inline_event_handlers():
    html = INDEX.read_text()
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", html)


def test_token_never_reaches_persistent_storage():
    html = INDEX.read_text()
    for api in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
        assert api not in html


def test_every_called_endpoint_is_served(tmp_path):
    from muvue.core.repo_init import init_repo

    init_repo(tmp_path)
    routes = [r.path for r in create_app(tmp_path).routes]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$") for p in routes]
    html = INDEX.read_text()
    # Fold `"/nodes/" + n.id + "/diff"` into `"/nodes/0/diff"`, and a
    # trailing `"/events/" + ev.id` into `"/events/0"`.
    # A concatenation after a path without a trailing slash is a query.
    html = re.sub(r'/"\s*\+\s*[\w.]+(?:\([^()]*\))?\s*\+\s*"', "/0", html)
    html = re.sub(r'/"\s*\+\s*[\w.]+(?:\([^()]*\))?\s*([,)])', r'/0"\1', html)
    called = set(re.findall(r"""(?:api|post)\(\s*"(/[^"?]*)""", html))
    assert called, "found no endpoint calls; the pattern is stale"
    missing = sorted(c for c in called if not any(p.match(c) for p in patterns))
    assert missing == []
