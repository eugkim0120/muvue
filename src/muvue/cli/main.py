"""muvue CLI (Typer). Every mutating command calls into muvue.core only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from muvue import core
from muvue.core.config import ConfigError

app = typer.Typer(no_args_is_help=True, add_completion=False)

NOT_IMPLEMENTED = "not implemented in P0"


def _find_repo_root(start: Path | None = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".muvue").is_dir():
            return candidate
    raise typer.BadParameter(
        "no .muvue/ found in this directory or its parents; run `muvue init`"
    )


def _db_connect(repo_root: Path):
    return core.db.connect(repo_root / ".muvue" / "muvue.db")


def _load_config(repo_root: Path) -> core.MuvueConfig:
    return core.load_config(repo_root / ".muvue" / "config.toml")


def _echo_json(obj) -> None:
    typer.echo(json.dumps(obj, default=str, indent=2))


# --------------------------------------------------------------------------
# Ops verbs
# --------------------------------------------------------------------------


@app.command()
def init(path: Path = typer.Argument(Path("."), help="Repo root to initialize")) -> None:
    """Scaffold .muvue/ in the given repo (default: cwd)."""
    repo_root = path.resolve()
    try:
        muvue_dir = core.repo_init.init_repo(repo_root)
    except core.repo_init.AlreadyInitialized as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"initialized {muvue_dir}")


@app.command()
def uninit(path: Path = typer.Argument(Path("."), help="Repo root to uninitialize")) -> None:
    """Remove .muvue/ and reverse every file it touched (hooks, .gitignore)."""
    repo_root = path.resolve()
    try:
        core.repo_init.uninit_repo(repo_root)
    except core.repo_init.NotInitialized as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    typer.echo(f"removed .muvue from {repo_root}")


@app.command()
def doctor(
    repair: bool = typer.Option(False, "--repair", help="Attempt to fix issues found"),
    path: Path = typer.Argument(Path("."), help="Repo root to check"),
) -> None:
    """Sanity-check an existing .muvue/ install."""
    repo_root = path.resolve()
    report = core.doctor.run_doctor(repo_root, repair=repair)
    for r in report.repaired:
        typer.echo(f"repaired: {r}")
    for issue in report.issues:
        typer.echo(f"issue: {issue}")
    if report.ok:
        typer.echo("doctor: ok")
    else:
        raise typer.Exit(1)


@app.command()
def migrate(path: Path = typer.Argument(Path("."), help="Repo root")) -> None:
    """Bring .muvue/muvue.db up to the current schema version."""
    repo_root = path.resolve()
    version = core.migrate.run_migrate(repo_root)
    typer.echo(f"schema_version={version}")


@app.command()
def rebuild(path: Path = typer.Argument(Path("."), help="Repo root")) -> None:
    """Replay `events` and report whether it reproduces the live DB."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        mismatches = core.rebuild.diff_state(conn)
    finally:
        conn.close()
    if mismatches:
        _echo_json(mismatches)
        raise typer.Exit(1)
    typer.echo("rebuild: live DB matches replayed events")


@app.command()
def export(path: Path = typer.Argument(Path("."), help="Repo root")) -> None:
    """Minimal event export to .muvue/history/ (full jsonl.gz export ships P6)."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        rows = [dict(r) for r in core.events.all_events(conn)]
    finally:
        conn.close()
    out = repo_root / ".muvue" / "history" / "events.json"
    out.write_text(json.dumps(rows, default=str, indent=2))
    typer.echo(f"exported {len(rows)} events to {out}")


@app.command()
def serve(
    path: Path = typer.Argument(Path("."), help="Repo root to serve"),
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
) -> None:
    """One daemon per repo (plan section 8): HTTP API + SSE dashboard.

    Reconciles expired leases and drains the event queue once at startup
    (`core.daemon.reconcile_on_start`) before opening the socket -- this
    is what makes a killed-and-restarted daemon self-healing (P2
    acceptance #2), since the daemon itself holds no in-memory state.
    Mints a fresh session token for human-verb API calls and prints it
    once; it is also written to `.muvue/session` for the dashboard to
    read, per `core.daemon.create_session`.
    """
    import uvicorn

    from muvue.api import create_app
    from muvue.core import daemon as daemon_mod

    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        result = daemon_mod.reconcile_on_start(conn)
    finally:
        conn.close()
    for reverted in result["reverted_nodes"]:
        typer.echo(f"reconciled expired lease: node {reverted['id']} -> {reverted['status']}")

    token = daemon_mod.create_session(repo_root)
    typer.echo(f"session token (human verbs): {token}")

    app_instance = create_app(repo_root, config=config)
    typer.echo(f"muvue daemon listening on http://{host}:{port}")
    uvicorn.run(app_instance, host=host, port=port, log_level="warning")


@app.command()
def audit() -> None:
    """Structure-graph drift audit. Ships P7."""
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def hook(name: str = typer.Argument(...)) -> None:
    """Entry point invoked by the shim files installed by `init`.

    P0 installs the shim (see repo_init.py) but real hook business logic
    (parsing trailers, enqueueing hash events, pre-push enforcement) ships
    in P3/P4; for now this is a cheap, append-only no-op so hooks stay
    under the 50ms budget from plan section 1.
    """
    return


# --------------------------------------------------------------------------
# Agent verbs
# --------------------------------------------------------------------------


@app.command()
def start(
    node_id: int = typer.Argument(...),
    owner: str = typer.Option(..., "--owner"),
    request_id: str = typer.Option(None, "--request-id"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        result = core.nodes.start(
            conn, node_id, owner=owner, request_id=request_id,
            lease_minutes=config.planning.lease_minutes,
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def done(
    node_id: int = typer.Argument(...),
    owner: str = typer.Option(..., "--owner"),
    request_id: str = typer.Option(None, "--request-id"),
    summary: str = typer.Option(None, "--summary"),
    version: int = typer.Option(
        None, "--version", help="expected nodes.version from start; mismatch fails the call"
    ),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        result = core.nodes.done(
            conn, node_id, owner=owner, request_id=request_id, summary=summary,
            config=config, expected_version=version,
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def fail(
    node_id: int = typer.Argument(...),
    owner: str = typer.Option(..., "--owner"),
    lesson: str = typer.Option(..., "--lesson"),
    request_id: str = typer.Option(None, "--request-id"),
    version: int = typer.Option(
        None, "--version", help="expected nodes.version from start; mismatch fails the call"
    ),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.nodes.fail(
            conn, node_id, owner=owner, lesson=lesson, request_id=request_id,
            expected_version=version,
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def brief() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def show() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def note() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def ask(
    node_id: int = typer.Argument(...),
    question: str = typer.Option(..., "--question"),
    default: str = typer.Option(None, "--default"),
    request_id: str = typer.Option(None, "--request-id"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.asks.ask(
            conn, node_id, question=question, default=default, request_id=request_id
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def wait(
    question_id: int = typer.Argument(...),
    timeout: int = typer.Option(None, "--timeout", help="seconds to poll before giving up"),
    default_ok: bool = typer.Option(False, "--default-ok"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Poll a question until answered or past `planning.ask_timeout_minutes`.

    This is a CLI-level polling wrapper only (no daemon in P0/P1); the real
    timeout/default-ok semantics live in `core.asks.wait` and are testable
    without a live process (see tests/test_ask_wait.py).
    """
    import time as _time

    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        deadline = _time.monotonic() + timeout if timeout else None
        while True:
            result = core.asks.wait(
                conn,
                question_id,
                timeout_minutes=config.planning.ask_timeout_minutes,
                default_ok=default_ok,
            )
            if result["status"] != "pending":
                break
            if deadline is not None and _time.monotonic() >= deadline:
                break
            _time.sleep(1)
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def replan(
    parent_task_id: int = typer.Argument(...),
    title: str = typer.Option(..., "--title"),
    body_md: str = typer.Option("", "--body"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Add a subtask within an already-approved task's stated scope. New
    tasks, deletions, or criteria changes need a plan revision instead
    (see `propose-revision` / `approve-revision`)."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.revisions.replan_add_subtask(
            conn, parent_task_id=parent_task_id, title=title, body_md=body_md
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command(name="propose-revision")
def propose_revision(
    project_id: int = typer.Argument(...),
    node_ids: str = typer.Option(..., "--node-ids", help="comma-separated node IDs"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        ids = [int(x) for x in node_ids.split(",") if x.strip()]
        result = core.revisions.propose_revision(conn, project_id, ids)
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def status() -> None:
    typer.echo(NOT_IMPLEMENTED)


# --------------------------------------------------------------------------
# Human verbs (never exposed over MCP; plan section 4)
# --------------------------------------------------------------------------


@app.command()
def approve(
    target: str = typer.Argument(
        ..., help="'spec:ID', 'node:ID', 'gate2:PROJECT_ID', or 'revision:PROJECT_ID:N'"
    ),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human-only approval verb (never exposed over MCP): Gate 1 (spec),
    Gate 2 (a project's whole decomposition, or a single already-decomposed
    node), or a plan revision's diff-only approval."""
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        kind, _, rest = target.partition(":")
        if kind == "spec":
            result = core.gates.approve_spec(conn, int(rest))
        elif kind == "node":
            result = core.gates.approve_node(conn, int(rest), config=config)
        elif kind == "gate2":
            result = core.gates.approve_gate2(conn, int(rest), config=config)
        elif kind == "revision":
            project_id_s, _, n_s = rest.partition(":")
            result = core.revisions.approve_revision(
                conn, int(project_id_s), int(n_s), config=config
            )
        else:
            typer.echo(f"unknown approve target: {target!r}", err=True)
            raise typer.Exit(1)
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def reject() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def ack() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def merge() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def close() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def pause() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def resume() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def handoff() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command(name="import")
def import_() -> None:
    typer.echo(NOT_IMPLEMENTED)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
