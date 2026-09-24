"""FastAPI app factory: mirrors the CLI verbs 1:1 (plan section 4),
OpenAPI-documented for free by FastAPI, versioned via `protocol_version`.

Security (plan v4 section 8a -- "the largest defect in v3"): this daemon
exposes `POST /nodes/{id}/start?agent=X`, which launches an arbitrary
configured agent CLI subprocess. Every mutating endpoint -- agent verbs
(`start`/`done`/`fail`/`ask`/`wait`/`replan`/`comment`/`propose-revision`)
and human verbs alike (`approve`/`reject`/`ack`/`merge`/`close`/`pause`/
`resume`/`handoff`/`import`) -- is therefore gated the same way: POST
only, `Content-Type: application/json` only, and a valid session token
in the `Authorization` header or the `muvue_session` cookie (never a
query string). This is a deliberate widening from the P2/P2b design,
where agent verbs were unauthenticated on the theory that this is a
single local daemon with no multi-machine sync; v4 section 8a's own
framing of `start` as a remote-code-execution primitive makes that
theory untenable -- see docs/decisions.md #84 and docs/threat-model.md.

`SecurityMiddleware` below enforces Host validation (control 2), Origin
validation (control 3, *before* auth -- this blocks simple-request CSRF
that a CORS preflight alone would not) and the JSON-content-type half of
control 4, all before any route handler runs. No `Access-Control-*`
header is ever emitted (there is no CORS middleware in this app at all).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from muvue import core
from muvue.core.config import MuvueConfig
from muvue.core.daemon import SessionManager

STATIC_DIR = Path(__file__).parent / "static"
SESSION_COOKIE_NAME = "muvue_session"
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost"})


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


def create_app(
    repo_root: Path,
    config: MuvueConfig | None = None,
    *,
    session: SessionManager | None = None,
    port: int | None = None,
) -> FastAPI:
    repo_root = Path(repo_root)
    db_path = repo_root / ".muvue" / "muvue.db"
    config = config or core.load_config(repo_root / ".muvue" / "config.toml")
    session = session or SessionManager()

    app = FastAPI(
        title="muvue",
        version=str(config.protocol_version),
        description="muvue daemon API (plan section 4): mirrors CLI verbs 1:1.",
    )
    app.state.session = session
    app.state.port = port

    # ------------------------------------------------------------------
    # Security middleware (plan v4 section 8a, controls 2/3/4).
    # Runs before every route handler, including read-only GETs for
    # Host/Origin (a rebound/cross-origin request has no business
    # reading state either), and before auth for the Origin check
    # specifically (control 3: "rejected with 403 *before* auth").
    # ------------------------------------------------------------------

    def _host_ok(host_header: str) -> bool:
        if not host_header:
            return False
        hostname = host_header.split(":", 1)[0].strip().lower()
        if hostname not in _LOOPBACK_HOSTNAMES:
            return False
        if port is not None and ":" in host_header:
            try:
                header_port = int(host_header.split(":", 1)[1])
            except ValueError:
                return False
            if header_port != port:
                return False
        return True

    def _origin_ok(origin: str) -> bool:
        try:
            parts = urlsplit(origin)
        except ValueError:
            return False
        if parts.scheme != "http":
            return False
        hostname = (parts.hostname or "").lower()
        if hostname not in _LOOPBACK_HOSTNAMES:
            return False
        if port is not None:
            # Default the implicit port for a bare "http://127.0.0.1"
            # Origin (no explicit ":80") to 80, same as a real browser.
            return (parts.port or 80) == port
        return True

    @app.middleware("http")
    async def security_gate(request: Request, call_next):
        # Control 2: reject DNS rebinding -- validate Host before
        # anything else runs.
        if not _host_ok(request.headers.get("host", "")):
            return JSONResponse({"detail": "invalid Host header"}, status_code=403)

        # Control 3: reject cross-origin *before* auth. Absence of an
        # Origin header (same-origin navigation, non-browser clients
        # like curl/the CLI/VS Code extension) is not itself rejected --
        # only a foreign Origin is.
        origin = request.headers.get("origin")
        if origin is not None and not _origin_ok(origin):
            return JSONResponse({"detail": "invalid Origin header"}, status_code=403)

        # Control 4 (JSON-only half): every mutating request that
        # actually carries a body must use Content-Type: application/
        # json. A plain HTML <form> can only submit application/x-www-
        # form-urlencoded or multipart/form-data without JavaScript, so
        # this alone defeats classic HTML-form CSRF; JS-driven cross-
        # origin requests are already blocked by the Origin check above.
        # A body-less mutation (e.g. `POST /projects/{id}/pause`, no
        # payload) has no attacker-controlled content to smuggle via a
        # form submission in the first place, so it's exempt from the
        # content-type check itself -- it still needs a valid session
        # token, enforced separately by each route's `_require_session`.
        if request.method in _MUTATING_METHODS:
            content_length = request.headers.get("content-length")
            has_body = content_length not in (None, "0")
            if has_body:
                content_type = request.headers.get("content-type", "")
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type != "application/json":
                    return JSONResponse(
                        {"detail": "mutating requests with a body must use "
                                   "Content-Type: application/json"},
                        status_code=403,
                    )

        response = await call_next(request)
        # Belt-and-suspenders: this app never adds CORS middleware, but
        # assert the invariant at the response boundary too so a future
        # accidental `add_middleware(CORSMiddleware, ...)` fails loudly
        # in the daemon-security tests rather than silently reopening
        # control 3.
        for header in list(response.headers.keys()):
            if header.lower().startswith("access-control-"):
                del response.headers[header]
        return response

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
        if not session.verify_and_touch(token):
            raise HTTPException(status_code=403, detail="missing or invalid session token")

    def _handle_core_error(exc: Exception) -> None:
        if isinstance(exc, LookupError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(
            exc,
            (
                core.nodes.NodeError,
                core.gates.GateError,
                core.asks.AskError,
                core.state_machine.InvalidTransition,
                core.state_machine.NotLeaseOwner,
                core.merge.MergeError,
                core.close.CloseError,
                core.imports.ImportError_,
            ),
        ):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise

    @contextmanager
    def _conn() -> Iterator[sqlite3.Connection]:
        conn = core.db.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Auth exchange (plan v4 section 8a, control 5): the dashboard's own
    # JS reads the one-time URL fragment (never sent over HTTP, so it
    # never reaches this endpoint or any server log via the URL itself)
    # and POSTs its value here as JSON. On a match, mints an HttpOnly,
    # SameSite=Strict cookie carrying that same token -- `Secure` is
    # omitted because loopback HTTP has no TLS to require it; SameSite
    # =Strict is what actually matters (a cross-site request, even a
    # "simple" same-site-looking one, never carries this cookie).
    # `HttpOnly` means page JS (or an XSS payload) can never read the
    # cookie back out. Nothing here is ever written to disk.
    # ------------------------------------------------------------------

    @app.post("/auth/exchange")
    def exchange_token(token: str = Body(..., embed=True)) -> JSONResponse:
        if not session.verify_and_touch(token):
            raise HTTPException(status_code=403, detail="invalid token")
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            SESSION_COOKIE_NAME,
            token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return resp

    # ------------------------------------------------------------------
    # Ops / dashboard
    # ------------------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "protocol_version": config.protocol_version}

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        index = STATIC_DIR / "index.html"
        return index.read_text()

    @app.get("/events/stream")
    async def events_stream():
        """SSE: polls `PRAGMA data_version` (plan section 8) and pushes a
        message whenever the DB has changed since the last poll -- no
        in-memory daemon state, restart-safe."""

        async def gen():
            # No `request.is_disconnected()` poll here: some ASGI test
            # transports never deliver the disconnect message to a still
            # -open GET stream's receive(), which would hang this
            # coroutine forever. When the client actually goes away,
            # Starlette closes this async generator (GeneratorExit) on its
            # own; the 600-iteration cap below is the real backstop for
            # any client that never explicitly disconnects.
            #
            # One connection held for the life of this stream, not one
            # opened per poll: `PRAGMA data_version` only reliably reflects
            # writes committed by *other* connections when read from a
            # single long-lived connection (see docs/decisions.md) --
            # reopening a fresh connection every poll does not increment
            # `data_version` reliably. This does not reintroduce
            # in-memory daemon state (plan section 1): the connection
            # holds no application state, only SQLite's own read snapshot,
            # and losing it costs nothing on restart.
            last_version: int | None = None
            iterations = 0
            # Two connections on purpose: `PRAGMA data_version` only
            # reliably reflects writes committed by *other* connections
            # (see this function's docstring/docs/decisions.md) -- a
            # drain-caused write issued on `conn` itself would never
            # show up in `conn`'s own subsequent `data_version` read.
            # `drain_conn` is the dedicated write connection for the
            # periodic drain task below.
            with _conn() as conn, _conn() as drain_conn:
                while iterations < 600:  # ~60s safety cap
                    version = conn.execute("PRAGMA data_version").fetchone()[0]
                    if version != last_version:
                        last_version = version
                        yield f"data: {json.dumps({'data_version': version})}\n\n"
                    # v4 section 4a: "the daemon drains continuously" --
                    # this SSE loop is the only continuous, restart-safe
                    # loop the daemon runs today (P2a's dedicated daemon
                    # process/task doesn't exist yet), so the bounded
                    # drain rides along as another periodic task here
                    # rather than a new competing loop. Best-effort: a
                    # drain failure must never break the SSE stream.
                    try:
                        core.hooks.drain_queue(drain_conn, repo_root)
                    except Exception:
                        pass
                    await asyncio.sleep(0.1)
                    iterations += 1

        return StreamingResponse(gen(), media_type="text/event-stream")

    # ------------------------------------------------------------------
    # Inbox / KPIs (plan section 8 dashboard views)
    # ------------------------------------------------------------------

    @app.get("/inbox")
    def inbox() -> dict:
        with _conn() as conn:
            questions = _rows_to_list(
                conn.execute("SELECT * FROM questions WHERE status = 'open'").fetchall()
            )
            review = _rows_to_list(
                conn.execute(
                    "SELECT * FROM nodes WHERE status = 'review' AND deleted_at IS NULL"
                ).fetchall()
            )
            blocked = _rows_to_list(
                conn.execute(
                    "SELECT * FROM nodes WHERE status = 'blocked' AND deleted_at IS NULL"
                ).fetchall()
            )
            # P7 drift loop items 2/4: unattributed commits and audit's
            # drafted diffs are unacked `events` rows (same "unacked ==
            # still in the inbox" convention `/events/{id}/ack` already
            # established) rather than a new dedicated table.
            signals = _rows_to_list(
                conn.execute(
                    "SELECT * FROM events WHERE type = 'inbox.unattributed_commit' "
                    "AND acked_at IS NULL ORDER BY id"
                ).fetchall()
            )
            audit_items = _rows_to_list(
                conn.execute(
                    "SELECT * FROM events WHERE type = 'inbox.audit_drift_signal' "
                    "AND acked_at IS NULL ORDER BY id"
                ).fetchall()
            )
        return {
            "questions": questions,
            "review": review,
            "blocked": blocked,
            "signals": signals,
            "audit_items": audit_items,
        }

    @app.get("/kpis")
    def kpis() -> dict:
        """Rubber-stamp rate is real: it's a ratio over events this phase
        already logs (`metric.rubber_stamp` vs total `node.done`
        approvals via `nodes.approve_review`). `tokens_per_node` /
        `spend_vs_budget` are real as of P5: `node_usage` is now populated
        by `core.runner`, so these read it directly instead of stubbing.
        `drift_pct` is real as of P7: `core.drift.drift_pct` (plan
        section 9 item 5, "% components verified within last K
        commits")."""
        with _conn() as conn:
            total_reviewed = conn.execute(
                "SELECT COUNT(*) c FROM events WHERE type = 'node.done'"
            ).fetchone()["c"]
            rubber_stamps = conn.execute(
                "SELECT COUNT(*) c FROM events WHERE type = 'metric.rubber_stamp'"
            ).fetchone()["c"]
            total_tokens = conn.execute(
                "SELECT COALESCE(SUM(in_tokens + out_tokens), 0) c FROM node_usage"
            ).fetchone()["c"]
            nodes_with_usage = conn.execute(
                "SELECT COUNT(DISTINCT node_id) c FROM node_usage"
            ).fetchone()["c"]
            budget = core.runner.budget_state(conn, config)
            drift = core.drift.drift_pct(conn, repo_root)
        rubber_stamp_rate = (rubber_stamps / total_reviewed) if total_reviewed else 0.0
        tokens_per_node = (total_tokens / nodes_with_usage) if nodes_with_usage else 0.0
        return {
            "drift_pct": drift,
            "rubber_stamp_rate": rubber_stamp_rate,
            "tokens_per_node": tokens_per_node,
            "spend_vs_budget": budget["pct"],
        }

    # ------------------------------------------------------------------
    # Agent verbs (plan section 4) -- CLI-equivalent, read endpoints
    # unauthenticated, mutating ones session-gated (see module docstring).
    # ------------------------------------------------------------------

    @app.get("/projects")
    def list_projects() -> list[dict]:
        with _conn() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY id").fetchall()
        return _rows_to_list(rows)

    @app.get("/projects/{project_id}")
    def show_project(project_id: int) -> dict:
        with _conn() as conn:
            try:
                row = core.projects.get_project(conn, project_id)
            except LookupError as e:
                _handle_core_error(e)
        return dict(row)

    @app.get("/projects/{project_id}/revisions")
    def list_revisions(project_id: int) -> list[dict]:
        """Plan-revision history (plan section 8 dashboard view)."""
        with _conn() as conn:
            revisions = conn.execute(
                "SELECT * FROM plan_revisions WHERE project_id = ? ORDER BY n",
                (project_id,),
            ).fetchall()
            out = []
            for rev in revisions:
                diff = core.revisions.diff_revision(conn, project_id, rev["n"])
                out.append({**dict(rev), "diff": diff})
        return out

    @app.get("/events")
    def list_events(
        since_id: int = 0,
        project_id: int | None = None,
        limit: int = 200,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict]:
        """Event timeline (plan section 8 dashboard view). `since`/`until`
        (P7, timeline scrubber): an optional inclusive `events.ts` window
        (`strftime('%Y-%m-%dT%H:%M:%fZ', ...)` strings, the same format
        every `ts` column already stores/sorts lexicographically as) --
        independent of `since_id`, which is the pre-existing "poll for
        anything newer than this row id" cursor, not a time filter."""
        with _conn() as conn:
            clauses = ["id > ?"]
            params: list = [since_id]
            if project_id is not None:
                clauses.append("project_id = ?")
                params.append(project_id)
            if since is not None:
                clauses.append("ts >= ?")
                params.append(since)
            if until is not None:
                clauses.append("ts <= ?")
                params.append(until)
            params.append(limit)
            rows = conn.execute(
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY id ASC LIMIT ?",
                params,
            ).fetchall()
        return _rows_to_list(rows)

    @app.get("/nodes")
    def list_nodes(project_id: int | None = None) -> list[dict]:
        with _conn() as conn:
            if project_id is not None:
                rows = conn.execute(
                    "SELECT * FROM nodes WHERE project_id = ? AND deleted_at IS NULL",
                    (project_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM nodes WHERE deleted_at IS NULL").fetchall()
        return _rows_to_list(rows)

    @app.get("/nodes/{node_id}")
    def show_node(node_id: int) -> dict:
        with _conn() as conn:
            try:
                node = core.nodes.get_node(conn, node_id)
            except LookupError as e:
                _handle_core_error(e)
            notes = _rows_to_list(
                conn.execute(
                    "SELECT * FROM notes WHERE node_id = ? ORDER BY id", (node_id,)
                ).fetchall()
            )
            commits = _rows_to_list(
                conn.execute(
                    "SELECT * FROM node_commits WHERE node_id = ?", (node_id,)
                ).fetchall()
            )
            touches = [
                r["path_glob"]
                for r in conn.execute(
                    "SELECT path_glob FROM predicted_touches WHERE node_id = ?", (node_id,)
                ).fetchall()
            ]
        return {
            "node": dict(node),
            "notes": notes,
            "commits": commits,
            "predicted_touches": touches,
        }

    @app.get("/nodes/{node_id}/diff")
    def node_diff(node_id: int) -> dict:
        """No real diff capture exists yet (no git integration until
        P3's hooks/structure layer) -- stub per the P2 prompt: returns
        what P0/P1 already track (committed files, predicted touches)."""
        with _conn() as conn:
            try:
                core.nodes.get_node(conn, node_id)
            except LookupError as e:
                _handle_core_error(e)
            commits = _rows_to_list(
                conn.execute(
                    "SELECT * FROM node_commits WHERE node_id = ?", (node_id,)
                ).fetchall()
            )
        return {"node_id": node_id, "commits": commits, "diff": None}

    @app.get("/nodes/{node_id}/logs")
    def node_logs(node_id: int) -> StreamingResponse:
        """Stub stream (plan section 4): no live agent process exists
        until P5's runner, so this streams the node's own event history
        as newline-delimited JSON -- real content, just not a live tail."""

        def gen():
            with _conn() as conn:
                try:
                    core.nodes.get_node(conn, node_id)
                except LookupError as e:
                    _handle_core_error(e)
                rows = conn.execute(
                    "SELECT ts, actor, type, payload FROM events WHERE node_id = ? "
                    "ORDER BY id ASC",
                    (node_id,),
                ).fetchall()
            for r in rows:
                yield json.dumps(dict(r)) + "\n"

        return StreamingResponse(gen(), media_type="application/x-ndjson")

    @app.post("/nodes/{node_id}/start")
    def start_node(
        node_id: int,
        request: Request,
        owner: str = Body(...),
        agent: str | None = None,
        request_id: str | None = Body(default=None),
    ) -> dict:
        """`POST /nodes/{id}/start?agent=X` (plan section 4): the driver
        that would actually spawn agent `X` ships in P5 (runner/drivers).
        Here `agent` is recorded on the start event but nothing is
        spawned -- documented stub per the P2 prompt.

        Session-gated (v4 section 8a, docs/decisions.md #84): this is
        the endpoint the plan itself names as the RCE surface -- "a
        daemon exposing that on localhost with a token stored in a
        world-readable file is a remote-code-execution surface reachable
        from any web page the user has open" -- so it is never left
        unauthenticated even though `start` is nominally an agent verb.
        """
        _require_session(request)
        with _conn() as conn:
            try:
                if agent:
                    core.events.record_event(
                        conn,
                        project_id=core.nodes.get_node(conn, node_id)["project_id"],
                        node_id=node_id,
                        actor="human",
                        actor_evidence="dashboard_token",
                        type_="node.agent_requested",
                        payload={"agent": agent},
                    )
                    conn.commit()
                result = core.nodes.start(
                    conn, node_id, owner=owner, request_id=request_id,
                    lease_minutes=config.planning.lease_minutes, actor_evidence="dashboard_token",
                    config=config, repo_root=repo_root,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/done")
    def done_node(
        node_id: int,
        request: Request,
        owner: str = Body(...),
        summary: str | None = Body(default=None),
        request_id: str | None = Body(default=None),
        version: int | None = Body(default=None),
        run_checks: bool = Body(
            default=False,
            description="actually run config.checks.test for auto-criteria nodes "
            "(opt-in -- see docs/decisions.md #38); default preserves risk-tier-only gating",
        ),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.nodes.done(
                    conn, node_id, owner=owner, summary=summary, request_id=request_id,
                    config=config, expected_version=version,
                    run_checks=core.review.default_run_checks if run_checks else None,
                    cwd=str(repo_root), actor_evidence="dashboard_token",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/fail")
    def fail_node(
        node_id: int,
        request: Request,
        owner: str = Body(...),
        lesson: str = Body(...),
        trigger: str = Body(default=""),
        do_instead: str = Body(default=""),
        scope: str = Body(default=""),
        request_id: str | None = Body(default=None),
        version: int | None = Body(default=None),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.nodes.fail(
                    conn, node_id, owner=owner, lesson=lesson, trigger=trigger,
                    do_instead=do_instead, scope=scope, request_id=request_id,
                    expected_version=version, actor_evidence="dashboard_token",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/ask")
    def ask_node(
        node_id: int,
        request: Request,
        question: str = Body(...),
        default: str | None = Body(default=None),
        request_id: str | None = Body(default=None),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.asks.ask(
                    conn, node_id, question=question, default=default, request_id=request_id,
                    actor_evidence="dashboard_token",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/questions/{question_id}/answer")
    def answer_question(
        question_id: int,
        request: Request,
        text: str = Body(..., embed=True),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.asks.answer(conn, question_id, text=text, actor_evidence="dashboard_token")
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/questions/{question_id}/wait")
    def wait_question(
        question_id: int, request: Request, default_ok: bool = Body(default=False, embed=True)
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.asks.wait(
                    conn, question_id,
                    timeout_minutes=config.planning.ask_timeout_minutes,
                    default_ok=default_ok, actor_evidence="dashboard_token",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/replan")
    def replan_node(
        node_id: int, request: Request, title: str = Body(...), body_md: str = Body(default="")
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.revisions.replan_add_subtask(
                    conn, parent_task_id=node_id, title=title, body_md=body_md,
                    actor_evidence="dashboard_token",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/comment")
    def comment_on_node(
        node_id: int, request: Request, text: str = Body(...), pinned: bool = Body(default=False)
    ) -> dict:
        """Spec-document inline comments (plan section 8: "spec document
        view with inline comments (stored as `feedback` events)") -- reuse
        `nodes.add_note`'s existing `feedback` note kind/dedupe machinery
        rather than adding a parallel comment table."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.nodes.add_note(
                    conn, node_id, kind="feedback", text=text, actor="human", pinned=pinned,
                    actor_evidence="dashboard_token",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/projects/{project_id}/propose-revision")
    def propose_revision(
        project_id: int, request: Request, node_ids: list[int] = Body(..., embed=True)
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.revisions.propose_revision(conn, project_id, node_ids, actor_evidence="dashboard_token")
            except Exception as e:
                _handle_core_error(e)
        return result

    # ------------------------------------------------------------------
    # Human verbs (plan section 4): gated by the in-memory session token.
    # ------------------------------------------------------------------

    @app.post("/nodes/{node_id}/approve")
    def approve_node(
        node_id: int,
        request: Request,
        target: str = Body(default="node"),
        n: int | None = Body(default=None),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                if target == "spec":
                    result = core.gates.approve_spec(conn, node_id, actor_evidence="dashboard_token")
                elif target == "gate2":
                    result = core.gates.approve_gate2(conn, node_id, config=config, actor_evidence="dashboard_token")
                elif target == "revision":
                    if n is None:
                        raise HTTPException(status_code=422, detail="revision approval needs n")
                    result = core.revisions.approve_revision(conn, node_id, n, config=config, actor_evidence="dashboard_token")
                elif target == "review":
                    result = core.nodes.approve_review(conn, node_id, actor_evidence="dashboard_token")
                else:
                    result = core.gates.approve_node(conn, node_id, config=config, actor_evidence="dashboard_token")
            except HTTPException:
                raise
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/reject")
    def reject_node(
        node_id: int, request: Request, feedback: str = Body(..., embed=True)
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.nodes.reject_review(conn, node_id, feedback=feedback, actor_evidence="dashboard_token")
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/events/{event_id}/ack")
    def ack_event(event_id: int, request: Request) -> dict:
        _require_session(request)
        with _conn() as conn:
            row = core.events.ack_event(conn, event_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
        return dict(row)

    @app.post("/nodes/{node_id}/merge")
    def merge_node(node_id: int, request: Request, pr: bool = False) -> dict:
        """Plan section 6 "Merging" (P5): attempt to merge this node's
        strict-mode branch onto the airlock's main. Light-mode / never
        strict-started nodes are a documented no-op (`core.merge`).
        `?pr=true` (P6, plan section 4/11): also include a generated PR
        description body (`core.pr.generate_pr_body`) in the response --
        no real `gh pr create` call, no network access here."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.merge.attempt_merge(conn, node_id, repo_root)  # merge.py: actor_evidence hardcoded "subprocess" internally
                if pr:
                    result["pr_body"] = core.pr.generate_pr_body(conn, node_id)
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/handoff")
    def handoff_node(node_id: int, request: Request, to: str = Body(..., embed=True)) -> dict:
        """Plan section 6 "Handoff" (P5): reassign the node's lease to `to`
        so a different driver can resume purely from DB state."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.nodes.handoff(
                    conn, node_id, new_owner=to, lease_minutes=config.planning.lease_minutes,
                    actor_evidence="dashboard_token",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/import")
    def import_(
        request: Request,
        node_id: int = Body(...),
        issue_number: int = Body(...),
        data: dict = Body(...),
    ) -> dict:
        """Plan section 4/11 (P6): link `node_id` to `github#issue_number`
        in `external_refs`. No live GitHub API call here (plan working
        rule 2) -- `data` is the issue payload the caller already has
        (see `core/imports.py`)."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.imports.import_github_issue(
                    conn, node_id, issue_number, data=data, actor="human",
                    actor_evidence="dashboard_token",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.get("/projects/{project_id}/close-preview")
    def close_preview(project_id: int, request: Request) -> dict:
        """P6: the dashboard-reviewable structure diff `close` would
        propose, without committing anything (`core.close.preview_close`).
        A read-only endpoint, but still session-gated since it's the
        pre-close review surface for a human verb, matching P2/P2b's
        original choice to gate it."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.close.preview_close(conn, project_id)
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/projects/{project_id}/close")
    def close_project(project_id: int, request: Request) -> dict:
        """Plan section 9 (P6): commit the project's structure diff
        (`.muvue/components.json`/`.muvue/decisions.json`, committed on
        `main`), flip the project to `closed`, and export its event
        history (`core.close.close_project`, `confirm=True`)."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.close.close_project(
                    conn, project_id, repo_root, actor="human", confirm=True,
                    actor_evidence="dashboard_token",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/projects/{project_id}/pause")
    def pause_project(project_id: int, request: Request) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                row = core.projects.set_phase(conn, project_id, "paused")
            except Exception as e:
                _handle_core_error(e)
        return dict(row)

    @app.post("/projects/{project_id}/resume")
    def resume_project(project_id: int, request: Request) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                row = core.projects.set_phase(conn, project_id, "executing")
            except Exception as e:
                _handle_core_error(e)
        return dict(row)

    return app
