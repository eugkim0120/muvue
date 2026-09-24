"""FastAPI app factory: mirrors the CLI verbs 1:1 (plan section 4),
OpenAPI-documented for free by FastAPI, versioned via `protocol_version`.

Human verbs (`approve`, `reject`, `ack`, `merge`, `close`, `pause`,
`resume`, `handoff`, `import`) are gated by the repo-scoped session token
(`core.daemon.verify_session`) instead of MCP exposure -- see
docs/decisions.md. Agent verbs and ops/read endpoints are unauthenticated
(local daemon, no multi-machine sync per plan non-goals).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from muvue import core
from muvue.core.config import MuvueConfig

STATIC_DIR = Path(__file__).parent / "static"


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _rows_to_list(rows) -> list[dict]:
    return [dict(r) for r in rows]


def create_app(repo_root: Path, config: MuvueConfig | None = None) -> FastAPI:
    repo_root = Path(repo_root)
    db_path = repo_root / ".muvue" / "muvue.db"
    config = config or core.load_config(repo_root / ".muvue" / "config.toml")

    app = FastAPI(
        title="muvue",
        version=str(config.protocol_version),
        description="muvue daemon API (plan section 4): mirrors CLI verbs 1:1.",
    )

    @contextmanager
    def _conn() -> Iterator[sqlite3.Connection]:
        conn = core.db.connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _require_session(authorization: str | None) -> None:
        token = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1].strip()
        if not core.daemon.verify_session(repo_root, token):
            raise HTTPException(status_code=401, detail="missing or invalid session token")

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
            ),
        ):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise

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
        return {"questions": questions, "review": review, "blocked": blocked}

    @app.get("/kpis")
    def kpis() -> dict:
        """Drift % and tokens/spend require the structure layer and
        node_usage population (P3+/P5) -- stubbed at zero per the P2
        prompt. Rubber-stamp rate is real: it's a ratio over events this
        phase already logs (`metric.rubber_stamp` vs total `node.done`
        approvals via `nodes.approve_review`)."""
        with _conn() as conn:
            total_reviewed = conn.execute(
                "SELECT COUNT(*) c FROM events WHERE type = 'node.done'"
            ).fetchone()["c"]
            rubber_stamps = conn.execute(
                "SELECT COUNT(*) c FROM events WHERE type = 'metric.rubber_stamp'"
            ).fetchone()["c"]
        rubber_stamp_rate = (rubber_stamps / total_reviewed) if total_reviewed else 0.0
        return {
            "drift_pct": 0.0,  # P3+: structure layer not built yet
            "rubber_stamp_rate": rubber_stamp_rate,
            "tokens_per_node": 0.0,  # P5: node_usage not populated yet
            "spend_vs_budget": 0.0,  # P5: node_usage not populated yet
        }

    # ------------------------------------------------------------------
    # Agent verbs (plan section 4) -- CLI-equivalent, unauthenticated
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
    def list_events(since_id: int = 0, project_id: int | None = None, limit: int = 200) -> list[dict]:
        """Event timeline (plan section 8 dashboard view)."""
        with _conn() as conn:
            if project_id is not None:
                rows = conn.execute(
                    "SELECT * FROM events WHERE id > ? AND project_id = ? "
                    "ORDER BY id ASC LIMIT ?",
                    (since_id, project_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
                    (since_id, limit),
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
        owner: str = Body(...),
        agent: str | None = None,
        request_id: str | None = Body(default=None),
    ) -> dict:
        """`POST /nodes/{id}/start?agent=X` (plan section 4): the driver
        that would actually spawn agent `X` ships in P5 (runner/drivers).
        Here `agent` is recorded on the start event but nothing is
        spawned -- documented stub per the P2 prompt."""
        with _conn() as conn:
            try:
                if agent:
                    core.events.record_event(
                        conn,
                        project_id=core.nodes.get_node(conn, node_id)["project_id"],
                        node_id=node_id,
                        actor="human",
                        type_="node.agent_requested",
                        payload={"agent": agent},
                    )
                    conn.commit()
                result = core.nodes.start(
                    conn, node_id, owner=owner, request_id=request_id,
                    lease_minutes=config.planning.lease_minutes,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/done")
    def done_node(
        node_id: int,
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
        with _conn() as conn:
            try:
                result = core.nodes.done(
                    conn, node_id, owner=owner, summary=summary, request_id=request_id,
                    config=config, expected_version=version,
                    run_checks=core.review.default_run_checks if run_checks else None,
                    cwd=str(repo_root),
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/fail")
    def fail_node(
        node_id: int,
        owner: str = Body(...),
        lesson: str = Body(...),
        trigger: str = Body(default=""),
        do_instead: str = Body(default=""),
        scope: str = Body(default=""),
        request_id: str | None = Body(default=None),
        version: int | None = Body(default=None),
    ) -> dict:
        with _conn() as conn:
            try:
                result = core.nodes.fail(
                    conn, node_id, owner=owner, lesson=lesson, trigger=trigger,
                    do_instead=do_instead, scope=scope, request_id=request_id,
                    expected_version=version,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/ask")
    def ask_node(
        node_id: int,
        question: str = Body(...),
        default: str | None = Body(default=None),
        request_id: str | None = Body(default=None),
    ) -> dict:
        with _conn() as conn:
            try:
                result = core.asks.ask(
                    conn, node_id, question=question, default=default, request_id=request_id,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/questions/{question_id}/wait")
    def wait_question(question_id: int, default_ok: bool = Body(default=False, embed=True)) -> dict:
        with _conn() as conn:
            try:
                result = core.asks.wait(
                    conn, question_id,
                    timeout_minutes=config.planning.ask_timeout_minutes,
                    default_ok=default_ok,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/replan")
    def replan_node(
        node_id: int, title: str = Body(...), body_md: str = Body(default="")
    ) -> dict:
        with _conn() as conn:
            try:
                result = core.revisions.replan_add_subtask(
                    conn, parent_task_id=node_id, title=title, body_md=body_md,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/comment")
    def comment_on_node(
        node_id: int, text: str = Body(...), pinned: bool = Body(default=False)
    ) -> dict:
        """Spec-document inline comments (plan section 8: "spec document
        view with inline comments (stored as `feedback` events)") -- reuse
        `nodes.add_note`'s existing `feedback` note kind/dedupe machinery
        rather than adding a parallel comment table."""
        with _conn() as conn:
            try:
                result = core.nodes.add_note(
                    conn, node_id, kind="feedback", text=text, actor="human", pinned=pinned,
                )
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/projects/{project_id}/propose-revision")
    def propose_revision(project_id: int, node_ids: list[int] = Body(..., embed=True)) -> dict:
        with _conn() as conn:
            try:
                result = core.revisions.propose_revision(conn, project_id, node_ids)
            except Exception as e:
                _handle_core_error(e)
        return result

    # ------------------------------------------------------------------
    # Human verbs (plan section 4): gated by the repo session token.
    # ------------------------------------------------------------------

    @app.post("/nodes/{node_id}/approve")
    def approve_node(
        node_id: int,
        target: str = Body(default="node"),
        n: int | None = Body(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict:
        _require_session(authorization)
        with _conn() as conn:
            try:
                if target == "spec":
                    result = core.gates.approve_spec(conn, node_id)
                elif target == "gate2":
                    result = core.gates.approve_gate2(conn, node_id, config=config)
                elif target == "revision":
                    if n is None:
                        raise HTTPException(status_code=422, detail="revision approval needs n")
                    result = core.revisions.approve_revision(conn, node_id, n, config=config)
                elif target == "review":
                    result = core.nodes.approve_review(conn, node_id)
                else:
                    result = core.gates.approve_node(conn, node_id, config=config)
            except HTTPException:
                raise
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/nodes/{node_id}/reject")
    def reject_node(
        node_id: int,
        feedback: str = Body(..., embed=True),
        authorization: str | None = Header(default=None),
    ) -> dict:
        _require_session(authorization)
        with _conn() as conn:
            try:
                result = core.nodes.reject_review(conn, node_id, feedback=feedback)
            except Exception as e:
                _handle_core_error(e)
        return result

    @app.post("/events/{event_id}/ack")
    def ack_event(event_id: int, authorization: str | None = Header(default=None)) -> dict:
        _require_session(authorization)
        with _conn() as conn:
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail=f"no such event: {event_id}")
            conn.execute(
                "UPDATE events SET acked_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') "
                "WHERE id = ?",
                (event_id,),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return dict(row)

    @app.post("/nodes/{node_id}/merge")
    def merge_node(node_id: int, authorization: str | None = Header(default=None)) -> dict:
        _require_session(authorization)
        return {"status": "not implemented in P2 (merge machinery ships P6)"}

    @app.post("/nodes/{node_id}/handoff")
    def handoff_node(node_id: int, authorization: str | None = Header(default=None)) -> dict:
        _require_session(authorization)
        return {"status": "not implemented in P2 (drivers ship P5)"}

    @app.post("/import")
    def import_(authorization: str | None = Header(default=None)) -> dict:
        _require_session(authorization)
        return {"status": "not implemented in P2 (github import ships P6)"}

    @app.post("/projects/{project_id}/close")
    def close_project(project_id: int, authorization: str | None = Header(default=None)) -> dict:
        _require_session(authorization)
        with _conn() as conn:
            try:
                row = core.projects.set_phase(conn, project_id, "closed")
            except Exception as e:
                _handle_core_error(e)
        return dict(row)

    @app.post("/projects/{project_id}/pause")
    def pause_project(project_id: int, authorization: str | None = Header(default=None)) -> dict:
        _require_session(authorization)
        with _conn() as conn:
            try:
                row = core.projects.set_phase(conn, project_id, "paused")
            except Exception as e:
                _handle_core_error(e)
        return dict(row)

    @app.post("/projects/{project_id}/resume")
    def resume_project(project_id: int, authorization: str | None = Header(default=None)) -> dict:
        _require_session(authorization)
        with _conn() as conn:
            try:
                row = core.projects.set_phase(conn, project_id, "executing")
            except Exception as e:
                _handle_core_error(e)
        return dict(row)

    return app
