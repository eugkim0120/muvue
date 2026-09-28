"""Static checks on the built dashboard (v4 section 8, working rules):
no CDN or external script, no inline event handlers in markup, no
persistent storage of the token, and every endpoint it calls exists.
The page is built from dashboard/ (decision #166); the API paths it can
call are the ones spelled in dashboard/src/api/routes.ts."""

from __future__ import annotations

import re
from pathlib import Path

from muvue.api import create_app

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "src" / "muvue" / "api" / "static" / "index.html"
ROUTES_TS = ROOT / "dashboard" / "src" / "api" / "routes.ts"


def _markup_only(html: str) -> str:
    """The page's script and style are inlined; only the markup around
    them can carry an event-handler attribute."""
    return re.sub(r"<(script|style)[\s\S]*?</\1>", "", html)


def test_no_external_resources():
    html = INDEX.read_text()
    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<link[^>]+href=\"https?:", html)
    assert "https://" not in _markup_only(html)


def test_no_inline_event_handlers():
    assert not re.search(r"<[^>]+\son[a-z]+\s*=", _markup_only(INDEX.read_text()))


def test_token_never_reaches_persistent_storage():
    """The auth token is kept in memory only (dashboard/src/api/client.ts).
    `localStorage` is also used for an unrelated, non-secret per-viewer
    convenience -- which activity-bar items were dismissed
    (dashboard/src/project/activity.ts) -- so the check has to be that no
    persistent-storage call carries the token, not that the APIs are absent.

    This is a textual guard, not a true data-flow check: it scans the
    source text of each `localStorage`/`sessionStorage` call statement for
    "token"/"authorization", so it can still be fooled by, say, storing a
    token under a name with neither substring (`btoa(secret)`). It is
    still worth keeping because it catches the direct, common-refactor
    ways this could regress; it is not a substitute for not writing the
    bug in the first place."""
    html = INDEX.read_text()
    assert "document.cookie" not in html
    assert "indexedDB" not in html
    # Capture the whole statement -- up to the next `;`, not just the
    # first balanced `)` -- so a nested call as an earlier argument (e.g.
    # `localStorage.setItem(deriveKey(a, b), token)`) doesn't truncate the
    # match before the sensitive argument is reached.
    for call in re.findall(r"(?:localStorage|sessionStorage)\.[a-zA-Z]+\([^;]*;?", html):
        assert "token" not in call.lower()
        assert "authorization" not in call.lower()


def test_the_page_is_the_built_bundle():
    html = INDEX.read_text()
    assert '<script type="module"' in html
    assert "--accent: #D97757" in html


def _routes_from_ts() -> set[str]:
    text = ROUTES_TS.read_text()
    paths = set(re.findall(r'"(/[^"?]*)"', text))
    paths |= {re.sub(r"\$\{[^}]+\}", "0", p) for p in re.findall(r"`(/[^`?]*)`", text)}
    return paths


def test_every_route_in_routes_ts_is_served(tmp_path):
    from muvue.core.repo_init import init_repo

    init_repo(tmp_path)
    served = [r.path for r in create_app(tmp_path).routes]
    patterns = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$") for p in served]
    called = _routes_from_ts()
    assert len(called) >= 20, "found too few paths; the routes.ts pattern is stale"
    missing = sorted(c for c in called if not any(p.match(c) for p in patterns))
    assert missing == []
