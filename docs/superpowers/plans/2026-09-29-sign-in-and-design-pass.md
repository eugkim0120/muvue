# Sign-in Recovery and Design Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make signing in to the dashboard work end to end, including after the 8-hour idle expiry and over Tailscale on a phone, and redesign the project page against seven UI principles so the DAG is the first thing you see.

**Architecture:** The daemon learns to say *why* a token fails (`missing` / `invalid` / `expired`, in an `X-Muvue-Auth` header). A new local control socket lets `muvue link` get a fresh one-time link from the running daemon, reviving an expired session with a new token, so no restart is needed. A pasted token is turned into the same HttpOnly cookie the link sets, so a reload stays signed in. The dashboard replaces the always-visible token form with a one-line sign-in strip and a sheet that shows the exact reason for a refusal. The page layout moves the Next step, activity and cards into a right rail on wide screens and compacts the header on phones, so the diagram starts near the top. Colour tokens are fixed to pass WCAG AA, and a unit test holds them there.

**Tech Stack:** Python 3.12 / FastAPI / SQLite / stdlib `socket` + `threading` (backend, `uv run pytest`); Preact + @preact/signals + Vite + Vitest + TypeScript (dashboard, `dashboard/`); headless Chromium via `playwright-core` for the UI check script (dev-only, not a dependency).

**Spec:** This plan's **Diagnosis** section below is the spec. It records what was observed on 2026-09-29 against the live `voxscore` daemon and a seeded scratch repo, with evidence. Vocabulary for the canvas comes from `docs/superpowers/specs/2026-09-27-project-canvas-design.md`. The previous plan, `docs/superpowers/plans/2026-09-28-dashboard-clarity.md`, is already merged. Where this plan changes a string it fixed (agent-chip labels), this plan wins, and Task 8 records the decision.

## Diagnosis (the spec)

User reports, verbatim: "get the api token working end to end (it doesn't work)", and "use the UI principles in https://www.figma.com/resource-library/ui-design-principles/ to critically evaluate and redesign the page a bit more, with the same central focus on the DAG".

The Figma article names seven principles:
- **Hierarchy:** size and weight lead the eye to what matters.
- **Progressive disclosure:** show detail step by step, not all at once.
- **Consistency:** the same thing looks and works the same everywhere.
- **Contrast:** high contrast is reserved for what must command attention.
- **Accessibility:** sufficient colour contrast, inclusive design.
- **Proximity:** related things sit together.
- **Alignment:** clean lines on a grid.

### Part 1: sign-in

| # | Defect | Evidence | Fixed in |
|---|---|---|---|
| A1 | The token printed by `muvue serve` was rejected. `SessionManager` expires a session after 480 idle minutes (`src/muvue/core/daemon.py:37`), counted from process start when nobody ever signed in. voxscore's daemon started 2026-09-28 13:03, so by the time the token was handed over (2026-09-29 07:02) it had been dead for about 10 hours. | `curl -H "Authorization: Bearer <token>" http://100.83.140.92:8766/auth/check` returned `403 {"detail":"missing or invalid session token"}`; `ps -o lstart= -p 3038462` gave `Mon Sep 28 13:03:47 2026` | Tasks 1, 2 |
| A2 | Missing, wrong and expired tokens all return the same `403 "missing or invalid session token"`. Neither a user nor the page can tell an expired session from a typo. | `src/muvue/api/app.py:198-212` | Task 1 |
| A3 | Short of restarting `serve`, which also kills every link already handed out, nothing can recover an expired session. The one-time link has the same problem: `consume_nonce` fails once the session has expired. | `daemon.py:180-186` | Task 2 |
| A4 | Pasting a token does nothing visible when it is refused. `TokenBanner.use()` calls `checkAuth()`, which returns `false` on 403, and the page just stays read-only. There is no message. | `dashboard/src/shell/TokenBanner.tsx:9-13` | Task 3 |
| A5 | Even a pasted token that works lives only in a JS variable, so a reload (common on a phone) signs you out. | `dashboard/src/api/client.ts:11` | Tasks 1, 3 |
| A6 | `onForbidden` fires on any POST 403, including the JSON-content-type and Origin rejections, which are not about sign-in. It also never fires for a GET. | `client.ts:23` | Task 3 |
| A7 | `secrets.compare_digest(token, self.token)` raises `TypeError` on a non-ASCII string. A pasted token containing a smart quote or an emoji causes a 500 instead of a refusal. | `daemon.py:193`; Python docs for `hmac.compare_digest` | Task 1 |

### Part 2: design

Screenshots from `dashboard/scripts/ui-check.mjs` at 390×844 and 1280×800, plus the user's own phone screenshot of voxscore in dark mode:

| # | Principle | Defect | Evidence | Fixed in |
|---|---|---|---|---|
| U1 | Hierarchy | On a phone (844px screen) the diagram starts at 672px with no tasks and 667px with tasks, below the first screen. On desktop it starts at 387px of 800. Six blocks sit above it: "Nothing waiting on you", the H1, the Run row, the fake-agent callout, the Next card and the status line. | ui-check `01-phone-no-tasks.png`, `03-phone-after-plan.png`, `04-desktop-after-resize.png` | Task 6 |
| U2 | Hierarchy / consistency | The project goal appears twice on phone (top bar and H1) and three times on desktop (sidebar header, sidebar nav item, H1). The nav item wraps to four lines. | same screenshots | Task 6 |
| U3 | Progressive disclosure | The read-only token form is a full block above everything on the user's phone. The fake-agent notice is a permanent 4-line callout. | user screenshot; `01-phone-no-tasks.png` | Tasks 3, 6 |
| U4 | Consistency / affordance | The task-box agent chip borrows `.chip`, the 44px bordered filter-chip style, so a status line looks like a button. The same long text ("fake · starts after the task list is approved") repeats on every box and wraps to two lines. | `03-phone-after-plan.png`; `canvas/AgentChip.tsx`, `styles/base.css` `.chip` | Task 5 |
| U5 | Consistency | The approved spec shows a blue "Ready" pill, the same blue as the phase pill "Planning" but with a different meaning. The spec is not "ready", it is approved. | `03-phone-after-plan.png` | Task 5 |
| U6 | Contrast / accessibility | The disabled "▶ Run tasks" is a faded version of the primary button (white on `#EAB8A6`, 1.72:1), so it looks like the main call to action next to the real one. | computed from `tokens.css`; `.btn:disabled { opacity: .5 }` | Task 4 |
| U7 | Accessibility | Filled buttons are white on `#D97757` (3.12:1) and accent-coloured links are 2.96:1. Status pills range from 2.04:1 (in progress) to 3.34:1 in light mode, and failed is 1.83:1 in dark mode. All are below WCAG AA's 4.5:1 for text. | computed from `tokens.css` | Task 4 |
| U8 | Alignment / proximity | Boxes have fixed heights sized for the two-line chip and the old placeholder buttons, so every task box has about 30px of dead space and the empty-state placeholder about 80px. | `01-phone-no-tasks.png`, `03-phone-after-plan.png`; `flowLayout.ts:9` | Task 5 |
| U9 | Proximity | "Nothing waiting on you" floats alone at the top right on desktop, far from anything it relates to. The "Added 3 tasks" toast sits over the diagram and covers the "audio frames" arrow label. | `04-desktop-after-resize.png` | Task 6 |
| U10 | Hierarchy | On desktop the 240px-wide diagram sits in a 1320px-wide column. The Next card spans the full width above it, so the page reads as a form with a diagram at the bottom. | `04-desktop-after-resize.png` | Task 6 |
| U11 | Flaky check | ui-check's "activity bar visible within 300ms" fails when the fake agent finishes in under 300ms, because the bar has already gone and the tasks are showing. | ui-check run 2026-09-29: 14/15 | Task 7 |

## Global Constraints

- No new runtime dependencies. Nothing is added to `pyproject.toml` `dependencies` or `dashboard/package.json` `dependencies`. The UI check loads `playwright-core` from a path in an environment variable and does not add it to `devDependencies`.
- The session token is never written to disk (v4 section 8a control 5; `tests/test_no_token_touches_disk.py`). The control socket is the only new file, and it holds no secret.
- Idle expiry stays at 8 hours and still resets only on a successful authenticated request (control 6). `muvue link` renews the session. Nothing else keeps it alive: no heartbeat, and no background polling counts as activity.
- Status codes: a missing or invalid token is `403`, as before (doctor's probes rely on it). An expired session is `401`. Every auth refusal carries `X-Muvue-Auth: missing|invalid|expired`.
- Every dashboard task ends with these, run from `dashboard/`:
  - `npm run typecheck` with 0 errors;
  - `npm test` with everything passing;
  - `npm run build`, which copies the bundle to `src/muvue/api/static/index.html`.

  Commit the rebuilt `src/muvue/api/static/index.html` in the same commit.
- Every backend task ends with `uv run pytest -q` fully green.
- Every tappable control keeps a touch target of at least 44 px (`dashboard/test/touch-targets.test.ts`).
- Text contrast is at least 4.5:1 in light and dark mode for every pair listed in `dashboard/test/contrast.test.ts` (Task 4).
- At most one filled (primary) button is enabled on the project page at a time.
- Fail loudly: every refusal shows its reason on the page. No silent `catch {}`.
- User-facing copy: use the exact strings in this plan's code blocks.
- The built page has no inline HTML event-handler attributes and no external resources (`tests/test_dashboard_static.py`).
- Every API path the dashboard calls is spelled only in `dashboard/src/api/routes.ts`.
- Work happens in the worktree `.worktrees/sign-in-design` on branch `feat/sign-in-design`, created from `main`.
- Commit messages end with the attribution lines the session provides.

---

## File map

Backend:
- `src/muvue/core/daemon.py`: `SessionManager` gains `check()`, `relink()` and a lock. `consume_nonce()` returns a status string. Compare digests as bytes. (Tasks 1, 2)
- `src/muvue/api/app.py`: `X-Muvue-Auth` header, 401 on expiry, `_set_session_cookie` helper, new `POST /auth/session`. (Task 1)
- `src/muvue/core/control.py` (new): the control socket server and client. (Task 2)
- `src/muvue/cli/main.py`: `serve` starts and stops the control socket and prints the `muvue link` hint; new `muvue link` command. (Task 2)

Dashboard (`dashboard/src/`):
- `api/client.ts`: `AuthProblem`, `ApiError.auth`, `authProblemOf`, Content-Type only when there is a body or a non-GET method, and `forbidden(problem)` driven by the header. (Task 3)
- `api/auth.ts`: `AuthResult`, `checkAuth`, `exchangeFragmentNonce`, `signInWithToken`, `applyAuthResult`. (Task 3)
- `api/routes.ts`: `authSession`. (Task 3)
- `state.ts`: `signedOutReason`, `signInOpen`, `setSignedIn`, `setSignedOut`; `setAuthed` is deleted. (Task 3)
- `shell/AuthStrip.tsx`, `shell/SignInSheet.tsx` (new); `shell/TokenBanner.tsx` (deleted). (Task 3)
- `app.tsx`, `main.tsx`, `shell/Sidebar.tsx`, `project/NextStepBar.tsx`: sign-in wiring. (Task 3)
- `styles/tokens.css`, `styles/base.css`, `ui/ui.css`, `shell/shell.css`, `cards/cards.css`: contrast-safe tokens. (Task 4)
- `canvas/AgentChip.tsx`, `canvas/SpecRoot.tsx`, `canvas/DagPlaceholder.tsx`, `canvas/Canvas.tsx`, `canvas/flowLayout.ts`, `canvas/canvas.css`: compact boxes. (Task 5)
- `project/ProjectPage.tsx`, `project/FakeAgentNotice.tsx` (new), `cards/CardRail.tsx`, `shell/Sidebar.tsx`, `canvas/canvas.css`, `ui/ui.css`: page layout. (Task 6)
- `dashboard/scripts/ui-check.mjs`: box-fit check (Task 5); layout and sign-in checks (Task 7).

Docs: `CHANGELOG.md`, `docs/decisions.md` (#173, #174), `docs/threat-model.md`, `docs/protocol.md`. (Task 8)

## Pre-flight conflict table (for the executor)

| Tasks | Shared surface | Note |
|---|---|---|
| 1 → 3 | `X-Muvue-Auth` values, `POST /auth/session`, 401 on expiry | Task 3's client reads exactly `missing`/`invalid`/`expired` from the header. |
| 1 → 2 | `SessionManager.relink()` / `consume_nonce()` return values | Task 2 calls `relink()`; Task 1 defines `consume_nonce` as returning `"ok"|"invalid"|"expired"`. |
| 3 → 6 | `NextStepBar` read-only branch; `Sidebar` foot | Task 3 edits both; Task 6 edits `Sidebar` again (header) and must keep Task 3's foot text. |
| 4 → 5, 6 | `.pill` colours, `.btn:disabled` | Tasks 5 and 6 use the classes; they don't redefine them. |
| 5 → 7 | `dagChecks` in ui-check | Task 5 adds the box-fit check; Task 7 adds the rest. Keep both. |
| 5 → 6 | `DagPlaceholder` props | Task 5 drops its unused props and updates `Canvas.tsx`; Task 6 does not touch them. |

---

### Task 1: The daemon says why a token fails, and a pasted token can become a cookie

**Files:**
- Modify: `src/muvue/core/daemon.py` (the `SessionManager` class, ~lines 157-200)
- Modify: `src/muvue/api/app.py` (`_require_session` ~198-212; `exchange_nonce` ~267-282; add `POST /auth/session` next to it)
- Test: `tests/test_daemon.py` (append), `tests/test_auth_states.py` (new)

**Interfaces:**
- Produces:
  - `daemon.AuthStatus = Literal["ok", "missing", "invalid", "expired"]`.
  - `SessionManager.check(token: str | None, *, now: datetime | None = None) -> AuthStatus`.
  - `SessionManager.verify_and_touch(...) -> bool`, unchanged: it returns `check(...) == "ok"`.
  - `SessionManager.consume_nonce(nonce, *, now=None) -> Literal["ok", "invalid", "expired"]`. **Changed from `bool`.** The string `"invalid"` is truthy, so every caller must compare with `== "ok"`.
  - `SessionManager.relink(*, now=None) -> tuple[str, bool]`, returning `(nonce, renewed)`.
  - HTTP:
    - refusals return `403` or `401` with the header `X-Muvue-Auth: <status>`;
    - new `POST /auth/session` (needs the token) sets the `muvue_session` cookie.

- [ ] **Step 1: Write the failing SessionManager tests**

Append to `tests/test_daemon.py`:

```python
# -- session auth states (sign-in plan, Task 1) --------------------------------


def test_check_distinguishes_missing_invalid_expired_and_ok():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    assert session.check(None, now=now) == "missing"
    assert session.check("", now=now) == "missing"
    assert session.check("not-the-token", now=now) == "invalid"
    assert session.check(session.token, now=now + timedelta(hours=1)) == "ok"
    assert session.check(session.token, now=now + timedelta(hours=1, minutes=1) + timedelta(hours=8)) == "expired"


def test_check_rejects_non_ascii_token_instead_of_raising():
    session = daemon.SessionManager()
    assert session.check("tok\u201cen") == "invalid"
    assert session.check("\U0001F600") == "invalid"


def test_expired_check_does_not_reset_the_idle_clock():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    later = now + timedelta(hours=9)
    assert session.check(session.token, now=later) == "expired"
    assert session.check(session.token, now=later + timedelta(minutes=1)) == "expired"


def test_consume_nonce_reports_ok_invalid_and_expired():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    nonce = session.mint_nonce()
    assert session.consume_nonce(nonce, now=now) == "ok"
    assert session.consume_nonce(nonce, now=now) == "invalid"  # single use
    assert session.consume_nonce("never-minted", now=now) == "invalid"
    assert session.consume_nonce(None, now=now) == "invalid"
    stale = session.mint_nonce()
    assert session.consume_nonce(stale, now=now + timedelta(hours=9)) == "expired"
    assert session.consume_nonce(stale, now=now + timedelta(hours=9)) == "invalid"  # spent either way


def test_relink_keeps_a_live_token_and_mints_a_working_nonce():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    token = session.token
    nonce, renewed = session.relink(now=now + timedelta(hours=2))
    assert renewed is False
    assert session.token == token
    assert session.consume_nonce(nonce, now=now + timedelta(hours=2)) == "ok"


def test_relink_after_expiry_renews_with_a_new_token_and_kills_the_old_one():
    now = datetime.now(timezone.utc)
    session = daemon.SessionManager(now=now)
    old_token = session.token
    old_nonce = session.mint_nonce()
    later = now + timedelta(hours=9)
    nonce, renewed = session.relink(now=later)
    assert renewed is True
    assert session.token != old_token
    assert session.check(old_token, now=later) == "invalid"
    assert session.consume_nonce(old_nonce, now=later) == "invalid"
    assert session.consume_nonce(nonce, now=later) == "ok"
    assert session.check(session.token, now=later) == "ok"
```

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_daemon.py -q -k "check or consume_nonce or relink"`
Expected: FAIL with `AttributeError: 'SessionManager' object has no attribute 'check'`.

- [ ] **Step 3: Implement the SessionManager changes**

In `src/muvue/core/daemon.py`, add `import threading` to the imports, and add `from typing import Literal` if it is not already imported. Next to `DEFAULT_IDLE_TIMEOUT_MINUTES`, add:

```python
AuthStatus = Literal["ok", "missing", "invalid", "expired"]
```

Replace the body of `class SessionManager` from `__init__` through the end of `verify_and_touch` with the following. Keep the class docstring.

```python
    def __init__(self, *, idle_timeout_minutes: int = DEFAULT_IDLE_TIMEOUT_MINUTES,
                 now: datetime | None = None) -> None:
        self.token: str = secrets.token_urlsafe(32)  # 256 bits
        self.idle_timeout_minutes = idle_timeout_minutes
        self.last_activity: datetime = now or _now()
        self._nonces: set[str] = set()
        # uvicorn's worker thread and the control-socket thread (`muvue link`)
        # both touch the token and the idle clock.
        self._lock = threading.Lock()

    def _idle(self, current: datetime) -> bool:
        return current - self.last_activity > timedelta(minutes=self.idle_timeout_minutes)

    def mint_nonce(self) -> str:
        """A single-use value for a dashboard link's `#n=` fragment
        (control 5). It is not the token, so a link that leaks through
        browser history or a screenshot is dead once the page has
        exchanged it."""
        nonce = secrets.token_urlsafe(32)
        with self._lock:
            self._nonces.add(nonce)
        return nonce

    def check(self, token: str | None, *, now: datetime | None = None) -> AuthStatus:
        """Why a token does or doesn't work: `missing` (none sent),
        `invalid` (not this process's token: wrong, or from before a
        restart), `expired` (this process's token, idle past the
        timeout). Only `ok` resets the idle clock. Compared as UTF-8
        bytes: `compare_digest` raises TypeError on non-ASCII `str`."""
        if not token:
            return "missing"
        with self._lock:
            if not secrets.compare_digest(token.encode(), self.token.encode()):
                return "invalid"
            current = now or _now()
            if self._idle(current):
                return "expired"
            self.last_activity = current
            return "ok"

    def verify_and_touch(self, token: str | None, *, now: datetime | None = None) -> bool:
        """Constant-time compare against the live token; on success,
        resets the idle clock. Never logged, never written anywhere."""
        return self.check(token, now=now) == "ok"

    def consume_nonce(self, nonce: str | None, *, now: datetime | None = None) -> Literal["ok", "invalid", "expired"]:
        """`ok` once per minted nonce while the session is live;
        `invalid` for an unknown or already-used nonce; `expired` when
        the nonce is real but the session idled out. A real nonce is
        spent either way."""
        if not nonce:
            return "invalid"
        with self._lock:
            if nonce not in self._nonces:
                return "invalid"
            self._nonces.discard(nonce)
        status = self.check(self.token, now=now)
        return "ok" if status == "ok" else "expired"

    def relink(self, *, now: datetime | None = None) -> tuple[str, bool]:
        """For `muvue link`: a fresh single-use nonce, reviving the
        session if it idled out. An expired session gets a brand-new
        token, so a token that leaked before the expiry stays dead; a
        live one keeps its token, so other signed-in tabs keep working.
        Asking is proof someone is at the server, so the idle clock
        resets. Returns (nonce, renewed)."""
        current = now or _now()
        with self._lock:
            renewed = self._idle(current)
            if renewed:
                self.token = secrets.token_urlsafe(32)
                self._nonces.clear()
            self.last_activity = current
            nonce = secrets.token_urlsafe(32)
            self._nonces.add(nonce)
        return nonce, renewed
```

- [ ] **Step 4: Run the daemon tests**

Run: `uv run pytest tests/test_daemon.py -q`
Expected: all pass, including the pre-existing `test_session_*` tests.

- [ ] **Step 5: Write the failing HTTP tests**

Create `tests/test_auth_states.py`:

```python
"""Sign-in plan Task 1: every auth refusal says why (X-Muvue-Auth), an
idle-expired session is 401 not 403, and a pasted token can be turned
into the HttpOnly session cookie."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muvue.api import create_app
from muvue.core.config import ChecksConfig, MuvueConfig
from muvue.core.daemon import SessionManager
from muvue.core.repo_init import init_repo

BASE_URL = "http://127.0.0.1"


@pytest.fixture
def session() -> SessionManager:
    return SessionManager()


@pytest.fixture
def client(tmp_path: Path, session: SessionManager) -> TestClient:
    init_repo(tmp_path)
    app = create_app(tmp_path, config=MuvueConfig(checks=ChecksConfig(test="true", lint="true")), session=session)
    return TestClient(app, base_url=BASE_URL)


def _expire(session: SessionManager) -> None:
    session.last_activity = datetime.now(timezone.utc) - timedelta(hours=9)


def test_no_token_is_403_missing(client):
    r = client.get("/auth/check")
    assert r.status_code == 403
    assert r.headers["x-muvue-auth"] == "missing"
    assert r.json()["detail"] == "missing session token"


def test_wrong_token_is_403_invalid_and_names_muvue_link(client):
    r = client.get("/auth/check", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 403
    assert r.headers["x-muvue-auth"] == "invalid"
    assert "muvue link" in r.json()["detail"]


def test_non_ascii_token_is_403_invalid_not_500(client):
    r = client.get("/auth/check", headers={"Authorization": "Bearer tok\u00e9n"})
    assert r.status_code == 403
    assert r.headers["x-muvue-auth"] == "invalid"


def test_expired_session_is_401_expired_and_says_how_to_recover(client, session):
    _expire(session)
    r = client.get("/auth/check", headers={"Authorization": f"Bearer {session.token}"})
    assert r.status_code == 401
    assert r.headers["x-muvue-auth"] == "expired"
    assert r.json()["detail"] == "session expired after 8 hours without activity; run `muvue link` on the server for a fresh link"


def test_expired_session_refuses_mutations_with_401(client, session):
    _expire(session)
    r = client.post("/projects", json={"goal": "x"}, headers={"Authorization": f"Bearer {session.token}"})
    assert r.status_code == 401
    assert r.headers["x-muvue-auth"] == "expired"


def test_exchange_with_a_real_nonce_after_expiry_is_401_expired(client, session):
    nonce = session.mint_nonce()
    _expire(session)
    r = client.post("/auth/exchange", json={"nonce": nonce})
    assert r.status_code == 401
    assert r.headers["x-muvue-auth"] == "expired"
    assert "muvue_session" not in client.cookies


def test_exchange_with_an_unknown_nonce_is_still_403(client):
    r = client.post("/auth/exchange", json={"nonce": "never-minted"})
    assert r.status_code == 403
    assert r.json()["detail"] == "invalid or already used nonce"
    assert r.headers["x-muvue-auth"] == "invalid"


def test_auth_session_turns_a_bearer_token_into_the_cookie(client, session):
    r = client.post("/auth/session", json={}, headers={"Authorization": f"Bearer {session.token}"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert client.cookies.get("muvue_session") == session.token
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=strict" in r.headers["set-cookie"].lower()
    assert client.get("/auth/check").status_code == 200  # the cookie alone now works


def test_auth_session_without_a_token_is_403_and_sets_no_cookie(client):
    r = client.post("/auth/session", json={})
    assert r.status_code == 403
    assert "muvue_session" not in client.cookies
```

- [ ] **Step 6: Run them and watch them fail**

Run: `uv run pytest tests/test_auth_states.py -q`
Expected: FAIL, because the `x-muvue-auth` header is missing and `/auth/session` returns 404 or 405.

- [ ] **Step 7: Implement the HTTP changes**

In `src/muvue/api/app.py`, next to `SESSION_COOKIE_NAME = "muvue_session"`, add:

```python
AUTH_HEADER = "X-Muvue-Auth"
```

Inside `create_app`, replace `_require_session` with:

```python
    def _auth_error(status: str) -> HTTPException:
        """403 for a missing or wrong token (doctor's control-4 probes
        rely on it), 401 for this process's token after idle expiry, and
        always `X-Muvue-Auth` so the dashboard can say which."""
        hours = session.idle_timeout_minutes // 60
        detail = {
            "missing": "missing session token",
            "invalid": "invalid session token: tokens change every time `muvue serve` restarts; run `muvue link` for the current one",
            "expired": f"session expired after {hours} hours without activity; run `muvue link` on the server for a fresh link",
        }[status]
        return HTTPException(status_code=401 if status == "expired" else 403, detail=detail, headers={AUTH_HEADER: status})

    def _require_session(request: Request) -> None:
        """Control 4 (token half): a valid token via `Authorization:
        Bearer <token>` (non-browser clients: CLI, VS Code extension) or
        the `muvue_session` HttpOnly cookie (the dashboard, after the
        one-time fragment exchange -- control 5). Never a query string:
        no endpoint here ever reads one, and a query-string `token=`
        param is simply ignored, not accepted."""
        authorization = request.headers.get("authorization")
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        if token is None:
            token = request.cookies.get(SESSION_COOKIE_NAME)
        status = session.check(token)
        if status != "ok":
            raise _auth_error(status)

    def _set_session_cookie(resp: JSONResponse) -> None:
        resp.set_cookie(
            SESSION_COOKIE_NAME,
            session.token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
```

Replace `exchange_nonce`'s body and add the new route right after it:

```python
    @app.post("/auth/exchange")
    def exchange_nonce(
        nonce: str = Body(..., embed=True), header: bool = Body(default=False, embed=True),
    ) -> JSONResponse:
        status = session.consume_nonce(nonce)
        if status == "expired":
            raise _auth_error("expired")
        if status != "ok":
            raise HTTPException(status_code=403, detail="invalid or already used nonce", headers={AUTH_HEADER: "invalid"})
        resp = JSONResponse({"ok": True, "token": session.token} if header else {"ok": True})
        _set_session_cookie(resp)
        return resp

    @app.post("/auth/session")
    def cookie_from_token(request: Request) -> JSONResponse:
        """A pasted api token (sent as the Authorization header) becomes
        the same HttpOnly cookie the nonce exchange sets, so a phone that
        signed in by pasting stays signed in across reloads."""
        _require_session(request)
        resp = JSONResponse({"ok": True})
        _set_session_cookie(resp)
        return resp
```

Then run `grep -rn "consume_nonce\|verify_and_touch" src/` and confirm that no caller still treats `consume_nonce` as a bool. The only one on 2026-09-29 was `exchange_nonce`.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass. This includes `tests/test_doctor_security_probes.py` and `tests/test_w9_security.py`, which still expect `403` for a missing token.

- [ ] **Step 9: Commit**

```bash
git add src/muvue/core/daemon.py src/muvue/api/app.py tests/test_daemon.py tests/test_auth_states.py
git commit -m "Say why a session token fails: X-Muvue-Auth, 401 on idle expiry, cookie from a pasted token"
```

---

### Task 2: `muvue link` gets a fresh link from the running daemon

**Files:**
- Create: `src/muvue/core/control.py`
- Modify: `src/muvue/cli/main.py`: the `serve` function (~lines 316-418) and a new `link` command placed right after `serve`
- Test: `tests/test_control_socket.py` (new)

**Interfaces:**
- Consumes: `SessionManager.relink()` and `consume_nonce()` from Task 1; `daemon.port_file_path(repo_root)`.
- Produces:
  - `control.socket_path(repo_root: Path) -> Path`, which is `~/.muvue/daemon/<repo-hash>.sock`.
  - `control.ControlServer(path: Path, session: SessionManager, *, base_url: str)` with `.start()` and `.stop()`.
  - `control.request_link(path: Path, *, timeout: float = 5.0) -> dict` returns `{"url": str, "token": str, "renewed": bool}` or raises `control.LinkError`.
  - CLI `muvue link [PATH]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_control_socket.py`:

```python
"""Sign-in plan Task 2: a same-user Unix socket lets `muvue link` get a
fresh one-time dashboard link from a running daemon, reviving an expired
session with a new token instead of requiring a restart."""

from __future__ import annotations

import os
import re
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from muvue.core import control
from muvue.core.daemon import SessionManager
from muvue.core.repo_init import init_repo

URL_RE = re.compile(r"^dashboard \(one-time link, works once\): (\S+)$", re.MULTILINE)
TOKEN_RE = re.compile(r"^api token: (\S+)$", re.MULTILINE)


@pytest.fixture
def server(tmp_path: Path):
    session = SessionManager()
    path = tmp_path / "d" / "x.sock"
    srv = control.ControlServer(path, session, base_url="http://127.0.0.1:9")
    srv.start()
    yield srv, session, path
    srv.stop()


def test_link_on_a_live_session_returns_a_working_nonce_and_the_same_token(server):
    _srv, session, path = server
    token = session.token
    reply = control.request_link(path)
    assert reply["renewed"] is False
    assert reply["token"] == token
    assert reply["url"].startswith("http://127.0.0.1:9/#n=")
    assert session.consume_nonce(reply["url"].split("#n=", 1)[1]) == "ok"


def test_link_on_an_expired_session_renews_it(server):
    _srv, session, path = server
    old = session.token
    session.last_activity = datetime.now(timezone.utc) - timedelta(hours=9)
    reply = control.request_link(path)
    assert reply["renewed"] is True
    assert reply["token"] == session.token != old
    assert session.check(old) == "invalid"
    assert session.consume_nonce(reply["url"].split("#n=", 1)[1]) == "ok"


def test_socket_is_owner_only(server):
    _srv, _session, path = server
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_unknown_request_gets_an_error_reply(server):
    _srv, _session, path = server
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(str(path))
        s.sendall(b"token please\n")
        line = s.makefile("r").readline()
    assert "unknown request" in line


def test_stop_removes_the_socket(tmp_path: Path):
    path = tmp_path / "x.sock"
    srv = control.ControlServer(path, SessionManager(), base_url="http://127.0.0.1:9")
    srv.start()
    srv.stop()
    assert not path.exists()
    with pytest.raises(control.LinkError):
        control.request_link(path)


def test_start_replaces_a_stale_socket_file(tmp_path: Path):
    path = tmp_path / "x.sock"
    path.write_text("left over from a crashed daemon")
    srv = control.ControlServer(path, SessionManager(), base_url="http://127.0.0.1:9")
    srv.start()
    try:
        assert control.request_link(path)["url"].startswith("http://127.0.0.1:9/#n=")
    finally:
        srv.stop()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_muvue_link_with_no_daemon_fails_loudly(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    r = subprocess.run([sys.executable, "-m", "muvue", "link", str(repo)], capture_output=True, text=True, env=env)
    assert r.returncode == 1
    assert "no `muvue serve` is running for" in r.stderr


def test_serve_then_link_gives_a_link_that_signs_in(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    env = {**os.environ, "HOME": str(tmp_path / "home")}
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "muvue", "serve", str(repo), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
    )
    try:
        lines: list[str] = []
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            lines.append(line)
            if "listening on" in line:
                break
        out = "".join(lines)
        assert "listening on" in out, out
        assert f"run `muvue link {repo}`" in out

        r = subprocess.run([sys.executable, "-m", "muvue", "link", str(repo)], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        url = URL_RE.search(r.stdout).group(1)
        assert TOKEN_RE.search(r.stdout).group(1) == TOKEN_RE.search(out).group(1)
        nonce = url.split("#n=", 1)[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as c:
            ex = c.post("/auth/exchange", json={"nonce": nonce})
            assert ex.status_code == 200, ex.text
            assert c.get("/auth/check").status_code == 200
    finally:
        proc.terminate()
        proc.wait(timeout=10)
```

Socket removal on shutdown is covered by `test_stop_removes_the_socket`. That check is not repeated here, because the test process's `Path.home()` is not the subprocess's `HOME`.

- [ ] **Step 2: Run them and watch them fail**

Run: `uv run pytest tests/test_control_socket.py -q`
Expected: FAIL with `ImportError: cannot import name 'control' from 'muvue.core'`.

- [ ] **Step 3: Implement `src/muvue/core/control.py`**

```python
"""Local control socket for a running `muvue serve`.

`muvue link` asks it for a fresh one-time dashboard link, so an expired
or lost session can be recovered without restarting the daemon (which
would also kill every link already handed out). It is a Unix socket next
to the port file: mode 0600 in a 0700 directory, and on Linux the peer's
uid must match ours. Only the user who runs the daemon can ask. The
socket holds no secret; the token stays in the daemon's memory and is
only sent back over this same-user channel (decision #173)."""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import threading
from pathlib import Path

from .daemon import SessionManager, port_file_path


class LinkError(RuntimeError):
    pass


def socket_path(repo_root: Path) -> Path:
    return port_file_path(repo_root).with_suffix(".sock")


def _peer_is_self(conn: socket.socket) -> bool:
    """Linux reports the connecting process's credentials; elsewhere the
    socket's 0600 mode is the whole check."""
    if not hasattr(socket, "SO_PEERCRED"):
        return True
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid == os.getuid()


class ControlServer:
    def __init__(self, path: Path, session: SessionManager, *, base_url: str) -> None:
        self.path = path
        self.session = session
        self.base_url = base_url.rstrip("/")
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.unlink(missing_ok=True)  # a crashed daemon's leftover
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)  # created 0600, never briefly wider
        try:
            sock.bind(str(self.path))
        finally:
            os.umask(old_umask)
        sock.listen(4)
        sock.settimeout(0.5)  # lets the loop notice stop() without closing under accept()
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, name="muvue-control", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        self.path.unlink(missing_ok=True)

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                print(f"muvue control socket stopped: {exc}", file=sys.stderr)
                return
            with conn:
                try:
                    self._handle(conn)
                except OSError as exc:
                    print(f"muvue control socket: a client request failed: {exc}", file=sys.stderr)

    def _reply(self, conn: socket.socket, payload: dict) -> None:
        conn.sendall(json.dumps(payload).encode() + b"\n")

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(5)
        if not _peer_is_self(conn):
            self._reply(conn, {"error": "refused: the control socket only answers the user running the daemon"})
            return
        request = conn.makefile("r").readline().strip()
        if request != "link":
            self._reply(conn, {"error": f"unknown request {request!r}"})
            return
        nonce, renewed = self.session.relink()
        self._reply(conn, {"url": f"{self.base_url}/#n={nonce}", "token": self.session.token, "renewed": renewed})


def request_link(path: Path, *, timeout: float = 5.0) -> dict:
    """Ask the daemon behind `path` for a fresh link. Raises LinkError
    with a message fit to show a user."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise LinkError(f"nothing is answering at {path} ({exc.strerror})") from exc
        s.sendall(b"link\n")
        line = s.makefile("r").readline()
    if not line:
        raise LinkError("the daemon closed the connection without replying")
    reply = json.loads(line)
    if "error" in reply:
        raise LinkError(str(reply["error"]))
    if not (isinstance(reply.get("url"), str) and isinstance(reply.get("token"), str) and isinstance(reply.get("renewed"), bool)):
        raise LinkError(f"unexpected reply from the daemon: {line.strip()[:200]}")
    return reply
```

- [ ] **Step 4: Wire it into `serve` and add `muvue link`**

In `src/muvue/cli/main.py`, inside `serve`:

1. Next to `from muvue.core import daemon as daemon_mod`, add `from muvue.core import control as control_mod`.
2. Right after the three `typer.echo` lines that print the dashboard link and the api token, add:

```python
    typer.echo(f"lost the link later? run `muvue link {repo_root}` on this machine for a fresh one")
```

3. Replace the block from `daemon_mod.write_port_file(...)` to the end of the function with:

```python
    daemon_mod.write_port_file(repo_root, port=port, pid=os.getpid())
    control = control_mod.ControlServer(
        control_mod.socket_path(repo_root), session, base_url=f"http://{host}:{port}",
    )
    control.start()
    try:
        typer.echo(f"muvue daemon listening on http://{host}:{port}")
        uvicorn.run(app_instance, fd=sock.fileno(), log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        control.stop()
        daemon_mod.remove_port_file(repo_root, pid=os.getpid())
```

Directly after the `serve` function, add:

```python
@app.command(help="Print a fresh one-time dashboard link for the running `muvue serve`.")
def link(path: Path = typer.Argument(Path("."), help="Repo root the daemon serves")) -> None:
    """Asks the running daemon, over its same-user control socket, for a
    new single-use dashboard link and the current api token. If the
    session idled out (8h), the daemon first starts a new one with a new
    token (decision #173)."""
    import json as json_mod

    from muvue.core import control as control_mod
    from muvue.core import daemon as daemon_mod

    repo_root = _find_repo_root(path)
    sock = control_mod.socket_path(repo_root)
    if not sock.exists():
        port_file = daemon_mod.port_file_path(repo_root)
        if port_file.exists():
            pid = json_mod.loads(port_file.read_text()).get("pid")
            typer.echo(
                f"muvue link: `muvue serve` (pid {pid}) is running for {repo_root} but has no control "
                "socket: it was started by an older muvue. Restart it once, then `muvue link` works.",
                err=True,
            )
        else:
            typer.echo(f"muvue link: no `muvue serve` is running for {repo_root}; start one with `muvue serve`", err=True)
        raise typer.Exit(1)
    try:
        reply = control_mod.request_link(sock)
    except control_mod.LinkError as exc:
        typer.echo(f"muvue link: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"dashboard (one-time link, works once): {reply['url']}")
    typer.echo(f"api token: {reply['token']}")
    if reply["renewed"]:
        typer.echo("(the old session had expired: this is a new token; earlier links, cookies and tokens no longer work)")
```

Check that `_find_repo_root` accepts a repo whose `.muvue/` exists; `init_repo` in the tests creates it.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_control_socket.py tests/test_no_token_touches_disk.py tests/test_serve_integration.py -q`
Expected: all pass.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass. If `tests/test_cli_version_help.py` or `tests/test_cli_top_level.py` lists every top-level command, add `link` to its expected list. That is the only allowed change to those tests.

- [ ] **Step 7: Commit**

```bash
git add src/muvue/core/control.py src/muvue/cli/main.py tests/test_control_socket.py
git commit -m "Add muvue link: a same-user control socket hands out fresh dashboard links, reviving expired sessions"
```

(Add the CLI-list test file if Step 6 touched it.)

---

### Task 3: The dashboard signs in with a clear reason for every refusal

**Files:**
- Modify: `dashboard/src/api/client.ts`, `dashboard/src/api/auth.ts`, `dashboard/src/api/routes.ts`, `dashboard/src/state.ts`, `dashboard/src/main.tsx`, `dashboard/src/app.tsx`, `dashboard/src/shell/Sidebar.tsx`, `dashboard/src/project/NextStepBar.tsx`, `dashboard/src/styles/base.css`, `dashboard/src/shell/shell.css`
- Create: `dashboard/src/shell/AuthStrip.tsx`, `dashboard/src/shell/SignInSheet.tsx`
- Delete: `dashboard/src/shell/TokenBanner.tsx`
- Test: `dashboard/test/client.test.ts` (modify), `dashboard/test/auth.test.ts` (new), `dashboard/test/signIn.test.tsx` (new), `dashboard/test/shell.test.tsx` (modify), `dashboard/test/NextStepBar.test.tsx` (modify), `dashboard/test/ProjectPage.test.tsx` (modify one assertion)

**Interfaces:**
- Consumes: from Task 1, the header `X-Muvue-Auth: missing|invalid|expired`, 401 on expiry, and `POST /auth/session`.
- Produces (used by Task 6 and Task 7):
  - `state.signedOutReason: Signal<"read_only" | "stale" | "expired">`;
  - `state.signInOpen: Signal<boolean>`;
  - `state.setSignedIn()`, `state.setSignedOut(reason)`;
  - DOM hooks `[data-auth-strip="read_only"|"stale"|"expired"]`, `input[aria-label="api token"]` and `[data-sign-in-error]`.

- [ ] **Step 1: Write the failing client and auth tests**

In `dashboard/test/client.test.ts`:
- Replace `mockFetch` with a version that also takes response headers.
- Replace the test named "detail is the error message and 403 on POST calls the forbidden hook".
- Keep every other test.

```ts
function mockFetch(status: number, body: unknown, json = true, extra: Record<string, string> = {}) {
  const fn = vi.fn(async () => ({
    ok: status < 400,
    status,
    statusText: "status " + status,
    headers: new Headers({ "content-type": json ? "application/json" : "text/plain", ...extra }),
    json: async () => body,
    text: async () => String(body),
  }));
  vi.stubGlobal("fetch", fn);
  return fn;
}

test("detail is the error message and an auth refusal calls the forbidden hook with its reason", async () => {
  mockFetch(401, { detail: "session expired" }, true, { "x-muvue-auth": "expired" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(post("/x")).rejects.toMatchObject({ message: "session expired", status: 401, auth: "expired" });
  expect(hook).toHaveBeenCalledWith("expired");
});

test("a 403 that is not about sign-in (no X-Muvue-Auth) does not call the forbidden hook", async () => {
  mockFetch(403, { detail: "mutating requests must use Content-Type: application/json" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(post("/x")).rejects.toMatchObject({ status: 403, auth: null });
  expect(hook).not.toHaveBeenCalled();
});

test("a GET auth refusal also reports its reason", async () => {
  mockFetch(403, { detail: "invalid session token" }, true, { "x-muvue-auth": "invalid" });
  const hook = vi.fn();
  onForbidden(hook);
  await expect(api("/auth/check")).rejects.toMatchObject({ auth: "invalid" });
  expect(hook).toHaveBeenCalledWith("invalid");
});

test("a bodyless GET sends no Content-Type", async () => {
  const fetchMock = mockFetch(200, {});
  await api("/x");
  const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
});
```

Create `dashboard/test/auth.test.ts`:

```ts
import { checkAuth, signInWithToken, applyAuthResult } from "../src/api/auth";
import { setToken } from "../src/api/client";
import { authed, signedOutReason } from "../src/state";

type Reply = { status: number; auth?: string };
function stub(replies: Record<string, Reply>) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const r = replies[url] ?? { status: 200 };
    return {
      ok: r.status < 400, status: r.status, statusText: "s",
      headers: new Headers({ "content-type": "application/json", ...(r.auth ? { "x-muvue-auth": r.auth } : {}) }),
      json: async () => ({ detail: "d" }), text: async () => "",
      _init: init,
    };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => { vi.unstubAllGlobals(); setToken(""); authed.value = false; signedOutReason.value = "read_only"; });

test("checkAuth maps 200/403 missing/403 invalid/401 expired", async () => {
  stub({ "/auth/check": { status: 200 } });
  expect(await checkAuth()).toBe("ok");
  stub({ "/auth/check": { status: 403, auth: "missing" } });
  expect(await checkAuth()).toBe("missing");
  stub({ "/auth/check": { status: 403, auth: "invalid" } });
  expect(await checkAuth()).toBe("invalid");
  stub({ "/auth/check": { status: 401, auth: "expired" } });
  expect(await checkAuth()).toBe("expired");
});

test("signInWithToken: a good token is checked, then turned into the session cookie", async () => {
  const fetchMock = stub({ "/auth/check": { status: 200 }, "/auth/session": { status: 200 } });
  expect(await signInWithToken("  tok  ")).toBe("ok");
  const urls = fetchMock.mock.calls.map((c) => c[0]);
  expect(urls).toEqual(["/auth/check", "/auth/session"]);
  const checkInit = fetchMock.mock.calls[0]![1] as RequestInit;
  expect((checkInit.headers as Record<string, string>)["Authorization"]).toBe("Bearer tok");
});

test("signInWithToken: a refused token is forgotten and reported, and no cookie is requested", async () => {
  const fetchMock = stub({ "/auth/check": { status: 403, auth: "invalid" } });
  expect(await signInWithToken("wrong")).toBe("invalid");
  expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(["/auth/check"]);
  const again = stub({ "/auth/check": { status: 200 } });
  await checkAuth();
  expect(((again.mock.calls[0]![1] as RequestInit).headers as Record<string, string>)["Authorization"]).toBeUndefined();
});

test("signInWithToken: blank input never calls the server", async () => {
  const fetchMock = stub({});
  expect(await signInWithToken("   ")).toBe("missing");
  expect(fetchMock).not.toHaveBeenCalled();
});

test("applyAuthResult sets authed and the signed-out reason", () => {
  applyAuthResult("ok");
  expect(authed.value).toBe(true);
  applyAuthResult("expired");
  expect([authed.value, signedOutReason.value]).toEqual([false, "expired"]);
  applyAuthResult("invalid");
  expect(signedOutReason.value).toBe("stale");
  applyAuthResult("missing");
  expect(signedOutReason.value).toBe("read_only");
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd dashboard && npx vitest run test/client.test.ts test/auth.test.ts`
Expected: FAIL. `auth` is not a field on `ApiError`, and `signInWithToken`, `applyAuthResult` and `signedOutReason` are not exported.

- [ ] **Step 3: Implement `client.ts`, `auth.ts`, `routes.ts` and `state.ts`**

Replace `dashboard/src/api/client.ts` with:

```ts
export type AuthProblem = "missing" | "invalid" | "expired";

export class ApiError extends Error {
  status: number;
  auth: AuthProblem | null;
  constructor(message: string, status: number, auth: AuthProblem | null = null) {
    super(message);
    this.status = status;
    this.auth = auth;
  }
}

// Inside the VS Code webview the session cookie is never sent, so the
// nonce exchange hands back the token and it is kept here, in memory. A
// pasted token also lives here until /auth/session turns it into the cookie.
let token = "";
let forbidden: (problem: AuthProblem) => void = () => {};

export function setToken(t: string): void { token = t; }
export function onForbidden(handler: (problem: AuthProblem) => void): void { forbidden = handler; }

// Only responses the daemon marks with X-Muvue-Auth are about sign-in; a
// 403 for a wrong Content-Type or Origin is not.
export function authProblemOf(status: number, header: string | null): AuthProblem | null {
  if (status !== 401 && status !== 403) return null;
  return header === "missing" || header === "invalid" || header === "expired" ? header : null;
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const sendsBody = init.body !== undefined || (init.method !== undefined && init.method !== "GET");
  const headers: Record<string, string> = { ...(sendsBody ? { "Content-Type": "application/json" } : {}), ...(init.headers as Record<string, string> | undefined) };
  if (token) headers["Authorization"] = "Bearer " + token;
  const r = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const type = r.headers.get("content-type") || "";
  const body: unknown = type.startsWith("application/json") ? await r.json() : await r.text();
  const problem = authProblemOf(r.status, r.headers.get("x-muvue-auth"));
  if (problem) forbidden(problem);
  if (!r.ok) {
    const detail = typeof body === "object" && body !== null && "detail" in body ? String((body as { detail: unknown }).detail) : "";
    throw new ApiError(detail || r.statusText || `HTTP ${r.status}`, r.status, problem);
  }
  return body as T;
}

export function post<T = unknown>(path: string, body: unknown = {}): Promise<T> {
  return api<T>(path, { method: "POST", body: JSON.stringify(body) });
}
```

In `dashboard/src/api/routes.ts`, add `authSession: () => "/auth/session",` directly after `authExchange`.

In `dashboard/src/state.ts`:
- delete `setAuthed`;
- after `export const authed = signal(false);`, add the block below.

```ts
// Why the page is read-only, so the sign-in strip can say it plainly.
export type SignedOutReason = "read_only" | "stale" | "expired";
export const signedOutReason = signal<SignedOutReason>("read_only");
export const signInOpen = signal(false);
export function setSignedIn(): void { authed.value = true; }
export function setSignedOut(reason: SignedOutReason): void { authed.value = false; signedOutReason.value = reason; }
```

Replace `dashboard/src/api/auth.ts` with:

```ts
import { routes } from "./routes";
import { api, post, ApiError, setToken, authProblemOf, type AuthProblem } from "./client";
import { setSignedIn, setSignedOut } from "../state";

export type AuthResult = "ok" | AuthProblem;

export async function checkAuth(): Promise<AuthResult> {
  try {
    await api(routes.authCheck());
    return "ok";
  } catch (e) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) return e.auth ?? "missing";
    throw e;
  }
}

// `muvue serve` and `muvue link` print a link whose `#n=` fragment is a
// single-use nonce. It is exchanged once for an HttpOnly cookie; in an
// iframe the exchange also returns the token for the Authorization header.
export async function exchangeFragmentNonce(): Promise<AuthResult> {
  const match = (window.location.hash || "").match(/(?:^#|[&#])n=([^&]+)/);
  if (!match) return checkAuth();
  const embedded = window.top !== window.self;
  history.replaceState(null, "", window.location.pathname + window.location.search);
  const r = await fetch(routes.authExchange(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ nonce: decodeURIComponent(match[1]!), header: embedded }),
  });
  if (r.ok) {
    const body = (await r.json()) as { token?: string };
    if (body.token) setToken(body.token);
  } else if (authProblemOf(r.status, r.headers.get("x-muvue-auth")) === "expired") {
    return "expired";
  }
  // An already-used nonce is not an error: a cookie from an earlier exchange may still be live.
  return checkAuth();
}

// A pasted api token: check it, then ask the daemon to set the HttpOnly
// cookie so a reload stays signed in. A refused token is forgotten at once.
export async function signInWithToken(pasted: string): Promise<AuthResult> {
  const t = pasted.trim();
  if (!t) return "missing";
  setToken(t);
  const result = await checkAuth();
  if (result !== "ok") {
    setToken("");
    return result;
  }
  await post(routes.authSession());
  return "ok";
}

export function applyAuthResult(r: AuthResult): void {
  if (r === "ok") setSignedIn();
  else setSignedOut(r === "expired" ? "expired" : r === "invalid" ? "stale" : "read_only");
}
```

- [ ] **Step 4: Run the client and auth tests**

Run: `cd dashboard && npx vitest run test/client.test.ts test/auth.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the failing sign-in UI tests**

Create `dashboard/test/signIn.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/preact";
import { AuthStrip } from "../src/shell/AuthStrip";
import { SignInSheet } from "../src/shell/SignInSheet";
import { authed, signedOutReason, signInOpen } from "../src/state";
import { setToken } from "../src/api/client";

function stubAuth(check: { status: number; auth?: string }) {
  const fn = vi.fn(async (url: string) => {
    const r = url === "/auth/check" ? check : { status: 200 };
    return { ok: r.status < 400, status: r.status, statusText: "s", headers: new Headers({ "content-type": "application/json", ...(r.auth ? { "x-muvue-auth": r.auth } : {}) }), json: async () => ({ detail: "d" }), text: async () => "" };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}
afterEach(() => { vi.unstubAllGlobals(); setToken(""); authed.value = false; signedOutReason.value = "read_only"; signInOpen.value = false; });

test("the strip says why the page is read-only, and hides when signed in", () => {
  const { rerender } = render(<AuthStrip />);
  expect(screen.getByText("Read-only view.")).toBeTruthy();
  signedOutReason.value = "stale";
  rerender(<AuthStrip />);
  expect(screen.getByText("Signed out: muvue serve restarted since you signed in.")).toBeTruthy();
  signedOutReason.value = "expired";
  rerender(<AuthStrip />);
  expect(screen.getByText("Signed out after 8 hours without activity.")).toBeTruthy();
  expect(screen.getByText("Sign in again")).toBeTruthy();
  authed.value = true;
  rerender(<AuthStrip />);
  expect(document.querySelector("[data-auth-strip]")).toBeNull();
});

test("the strip's button opens the sign-in sheet", () => {
  render(<AuthStrip />);
  fireEvent.click(screen.getByText("Sign in"));
  expect(signInOpen.value).toBe(true);
});

test("a refused token shows why, in the sheet", async () => {
  stubAuth({ status: 403, auth: "invalid" });
  render(<SignInSheet />);
  fireEvent.input(screen.getByLabelText("api token"), { target: { value: "wrong" } });
  fireEvent.click(screen.getByText("Use token"));
  await waitFor(() => expect(document.querySelector("[data-sign-in-error]")?.textContent).toBe(
    "That token was not accepted. Tokens change every time muvue serve restarts — run muvue link for the current one.",
  ));
  expect(authed.value).toBe(false);
});

test("an expired token says so and marks the page expired", async () => {
  stubAuth({ status: 401, auth: "expired" });
  render(<SignInSheet />);
  fireEvent.input(screen.getByLabelText("api token"), { target: { value: "old" } });
  fireEvent.click(screen.getByText("Use token"));
  await waitFor(() => expect(document.querySelector("[data-sign-in-error]")?.textContent).toContain("expired after 8 hours"));
  expect(signedOutReason.value).toBe("expired");
});

test("a good token signs in and closes the sheet", async () => {
  signInOpen.value = true;
  stubAuth({ status: 200 });
  render(<SignInSheet />);
  fireEvent.input(screen.getByLabelText("api token"), { target: { value: "good" } });
  fireEvent.click(screen.getByText("Use token"));
  await waitFor(() => expect(authed.value).toBe(true));
  expect(signInOpen.value).toBe(false);
});

test("Use token is disabled until something is typed", () => {
  render(<SignInSheet />);
  expect((screen.getByText("Use token").closest("button") as HTMLButtonElement).disabled).toBe(true);
});
```

In `dashboard/test/shell.test.tsx`, replace both tests with:

```tsx
test("the shell shows the project and no sign-in strip when signed in", () => {
  render(<App />);
  expect(screen.getAllByText("build the thing").length).toBeGreaterThan(0);
  expect(document.querySelector("[data-auth-strip]")).toBeNull();
});

test("read-only shows the one-line sign-in strip, not a token form", () => {
  authed.value = false;
  render(<App />);
  expect(document.querySelector('[data-auth-strip="read_only"]')).toBeTruthy();
  expect(screen.queryByLabelText("api token")).toBeNull();
});
```

In `dashboard/test/NextStepBar.test.tsx`, the test "read-only viewers see how to act instead of buttons": replace the `getByText("Read-only. Open the link printed by muvue serve to act.")` assertion with `expect(screen.getByText("Sign in to act")).toBeTruthy();`. In `dashboard/test/ProjectPage.test.tsx` (~line 72), replace `screen.getByText("Read-only. Open the link printed by muvue serve to act.")` with `screen.getByText("Sign in to act")`.

- [ ] **Step 6: Run and watch them fail**

Run: `cd dashboard && npx vitest run test/signIn.test.tsx test/shell.test.tsx test/NextStepBar.test.tsx`
Expected: FAIL, because `AuthStrip` and `SignInSheet` do not exist.

- [ ] **Step 7: Implement the components and the wiring**

Create `dashboard/src/shell/AuthStrip.tsx`:

```tsx
import { authed, signedOutReason, signInOpen } from "../state";
import { Button } from "../ui/Button";

const COPY = {
  read_only: { text: "Read-only view.", action: "Sign in" },
  stale: { text: "Signed out: muvue serve restarted since you signed in.", action: "Sign in" },
  expired: { text: "Signed out after 8 hours without activity.", action: "Sign in again" },
} as const;

// One line, not a form: the form lives in SignInSheet, one tap away.
export function AuthStrip() {
  if (authed.value) return null;
  const reason = signedOutReason.value;
  const c = COPY[reason];
  return (
    <div class={"auth-strip" + (reason === "read_only" ? "" : " warn")} data-auth-strip={reason} role="status">
      <span class="grow">{c.text}</span>
      <Button variant="outline" onClick={() => { signInOpen.value = true; }}>{c.action}</Button>
    </div>
  );
}
```

Create `dashboard/src/shell/SignInSheet.tsx`:

```tsx
import { useState } from "preact/hooks";
import { Sheet } from "../ui/Sheet";
import { Button } from "../ui/Button";
import { useAction } from "../ui/useAction";
import { signInWithToken, applyAuthResult, type AuthResult } from "../api/auth";
import { signInOpen, refresh, toast } from "../state";

const REFUSED: Record<Exclude<AuthResult, "ok">, string> = {
  missing: "Paste the api token first.",
  invalid: "That token was not accepted. Tokens change every time muvue serve restarts — run muvue link for the current one.",
  expired: "That token's session expired after 8 hours without activity. Run muvue link on the server to start a new one.",
};

export function SignInSheet() {
  const [value, setValue] = useState("");
  const [refused, setRefused] = useState<string | null>(null);
  const a = useAction();
  const close = () => { signInOpen.value = false; };

  async function submit(e: Event) {
    e.preventDefault();
    setRefused(null);
    // `as`, not an annotation: an annotated `let` narrows to "missing" here,
    // and TS can't see the closure's assignment, so the checks below would
    // fail to compile.
    let result = "missing" as AuthResult;
    const ok = await a.run(async () => { result = await signInWithToken(value); });
    setValue("");
    if (!ok) return;
    if (result === "ok") {
      applyAuthResult("ok");
      toast("Signed in");
      close();
      refresh();
      return;
    }
    setRefused(REFUSED[result]);
    if (result === "expired") applyAuthResult("expired");
  }

  return (
    <Sheet title="Sign in" onClose={close}>
      <div class="stack">
        <p>Signing in lets this page approve, plan and run. Either way works:</p>
        <div class="stack tight">
          <h3>Open a fresh link</h3>
          <p class="muted">On the machine running muvue, in the project's folder, run <code>muvue link</code>, then open the link it prints on this device.</p>
        </div>
        <form class="stack tight" onSubmit={submit}>
          <h3>Or paste the api token</h3>
          <p class="muted"><code>muvue serve</code> and <code>muvue link</code> print it. It changes every time <code>muvue serve</code> restarts.</p>
          <div class="row nowrap">
            <input type="password" autocomplete="off" aria-label="api token" placeholder="api token" value={value} onInput={(e) => setValue((e.target as HTMLInputElement).value)} />
            <Button type="submit" variant="filled" busy={a.busy} busyLabel="Checking…" disabled={!value.trim()}>Use token</Button>
          </div>
          {refused ? <div class="callout danger" data-sign-in-error>{refused}</div> : null}
          {a.error ? <div class="callout danger">{a.error}</div> : null}
        </form>
      </div>
    </Sheet>
  );
}
```

Delete `dashboard/src/shell/TokenBanner.tsx`.

In `dashboard/src/app.tsx`:
- replace the `TokenBanner` import with `import { AuthStrip } from "./shell/AuthStrip";` and `import { SignInSheet } from "./shell/SignInSheet";`;
- change the state import to `import { authed, projectId, signInOpen } from "./state";`;
- replace `{authed.value ? null : <TokenBanner />}` with `<AuthStrip />`;
- after `{palette ? … : null}`, add `{signInOpen.value ? <SignInSheet /> : null}`.

If `authed` is then unused in `app.tsx`, remove it from the import.

In `dashboard/src/main.tsx`:
- change the imports to `import { exchangeFragmentNonce, applyAuthResult } from "./api/auth";` and `import { authed, setSignedOut, loadProjects, protocolVersion, refreshTick, toastError } from "./state";`;
- replace `onForbidden(() => setAuthed(false));` with:

```ts
// An expired session is always worth saying. Otherwise only a page that
// thought it was signed in changes state: a refused paste attempt
// reports itself in the sign-in sheet instead.
onForbidden((problem) => {
  if (problem === "expired") setSignedOut("expired");
  else if (authed.value) setSignedOut(problem === "invalid" ? "stale" : "read_only");
});
```

- in `boot()`, replace `setAuthed(await exchangeFragmentNonce());` with `applyAuthResult(await exchangeFragmentNonce());`.

In `dashboard/src/shell/Sidebar.tsx`:
- import `signedOutReason` from `../state`;
- replace `<div>{authed.value ? "signed in" : "read-only"}</div>` with `<div>{authed.value ? "signed in" : signedOutReason.value === "expired" ? "session expired" : "read-only"}</div>`.

In `dashboard/src/project/NextStepBar.tsx`:
- add `import { signInOpen } from "../state";` (merge it into the existing `../state` import);
- replace `<p class="caption">Read-only. Open the link printed by muvue serve to act.</p>` with:

```tsx
        <Button variant="plain" onClick={() => { signInOpen.value = true; }}>Sign in to act</Button>
```

In `dashboard/src/styles/base.css`, after `.row.between { … }`, add:

```css
.row.nowrap { flex-wrap: nowrap; }
.row.nowrap input { flex: 1; min-width: 0; }
```

In `dashboard/src/shell/shell.css`, replace the three `.token-banner` rules with:

```css
.auth-strip { display: flex; align-items: center; gap: 12px; margin: 8px 16px 0; padding: 4px 4px 4px 14px; border-radius: var(--radius); background: var(--surface-2); border: 1px solid var(--border); font-size: 14px; }
.auth-strip.warn { border-color: var(--st-in_progress); background: color-mix(in srgb, var(--st-in_progress) 12%, var(--surface)); }
```

- [ ] **Step 8: Typecheck, test, build**

Run: `cd dashboard && npm run typecheck && npm test && npm run build`
Expected: 0 type errors and all tests passing. The build copies to `src/muvue/api/static/index.html`.
Then run `uv run pytest tests/test_dashboard_static.py -q` from the repo root. Expected: PASS. `/auth/session` is served, and `test_token_never_reaches_persistent_storage` still passes because nothing writes the token to storage.

- [ ] **Step 9: Commit**

```bash
git add dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Dashboard sign-in: one-line strip, sign-in sheet with the exact refusal reason, pasted token becomes the cookie"
```

---

### Task 4: Colour tokens pass WCAG AA, and a test keeps them there

**Files:**
- Modify: `dashboard/src/styles/tokens.css`, `dashboard/src/styles/base.css`, `dashboard/src/ui/ui.css`, `dashboard/src/shell/shell.css`, `dashboard/src/cards/cards.css`
- Test: `dashboard/test/contrast.test.ts` (new)

**Interfaces:**
- Produces: CSS custom properties `--accent-strong` and `--accent-strong-hover` in both modes. `--accent` stays for decoration only (borders, dots, focus rings).

- [ ] **Step 1: Write the failing contrast test**

Create `dashboard/test/contrast.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";

// WCAG AA: text needs 4.5:1 against what it sits on. Colours are read from
// tokens.css, so a future palette tweak that breaks a pair fails here.
const css = readFileSync(join(__dirname, "..", "src", "styles", "tokens.css"), "utf8");
function tokens(block: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of block.matchAll(/--([a-z0-9_-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) out[m[1]!] = m[2]!;
  return out;
}
const split = css.indexOf("@media (prefers-color-scheme: dark)");
const light = tokens(css.slice(0, split));
const dark = { ...light, ...tokens(css.slice(split)) };

function rgb(hex: string): number[] { const n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function toHex(c: number[]): string { return "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join(""); }
// Same arithmetic as CSS `color-mix(in srgb, a <pct>, b)`.
function mix(a: string, pct: number, b: string): string { const x = rgb(a), y = rgb(b); return toHex(x.map((v, i) => v * pct + y[i]! * (1 - pct))); }
function lum(hex: string): number {
  const w = [0.2126, 0.7152, 0.0722];
  return rgb(hex).reduce((s, v, i) => { const c = v / 255; return s + w[i]! * (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4); }, 0);
}
function ratio(a: string, b: string): number { const [hi, lo] = [lum(a), lum(b)].sort((p, q) => q - p); return (hi! + 0.05) / (lo! + 0.05); }

const STATUSES = ["pending", "ready", "in_progress", "review", "awaiting_approval", "done", "blocked", "failed"];

describe.each([["light", light], ["dark", dark]] as const)("%s mode: text contrast is at least 4.5:1", (_mode, t) => {
  const pairs: [string, string, string][] = [
    ["body text on the page", t["text"]!, t["bg"]!],
    ["secondary text on the page", t["text-2"]!, t["bg"]!],
    ["secondary text on surface-2 (callouts, disabled buttons)", t["text-2"]!, t["surface-2"]!],
    ["filled button label", t["on-accent"]!, t["accent-strong"]!],
    ["filled button label on hover", t["on-accent"]!, t["accent-strong-hover"]!],
    ["links and plain buttons on the page", t["accent-strong"]!, t["bg"]!],
    ["links and plain buttons on a surface", t["accent-strong"]!, t["surface"]!],
    ["selected sidebar item", t["accent-strong"]!, mix(t["accent"]!, 0.12, t["surface"]!)],
    ...STATUSES.map((s): [string, string, string] => [`${s} pill`, mix(t["st-" + s]!, 0.5, t["text"]!), mix(t["st-" + s]!, 0.14, t["surface"]!)]),
  ];
  test.each(pairs)("%s", (_name, fg, bg) => {
    expect(ratio(fg, bg)).toBeGreaterThanOrEqual(4.5);
  });
});

test("the disabled button style is not a faded primary", () => {
  const ui = readFileSync(join(__dirname, "..", "src", "ui", "ui.css"), "utf8");
  expect(ui).not.toMatch(/\.btn:disabled\s*\{[^}]*opacity:\s*\.5/);
  expect(ui).toMatch(/\.btn:disabled:not\(\.busy\)\s*\{[^}]*background:\s*var\(--surface-2\)[^}]*color:\s*var\(--text-2\)/);
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd dashboard && npx vitest run test/contrast.test.ts`
Expected: FAIL. The `accent-strong` tokens are undefined (`rgb(undefined)` throws), and the pill pairs sit around 2-3.3:1.

- [ ] **Step 3: Implement the tokens and rules**

In `dashboard/src/styles/tokens.css`:
- In `:root`, after `--accent-hover: #C4623F;`, add `--accent-strong: #AE5130;` and `--accent-strong-hover: #93432A;`.
- In the dark `@media` block's `:root`, add `--accent-strong: #E08A6D;` and `--accent-strong-hover: #EA9D82;`.

The computed ratios, for the record:
- light: white on `#AE5130` is 5.22, and `#AE5130` on the page is 4.96;
- dark: `#1F1E1D` on `#E08A6D` is 6.36, and `#E08A6D` on the surface is 5.49.

In `dashboard/src/styles/base.css`:
- `a { color: var(--accent); }` → `a { color: var(--accent-strong); }`
- in `.chip.on`, change `background: var(--accent); border-color: var(--accent);` to `background: var(--accent-strong); border-color: var(--accent-strong);`

In `dashboard/src/ui/ui.css`, replace the lines below (keep every other rule):

```css
.btn:disabled:not(.busy) { cursor: default; background: var(--surface-2); border-color: var(--border); color: var(--text-2); }
.btn-filled { background: var(--accent-strong); border-color: var(--accent-strong); color: var(--on-accent); }
.btn-filled:hover:not(:disabled) { background: var(--accent-strong-hover); border-color: var(--accent-strong-hover); }
.btn-plain { border-color: transparent; background: transparent; color: var(--accent-strong); }
```

These replace `.btn:disabled { opacity: .5; cursor: default; }`, `.btn-filled {…}`, `.btn-filled:hover… {…}` and `.btn-plain {…}`. Keep `.btn.busy:disabled { opacity: 1; cursor: progress; }` as it is.

Then change every `.pill` colour rule so its text is mixed halfway toward `--text`. The background stays the same. For example:

```css
.pill { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 500; color: color-mix(in srgb, var(--st-pending) 50%, var(--text)); background: color-mix(in srgb, var(--st-pending) 14%, var(--surface)); text-transform: capitalize; }
.pill.st-ready { color: color-mix(in srgb, var(--st-ready) 50%, var(--text)); background: color-mix(in srgb, var(--st-ready) 14%, var(--surface)); }
```

Do the same for:
- `st-in_progress`, `st-review`, `st-awaiting_approval`, `st-failed`;
- `st-done, st-executing` (uses `--st-done`);
- `st-blocked, st-paused` (uses `--st-blocked`);
- `st-planning` (uses `--st-ready`).

In `dashboard/src/shell/shell.css`, in `.nav-item.on`, change `color: var(--accent);` to `color: var(--accent-strong);`.
In `dashboard/src/cards/cards.css`, in `.card-rail-pill`, change `background: var(--accent);` to `background: var(--accent-strong);`.

Finally, run `grep -rn "var(--accent)" dashboard/src --include=*.css` and confirm every remaining use is a border, outline, dot or `color-mix` background tint, never text or a background behind text.

- [ ] **Step 4: Run tests, typecheck, build**

Run: `cd dashboard && npm run typecheck && npm test && npm run build`
Expected: all pass, including the 34 contrast cases (17 per mode).

- [ ] **Step 5: Commit**

```bash
git add dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Contrast: text tokens pass WCAG AA in both modes; disabled buttons no longer look like a faded primary"
```

---

### Task 5: Compact, honest boxes in the diagram

**Files:**
- Modify: `dashboard/src/canvas/AgentChip.tsx`, `dashboard/src/canvas/SpecRoot.tsx`, `dashboard/src/canvas/DagPlaceholder.tsx`, `dashboard/src/canvas/Canvas.tsx` (the `<DagPlaceholder …/>` call only), `dashboard/src/canvas/flowLayout.ts` (constants and `taskHeight`), `dashboard/src/canvas/canvas.css`, `dashboard/scripts/ui-check.mjs` (the `dagChecks` function)
- Test: `dashboard/test/AgentChip.test.tsx` (new), `dashboard/test/SpecRoot.test.tsx` (modify), `dashboard/test/flowLayout.test.ts` (modify the `taskHeight` test)

**Interfaces:**
- Produces:
  - `AgentChip` renders `.agent-tag.st-<state>` holding `.agent-dot` and `.agent-tag-text`;
  - `SpecRoot` renders `.pill` with the text "needs approval" or "approved";
  - `flowLayout` exports `TASK_H_BASE = 118`, `SUBTASK_LIST_EXTRA = 12`, `PLACEHOLDER_H = 140`, `SPEC_H = 112`;
  - `DagPlaceholder({ planning, style })`.

- [ ] **Step 1: Write the failing tests**

Create `dashboard/test/AgentChip.test.tsx`:

```tsx
import { render } from "@testing-library/preact";
import { AgentChip } from "../src/canvas/AgentChip";
import type { AgentState } from "../src/canvas/agentState";

const EXPECTED: Record<AgentState, string> = {
  unassigned: "no agent",
  waiting_for_plan_approval: "waits for approval",
  waiting_on_earlier: "waits for earlier tasks",
  ready: "ready to run",
  running: "working now",
  waiting_on_you: "needs you",
  done: "done",
  failed: "failed",
  paused: "paused",
};

test.each(Object.entries(EXPECTED))("%s reads as a short status line", (state, label) => {
  const { container } = render(<AgentChip agent="claude" state={state as AgentState} />);
  expect(container.querySelector(".agent-tag-text")!.textContent).toBe(`claude · ${label}`);
});

test("it is a status line, not a control: no .chip (44px button look), not focusable", () => {
  const { container } = render(<AgentChip agent="fake" state="ready" />);
  const tag = container.querySelector(".agent-tag")!;
  expect(tag.classList.contains("chip")).toBe(false);
  expect(tag.getAttribute("tabindex")).toBeNull();
  expect(tag.getAttribute("title")).toBe("fake · ready to run");
});

test("no agent shows the label alone; running pulses", () => {
  const { container, rerender } = render(<AgentChip agent={null} state="unassigned" />);
  expect(container.querySelector(".agent-tag-text")!.textContent).toBe("no agent");
  rerender(<AgentChip agent="claude" state="running" />);
  expect(container.querySelector(".agent-dot.pulse")).toBeTruthy();
});
```

In `dashboard/test/SpecRoot.test.tsx`, add:

```tsx
test("the spec's badge says approved or needs approval, never a node status like Ready", () => {
  const base = { id: 1, title: "voxscore v0.1", body_md: "Record voices", agent: null };
  const { container, rerender } = render(<SpecRoot spec={{ ...base, status: "pending" } as any} />);
  expect(container.querySelector(".pill")!.textContent).toBe("needs approval");
  rerender(<SpecRoot spec={{ ...base, status: "ready" } as any} />);
  expect(container.querySelector(".pill")!.textContent).toBe("approved");
});
```

Before adding it, check which `render`/`SpecRoot` imports the file already has, and adjust the object to match `CanvasSpec`'s required fields in `dashboard/src/canvas/canvasData.ts`. Add every required field instead of `as any` if that typechecks.

In `dashboard/test/flowLayout.test.ts`, update the import to include `SUBTASK_LIST_EXTRA`, and change the `taskHeight` expectations to:

```ts
  expect(taskHeight(0)).toBe(TASK_H_BASE);
  expect(taskHeight(2)).toBe(TASK_H_BASE + SUBTASK_LIST_EXTRA + 2 * SUBTASK_ROW_H);
  expect(taskHeight(4)).toBe(taskHeight(10));
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd dashboard && npx vitest run test/AgentChip.test.tsx test/SpecRoot.test.tsx test/flowLayout.test.ts`
Expected: FAIL (old labels, `.chip` class, a "ready" pill, and no `SUBTASK_LIST_EXTRA` export).

- [ ] **Step 3: Implement**

Replace `dashboard/src/canvas/AgentChip.tsx` with:

```tsx
import type { AgentState } from "./agentState";

// Short on purpose: the box is 240px wide and the Next bar already explains
// the project-wide gate ("Approve the task list"), so each box only needs
// its own state in a few words (decision #174).
const LABEL: Record<AgentState, string> = {
  unassigned: "no agent",
  waiting_for_plan_approval: "waits for approval",
  waiting_on_earlier: "waits for earlier tasks",
  ready: "ready to run",
  running: "working now",
  waiting_on_you: "needs you",
  done: "done",
  failed: "failed",
  paused: "paused",
};

// A status line, not a control: tapping the box opens the node, so this must
// not borrow `.chip`, the bordered 44px filter-chip style.
export function AgentChip({ agent, state }: { agent: string | null; state: AgentState }) {
  const label = agent ? `${agent} · ${LABEL[state]}` : LABEL[state];
  return (
    <span class={"agent-tag st-" + state} title={label}>
      <span class={"agent-dot" + (state === "running" ? " pulse" : "")} aria-hidden="true" />
      <span class="agent-tag-text">{label}</span>
    </span>
  );
}
```

In `dashboard/src/canvas/SpecRoot.tsx`, replace `<Pill status={spec.status} />` with `<SpecBadge status={spec.status} />`, remove the now-unused `Pill` import, and add above `SpecRoot`:

```tsx
// The spec's own badge: whether it is approved, in the words the Next bar
// uses. A node status like "ready" read as the same blue as the phase pill
// but meant something else.
function SpecBadge({ status }: { status: string }) {
  return status === "pending"
    ? <span class="pill st-awaiting_approval">needs approval</span>
    : <span class="pill st-done">approved</span>;
}
```

Replace `dashboard/src/canvas/DagPlaceholder.tsx`'s component signature and copy. Keep `stopBubble` and the existing explanatory comment:

```tsx
export function DagPlaceholder({ planning, style }: { planning: boolean; style?: Record<string, string | number> }) {
  return (
    <div class="task-box dag-empty" style={style} onClick={stopBubble} onKeyDown={stopBubble}>
      {planning ? (
        <>
          <div class="row"><span class="spinner" /><span class="title">Planning tasks…</span></div>
          <div class="caption">Tasks appear here when the agent finishes.</div>
          <div class="dag-skeleton" />
          <div class="dag-skeleton" />
          <div class="dag-skeleton" />
        </>
      ) : (
        <>
          <span class="title">No tasks yet</span>
          <div class="caption">Tasks will appear here, with arrows showing what each one hands to the next. Start with "✨ Plan tasks with agent" under Next.</div>
        </>
      )}
    </div>
  );
}
```

Remove the now-unused `Activity` import from that file. In `dashboard/src/canvas/Canvas.tsx`, change the placeholder call to `<DagPlaceholder planning={planning} style={boxStyle(lay.pos[PLACEHOLDER_ID]!)} />`. Leave `Canvas`'s own props alone; `onAddTask` stays in its signature because `ProjectPage` still passes it. If `tsc` reports `onAddTask` unused in `Canvas`, prefix it `_onAddTask` only in the destructuring.

In `dashboard/src/canvas/flowLayout.ts`, replace the comment block and the constants line with:

```ts
// Box heights are fixed by the layout and set as CSS heights on the boxes, so
// a wrong estimate can only clip a box's content, never overlap two boxes.
// From the built CSS (15px/1.4 body, 12px/1.4 caption): a task box with a
// 2-line title, a 1-line purpose and the 1-line agent tag is 114px of content,
// padding and border; 118 leaves 4px of slack. The subtask list adds its
// 4px+4px margins and a 4px flex gap once. The placeholder's 4-line caption
// needs 131px. ui-check's "every box's content fits" check verifies all of
// this in a real browser: raise a constant if it fails, never lower it below
// what that check measures.
export const NODE_W = 240, SPEC_H = 112, TASK_H_BASE = 118, SUBTASK_LIST_EXTRA = 12, SUBTASK_ROW_H = 44, MAX_SUBTASK_ROWS = 3, PLACEHOLDER_H = 140, PAD = 16, GAP_X = 24, GAP_Y = 72;
```

and `taskHeight` with:

```ts
export function taskHeight(subtaskCount: number): number {
  const rows = Math.min(subtaskCount, MAX_SUBTASK_ROWS) + (subtaskCount > MAX_SUBTASK_ROWS ? 1 : 0);
  return rows ? TASK_H_BASE + SUBTASK_LIST_EXTRA + rows * SUBTASK_ROW_H : TASK_H_BASE;
}
```

In `dashboard/src/canvas/canvas.css`:
- delete `.task-box .agent-chip { … }`, `.agent-chip { … }` and `.agent-chip .pulse { … }`;
- delete `.dag-empty .btn { width: 100%; }`, since the placeholder has no buttons now;
- add:

```css
.agent-tag { display: flex; align-items: center; gap: 6px; margin-top: auto; min-width: 0; font-size: 12px; line-height: 1.4; color: var(--text-2); }
.agent-tag-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.agent-dot { width: 8px; height: 8px; border-radius: 50%; flex: none; background: var(--st-pending); }
.agent-tag.st-ready .agent-dot { background: var(--st-ready); }
.agent-tag.st-running .agent-dot { background: var(--st-in_progress); }
.agent-tag.st-waiting_on_you .agent-dot { background: var(--st-review); }
.agent-tag.st-done .agent-dot { background: var(--st-done); }
.agent-tag.st-failed .agent-dot { background: var(--st-failed); }
.agent-tag.st-paused .agent-dot { background: var(--st-blocked); }
.agent-dot.pulse { animation: pulse 1.2s ease-in-out infinite; }
```

In `dashboard/scripts/ui-check.mjs`, inside `dagChecks`, after the `offscreen` check, add:

```js
  const overflowing = await page.locator(".task-box, .spec-root-card").evaluateAll((els) => els.filter((e) => e.scrollHeight > e.clientHeight + 1).map((e) => `${e.querySelector(".title")?.textContent ?? "?"}: needs ${e.scrollHeight}px, has ${e.clientHeight}px`));
  check(`${label}: every box's content fits (nothing clipped)`, overflowing.length === 0, overflowing.join("; "));
```

and after the existing `check("phone: empty-state placeholder node is shown", …)`, add:

```js
  const placeholderFits = await page.locator(".dag-empty").evaluate((e) => e.scrollHeight <= e.clientHeight + 1);
  check("phone: the empty-state placeholder's text fits", placeholderFits);
```

- [ ] **Step 4: Typecheck, test, build**

Run: `cd dashboard && npm run typecheck && npm test && npm run build`
Expected: all pass. If an older test asserts an old chip label ("starts after the task list is approved", "ready — starts on Run") or the old placeholder copy, update that one assertion to the new string from this task's code. Do not change what any other test checks.

- [ ] **Step 5: Verify in a real browser**

Run from the repo root:
```bash
PLAYWRIGHT_CORE=/home/eugene/.npm/_npx/9833c18b2d85bc59/node_modules/playwright-core \
CHROME_PATH=$HOME/.cache/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell \
UI_CHECK_PORT=8912 node dashboard/scripts/ui-check.mjs /tmp/muvue-ui-check-task5
```
Expected:
- every new "content fits" check passes (the old 300ms activity check may still fail; Task 7 fixes it);
- `03-phone-after-plan.png` shows one-line agent tags with a coloured dot and no bordered pill, and no dead band inside the boxes;
- the spec card's badge says "approved".

If a "content fits" check fails, raise the named constant by the reported shortfall plus 2px, rebuild, and rerun. Report the final numbers.

- [ ] **Step 6: Commit**

```bash
git add dashboard/src dashboard/test dashboard/scripts/ui-check.mjs src/muvue/api/static/index.html
git commit -m "Diagram boxes: one-line agent status (not a button look), spec badge says approved, box heights fit content"
```

---

### Task 6: The diagram is the page, and everything else takes one line or the rail

**Files:**
- Modify: `dashboard/src/project/ProjectPage.tsx`, `dashboard/src/cards/CardRail.tsx`, `dashboard/src/shell/Sidebar.tsx`, `dashboard/src/canvas/canvas.css`, `dashboard/src/cards/cards.css`, `dashboard/src/ui/ui.css` (toasts), `dashboard/src/shell/shell.css`
- Create: `dashboard/src/project/FakeAgentNotice.tsx`
- Test: `dashboard/test/ProjectPage.test.tsx` (add tests), `dashboard/test/layout.test.ts` (new, CSS rules)

**Interfaces:**
- Consumes:
  - Task 3: `AuthStrip` (rendered by `app.tsx`) and `signedOutReason` in the Sidebar foot. Keep both.
  - Task 4: `.btn-filled` and the disabled style.
- Produces the DOM hooks Task 7 measures:
  - `.project-grid`, `.project-head`, `.project-rail`, `.project-dag`;
  - `[data-next-step]` (already exists), `.canvas-wrap`;
  - `[data-fake-notice]`, now a `<details>`.

- [ ] **Step 1: Write the failing tests**

Append to `dashboard/test/ProjectPage.test.tsx`. Reuse the file's `vi.spyOn(client, "api")` fixture shape; the helper below builds it:

```tsx
function mockProject(specStatus: string, tasks: Array<{ id: number; status: string }>, phase: "planning" | "executing") {
  projects.value = [{ id: 1, goal: "Voxscore", phase }];
  projectId.value = 1;
  const spec = { id: 1, project_id: 1, parent_id: null, kind: "spec", title: "Voxscore", status: specStatus, risk_tier: "low", owner: null, agent: "fake" };
  const taskRows = tasks.map((t) => ({ id: t.id, project_id: 1, parent_id: 1, kind: "task", title: "T" + t.id, status: t.status, risk_tier: "low", owner: null, agent: "fake" }));
  const full = (n: any) => ({ ...n, body_md: "x", criteria_json: "[]", criteria_hash: null, block_reason: null, deleted_at: null });
  vi.spyOn(client, "api").mockImplementation((path: string) => {
    if (path.startsWith("/graph")) return Promise.resolve({ nodes: [spec, ...taskRows], edges: [] });
    if (path.startsWith("/nodes")) return Promise.resolve([full(spec), ...taskRows.map(full)]);
    if (path === "/inbox") return Promise.resolve({ questions: [], review: [], unverified_external: [], structure_updates: [], blocked: [], awaiting_approval: [], signals: [], audit_items: [], unattributed_commits: [] });
    if (path.startsWith("/projects/1/revisions")) return Promise.resolve([]);
    if (path.startsWith("/projects/1/activity")) return Promise.resolve({ active: [], breakdowns: [], working: [] });
    return Promise.resolve({});
  });
}

test("one primary action: while the task list awaits approval, Run is not filled", async () => {
  authed.value = true;
  mockProject("ready", [{ id: 2, status: "pending" }], "planning");
  const { container } = render(<ProjectPage />);
  await waitFor(() => screen.getByText("Approve task list"));
  const filledEnabled = [...container.querySelectorAll(".btn-filled")].filter((b) => !(b as HTMLButtonElement).disabled);
  expect(filledEnabled.map((b) => b.textContent)).toEqual(["Approve task list"]);
  authed.value = false;
});

test("when Run is the next step, Run is the one filled button", async () => {
  authed.value = true;
  mockProject("ready", [{ id: 2, status: "ready" }], "executing");
  const { container } = render(<ProjectPage />);
  await waitFor(() => screen.getByText("▶ Run tasks"));
  const filled = [...container.querySelectorAll(".btn-filled")].filter((b) => !(b as HTMLButtonElement).disabled);
  expect(filled.map((b) => b.textContent)).toEqual(["▶ Run tasks"]);
  authed.value = false;
});

test("the fake-agent notice is one tappable line that expands for detail", async () => {
  mockProject("ready", [{ id: 2, status: "pending" }], "planning");
  render(<ProjectPage />);
  await waitFor(() => expect(document.querySelector("[data-fake-notice]")).toBeTruthy());
  const notice = document.querySelector("[data-fake-notice]")!;
  expect(notice.tagName).toBe("DETAILS");
  expect(notice.querySelector("summary")!.textContent).toBe("Demo agent: writes no code");
  expect(notice.textContent).toContain("demo agent");
});

test("the header, rail and diagram are separate regions, with the Next step in the rail", async () => {
  mockProject("ready", [{ id: 2, status: "pending" }], "planning");
  const { container } = render(<ProjectPage />);
  await waitFor(() => container.querySelector(".canvas-wrap"));
  expect(container.querySelector(".project-head h1")!.textContent).toBe("Voxscore");
  expect(container.querySelector(".project-rail [data-next-step]")).toBeTruthy();
  expect(container.querySelector(".project-dag .canvas-wrap")).toBeTruthy();
  expect(screen.queryByText("Nothing waiting on you")).toBeNull();
});
```

Create `dashboard/test/layout.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { join } from "node:path";

const read = (...p: string[]) => readFileSync(join(__dirname, "..", "src", ...p), "utf8");

test("wide screens put the Next step, activity and cards in a right rail beside the diagram", () => {
  const css = read("canvas", "canvas.css");
  expect(css).toMatch(/@media \(min-width: 1100px\)\s*\{[^@]*\.project-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s+340px/);
  expect(css).toMatch(/grid-template-areas:\s*"head rail"\s*"dag rail"/);
});

test("phones do not repeat the project title the sticky top bar already shows", () => {
  const css = read("canvas", "canvas.css");
  expect(css).toMatch(/@media \(max-width: 899px\)\s*\{[^}]*\.project-head \.page-title,\s*\.project-menu-btn\s*\{\s*display:\s*none/);
});

test("toasts sit off the diagram: top on phones, bottom-right on desktop", () => {
  const css = read("ui", "ui.css");
  expect(css).toMatch(/\.toasts\s*\{[^}]*top:\s*calc\(env\(safe-area-inset-top\) \+ 68px\)/);
  expect(css).toMatch(/@media \(min-width: 900px\)\s*\{\s*\.toasts\s*\{[^}]*right:\s*20px/);
});
```

- [ ] **Step 2: Run them and watch them fail**

Run: `cd dashboard && npx vitest run test/ProjectPage.test.tsx test/layout.test.ts`
Expected: the new tests FAIL. Run is always filled, the notice is a `div`, there is no `.project-grid`, and "Nothing waiting on you" is rendered.

- [ ] **Step 3: Implement**

Create `dashboard/src/project/FakeAgentNotice.tsx`:

```tsx
// Progressive disclosure: one line says what matters (nothing real will be
// written); the how-to-fix stays one tap away.
export function FakeAgentNotice() {
  return (
    <details class="demo-notice" data-fake-notice>
      <summary>Demo agent: writes no code</summary>
      <p class="muted">Tasks here are routed to the built-in "fake" demo agent. It returns canned results and writes no code. To do real work, point [routing] in .muvue/config.toml at a real agent such as claude.</p>
    </details>
  );
}
```

In `dashboard/src/cards/CardRail.tsx`, change `if (!cards.length) return <div class="card-rail-empty caption">Nothing waiting on you</div>;` to `if (!cards.length) return null;`.

In `dashboard/src/project/ProjectPage.tsx`:
- Add imports: `import { FakeAgentNotice } from "./FakeAgentNotice";`, `import { ProjectMenu } from "../shell/ProjectMenu";`, `import { Icon } from "../ui/Icon";`.
- Add `const [menu, setMenu] = useState(false);` next to the other `useState`s.
- Replace everything from `return (` to the end of the component with:

```tsx
  return (
    <div class="page-canvas">
      <div class="project-grid">
        <header class="project-head">
          <div class="row between nowrap">
            <h1 class="page-title clamp-2">{p.goal}</h1>
            <button type="button" class="icon-btn project-menu-btn" aria-label="project menu" onClick={() => setMenu(true)}><Icon name="more" /></button>
          </div>
          <div class="row meta-row">
            <button type="button" class="status-line-btn" onClick={() => setAgentsSheet(true)}>
              <StatusLine phase={p.phase} done={done} total={total} working={working} />
            </button>
            {hasFakeAgent ? <FakeAgentNotice /> : null}
          </div>
          <div class="row toolbar">
            {authed.value && data?.spec && data.spec.status !== "pending" && step.id !== "plan_tasks" ? (
              <Button variant="outline" onClick={() => setAddingTask((a) => !a)}>+ Task</Button>
            ) : null}
            <Button variant={step.id === "run" ? "filled" : "outline"} busy={runA.busy} busyLabel="Starting…" disabled={!!reason} onClick={run}>▶ Run tasks</Button>
            {reason ? <span class="caption" data-run-reason>{reason}</span> : null}
          </div>
          {runA.error ? <div class="callout danger">{runA.error}</div> : null}
        </header>
        <aside class="project-rail">
          <NextStepBar step={step} authed={authed.value} projectId={pid!} specId={data?.spec?.id ?? null} activity={activity} onAddTask={openAddTask} />
          <ActivityBar items={items} now={now} onDismiss={dismiss} onOpenLog={setOpenLog} />
          <CardRail cards={cards} />
        </aside>
        <section class="project-dag">
          {authed.value && addingTask && data?.spec ? (
            <AddForm parentId={data.spec.id} kind="task" candidates={data.tasks} onClose={() => setAddingTask(false)} />
          ) : null}
          {graphQ.error || nodesQ.error ? <div class="callout danger">{graphQ.error || nodesQ.error}</div> : !data ? <Empty text="loading…" /> : step.id === "write_spec" ? (
            authed.value ? <SubmitSpecForm projectId={pid!} /> : null
          ) : (
            <Canvas data={data} projectId={pid!} projectPhase={p.phase} needsYou={needsYou} activity={activity} onAddTask={openAddTask} />
          )}
        </section>
      </div>
      {menu ? <ProjectMenu onClose={() => setMenu(false)} /> : null}
      {agentsSheet ? <AgentsSheet projectId={pid!} onClose={() => setAgentsSheet(false)} /> : null}
      {openLog ? <LogSheet log={openLog} onClose={() => setOpenLog(null)} /> : null}
    </div>
  );
```

In `dashboard/src/shell/Sidebar.tsx`, replace the header `project-btn` button (the goal, pill and "more" icon, plus its `menu` state and the `ProjectMenu` render) with a plain label. Project actions now live next to the page title, so they sit beside the thing they act on.

```tsx
      <div class="sidebar-head caption">Projects</div>
```

Remove the now-unused `useState`, `currentProject`, `Pill`, `Icon` (only if unused) and `ProjectMenu` imports. Keep the nav list and the foot, including Task 3's `signedOutReason` text.

In `dashboard/src/canvas/canvas.css`:
- delete the three `.project-layout …` rules at the top of the file (the selector no longer exists);
- replace `.status-line { padding: 4px 16px; }` and the `.status-line-btn { … }` rule with the block below;
- leave `.next-step { margin: 0 16px 12px; … }` as it is (the rail supplies the column), and add right after it `.next-step p { margin: 0 0 8px; }`. The browser's default 1em paragraph margins made the card about 18px taller than its content.

```css
.project-grid { display: grid; grid-template-columns: minmax(0, 1fr); grid-template-areas: "head" "rail" "dag"; }
.project-head { grid-area: head; display: flex; flex-direction: column; gap: 6px; padding: 12px 16px 8px; min-width: 0; }
.project-head .page-title { margin: 0; font-size: 20px; flex: 1; min-width: 0; }
.project-rail { grid-area: rail; display: flex; flex-direction: column; min-width: 0; }
.project-dag { grid-area: dag; padding: 0 16px 16px; min-width: 0; display: flex; flex-direction: column; gap: 12px; }
.meta-row { gap: 8px; }
.toolbar { gap: 8px; }
.status-line { padding: 0; }
.status-line-btn { display: inline-flex; align-items: center; min-height: 44px; padding: 0; border: 0; background: none; text-align: left; cursor: pointer; color: inherit; font: inherit; }
.demo-notice summary { display: inline-flex; align-items: center; min-height: 44px; padding: 0 14px; border-radius: 999px; cursor: pointer; list-style: none; font-size: 13px; color: var(--text); background: color-mix(in srgb, var(--st-in_progress) 16%, var(--surface)); border: 1px solid color-mix(in srgb, var(--st-in_progress) 45%, var(--surface)); }
.demo-notice summary::-webkit-details-marker { display: none; }
.demo-notice[open] { flex-basis: 100%; }
.demo-notice p { margin: 8px 0 0; max-width: 60ch; }
@media (max-width: 899px) {
  .project-head .page-title, .project-menu-btn { display: none; }
}
@media (min-width: 1100px) {
  .project-grid { grid-template-columns: minmax(0, 1fr) 340px; grid-template-areas: "head rail" "dag rail"; align-items: start; }
  .project-head { padding-top: 16px; }
  .project-rail { position: sticky; top: 0; max-height: 100dvh; overflow-y: auto; padding-top: 16px; border-left: 1px solid var(--border); }
}
```

In `dashboard/src/cards/cards.css`:
- replace `.card-rail { width: 320px; flex: none; padding: 12px; border-left: 1px solid var(--border); overflow-y: auto; }` with `.card-rail { padding: 0 16px 12px; }`;
- delete `.card-rail-empty { … }` and the `@media (max-width: 899px) { .card-rail … }` rule.

In `dashboard/src/shell/shell.css`:
- add `.sidebar-head { padding: 12px 12px 4px; }`;
- inside the `@media (min-width: 900px)` block, change `.nav-item .grow { flex: 1; }` to `.nav-item .grow { flex: 1; display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }`;
- delete `.sidebar .project-btn { flex: none; }`.

In `dashboard/src/ui/ui.css`, replace the `.toasts` rule and its `@media (min-width: 900px)` override with:

```css
.toasts { position: fixed; left: 0; right: 0; top: calc(env(safe-area-inset-top) + 68px); display: flex; flex-direction: column; align-items: center; gap: 8px; z-index: 30; pointer-events: none; }
@media (min-width: 900px) { .toasts { top: auto; bottom: 20px; left: auto; right: 20px; align-items: flex-end; } }
```

- [ ] **Step 4: Typecheck, test, build**

Run: `cd dashboard && npm run typecheck && npm test && npm run build`
Expected: all pass. Existing tests that looked for the old `.project-layout`, `.card-rail-empty` or the sidebar project button get their selector or text updated to the new structure. Keep what they check (for example "the goal is shown").

- [ ] **Step 5: Commit**

```bash
git add dashboard/src dashboard/test src/muvue/api/static/index.html
git commit -m "Project page: compact header, Next/activity/cards in a right rail on wide screens, one primary action, diagram near the top"
```

---

### Task 7: ui-check proves the layout and the sign-in flow in a real browser

**Files:**
- Modify: `dashboard/scripts/ui-check.mjs`

**Interfaces:**
- Consumes the DOM hooks from Tasks 3, 5 and 6, and the `api token:` line that `serve` prints.

- [ ] **Step 1: Capture the token alongside the link**

Replace the `const link = await new Promise(...)` block with one that resolves both:

```js
const { link, token } = await new Promise((res, rej) => {
  let buf = "";
  const t = setTimeout(() => rej(new Error("serve printed no link in 30s:\n" + buf)), 30000);
  const on = (d) => {
    buf += d;
    const m = buf.match(/http:\/\/\S+#n=\S+/);
    const k = buf.match(/api token: (\S+)/);
    if (m && k) { clearTimeout(t); res({ link: m[0], token: k[1] }); }
  };
  serve.stdout.on("data", on); serve.stderr.on("data", on);
});
const base = link.split("#")[0];
```

- [ ] **Step 2: Fix the flaky 300ms check (U11)**

Replace:

```js
  check("phone: activity bar visible within 300ms of launching", await page.locator("[data-activity]").isVisible());
```

with:

```js
  const feedback = await page.locator("[data-activity], .task-box:not(.dag-empty)").first().isVisible();
  check("phone: within 300ms of launching, the activity bar or the new tasks are showing", feedback);
```

(The fake agent can finish in under 300ms. At that point the bar has already given way to the tasks, and that is correct feedback.)

- [ ] **Step 3: Add the layout checks**

Right after `await dagChecks(page, "phone");`, add:

```js
  const dagTopPhone = await page.evaluate(() => document.querySelector(".canvas-wrap").getBoundingClientRect().top + window.scrollY);
  // Was 667px before this plan. With a header, the demo notice and the Next
  // card still above it, the top half of the screen is the honest target.
  check("phone: the diagram starts in the top half of the screen", dagTopPhone < 0.5 * 844, `starts at ${Math.round(dagTopPhone)}px`);
  const primaries = await page.locator(".btn-filled").evaluateAll((els) => els.filter((b) => !b.disabled && b.offsetParent !== null).length);
  check("phone: at most one enabled primary button", primaries <= 1, `${primaries} found`);
  const toastOverDag = await page.evaluate(() => {
    const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    const boxes = [...document.querySelectorAll(".task-box, .spec-root-card, .arrow-label-bg")].map((e) => e.getBoundingClientRect());
    return [...document.querySelectorAll(".toast")].some((t) => boxes.some((b) => hit(t.getBoundingClientRect(), b)));
  });
  check("phone: no toast covers a box or an arrow label", !toastOverDag);
```

Right after `await dagChecks(page, "desktop (resized, no reload)");`, add:

```js
  const desk = await page.evaluate(() => {
    const dag = document.querySelector(".canvas-wrap").getBoundingClientRect();
    const next = document.querySelector("[data-next-step]").getBoundingClientRect();
    return { dagTop: dag.top + window.scrollY, nextBeside: next.left >= dag.right - 1 };
  });
  check("desktop: the diagram starts above 200px", desk.dagTop < 200, `starts at ${Math.round(desk.dagTop)}px`);
  check("desktop: the Next step sits beside the diagram, not above it", desk.nextBeside);
  const deskToast = await page.evaluate(() => {
    const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    const boxes = [...document.querySelectorAll(".task-box, .spec-root-card, .arrow-label-bg")].map((e) => e.getBoundingClientRect());
    return [...document.querySelectorAll(".toast")].some((t) => boxes.some((b) => hit(t.getBoundingClientRect(), b)));
  });
  check("desktop: no toast covers a box or an arrow label", !deskToast);
```

- [ ] **Step 4: Add the sign-in checks**

Before the `finally` of the main `try`, still inside it, add:

```js
  // --- sign-in: a visitor without the link
  const guest = await browser.newContext({ deviceScaleFactor: 2 });
  const g = await guest.newPage();
  await g.setViewportSize({ width: 390, height: 844 });
  await g.goto(base);
  await g.waitForTimeout(1000);
  await g.screenshot({ path: join(outDir, "05-phone-read-only.png"), fullPage: true });
  check("read-only: a one-line sign-in strip, no token form on the page", (await g.locator('[data-auth-strip="read_only"]').count()) === 1 && (await g.locator('input[aria-label="api token"]').count()) === 0);
  await g.getByText("Sign in", { exact: true }).click();
  await g.locator('input[aria-label="api token"]').fill("not-the-token");
  await g.getByText("Use token").click();
  await g.waitForSelector("[data-sign-in-error]", { timeout: 3000 }).catch(() => {});
  await g.screenshot({ path: join(outDir, "06-phone-sign-in-refused.png"), fullPage: true });
  check("sign-in: a wrong token says it was not accepted", /not accepted/.test(await g.locator("[data-sign-in-error]").innerText().catch(() => "")));
  await g.locator('input[aria-label="api token"]').fill(token);
  await g.getByText("Use token").click();
  await g.waitForTimeout(800);
  check("sign-in: the printed token signs in", (await g.locator("[data-auth-strip]").count()) === 0);
  await g.reload();
  await g.waitForTimeout(1000);
  check("sign-in: still signed in after a reload (cookie set from the pasted token)", (await g.locator("[data-auth-strip]").count()) === 0);
  await guest.close();

  // --- muvue link: a fresh one-time link from the running daemon
  const linked = muvue("link", repo).match(/http:\/\/\S+#n=\S+/)?.[0];
  check("muvue link: prints a fresh one-time link", !!linked);
  if (linked) {
    const fresh = await browser.newContext();
    const f = await fresh.newPage();
    await f.goto(linked);
    await f.waitForTimeout(1000);
    check("muvue link: the link signs a new browser in", (await f.locator("[data-auth-strip]").count()) === 0);
    await f.setViewportSize({ width: 1280, height: 800 });
    await f.screenshot({ path: join(outDir, "07-desktop-signed-in.png") });
    await fresh.close();
  }
```

- [ ] **Step 5: Run it and inspect every screenshot**

Run the same command as Task 5 Step 5, with the output directory set to `/tmp/muvue-ui-check-task7`.
Expected: every check passes. Report the final `N/N checks passed` line verbatim.

Then open each PNG, 01 through 07, and confirm the following. Write one line per screenshot in the report.
- **01:** the placeholder has no large empty band, and the diagram starts before mid-screen.
- **03:** agent tags are one line; the spec badge says "approved"; the toast does not cover anything in the diagram.
- **04:** on desktop, the Next card sits in a right-hand rail; the diagram starts near the top; the project title appears once in the main area; the sidebar shows "Projects" and 2-line-clamped nav items.
- **05:** a one-line "Read-only view." strip with a "Sign in" button, and nothing like the old token block.
- **06:** the sign-in sheet shows the refusal text in a red callout.
- **07:** signed in, with no strip.

- [ ] **Step 6: Commit**

```bash
git add dashboard/scripts/ui-check.mjs
git commit -m "ui-check: layout, one-primary-button, toast placement, sign-in and muvue link checks; fix the flaky 300ms check"
```

---

### Task 8: Docs

**Files:**
- Modify: `CHANGELOG.md` (the `## [Unreleased]` section), `docs/decisions.md` (append #173 and #174 after #172), `docs/threat-model.md` (control 6 item and a new item), `docs/protocol.md` (the "Daemon and API" section)

- [ ] **Step 1: CHANGELOG**

Under `## [Unreleased]` → `### Changed`, append:

```markdown
- Sign-in says why it fails. The daemon marks every refusal with `X-Muvue-Auth: missing|invalid|expired`, and an idle-expired session is now `401` (missing or wrong tokens stay `403`). The dashboard shows the exact reason: a one-line strip says whether the page is read-only, whether `serve` restarted, or whether the session expired, and a sign-in sheet explains a refused token.
- New `muvue link [PATH]` prints a fresh one-time dashboard link and the current api token from the running `muvue serve`, over a same-user local socket (`~/.muvue/daemon/<repo-hash>.sock`). If the 8-hour idle expiry has passed, it starts a new session with a new token, so you no longer need to restart `serve`, which also killed every link already handed out.
- A pasted api token now becomes the same HttpOnly session cookie the link sets (`POST /auth/session`), so a phone stays signed in across reloads.
- Dashboard layout:
  - On wide screens the Next step, activity and cards sit in a right-hand rail beside the diagram.
  - On phones the title is no longer repeated under the top bar, so the diagram starts near the top.
  - Only the next step's button is filled; a disabled button no longer looks like a faded primary.
  - The demo-agent notice is one line that expands.
  - Toasts stay off the diagram.
- Diagram boxes:
  - The agent line is a short one-line status ("waits for approval", "ready to run", "working now"), not a button-shaped chip.
  - The spec's badge says "approved" or "needs approval".
  - Box heights fit their content.
- Text colours pass WCAG AA (4.5:1) in light and dark mode, including buttons, links and every status pill; `dashboard/test/contrast.test.ts` checks it.
```

- [ ] **Step 2: Decisions**

Append to `docs/decisions.md`, after #172:

```markdown
173. **Recovering a session is `muvue link`, not a restart, and idle
    expiry is unchanged.** The 8-hour idle expiry (control 6) stays, and
    only a successful authenticated request resets it. There is no
    heartbeat, because an open tab polling in the background is not a
    person. What changes is recovery. The running daemon listens on a
    Unix socket beside its port file (0600 in a 0700 directory, peer uid
    checked on Linux), and `muvue link` asks it for a fresh one-time
    link. If the session has expired, that also mints a new token, so a
    token that leaked before the expiry stays dead. The socket answers
    only the user who could already read the daemon's stdout, so it adds
    no new reader of the token. Refusals now carry `X-Muvue-Auth` and
    expiry is `401`, so the page can say which problem it hit instead of
    one generic "missing or invalid". A pasted token is exchanged for
    the HttpOnly cookie (`POST /auth/session`), which is what the link
    already did. Cost if wrong: one more local surface (the socket) to
    keep same-user, and it is covered by `tests/test_control_socket.py`.

174. **Design pass: the diagram is the page.** A review against the
    seven Figma UI principles (hierarchy, progressive disclosure,
    consistency, contrast, accessibility, proximity, alignment) moved
    everything that is not the diagram into one line or a side rail:
    - the Next step, activity and cards go into a right rail at 1100px
      and wider;
    - there is one filled button per page;
    - the demo-agent notice becomes a one-line `<details>`;
    - toasts move off the diagram;
    - the sidebar no longer repeats the project title.

    Two changes reverse parts of the dashboard-clarity plan on purpose:
    - The per-box agent labels are shorter ("waits for approval" instead
      of "starts after the task list is approved"). The Next bar already
      says the project-wide step, and the long label wrapped to two
      lines on every box.
    - The approved spec shows "approved" instead of the node status
      "ready".

    Colour tokens were changed to pass WCAG AA, and a unit test computes
    the ratios from `tokens.css`. Cost if wrong: layout churn for anyone
    used to the old page; no behavior change.
```

- [ ] **Step 3: Threat model**

In `docs/threat-model.md`, after control 6's paragraph, add:

```markdown
   Recovering after expiry does not need a restart. `muvue link` asks
   the running daemon over a Unix socket at
   `~/.muvue/daemon/<repo-hash>.sock`. The socket is created 0600
   (umask set before `bind`, so it is never briefly wider), lives in the
   0700 daemon directory, and on Linux checks the peer's uid with
   `SO_PEERCRED`. It answers one request, `link`, with a new one-time
   nonce link and the current token. That is what `serve` already
   printed to the same user's terminal. An expired session is renewed
   with a new token, never revived with the old one (decision #173).
```

- [ ] **Step 4: Protocol**

In `docs/protocol.md`, in the "Daemon and API" section, after the paragraph ending "`api token: <token>` line, the token itself for API clients.", add:

```markdown
`serve` also prints a hint line naming `muvue link`, and listens on a
same-user Unix socket, `~/.muvue/daemon/<repo-hash>.sock`, removed on
exit. `muvue link [PATH]` asks that socket for a fresh
`dashboard (one-time link, works once): …` line and the current
`api token: …` line. If the session had idled out, the daemon first
mints a new token and prints a note saying so. Exit code 1 means no
daemon is running, or the one running predates the socket.

Auth refusals: `403` with `X-Muvue-Auth: missing` (no token) or
`invalid` (not this process's token), `401` with
`X-Muvue-Auth: expired` (this process's token after 8h idle). The same
header appears on `/auth/exchange` refusals. `POST /auth/session`, with
a valid token in the Authorization header, sets the HttpOnly
`muvue_session` cookie.
```

- [ ] **Step 5: Final full verification**

Run from the repo root: `uv run pytest -q && (cd dashboard && npm run typecheck && npm test)`.
Expected: all green. Report the counts.

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md docs/decisions.md docs/threat-model.md docs/protocol.md
git commit -m "Docs: muvue link, auth refusal reasons, design pass decisions"
```

---

## After merge (controller, with the user's go-ahead)

The fix reaches voxscore only after its daemon restarts: the running process has the old code and no control socket (see memory "verify the running process, not the file"). With the user's confirmation, do the following:
1. Stop PIDs 3038462 and 3038418.
2. From `/home/eugene/projects/voxscore`, start the daemon again with the same flags:
   `nohup uv run --project /home/eugene/projects/muvue muvue serve . --host 100.83.140.92 --port 8766 --i-know-this-is-exposed > <log> 2>&1 &`
3. Run `uv run --project /home/eugene/projects/muvue muvue link /home/eugene/projects/voxscore` and give the user the printed link to open on the phone.
4. Confirm `curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer <printed token>" http://100.83.140.92:8766/auth/check` prints `200`.
