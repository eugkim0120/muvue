"""muvue CLI (Typer). Every mutating command calls into muvue.core only."""

from __future__ import annotations

import functools
import json
import os
import sys
from pathlib import Path

import typer

from muvue import _hook as _hook_mod
from muvue import core
from muvue.core.config import ConfigError

app = typer.Typer(no_args_is_help=True, add_completion=False)
adapter_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Vendor adapter config writers (plan section 7).")
app.add_typer(adapter_app, name="adapter")
project_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Project-level verbs.")
app.add_typer(project_app, name="project")




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


REQUEST_ID_HELP = (
    "idempotency key: a repeat with the same key within 24h returns the first result "
    "instead of running again"
)


@functools.lru_cache(maxsize=1)
def _invoker() -> tuple[str, str]:
    """`(actor, actor_evidence)` for a human verb issued from this CLI
    process: an agent CLI in the parent chain records the call as the
    agent's, and a missing TTY is recorded as `no_tty` (plan section 4;
    see core/actor.py). Detection, not prevention."""
    return core.actor.detect_invoker()


def _evidence() -> str:
    """How this CLI process's caller was identified, for any verb:
    `agent_parent:<name>`, `tty` or `no_tty` (see `_invoker`)."""
    return _invoker()[1]


def _human_kwargs() -> dict:
    actor, evidence = _invoker()
    return {"actor": actor, "actor_evidence": evidence}


def _echo_json(obj) -> None:
    typer.echo(json.dumps(obj, default=str, indent=2))


@app.callback()
def _drain_before_every_command(ctx: typer.Context) -> None:
    """v4 section 4a: "absent a daemon, the next CLI call drains at
    most 200 items or 200 ms, whichever comes first" -- `muvue._hook`
    (the fast path) never does this itself, so any normal CLI
    invocation is the catch-up point when no daemon is running. This is
    the "next CLI call" the plan text describes, applied globally as a
    Typer app callback rather than duplicated into every command.

    Best-effort and silent: no `.muvue/` yet (`init` itself, or any
    command run outside a muvue repo) or any error draining is not this
    callback's problem to report -- the command it's a prefix to either
    doesn't need a repo at all or will raise its own, clearer error a
    moment later.

    Skipped for `doctor`, which reports the queue depth it finds."""
    if ctx.invoked_subcommand == "doctor":
        return
    try:
        repo_root = _find_repo_root()
    except typer.BadParameter:
        return
    try:
        conn = _db_connect(repo_root)
    except Exception:
        return
    try:
        core.hooks.drain_queue(conn, repo_root)
    except Exception:
        pass
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Ops verbs
# --------------------------------------------------------------------------


@app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Repo root to initialize"),
    sandbox: bool = typer.Option(
        False, "--sandbox",
        help="Also emit .muvue/sandbox-compose.yml, a documented container-isolation "
        "scaffold (plan section 5: '(later)') -- muvue does not run it.",
    ),
) -> None:
    """Scaffold .muvue/ in the given repo (default: cwd)."""
    repo_root = path.resolve()
    try:
        muvue_dir = core.repo_init.init_repo(repo_root, sandbox=sandbox)
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
    skip_security_probes: bool = typer.Option(
        False, "--skip-security-probes",
        help="skip the live daemon-security probes (v4 section 8a control 7)",
    ),
    daemon_port: int = typer.Option(
        None, "--daemon-port",
        help="port to probe/spin up a throwaway daemon on for the security checks "
        "(default: the `serve` default, 8765)",
    ),
) -> None:
    """Sanity-check an existing .muvue/ install."""
    repo_root = path.resolve()
    report = core.doctor.run_doctor(
        repo_root, repair=repair,
        skip_security_probes=skip_security_probes, daemon_port=daemon_port,
    )
    for r in report.repaired:
        typer.echo(f"repaired: {r}")
    for issue in report.issues:
        typer.echo(f"issue: {issue}")
    for warning in report.warnings:
        typer.echo(f"warning: {warning}")
    for line in report.info:
        typer.echo(line)
    if report.ok:
        typer.echo("doctor: ok")
    else:
        raise typer.Exit(1)


@app.command()
def migrate(path: Path = typer.Argument(Path("."), help="Repo root")) -> None:
    """Bring .muvue/muvue.db up to the current schema version."""
    repo_root = path.resolve()
    db_path = repo_root / ".muvue" / "muvue.db"
    if 0 < core.migrate.recorded_version(db_path) < core.schema.SCHEMA_VERSION:
        # Upgrades drop columns; keep a copy to go back to.
        typer.echo(f"backup: {_backup_db(repo_root)}")
    version = core.migrate.run_migrate(repo_root)
    typer.echo(f"schema_version={version}")


def _backup_db(repo_root: Path) -> Path:
    """Consistent copy of the live DB (sqlite's online backup API, safe
    under WAL with other connections open)."""
    import datetime
    import sqlite3

    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    src_path = repo_root / ".muvue" / "muvue.db"
    dest_path = src_path.with_name(f"muvue.db.bak-{stamp}")
    src = sqlite3.connect(str(src_path))
    dest = sqlite3.connect(str(dest_path))
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    return dest_path


@app.command()
def rebuild(
    path: Path = typer.Argument(Path("."), help="Repo root"),
    project_id: int = typer.Option(
        None, "--project-id", help="scope to one project, replayed from its exported archive"
    ),
    from_archive: Path = typer.Option(
        None, "--from-archive",
        help="replay this .jsonl.gz archive instead of the default "
        ".muvue/history/<project-id>.jsonl.gz (requires --project-id)",
    ),
    apply: bool = typer.Option(
        False, "--apply",
        help="rewrite the replayable tables from the event log (backs the DB up first)",
    ),
) -> None:
    """Replay `events` and report whether it reproduces the live DB.

    With `--apply`: back up `.muvue/muvue.db` to `muvue.db.bak-<UTC time>`,
    then rewrite the replayable set from the event log
    (`core.rebuild.apply_rebuild`).

    With `--project-id` (P6 acceptance #3): replay only that project's
    exported `.jsonl.gz` history archive (`core.history`) and compare
    against that project's live state instead of the whole DB.
    """
    repo_root = _find_repo_root(path)
    if apply:
        if project_id is not None:
            typer.echo(
                "--apply rebuilds the whole DB; it can't be combined with --project-id", err=True
            )
            raise typer.Exit(2)
        backup = _backup_db(repo_root)
        conn = _db_connect(repo_root)
        try:
            result = core.rebuild.apply_rebuild(conn)
            remaining = core.rebuild.diff_state(conn)
        finally:
            conn.close()
        typer.echo(f"backup: {backup}")
        typer.echo(f"rebuilt tables: {', '.join(result['tables']) or 'none (already matched)'}")
        if remaining:
            _echo_json(remaining)
            raise typer.Exit(1)
        return
    conn = _db_connect(repo_root)
    try:
        if project_id is not None:
            archive = from_archive or core.history.archive_path(repo_root, project_id)
            if not archive.exists():
                typer.echo(f"no archive found at {archive}", err=True)
                raise typer.Exit(1)
            mismatches = core.rebuild.diff_project_from_archive(conn, project_id, archive)
        else:
            mismatches = core.rebuild.diff_state(conn)
    finally:
        conn.close()
    if mismatches:
        _echo_json(mismatches)
        raise typer.Exit(1)
    typer.echo("rebuild: live DB matches replayed events")


@app.command()
def export(
    path: Path = typer.Argument(Path("."), help="Repo root"),
    project_id: int = typer.Option(
        None, "--project-id", help="export only this project to .muvue/history/<id>.jsonl.gz"
    ),
) -> None:
    """Event export to `.muvue/history/*.jsonl.gz` (plan section 2). With
    `--project-id`: that project's archive, the same one `close` writes.
    Without it: every project's archive plus `unscoped.jsonl.gz` for
    events that belong to no project."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        if project_id is not None:
            out = core.history.export_project(conn, project_id, repo_root)
            written = [(out, len(core.history.export_project_events(conn, project_id)))]
        else:
            written = core.history.export_all(conn, repo_root)
    finally:
        conn.close()
    for out, count in written:
        typer.echo(f"exported {count} events to {out}")


@app.command()
def serve(
    path: Path = typer.Argument(Path("."), help="Repo root to serve"),
    host: str | None = typer.Option(
        None, "--host", help="bind address (default: [daemon] bind in config.toml, 127.0.0.1)",
    ),
    port: int = typer.Option(8765, "--port"),
    i_know_this_is_exposed: bool = typer.Option(
        False, "--i-know-this-is-exposed",
        help="required to bind anything other than 127.0.0.1/localhost (v4 section 8a control 1)",
    ),
) -> None:
    """One daemon per repo (plan section 8): HTTP API + SSE dashboard.

    Reconciles expired leases and drains the event queue once at startup
    (`core.daemon.reconcile_on_start`) before opening the socket -- this
    is what makes a killed-and-restarted daemon self-healing (P2
    acceptance #2), since the daemon itself holds no in-memory state.

    Security (plan v4 section 8a): binds loopback-only unless
    `--i-know-this-is-exposed` is passed (control 1). Mints a fresh
    256-bit session token in memory -- never written to disk (control
    5/6) -- and prints the dashboard URL with a single-use nonce in its
    `#fragment`; the dashboard's own JS exchanges that nonce for an
    HttpOnly session cookie on first load (`POST /auth/exchange`), after
    which the link is dead. The token itself is printed once for API
    clients such as the VS Code extension. v3's `~/.muvue/session` file
    is gone entirely.
    """
    import signal
    import socket

    import uvicorn

    from muvue.api import create_app
    from muvue.core import daemon as daemon_mod

    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    if host is None:
        host = config.daemon.bind
    hostname = host.split("%", 1)[0].strip().lower()
    if hostname not in ("127.0.0.1", "localhost") and not i_know_this_is_exposed:
        typer.echo(
            f"refusing to bind {host!r}: only 127.0.0.1/localhost is allowed without "
            "--i-know-this-is-exposed (v4 section 8a control 1 -- this daemon can launch "
            "agent CLI subprocesses; do not expose it beyond loopback without understanding "
            "the risk)",
            err=True,
        )
        raise typer.Exit(1)
    if hostname not in ("127.0.0.1", "localhost") and i_know_this_is_exposed:
        typer.echo(
            f"WARNING: binding {host!r} -- this daemon is a local code-execution surface "
            "(plan v4 section 1, principle 8) and this bind is now reachable beyond this "
            "machine's loopback interface. Proceed only if you understand the risk.",
            err=True,
        )

    conn = _db_connect(repo_root)
    try:
        result = daemon_mod.reconcile_on_start(conn, repo_root=repo_root)
    finally:
        conn.close()
    for reverted in result["reverted_nodes"]:
        typer.echo(f"reconciled expired lease: node {reverted['id']} -> {reverted['status']}")

    session = daemon_mod.SessionManager()
    dashboard_url = f"http://{host}:{port}/#n={session.mint_nonce()}"
    typer.echo(f"dashboard (one-time link, works once): {dashboard_url}")
    typer.echo(f"api token: {session.token}")
    typer.echo(
        "  (send as `Authorization: Bearer <token>`; the VS Code extension asks for it. "
        "It lives only in this process: do not paste it into files or logs.)"
    )

    app_instance = create_app(repo_root, config=config, session=session, port=port)

    # Bind the socket ourselves and start listening *before* printing
    # the readiness line, so a caller that waits for "listening on"
    # (the CLI/daemon-security integration tests; a supervising
    # process) never races a not-yet-accepting port -- handing
    # `uvicorn.run` the already-listening socket's fd (rather than
    # `host=`/`port=`, which lets uvicorn bind on its own schedule
    # after this function has already returned control to it) makes
    # "the readiness line was printed" and "the socket accepts
    # connections" the same moment.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(2048)
    # uvicorn re-raises the SIGINT/SIGTERM it captured once it has shut
    # down. SIGINT's default handler turns that into KeyboardInterrupt;
    # give SIGTERM the same treatment so the `finally` below always
    # removes the port file instead of the process dying mid-unwind.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    daemon_mod.write_port_file(repo_root, port=port, pid=os.getpid())
    try:
        typer.echo(f"muvue daemon listening on http://{host}:{port}")
        uvicorn.run(app_instance, fd=sock.fileno(), log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        daemon_mod.remove_port_file(repo_root, pid=os.getpid())


@adapter_app.command("install")
def adapter_install(
    name: str = typer.Argument(..., help="claude-code, codex, gemini, or cursor"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Write the given vendor's config, pointing it at muvue's CLI/MCP
    surface (plan section 7). The Claude Code hook contract was checked
    against claude 2.1.281; the Codex/Gemini/Cursor writers are
    best-effort -- see docs/providers.md for what was verified."""
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    installers = {
        "claude-code": core.adapters.install_claude_code,
        "codex": core.adapters.install_codex,
        "gemini": core.adapters.install_gemini,
        "cursor": core.adapters.install_cursor,
    }
    installer = installers.get(name)
    if installer is None:
        typer.echo(f"unknown adapter: {name!r} (known: {sorted(installers)})", err=True)
        raise typer.Exit(1)
    written = installer(repo_root, protocol_version=config.protocol_version)
    typer.echo(f"installed {name} adapter: {written}")


@app.command()
def mcp(path: Path = typer.Argument(Path("."), help="Repo root to serve")) -> None:
    """MCP stdio server (plan section 4/7): agent verbs only, never the
    human verbs -- see src/muvue/mcp_server.py."""
    from muvue.mcp_server import run_stdio

    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    run_stdio(repo_root, config=config)


@app.command()
def audit(
    path: Path = typer.Option(Path("."), "--path"),
    n: int = typer.Option(
        core.drift.DEFAULT_AUDIT_SAMPLE, "--n",
        help="How many oldest-verified components to sample per run.",
    ),
) -> None:
    """Structure-graph drift audit (plan section 9): samples the `n`
    oldest-verified (or never-verified) components and puts a draft
    update for each in the inbox: the diff of its anchor files since it
    was verified, and the anchors it would get. Then archives lessons
    unused in the last K projects."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.drift.run_audit(conn, n=n, repo_root=repo_root)
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def hook(
    name: str = typer.Argument(...),
    path: Path = typer.Argument(Path("."), help="Repo root (default: cwd)"),
) -> None:
    """Entry point invoked by the shim files installed by `init`.

    `post-commit` (P3): parses `Muvue-Node:`/`Refs:` trailers out of
    HEAD's commit message, links the commit to any resolvable node in
    `node_commits`, and enqueues anchor-hash/staleness no-op signals (see
    core/hooks.py).

    `session-start`/`pre-tool-use`/`pre-compact`/`stop`: the Claude Code
    adapter's decision hooks, served by `muvue._hook.run`. Exit 2 blocks
    with the reason on stderr; on exit 0 SessionStart prints the node's
    brief as session context.

    `pre-push` is still a no-op -- pre-push strict-mode enforcement is P4
    scope (plan section 12 working rule 7: no strict-mode airlock in P3).
    Stays well under the 50ms budget from plan section 1 either way.
    """
    if name == "post-commit":
        repo_root = _find_repo_root(path)
        conn = _db_connect(repo_root)
        try:
            core.hooks.handle_post_commit_from_git(conn, repo_root)
        finally:
            conn.close()
        return

    if name == "pre-receive":
        # Runs server-side, inside the airlock bare repo (plan section 5,
        # P4) -- `path` is meaningless here (no .muvue/ in a bare repo);
        # core.strict.handle_pre_receive_cli locates the real repo via the
        # airlock's REPO_ROOT_MARKER file instead. Exits non-zero (git's
        # documented pre-receive convention) to reject the whole push.
        exit_code = core.strict.handle_pre_receive_cli(Path.cwd())
        raise typer.Exit(exit_code)

    if name not in _hook_mod.DECISION_HOOKS:
        return

    # Same decision code the installed fast-path shims run
    # (`muvue._hook.run`), so the two entry points can't drift apart.
    repo_root = _find_repo_root(path)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    outcome = _hook_mod.run(name, str(repo_root), payload)
    sys.stdout.write(outcome.stdout)
    sys.stderr.write(outcome.stderr)
    raise typer.Exit(outcome.exit_code)


@project_app.command("create")
def project_create(
    goal: str = typer.Option(..., "--goal"),
    follows: list[int] = typer.Option([], "--follows", help="repeatable; project id this one follows"),
    supersedes: list[int] = typer.Option(
        [], "--supersedes", help="repeatable; project id this one supersedes"
    ),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Create a new project (`core.projects.create_project`). Prints the
    created project row, including its `id`. Budgets are per driver
    (`[agents.<name>.budget]` in config.toml), not per project."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    who, evidence = _invoker()
    try:
        def _apply():
            return core.projects.create_project(
                conn, goal=goal, repo_root=repo_root, follows=list(follows),
                supersedes=list(supersedes), actor=who, actor_evidence=evidence,
            )
        result = core.idempotency.once(
            conn, request_id, "project.create", _apply, actor=who,
        )
    finally:
        conn.close()
    _echo_json(dict(result))


# --------------------------------------------------------------------------
# Agent verbs
# --------------------------------------------------------------------------


@app.command()
def spec(
    project_id: int = typer.Argument(...),
    title: str = typer.Option(..., "--title"),
    body: str = typer.Option(..., "--body"),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Gate 1: agent submits a spec node for a project
    (`core.gates.submit_spec`). Created `pending`; a human then calls
    `approve spec:ID` before decomposition into tasks."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            return core.gates.submit_spec(
                conn, project_id=project_id, title=title, body_md=body, actor_evidence=_evidence(),
            )
        result = core.idempotency.once(
            conn, request_id, "spec", _apply, actor="agent",
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def decompose(
    spec_id: int = typer.Argument(..., help="the Gate-1 spec node's id (must be ready)"),
    title: str = typer.Option(..., "--title"),
    body: str = typer.Option("", "--body"),
    criteria: list[str] = typer.Option([], "--criteria", help="repeatable; acceptance criteria"),
    predicted_touches: list[str] = typer.Option(
        [], "--predicted-touches", help="repeatable; path globs the task expects to touch"
    ),
    criteria_mode: str = typer.Option("manual", "--criteria-mode", help="auto|external|manual"),
    depends_on: list[int] = typer.Option(
        [], "--depends-on", help="repeatable; id of a node in the same project this task waits on"
    ),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Gate 2: agent decomposes an approved spec into a task node
    (`core.nodes.create_node`). Created `pending` under the spec (`parent_id`);
    a human then calls `approve gate2:PROJECT_ID` to freeze criteria and
    unblock `start`."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            spec_node = core.nodes.get_node(conn, spec_id)
            result = core.nodes.create_node(
                conn,
                project_id=spec_node["project_id"],
                kind="task",
                title=title,
                parent_id=spec_id,
                body_md=body,
                criteria=list(criteria),
                criteria_mode=criteria_mode,
                predicted_touches=list(predicted_touches),
                depends_on=list(depends_on),
                status="pending",
                actor="agent",
                actor_evidence=_evidence(),
            )
            return result
        result = core.idempotency.once(
            conn, request_id, "decompose", _apply, actor="agent",
        )
    finally:
        conn.close()
    _echo_json(dict(result))


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
            config=config, repo_root=repo_root, actor_evidence=_evidence(),
        )
    finally:
        conn.close()
    if not result.get("noop"):
        # Bridges `start` to the Claude Code adapter's hooks (P3), which
        # have no other way to know which node an editor session is
        # working on -- see core/adapters.py.
        core.adapters.set_current_node(repo_root, node_id)
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
    """Finish a node. For `auto` criteria muvue runs `[checks] test` and
    `[checks] lint` itself (v4 section 5); a failure stops at review."""
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        result = core.nodes.done(
            conn, node_id, owner=owner, request_id=request_id, summary=summary,
            config=config, expected_version=version,
            run_checks=core.review.default_run_checks, cwd=str(repo_root),
            actor_evidence=_evidence(),
        )
    finally:
        conn.close()
    if core.adapters.get_current_node(repo_root) == node_id:
        core.adapters.clear_current_node(repo_root)
    _echo_json(result)


@app.command()
def fail(
    node_id: int = typer.Argument(...),
    owner: str = typer.Option(..., "--owner"),
    lesson: str = typer.Option(..., "--lesson", help="what failed"),
    trigger: str = typer.Option(..., "--trigger", help="what led to the failure"),
    do_instead: str = typer.Option(..., "--do-instead", help="what to do next time"),
    scope: str = typer.Option(..., "--scope", help="where the lesson applies (paths, area)"),
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
            conn, node_id, owner=owner, lesson=lesson, trigger=trigger, do_instead=do_instead,
            scope=scope, request_id=request_id, expected_version=version,
            actor_evidence=_evidence(),
        )
    finally:
        conn.close()
    if core.adapters.get_current_node(repo_root) == node_id:
        core.adapters.clear_current_node(repo_root)
    _echo_json(result)


@app.command()
def brief(
    node_id: int = typer.Argument(...),
    budget: int = typer.Option(
        core.brief.DEFAULT_BUDGET_TOKENS, "--budget", help="approximate tokens (characters / 4)"
    ),
    since: int = typer.Option(
        None, "--since", help="only events after this id (the header's cursor:E<id>)"
    ),
    as_json: bool = typer.Option(False, "--json", help="the structured form instead of lines"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """What an agent needs to start work on a node, one fact per line
    (plan section 4, "Brief"), highest priority first, cut to --budget."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        if as_json:
            _echo_json(core.queries.brief_node(conn, node_id))
            return
        result = core.brief.render_brief(conn, node_id, budget=budget, since=since)
    finally:
        conn.close()
    typer.echo(result["text"], nl=False)


@app.command()
def show(
    node_id: int = typer.Argument(...),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.queries.show_node(conn, node_id)
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def note(
    node_id: int = typer.Argument(...),
    text: str = typer.Option(..., "--text"),
    kind: str = typer.Option("discovery", "--kind"),
    pinned: bool = typer.Option(False, "--pinned"),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            return core.nodes.add_note(
                conn, node_id, kind=kind, text=text, actor="agent", pinned=pinned,
                actor_evidence=_evidence(),
            )
        result = core.idempotency.once(
            conn, request_id, "note", _apply, actor="agent",
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def ask(
    node_id: int = typer.Argument(...),
    question: str = typer.Option(..., "--question"),
    default: str = typer.Option(..., "--default", help="proposed answer if no human replies"),
    default_ok: bool = typer.Option(
        False, "--default-ok", help="proceed with --default once ask_timeout_minutes passes"
    ),
    request_id: str = typer.Option(None, "--request-id"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.asks.ask(
            conn, node_id, question=question, default=default, default_ok=default_ok,
            request_id=request_id, actor_evidence=_evidence(),
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
                actor_evidence=_evidence(),
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
    depends_on: list[int] = typer.Option(
        [], "--depends-on", help="repeatable; id of a node in the same project this subtask waits on"
    ),
    predicted_touches: list[str] = typer.Option(
        [], "--predicted-touches", help="repeatable path glob; must fall inside the parent's touches"
    ),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Add a subtask within an already-approved task's stated scope. A
    subtask outside the parent's touches, or past `max_subtasks`, is
    created `pending` and needs `approve node:ID`. New tasks, deletions,
    or criteria changes need a plan revision instead."""
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            return core.revisions.replan_add_subtask(
                conn, parent_task_id=parent_task_id, title=title, body_md=body_md,
                depends_on=list(depends_on), predicted_touches=list(predicted_touches),
                config=config, actor_evidence=_evidence(),
            )
        result = core.idempotency.once(
            conn, request_id, "replan", _apply, actor="agent",
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command(name="propose-revision")
def propose_revision(
    project_id: int = typer.Argument(...),
    node_ids: str = typer.Option(..., "--node-ids", help="comma-separated node IDs"),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            ids = [int(x) for x in node_ids.split(",") if x.strip()]
            result = core.revisions.propose_revision(conn, project_id, ids, actor_evidence=_evidence())
            return result
        result = core.idempotency.once(
            conn, request_id, "propose-revision", _apply, actor="agent",
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def status(
    project_id: int = typer.Option(None, "--project-id"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.queries.status_summary(conn, project_id)
    finally:
        conn.close()
    _echo_json(result)


# --------------------------------------------------------------------------
# Human verbs (never exposed over MCP; plan section 4)
# --------------------------------------------------------------------------


@app.command()
def approve(
    target: str = typer.Argument(
        ..., help="'spec:ID', 'node:ID', 'gate2:PROJECT_ID', 'revision:PROJECT_ID:N', "
        "or 'review:ID'"
    ),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human-only approval verb (never exposed over MCP): Gate 1 (spec),
    Gate 2 (a project's whole decomposition, or a single already-decomposed
    node), a plan revision's diff-only approval, or a node sitting in
    `review` (`core.nodes.approve_review`)."""
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            kind, _, rest = target.partition(":")
            who = _human_kwargs()
            if kind == "spec":
                result = core.gates.approve_spec(conn, int(rest), **who)
            elif kind == "node":
                result = core.gates.approve_node(conn, int(rest), config=config, **who)
            elif kind == "gate2":
                result = core.gates.approve_gate2(conn, int(rest), config=config, **who)
            elif kind == "revision":
                project_id_s, _, n_s = rest.partition(":")
                result = core.revisions.approve_revision(
                    conn, int(project_id_s), int(n_s), config=config, **who
                )
            elif kind == "review":
                result = core.nodes.approve_review(conn, int(rest), repo_root=repo_root, **who)
            else:
                typer.echo(f"unknown approve target: {target!r}", err=True)
                raise typer.Exit(1)
            return result
        result = core.idempotency.once(
            conn, request_id, "approve", _apply, actor=_invoker()[0],
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def answer(
    question_id: int = typer.Argument(...),
    text: str = typer.Option(..., "--text"),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human-only: answer an open question created by `ask` (never exposed
    over MCP; plan section 4). Calls core.asks.answer, which records the
    answer as a `feedback` note on the question's node."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            return core.asks.answer(conn, question_id, text=text, **_human_kwargs())
        result = core.idempotency.once(
            conn, request_id, "answer", _apply, actor=_invoker()[0],
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def reject(
    target: str = typer.Argument(..., help="'review:NODE_ID' -- a node sitting in review"),
    feedback: str = typer.Option(..., "--feedback", help="why, recorded as a feedback note"),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb: send a node in `review` back to its owner (`in_progress`)
    with feedback (`core.nodes.reject_review`)."""
    kind, _, rest = target.partition(":")
    if kind != "review" or not rest.isdigit():
        typer.echo(f"unknown reject target: {target!r}; expected 'review:NODE_ID'", err=True)
        raise typer.Exit(1)
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            return core.nodes.reject_review(conn, int(rest), feedback=feedback, **_human_kwargs())
        result = core.idempotency.once(
            conn, request_id, "reject", _apply, actor=_invoker()[0],
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def ack(
    event_id: int = typer.Argument(..., help="inbox item (event) id"),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb: acknowledge an inbox item so it leaves the inbox."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            row = core.events.ack_event(conn, event_id, **_human_kwargs())
            if row is None:
                typer.echo(f"no such event: {event_id}", err=True)
                raise typer.Exit(1)
            return dict(row)
        result = core.idempotency.once(
            conn, request_id, "ack", _apply, actor=_invoker()[0],
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def run(
    agent: str = typer.Option(
        None, "--agent", help="override [routing]: use this agent for every scheduled node"
    ),
    parallel: int = typer.Option(
        1, "--parallel", help="max nodes scheduled concurrently (still capped by each "
        "agent's own max_concurrency)"
    ),
    project_id: int = typer.Option(None, "--project-id", help="scope to one project"),
    node_id: int = typer.Option(None, "--node", help="run only this ready node"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Unattended runner (plan section 6): spawns one configured driver
    subprocess per ready node, records real usage to `node_usage`, and
    applies `[routing]`/`[budget]`/`on_rate_limit` policy from
    `config.toml`. Pauses (returns control, never crashes) on a node that
    needs human attention (`awaiting_approval`/`blocked`/`failed`/`review`)
    or budget exhaustion."""
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    db_path = repo_root / ".muvue" / "muvue.db"
    try:
        result = core.runner.run(
            db_path, config, repo_root, agent_override=agent, parallel=parallel, project_id=project_id,
            node_id=node_id,
        )
    except core.runner.RunRefused as e:
        # v4 section 6 Delta C: refused before the runner does anything --
        # core.runner.run raises this before opening a DB connection.
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    _echo_json(result)


@app.command()
def merge(
    node_id: int = typer.Argument(
        None, help="merge this done node's branch (onto the airlock's main in strict mode, "
        "into the checkout in light per_node mode); omit to merge every pending done node "
        "in dependency order"
    ),
    pr: bool = typer.Option(
        False, "--pr", help="also generate a PR description body (plan section 6/11, P6) -- "
        "criteria + relevant decisions/notes/linked issues; requires a single NODE_ID",
    ),
    create: bool = typer.Option(
        False, "--create", help="with --pr: actually open the PR via a real `gh pr create` "
        "call (default: body text only, no GitHub write)",
    ),
    repo: str = typer.Option(
        None, "--repo", help="'owner/name' for --create's `gh` call; omit to use the current "
        "directory's gh-detected repo",
    ),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb (plan section 6 "Merging"): merge node branch(es) onto
    the airlock's main (strict mode) or into the checkout (light mode with
    `worktree_mode = "per_node"`, only while the checkout is clean). On
    conflict: the node -> blocked(conflict), attempts + 1, and a "rebase
    onto main" subtask is created with the same owner. A node with no
    worktree is a documented no-op (see core/merge.py). `--pr --create` opens a real PR via `gh`
    (see core/github.py); `--pr` alone only returns the body text."""
    if pr and node_id is None:
        typer.echo("--pr requires a single NODE_ID", err=True)
        raise typer.Exit(1)
    if create and not pr:
        typer.echo("--create requires --pr", err=True)
        raise typer.Exit(1)
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            if node_id is not None:
                result = core.merge.attempt_merge(conn, node_id, repo_root)
                if pr:
                    node = core.nodes.get_node(conn, node_id)
                    body = core.pr.generate_pr_body(conn, node_id)
                    result["pr_body"] = body
                    if create:
                        try:
                            result["pr"] = core.github.create_pr_via_gh(
                                head=f"node-{node_id}", base="main",
                                title=node["title"], body=body, repo=repo,
                            )
                        except core.github.GithubError as exc:
                            typer.echo(f"gh pr create failed: {exc}", err=True)
                            raise typer.Exit(1) from exc
            else:
                result = core.merge.merge_pending(conn, repo_root)
            return result
        result = core.idempotency.once(
            conn, request_id, "merge", _apply, actor=_invoker()[0], atomic=False,
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def close(
    project_id: int = typer.Argument(...),
    yes: bool = typer.Option(
        False, "--yes", help="commit the proposed structure diff and close the project "
        "(default: dry-run preview only)",
    ),
    pr: bool = typer.Option(
        False, "--pr", help="if main can't be fast-forwarded, push muvue/structure to a GitHub "
        "origin and open a PR with gh",
    ),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb (plan section 9): with `--yes`, commit the project's
    proposed structure diff (new/changed components, decisions, promoted
    lessons) onto the `muvue/structure` branch, fast-forward `main` to it
    when `main` is checked out and clean (otherwise leave an inbox item,
    or open a PR with `--pr`), flip the project to `closed`, and export
    its event history to `.muvue/history/<id>.jsonl.gz`. Without
    `--yes`: a dry-run preview of that same diff, no mutation."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            return core.close.close_project(
                conn, project_id, repo_root, confirm=yes, open_pr=pr, **_human_kwargs()
            )
        result = core.idempotency.once(
            conn, request_id, "close", _apply, actor=_invoker()[0], atomic=False,
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def pause(
    project_id: int = typer.Argument(...),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Emergency stop (plan section 5): the project refuses `start`, the
    dashboard turns red, and its runner processes are killed -- their
    in-flight nodes go back to `ready` without using up an attempt."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            return core.projects.pause_project(
                conn, project_id, repo_root=repo_root, **_human_kwargs()
            )
        result = core.idempotency.once(
            conn, request_id, "pause", _apply, actor=_invoker()[0], atomic=False,
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def resume(
    project_id: int = typer.Argument(...),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Reverse `pause`: the project goes back to `executing`."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            return core.projects.resume_project(conn, project_id, **_human_kwargs())
        result = core.idempotency.once(
            conn, request_id, "resume", _apply, actor=_invoker()[0],
        )
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from e
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def handoff(
    node_id: int = typer.Argument(...),
    to: str = typer.Option(
        ..., "--to", help="new owner identity, e.g. a human session id or 'runner:<agent>'"
    ),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb (plan section 6 "Handoff"): reassign a node's lease so a
    different driver (an interactive session <-> the unattended runner)
    can resume purely from DB state."""
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            return core.nodes.handoff(
                conn, node_id, new_owner=to, lease_minutes=config.planning.lease_minutes,
                **_human_kwargs(),
            )
        result = core.idempotency.once(
            conn, request_id, "handoff", _apply, actor=_invoker()[0],
        )
    finally:
        conn.close()
    _echo_json(result)


@app.command(name="import")
def import_(
    from_: str = typer.Option(
        ..., "--from", help="'github#N', e.g. 'github#123'",
    ),
    node_id: int = typer.Option(..., "--node-id", help="node to link the issue to"),
    data: Path = typer.Option(
        None, "--data", help="local JSON file with the issue's data, instead of a real `gh` "
        'fetch; shape: {"number": N, "title": ..., "url": ..., "body": ...}',
    ),
    repo: str = typer.Option(
        None, "--repo", help="'owner/name' for the `gh` fetch; omit to use the current "
        "directory's gh-detected repo",
    ),
    request_id: str = typer.Option(None, "--request-id", help=REQUEST_ID_HELP),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb (plan section 4/11): link NODE_ID to a GitHub issue/PR
    in `external_refs`. `--from github#N` names the issue. Fetches the
    issue's data via a real `gh issue view`/`gh pr view` call unless
    `--data PATH` supplies it locally instead (see core/imports.py,
    core/github.py)."""
    system, _, rest = from_.partition("#")
    if system != "github" or not rest.isdigit():
        typer.echo(f"unsupported --from {from_!r}; expected 'github#N'", err=True)
        raise typer.Exit(1)
    issue_number = int(rest)
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        def _apply():
            if data is not None:
                result = core.imports.import_github_issue(
                    conn, node_id, issue_number, data_path=data, **_human_kwargs(),
                )
            else:
                fetch_fn = functools.partial(core.github.fetch_issue_via_gh, repo=repo)
                try:
                    result = core.imports.import_github_issue(
                        conn, node_id, issue_number, fetch_fn=fetch_fn, **_human_kwargs(),
                    )
                except core.github.GithubError as exc:
                    typer.echo(f"gh fetch failed: {exc}", err=True)
                    raise typer.Exit(1) from exc
            return result
        result = core.idempotency.once(
            conn, request_id, "import", _apply, actor=_invoker()[0], atomic=False,
        )
    finally:
        conn.close()
    _echo_json(result)


def _user_errors() -> tuple[type[Exception], ...]:
    """Core's domain errors: the ones the API maps to 404/409, plus
    strict-mode and config failures. They describe the user's input or
    the project's state, so the CLI reports them as one line."""
    return (
        LookupError,
        core.nodes.NodeError,
        core.gates.GateError,
        core.asks.AskError,
        core.state_machine.InvalidTransition,
        core.state_machine.NotLeaseOwner,
        core.merge.MergeError,
        core.close.CloseError,
        core.imports.ImportError_,
        core.actor.HumanOnly,
        core.strict.StrictModeError,
        core.config.ConfigError,
        core.migrate.MigrateError,
    )


def main() -> None:
    try:
        app()
    except _user_errors() as e:
        # KeyError and IndexError are LookupErrors too, but from muvue
        # they mean a bug, so they keep their traceback.
        if isinstance(e, (KeyError, IndexError)):
            raise
        typer.echo(f"error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
