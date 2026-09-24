"""muvue CLI (Typer). Every mutating command calls into muvue.core only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from muvue import core
from muvue.core.config import ConfigError

app = typer.Typer(no_args_is_help=True, add_completion=False)
adapter_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Vendor adapter config writers (plan section 7).")
app.add_typer(adapter_app, name="adapter")
project_app = typer.Typer(no_args_is_help=True, add_completion=False, help="Project-level verbs.")
app.add_typer(project_app, name="project")

NOT_IMPLEMENTED = "not implemented in P0"

_CLAUDE_HOOK_NAMES = {"session-start", "pre-tool-use", "pre-compact", "stop"}


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
) -> None:
    """Replay `events` and report whether it reproduces the live DB.

    With `--project-id` (P6 acceptance #3): replay only that project's
    exported `.jsonl.gz` history archive (`core.history`) and compare
    against that project's live state instead of the whole DB.
    """
    repo_root = _find_repo_root(path)
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
    """Event export. With `--project-id` (P6): the real per-project
    `.jsonl.gz` archive (`core.history.export_project`) `close` also
    writes. Without it: the whole-DB flat dump `export` has always done
    (P0)."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        if project_id is not None:
            out = core.history.export_project(conn, project_id, repo_root)
            count = len(core.history.export_project_events(conn, project_id))
        else:
            rows = [dict(r) for r in core.events.all_events(conn)]
            out = repo_root / ".muvue" / "history" / "events.json"
            out.write_text(json.dumps(rows, default=str, indent=2))
            count = len(rows)
    finally:
        conn.close()
    typer.echo(f"exported {count} events to {out}")


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


@adapter_app.command("install")
def adapter_install(
    name: str = typer.Argument(..., help="claude-code, codex, gemini, or cursor"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Write the given vendor's config, pointing it at muvue's CLI/MCP
    surface (plan section 7). Codex/Gemini/Cursor writers are best-effort
    and unverified against live vendor docs -- see docs/providers.md."""
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
def audit() -> None:
    """Structure-graph drift audit. Ships P7."""
    typer.echo(NOT_IMPLEMENTED)


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

    `session-start`/`pre-tool-use`/`pre-compact`/`stop` (P3): the Claude
    Code adapter's hook handlers (see core/claude_hooks.py). Claude Code
    passes hook-specific JSON on stdin and reads a JSON decision back
    from stdout; a `"decision": "block"` response exits 2 (Claude Code's
    documented block convention) so its reason text reaches the model,
    exit 0 otherwise.

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

    if name not in _CLAUDE_HOOK_NAMES:
        return

    repo_root = _find_repo_root(path)
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    node_id = payload.get("node_id")
    if node_id is None:
        node_id = core.adapters.get_current_node(repo_root)

    conn = _db_connect(repo_root)
    try:
        if name == "session-start":
            result = core.claude_hooks.session_start(conn, node_id=node_id)
        elif name == "pre-tool-use":
            result = core.claude_hooks.pre_tool_use(
                conn, tool_name=payload.get("tool_name", ""),
                tool_input=payload.get("tool_input"), node_id=node_id,
            )
        elif name == "pre-compact":
            result = core.claude_hooks.pre_compact(
                conn, node_id=node_id, summary=payload.get("summary"),
            )
        else:  # stop
            result = core.claude_hooks.stop(conn, node_id=node_id)
    finally:
        conn.close()

    typer.echo(json.dumps(result))
    if result.get("decision") == "block":
        raise typer.Exit(2)


@project_app.command("create")
def project_create(
    goal: str = typer.Option(..., "--goal"),
    budget_unit: str = typer.Option("usd", "--budget-unit"),
    budget_limit: float = typer.Option(0, "--budget-limit"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Create a new project (`core.projects.create_project`). Prints the
    created project row, including its `id`."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.projects.create_project(
            conn, goal=goal, budget_unit=budget_unit, budget_limit=budget_limit,
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
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Gate 1: agent submits a spec node for a project
    (`core.gates.submit_spec`). Created `pending`; a human then calls
    `approve spec:ID` before decomposition into tasks."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.gates.submit_spec(conn, project_id=project_id, title=title, body_md=body)
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
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Gate 2: agent decomposes an approved spec into a task node
    (`core.nodes.create_node`). Created `pending` under the spec (`parent_id`);
    a human then calls `approve gate2:PROJECT_ID` to freeze criteria and
    unblock `start`."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
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
            status="pending",
            actor="agent",
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
            config=config, repo_root=repo_root,
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
    run_checks: bool = typer.Option(
        False, "--run-checks",
        help="actually run config.checks.test for auto-criteria nodes (light mode, "
        "opt-in -- see docs/decisions.md #38); default preserves risk-tier-only gating",
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
            run_checks=core.review.default_run_checks if run_checks else None,
            cwd=str(repo_root),
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
    if core.adapters.get_current_node(repo_root) == node_id:
        core.adapters.clear_current_node(repo_root)
    _echo_json(result)


@app.command()
def brief(
    node_id: int = typer.Argument(...),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """What an agent needs to start work on a node (plan section 4):
    the node, its lessons/pinned notes, and any open question."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.queries.brief_node(conn, node_id)
    finally:
        conn.close()
    _echo_json(result)


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
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.nodes.add_note(conn, node_id, kind=kind, text=text, actor="agent", pinned=pinned)
    finally:
        conn.close()
    _echo_json(result)


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
        elif kind == "review":
            result = core.nodes.approve_review(conn, int(rest))
        else:
            typer.echo(f"unknown approve target: {target!r}", err=True)
            raise typer.Exit(1)
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def answer(
    question_id: int = typer.Argument(...),
    text: str = typer.Option(..., "--text"),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human-only: answer an open question created by `ask` (never exposed
    over MCP; plan section 4). Calls core.asks.answer, which records the
    answer as a `feedback` note on the question's node."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.asks.answer(conn, question_id, text=text)
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
def run(
    agent: str = typer.Option(
        None, "--agent", help="override [routing]: use this agent for every scheduled node"
    ),
    parallel: int = typer.Option(
        1, "--parallel", help="max nodes scheduled concurrently (still capped by each "
        "agent's own max_concurrency)"
    ),
    project_id: int = typer.Option(None, "--project-id", help="scope to one project"),
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
    result = core.runner.run(
        db_path, config, repo_root, agent_override=agent, parallel=parallel, project_id=project_id,
    )
    _echo_json(result)


@app.command()
def merge(
    node_id: int = typer.Argument(
        None, help="merge this done node's branch onto main; omit to merge every "
        "pending done node in dependency order"
    ),
    pr: bool = typer.Option(
        False, "--pr", help="also generate a PR description body (plan section 6/11, P6) -- "
        "criteria + relevant decisions/notes/linked issues; requires a single NODE_ID, no real "
        "`gh pr create` call (no network access here)",
    ),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb (plan section 6 "Merging"): attempt to merge strict-mode
    node branch(es) onto the airlock's main. On conflict: the node ->
    blocked(conflict), attempts + 1, and a "rebase onto main" subtask is
    created. Light-mode / never-started-strict nodes are a documented
    no-op (see core/merge.py)."""
    if pr and node_id is None:
        typer.echo("--pr requires a single NODE_ID", err=True)
        raise typer.Exit(1)
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        if node_id is not None:
            result = core.merge.attempt_merge(conn, node_id, repo_root)
            if pr:
                result["pr_body"] = core.pr.generate_pr_body(conn, node_id)
        else:
            result = core.merge.merge_pending(conn, repo_root)
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
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb (plan section 9): with `--yes`, commit the project's
    proposed structure diff (new/changed components, decisions, promoted
    lessons) to `.muvue/components.json`/`.muvue/decisions.json` (committed
    on `main`), flip the project to `closed`, and export its event
    history to `.muvue/history/<id>.jsonl.gz`. Without `--yes`: a
    dry-run preview of that same diff, no mutation (see core/close.py)."""
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.close.close_project(conn, project_id, repo_root, confirm=yes)
    finally:
        conn.close()
    _echo_json(result)


@app.command()
def pause() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def resume() -> None:
    typer.echo(NOT_IMPLEMENTED)


@app.command()
def handoff(
    node_id: int = typer.Argument(...),
    to: str = typer.Option(
        ..., "--to", help="new owner identity, e.g. a human session id or 'runner:<agent>'"
    ),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb (plan section 6 "Handoff"): reassign a node's lease so a
    different driver (an interactive session <-> the unattended runner)
    can resume purely from DB state."""
    repo_root = _find_repo_root(path)
    config = _load_config(repo_root)
    conn = _db_connect(repo_root)
    try:
        result = core.nodes.handoff(
            conn, node_id, new_owner=to, lease_minutes=config.planning.lease_minutes,
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
        None, "--data", help="local JSON file with the issue's data (no live GitHub API access "
        "in this environment -- plan section 11/working rule 2; shape: "
        '{"number": N, "title": ..., "url": ..., "body": ...}',
    ),
    path: Path = typer.Option(Path("."), "--path"),
) -> None:
    """Human verb (plan section 4/11): link NODE_ID to a GitHub issue/PR
    in `external_refs`. `--from github#N` names the issue; `--data PATH`
    supplies its data locally (see core/imports.py -- the real GitHub
    fetch is a documented, injectable seam, not wired to a live API
    here)."""
    system, _, rest = from_.partition("#")
    if system != "github" or not rest.isdigit():
        typer.echo(f"unsupported --from {from_!r}; expected 'github#N'", err=True)
        raise typer.Exit(1)
    issue_number = int(rest)
    repo_root = _find_repo_root(path)
    conn = _db_connect(repo_root)
    try:
        result = core.imports.import_github_issue(
            conn, node_id, issue_number, data_path=data,
        )
    finally:
        conn.close()
    _echo_json(result)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
