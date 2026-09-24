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
    conn = _db_connect(repo_root)
    try:
        result = core.nodes.start(conn, node_id, owner=owner, request_id=request_id)
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def done(
    node_id: int = typer.Argument(...),
    owner: str = typer.Option(..., "--owner"),
    request_id: str = typer.Option(None, "--request-id"),
    summary: str = typer.Option(None, "--summary"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.nodes.done(conn, node_id, owner=owner, request_id=request_id, summary=summary)
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def fail(
    node_id: int = typer.Argument(...),
    owner: str = typer.Option(..., "--owner"),
    lesson: str = typer.Option(..., "--lesson"),
    request_id: str = typer.Option(None, "--request-id"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.nodes.fail(conn, node_id, owner=owner, lesson=lesson, request_id=request_id)
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
def ask() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def wait() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def replan() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def status() -> None:
    typer.echo(NOT_IMPLEMENTED)


# --------------------------------------------------------------------------
# Human verbs (never exposed over MCP; plan section 4)
# --------------------------------------------------------------------------


@app.command()
def approve() -> None:
    typer.echo(NOT_IMPLEMENTED)


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
