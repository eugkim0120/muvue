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
import subprocess
import sys
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

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
    drain_interval_s: float = 0.5,
) -> FastAPI:
    repo_root = Path(repo_root)
    db_path = repo_root / ".muvue" / "muvue.db"
    config = config or core.load_config(repo_root / ".muvue" / "config.toml")
    session = session or SessionManager()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # v4 section 4a: "the daemon drains continuously". One background
        # task for the daemon's lifetime, independent of any dashboard
        # connection.
        task = asyncio.create_task(_drain_forever())
        try:
            yield
        finally:
            task.cancel()

    def _drain_once() -> None:
        with _conn() as conn:
            core.hooks.drain_queue(conn, repo_root)
            core.notify.flush(conn, config)

    async def _drain_forever() -> None:
        while True:
            try:
                await asyncio.to_thread(_drain_once)
            except Exception as e:  # keep the daemon up; the spool stays for next time
                print(f"muvue: hook queue drain failed: {e!r}", flush=True)
            await asyncio.sleep(drain_interval_s)

    app = FastAPI(
        lifespan=lifespan,
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
        if origin in config.daemon.allowed_origins:
            return True
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

        # Control 4 (JSON-only half): every mutating request must use
        # Content-Type: application/json, with or without a body. A
        # plain HTML <form> can only submit form-encoded or multipart
        # bodies, and a cross-site `fetch` with a JSON content type
        # needs a preflight this app never answers, so this defeats
        # form CSRF. Body-less POSTs such as `pause` are included: an
        # empty form submission to them would otherwise carry the
        # session cookie (decision #143).
        if request.method in _MUTATING_METHODS:
            content_type = request.headers.get("content-type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                return JSONResponse(
                    {"detail": "mutating requests must use Content-Type: application/json"},
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

    def _request_id(request: Request) -> str | None:
        """Plan section 4: every mutating verb accepts a request id. Verbs
        whose body already carries `request_id` (start/done/fail/ask) read
        it there; the rest take the `X-Request-Id` header so body-less
        POSTs can be deduped too."""
        return request.headers.get("x-request-id") or None

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
                core.actor.HumanOnly,
                ValueError,
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
    # Auth exchange (plan v4 section 8a, control 5). `serve` prints a
    # dashboard link whose `#n=` fragment is a single-use nonce, not the
    # session token. The fragment never goes over HTTP; the page's own
    # JS POSTs it here and gets an HttpOnly, SameSite=Strict cookie
    # carrying the token. `Secure` is omitted because loopback HTTP has
    # no TLS. Nothing here is written to disk.
    #
    # `"header": true` also returns the token in the body. The
    # dashboard asks for it only when it runs inside an iframe (the VS
    # Code webview), where the page is a cross-site subframe and the
    # SameSite=Strict cookie is never sent. The page keeps it in
    # memory (decision #144).
    # ------------------------------------------------------------------

    @app.post("/auth/exchange")
    def exchange_nonce(
        nonce: str = Body(..., embed=True), header: bool = Body(default=False, embed=True),
    ) -> JSONResponse:
        if not session.consume_nonce(nonce):
            raise HTTPException(status_code=403, detail="invalid or already used nonce")
        resp = JSONResponse({"ok": True, "token": session.token} if header else {"ok": True})
        resp.set_cookie(
            SESSION_COOKIE_NAME,
            session.token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return resp

    @app.post("/auth/nonce")
    def mint_nonce(request: Request) -> dict:
        """A fresh one-time dashboard nonce for a caller that already
        holds the token (the VS Code extension opening its webview)."""
        _require_session(request)
        return {"nonce": session.mint_nonce()}

    @app.get("/auth/check")
    def auth_check(request: Request) -> dict:
        """200 when the request carries a live session, else 403. The
        dashboard uses it to tell whether its cookie is being sent."""
        _require_session(request)
        return {"ok": True}

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
            with _conn() as conn:
                while iterations < 600:  # ~60s safety cap
                    version = conn.execute("PRAGMA data_version").fetchone()[0]
                    if version != last_version:
                        last_version = version
                        yield f"data: {json.dumps({'data_version': version})}\n\n"
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
                core.db.query_all(conn, "SELECT * FROM questions WHERE status = 'open'")
            )
            review = _rows_to_list(
                core.db.query_all(
                    conn,
                    "SELECT * FROM nodes WHERE status = 'review' AND deleted_at IS NULL",
                )
            )
            blocked = _rows_to_list(
                core.db.query_all(
                    conn,
                    "SELECT * FROM nodes WHERE status = 'blocked' AND deleted_at IS NULL",
                )
            )
            awaiting_approval = _rows_to_list(
                core.db.query_all(
                    conn,
                    "SELECT * FROM nodes WHERE status = 'awaiting_approval' AND deleted_at IS NULL",
                )
            )
            # P7 drift loop items 2/4: unattributed commits and audit's
            # drafted diffs are unacked `events` rows (same "unacked ==
            # still in the inbox" convention `/events/{id}/ack` already
            # established) rather than a new dedicated table.
            signals = _rows_to_list(
                core.db.query_all(
                    conn,
                    "SELECT * FROM events WHERE type = 'inbox.unattributed_commit' "
                    "AND acked_at IS NULL ORDER BY id",
                )
            )
            audit_items = _rows_to_list(
                core.db.query_all(
                    conn,
                    "SELECT * FROM events WHERE type = 'inbox.audit_drift_signal' "
                    "AND acked_at IS NULL ORDER BY id",
                )
            )
            # v4 section 7 / changelog item 10: the general (not
            # component-anchor-scoped) unattributed-commit detection that
            # replaced PreToolUse's removed git-commit-trailer string
            # match -- see core.drift.flag_general_unattributed_commit
            # and docs/decisions.md #100. Kept as its own list rather than
            # folded into `signals` (which stays `inbox.unattributed_commit`
            # only, P7's narrower anchored-component signal) since the two
            # have different firing conditions and a client may want to
            # distinguish them.
            unattributed_commits = _rows_to_list(
                core.db.query_all(
                    conn,
                    "SELECT * FROM events WHERE type = 'unattributed_commit' "
                    "AND acked_at IS NULL ORDER BY id",
                )
            )
            unverified = core.queries.unverified_external(conn)
            structure_updates = _rows_to_list(
                core.db.query_all(
                    conn,
                    "SELECT * FROM events WHERE type = 'inbox.structure_update_ready' "
                    "AND acked_at IS NULL ORDER BY id",
                )
            )
        return {
            "questions": questions,
            "review": review,
            "unverified_external": unverified,
            "structure_updates": structure_updates,
            "blocked": blocked,
            "awaiting_approval": awaiting_approval,
            "signals": signals,
            "audit_items": audit_items,
            "unattributed_commits": unattributed_commits,
        }

    @app.get("/kpis")
    def kpis() -> dict:
        """Rubber-stamp rate: fast approvals over all timed approvals, both
        counted on medium/high nodes only (`nodes.log_approval_timing`,
        v4 section 5). `tokens_per_node` /
        `spend_vs_budget` are real as of P5: `node_usage` is now populated
        by `core.runner`, so these read it directly instead of stubbing.
        `drift_pct` is real as of P7: `core.drift.drift_pct` (plan
        section 9 item 5, "% components verified within last K
        commits")."""
        with _conn() as conn:
            total_reviewed = core.db.query_one(
                conn,
                "SELECT COUNT(*) c FROM events WHERE type = 'metric.approval_timed'",
            )["c"]
            rubber_stamps = core.db.query_one(
                conn,
                "SELECT COUNT(*) c FROM events WHERE type = 'metric.rubber_stamp'",
            )["c"]
            total_tokens = core.db.query_one(
                conn,
                "SELECT COALESCE(SUM(in_tokens + out_tokens), 0) c FROM node_usage",
            )["c"]
            nodes_with_usage = core.db.query_one(
                conn,
                "SELECT COUNT(DISTINCT node_id) c FROM node_usage",
            )["c"]
            # v4 section 2: no single global budget number any more --
            # each driver has its own. `spend_vs_budget` becomes the
            # worst-case (max) pct across every driver that has a
            # `[agents.<x>.budget]` configured; 0.0 if none do (see
            # docs/decisions.md).
            driver_states = core.runner.driver_budget_states(conn, config)
            drift = core.drift.drift_pct(conn, repo_root)
            touch_drift = core.queries.touch_drift(conn)
        rubber_stamp_rate = (rubber_stamps / total_reviewed) if total_reviewed else 0.0
        tokens_per_node = (total_tokens / nodes_with_usage) if nodes_with_usage else 0.0
        spend_vs_budget = max((s["pct"] for s in driver_states.values()), default=0.0)
        return {
            "drift_pct": drift,
            "touch_drift": touch_drift,
            "rubber_stamp_rate": rubber_stamp_rate,
            "approvals_timed": total_reviewed,
            "rubber_stamps": rubber_stamps,
            "tokens_per_node": tokens_per_node,
            "spend_vs_budget": spend_vs_budget,
            "spend_by_driver": driver_states,
        }

    # ------------------------------------------------------------------
    # Agent verbs (plan section 4) -- CLI-equivalent, read endpoints
    # unauthenticated, mutating ones session-gated (see module docstring).
    # ------------------------------------------------------------------

    @app.get("/projects")
    def list_projects() -> list[dict]:
        with _conn() as conn:
            rows = core.db.query_all(conn, "SELECT * FROM projects ORDER BY id")
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
            revisions = core.db.query_all(
                conn,
                "SELECT * FROM plan_revisions WHERE project_id = ? ORDER BY n",
                (project_id,),
            )
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
            # With a cursor, page forward from it; without one, return the
            # newest `limit` events (still oldest-first in the response).
            order = "ASC" if since_id > 0 else "DESC"
            rows = core.db.query_all(
                conn,
                f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY id {order} LIMIT ?",
                params,
            )
        if order == "DESC":
            rows = list(reversed(rows))
        return _rows_to_list(rows)

    @app.get("/nodes")
    def list_nodes(project_id: int | None = None) -> list[dict]:
        with _conn() as conn:
            if project_id is not None:
                rows = core.db.query_all(
                    conn,
                    "SELECT * FROM nodes WHERE project_id = ? AND deleted_at IS NULL",
                    (project_id,),
                )
            else:
                rows = core.db.query_all(conn, "SELECT * FROM nodes WHERE deleted_at IS NULL")
        return _rows_to_list(rows)

    @app.get("/graph")
    def graph(project_id: int | None = None) -> dict:
        """The tree/DAG view (plan section 8): nodes plus parent->child
        and dependency->dependent edges."""
        with _conn() as conn:
            where, params = ("AND project_id = ?", (project_id,)) if project_id is not None else ("", ())
            rows = core.db.query_all(
                conn,
                "SELECT id, project_id, parent_id, kind, title, status, risk_tier, owner FROM nodes "
                f"WHERE deleted_at IS NULL {where} ORDER BY id",
                params,
            )
            ids = {r["id"] for r in rows}
            edges = [
                {"from": r["parent_id"], "to": r["id"], "kind": "parent"}
                for r in rows if r["parent_id"] in ids
            ]
            edges += [
                {"from": d["depends_on"], "to": d["node_id"], "kind": "dep"}
                for d in core.db.query_all(conn, "SELECT node_id, depends_on FROM deps ORDER BY rowid")
                if d["node_id"] in ids and d["depends_on"] in ids
            ]
        return {"nodes": _rows_to_list(rows), "edges": edges}

    @app.get("/brief")
    def get_brief(
        node_id: int, budget: int = core.brief.DEFAULT_BUDGET_TOKENS, since: int | None = None
    ) -> dict:
        """The agent verb `brief` (plan section 4): `{"text", "cursor",
        "tokens", "omitted"}`."""
        with _conn() as conn:
            try:
                return core.brief.render_brief(conn, node_id, budget=budget, since=since)
            except LookupError as e:
                _handle_core_error(e)

    @app.get("/status")
    def get_status(project_id: int | None = None) -> dict:
        with _conn() as conn:
            return core.queries.status_summary(conn, project_id)

    @app.get("/nodes/{node_id}")
    def show_node(node_id: int) -> dict:
        with _conn() as conn:
            try:
                return core.queries.show_node(conn, node_id)
            except LookupError as e:
                _handle_core_error(e)

    @app.get("/nodes/{node_id}/diff")
    def node_diff(node_id: int) -> dict:
        """The node's real diff: its commits' patches, or its worktree
        against the branch point (`core.queries.node_diff`)."""
        with _conn() as conn:
            try:
                return core.queries.node_diff(conn, node_id, repo_root)
            except LookupError as e:
                _handle_core_error(e)

    @app.get("/nodes/{node_id}/logs", response_class=PlainTextResponse)
    def node_logs(node_id: int, lines: int = 200) -> str:
        """Tail of the node's driver output (`.muvue/logs/`, written by
        the runner). Empty until a runner has driven the node."""
        with _conn() as conn:
            try:
                core.nodes.get_node(conn, node_id)
            except LookupError as e:
                _handle_core_error(e)
        return core.queries.tail_log(repo_root, node_id, min(max(lines, 1), 5000))

    @app.post("/nodes/{node_id}/start")
    def start_node(
        node_id: int,
        request: Request,
        owner: str | None = Body(default=None),
        agent: str | None = None,
        request_id: str | None = Body(default=None),
    ) -> dict:
        """`POST /nodes/{id}/start` takes the lease for `owner`. With
        `?agent=X` (plan section 4) it instead launches `muvue run --node
        ID --agent X` as a detached subprocess. That runner takes the
        lease itself, registers in `.muvue/runners/` so `pause` can stop
        it, and logs to `.muvue/logs/run-<id>.log`.

        Session-gated (v4 section 8a, docs/decisions.md #84): this is
        the endpoint the plan itself names as the RCE surface -- "a
        daemon exposing that on localhost with a token stored in a
        world-readable file is a remote-code-execution surface reachable
        from any web page the user has open" -- so it is never left
        unauthenticated even though `start` is nominally an agent verb.
        """
        _require_session(request)
        if agent:
            return _spawn_runner(node_id, agent)
        if not owner:
            raise HTTPException(status_code=422, detail="start needs an owner (or ?agent=X)")
        with _conn() as conn:
            try:
                result = core.nodes.start(
                    conn, node_id, owner=owner, request_id=request_id,
                    lease_minutes=config.planning.lease_minutes, actor_evidence="dashboard_token",
                    config=config, repo_root=repo_root,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    def _spawn_runner(node_id: int, agent: str) -> dict:
        if agent not in config.agents:
            raise HTTPException(
                status_code=422,
                detail=f"unknown agent {agent!r} (configured: {sorted(config.agents)})",
            )
        with _conn() as conn:
            try:
                with core.db.write_txn(conn):
                    node = core.nodes.get_node(conn, node_id)
                    if node["status"] != "ready":
                        raise HTTPException(
                            status_code=409,
                            detail=f"node {node_id} is {node['status']!r}, not ready",
                        )
                    core.events.record_event(
                        conn, project_id=node["project_id"], node_id=node_id, actor="human",
                        actor_evidence="dashboard_token", type_="node.agent_requested",
                        payload={"agent": agent},
                    )
            except HTTPException:
                raise
            except Exception as e:
                _handle_core_error(e)
        log_path = repo_root / core.runner.LOGS_RELDIR / f"run-{node_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "muvue", "run", "--node", str(node_id), "--agent", agent,
                 "--path", str(repo_root)],
                cwd=repo_root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        return {"spawned": {"pid": proc.pid, "node_id": node_id, "agent": agent,
                            "log": str(log_path.relative_to(repo_root))}}

    @app.post("/nodes/{node_id}/done")
    def done_node(
        node_id: int,
        request: Request,
        owner: str = Body(...),
        summary: str | None = Body(default=None),
        request_id: str | None = Body(default=None),
        version: int | None = Body(default=None),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.nodes.done(
                    conn, node_id, owner=owner, summary=summary, request_id=request_id,
                    config=config, expected_version=version,
                    run_checks=core.review.default_run_checks,
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
        trigger: str = Body(...),
        do_instead: str = Body(...),
        scope: str = Body(...),
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
        default: str = Body(...),
        default_ok: bool = Body(default=False),
        request_id: str | None = Body(default=None),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.asks.ask(
                    conn, node_id, question=question, default=default, default_ok=default_ok,
                    request_id=request_id,
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
        node_id: int,
        request: Request,
        title: str = Body(...),
        body_md: str = Body(default=""),
        predicted_touches: list[str] | None = Body(default=None),
    ) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.idempotency.once(
                    conn, _request_id(request), "replan",
                    lambda: core.revisions.replan_add_subtask(
                    conn, parent_task_id=node_id, title=title, body_md=body_md,
                    predicted_touches=predicted_touches, config=config,
                    actor_evidence="dashboard_token",
                ), actor="agent",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/note")
    def note_on_node(
        node_id: int,
        request: Request,
        text: str = Body(...),
        kind: str = Body(default="discovery"),
        pinned: bool = Body(default=False),
    ) -> dict:
        """The agent verb `note` (plan section 4), mirrored 1:1."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.idempotency.once(
                    conn, _request_id(request), "note",
                    lambda: core.nodes.add_note(
                        conn, node_id, kind=kind, text=text, actor="agent", pinned=pinned,
                        actor_evidence="dashboard_token",
                    ),
                    actor="agent",
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/comment")
    def comment_on_node(
        node_id: int, request: Request, text: str = Body(...), pinned: bool = Body(default=False),
        line: int | None = Body(default=None),
    ) -> dict:
        """Spec-document inline comments (plan section 8: "spec document
        view with inline comments (stored as `feedback` events)") -- reuse
        `nodes.add_note`'s existing `feedback` note kind/dedupe machinery
        rather than adding a parallel comment table. `line` anchors the
        comment to a line of the node's body as an `[L<n>] ` prefix, so
        the agent reading the note in its brief sees the anchor too
        (decision #145)."""
        _require_session(request)
        with _conn() as conn:
            try:
                note_text = text
                if line is not None:
                    body_lines = (core.nodes.get_node(conn, node_id)["body_md"] or "").splitlines()
                    if not 1 <= line <= len(body_lines):
                        raise ValueError(f"line {line} is outside the node body (1-{len(body_lines)})")
                    note_text = f"[L{line}] {text}"
                result = core.idempotency.once(
                    conn, _request_id(request), "comment",
                    lambda: core.nodes.add_note(
                    conn, node_id, kind="feedback", text=note_text, actor="human", pinned=pinned,
                    actor_evidence="dashboard_token",
                ),
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
                result = core.idempotency.once(
                    conn, _request_id(request), "propose-revision",
                    lambda: core.revisions.propose_revision(conn, project_id, node_ids, actor_evidence="dashboard_token"),
                )
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
        if target == "revision" and n is None:
            raise HTTPException(status_code=422, detail="revision approval needs n")

        def _apply() -> dict:
            if target == "spec":
                return core.gates.approve_spec(conn, node_id, actor_evidence="dashboard_token")
            if target == "gate2":
                return core.gates.approve_gate2(conn, node_id, config=config, actor_evidence="dashboard_token")
            if target == "revision":
                return core.revisions.approve_revision(conn, node_id, n, config=config, actor_evidence="dashboard_token")
            if target == "review":
                return core.nodes.approve_review(
                    conn, node_id, actor_evidence="dashboard_token", repo_root=repo_root,
                )
            return core.gates.approve_node(conn, node_id, config=config, actor_evidence="dashboard_token")

        with _conn() as conn:
            try:
                result = core.idempotency.once(conn, _request_id(request), "approve", _apply)
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
                result = core.idempotency.once(
                    conn, _request_id(request), "reject",
                    lambda: core.nodes.reject_review(conn, node_id, feedback=feedback, actor_evidence="dashboard_token"),
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/events/{event_id}/ack")
    def ack_event(event_id: int, request: Request) -> dict:
        _require_session(request)
        def _apply() -> dict:
            row = core.events.ack_event(conn, event_id, actor_evidence="dashboard_token")
            if row is None:
                raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
            return dict(row)

        with _conn() as conn:
            return core.idempotency.once(conn, _request_id(request), "ack", _apply)

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
                result = core.idempotency.once(
                    conn, _request_id(request), "merge",
                    lambda: core.merge.attempt_merge(conn, node_id, repo_root),  # merge.py: actor_evidence hardcoded "subprocess" internally
                    atomic=False,
                )
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
                result = core.idempotency.once(
                    conn, _request_id(request), "handoff",
                    lambda: core.nodes.handoff(
                    conn, node_id, new_owner=to, lease_minutes=config.planning.lease_minutes,
                    actor_evidence="dashboard_token",
                ),
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
                result = core.idempotency.once(
                    conn, _request_id(request), "import",
                    lambda: core.imports.import_github_issue(
                    conn, node_id, issue_number, data=data, actor="human",
                    actor_evidence="dashboard_token",
                ), atomic=False,
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
    def close_project(
        project_id: int, request: Request, pr: bool = Body(default=False, embed=True),
    ) -> dict:
        """Plan section 9: commit the project's structure diff onto
        `muvue/structure`, fast-forward `main` when safe (else an inbox
        item, or a PR with `{"pr": true}`), flip the project to `closed`,
        and export its event history (`core.close.close_project`)."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.idempotency.once(
                    conn, _request_id(request), "close",
                    lambda: core.close.close_project(
                    conn, project_id, repo_root, actor="human", confirm=True, open_pr=pr,
                    actor_evidence="dashboard_token",
                ), atomic=False,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/projects/{project_id}/pause")
    def pause_project(project_id: int, request: Request) -> dict:
        """Emergency stop: pause the project and kill its runner processes
        (`core.projects.pause_project`)."""
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.idempotency.once(
                    conn, _request_id(request), "pause",
                    lambda: core.projects.pause_project(
                    conn, project_id, repo_root=repo_root, actor_evidence="dashboard_token",
                ), atomic=False,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/projects/{project_id}/resume")
    def resume_project(project_id: int, request: Request) -> dict:
        _require_session(request)
        with _conn() as conn:
            try:
                result = core.idempotency.once(
                    conn, _request_id(request), "resume",
                    lambda: core.projects.resume_project(
                    conn, project_id, actor_evidence="dashboard_token",
                ),
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    return app
