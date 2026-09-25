"""`doctor [--repair]`: sanity-check a .muvue/ install (plan section 5)."""

from __future__ import annotations

import json
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import adapters as adapters_mod
from . import db as core_db
from . import gitutil
from . import hooks as hooks_mod
from . import runner as runner_mod
from .config import PROTOCOL_VERSION, ConfigError, load_config
from .repo_init import HOOK_NAMES, _hook_marker, _install_hook_shim, init_repo


QUEUE_DEPTH_WARN_THRESHOLD = 1000

# v4 section 8a control 7: live daemon-security probes. Matches
# `cli.main.serve`'s own `--port` default so a `doctor` run with no
# explicit port finds an already-running default-configured daemon.
DEFAULT_DAEMON_PORT = 8765
_PROBE_TIMEOUT_SECONDS = 2.0
_THROWAWAY_STARTUP_TIMEOUT_SECONDS = 15.0


@dataclass
class DoctorReport:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    repaired: list[str] = field(default_factory=list)
    # v4 section 4a: "`doctor` reports queue depth and warns above
    # 1000." Non-fatal -- unlike `issues`, a warning here never flips
    # `ok` to False; a deep-but-still-draining queue isn't a broken
    # install.
    warnings: list[str] = field(default_factory=list)
    # Informational lines, always printed (e.g. the hook queue depth).
    info: list[str] = field(default_factory=list)
    queue_depth: int = 0

    def fail(self, msg: str) -> None:
        self.ok = False
        self.issues.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _probe_request(
    base_url: str, path: str, *, method: str = "GET",
    headers: dict | None = None, json_body: dict | None = None, form_body: dict | None = None,
) -> tuple[int | None, dict]:
    """Stdlib-only (`urllib`, no new dependency) probe request. Returns
    `(status_code, response_headers)`; `status_code` is `None` if the
    daemon didn't answer at all (connection refused/timeout)."""
    headers = dict(headers or {})
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers.setdefault("Content-Type", "application/json")
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(base_url + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_SECONDS) as resp:
            return resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers.items()) if e.headers else {}
    except OSError:
        return None, {}


def non_loopback_addresses() -> list[str]:
    """This machine's IPv4 addresses other than 127.0.0.0/8, found
    without sending a packet: connecting a UDP socket only selects a
    route, and the host's own name may resolve to more."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    return sorted({a for a in found if not a.startswith("127.") and a != "0.0.0.0"})


def probe_bind(port: int) -> list[str]:
    """Control 1: the daemon must not accept connections on any address
    but loopback. Tries a TCP connect to `port` on each non-loopback
    address of this machine."""
    issues = []
    for address in non_loopback_addresses():
        try:
            with socket.create_connection((address, port), timeout=_PROBE_TIMEOUT_SECONDS):
                pass
        except OSError:
            continue
        issues.append(
            f"doctor: port {port} accepts connections on {address}, not only loopback "
            "(v4 section 8a control 1); if that is `muvue serve`, restart it without "
            "--i-know-this-is-exposed"
        )
    return issues


def _daemon_reachable(base_url: str) -> bool:
    status, _ = _probe_request(base_url, "/healthz")
    return status == 200


def run_security_probes(
    repo_root: Path, *, port: int | None = None
) -> tuple[list[str], list[str]]:
    """v4 section 8a control 7: "`doctor` verifies 1-4 by issuing live
    probe requests against a running daemon and fails loudly." Returns
    `(issues, notes)`.

    Controls 2 (Host), 3 (Origin) and 4 (JSON-content-type /
    query-string-token halves) are verifiable *without* a valid session
    token -- they reject before or independent of auth -- which is
    exactly what makes probing an *already-running* daemon possible at
    all: this process has no way to learn that daemon's in-memory-only
    token (control 5's own point), so any probe strategy that required
    one would only ever be able to test a daemon this same `doctor`
    invocation started. Control 1 (bind) is probed against an
    already-running daemon by connecting to its port on each of this
    machine's non-loopback addresses (`probe_bind`); a throwaway daemon
    is always loopback, so it isn't probed. See
    docs/decisions.md #87 for the "probe or spin up a throwaway" choice
    documented below.
    """
    issues: list[str] = []
    notes: list[str] = []
    target_port = port or DEFAULT_DAEMON_PORT
    base_url = f"http://127.0.0.1:{target_port}"
    own_proc: subprocess.Popen | None = None
    scratch_dir: tempfile.TemporaryDirectory | None = None

    try:
        # Before anything else: a daemon bound to a non-loopback address
        # only is invisible on 127.0.0.1, so probe the bind first.
        issues.extend(probe_bind(target_port))
        if not _daemon_reachable(base_url):
            # v4 section 8a control 7 explicitly leaves this choice to
            # the implementer ("decide sensibly whether doctor should
            # skip this check ... or start a throwaway daemon instance
            # to test against and tear it down"). Decision (docs/
            # decisions.md #87): spin up a throwaway daemon rather than
            # skip -- a skip-only doctor would silently stop verifying
            # this phase's own controls on any machine without a daemon
            # already running, which is the common case right after
            # `muvue init`. The throwaway daemon serves an isolated
            # scratch repo (a fresh `init_repo` in a tempdir), never
            # `repo_root` itself: `muvue serve` reconciles leases and
            # drains the event queue on startup (plan section 8), and a
            # read-only diagnostic command must never have that kind of
            # side effect on the actual repo being checked.
            scratch_dir = tempfile.TemporaryDirectory(prefix="muvue-doctor-probe-")
            scratch_root = Path(scratch_dir.name)
            init_repo(scratch_root)
            free_port = _free_port()
            own_proc = subprocess.Popen(
                [sys.executable, "-m", "muvue", "serve", str(scratch_root), "--port", str(free_port)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            ready = False
            deadline = time.monotonic() + _THROWAWAY_STARTUP_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                line = own_proc.stdout.readline()
                if not line:
                    if own_proc.poll() is not None:
                        break
                    continue
                if "listening on" in line:
                    ready = True
                    break
            if not ready:
                issues.append(
                    "doctor: could not start a throwaway daemon to verify daemon security "
                    "controls (v4 section 8a control 7) -- check `muvue serve` starts cleanly"
                )
                return issues, notes
            base_url = f"http://127.0.0.1:{free_port}"
            notes.append(
                f"doctor: no daemon was already running on port {target_port}; started a "
                f"throwaway one (against a scratch repo, not this one) on port {free_port} "
                "to verify security controls, and will tear it down when done"
            )
        else:
            notes.append(f"doctor: probing the daemon already running on port {target_port}")

        # Control 2: bad Host must 403.
        status, _ = _probe_request(base_url, "/healthz", headers={"Host": "evil.example.com"})
        if status != 403:
            issues.append(
                f"doctor: daemon did NOT reject a bad Host header (v4 section 8a control 2) "
                f"-- got status {status}"
            )

        # Control 3: bad Origin must 403.
        status, _ = _probe_request(
            base_url, "/healthz", headers={"Origin": "http://evil.example.com"}
        )
        if status != 403:
            issues.append(
                f"doctor: daemon did NOT reject a bad Origin header (v4 section 8a control 3) "
                f"-- got status {status}"
            )

        # Control 3 cont.: no CORS headers ever, even on a normal request.
        _, headers_out = _probe_request(base_url, "/healthz")
        cors_headers = [h for h in headers_out if h.lower().startswith("access-control-")]
        if cors_headers:
            issues.append(
                f"doctor: daemon emitted CORS headers ({cors_headers}) -- v4 section 8a "
                "control 3 requires none, ever"
            )

        # Control 4: form-encoded mutating POST must 403 (HTML-form CSRF).
        status, _ = _probe_request(
            base_url, "/nodes/999999999/start", method="POST",
            form_body={"owner": "doctor-probe"},
        )
        if status != 403:
            issues.append(
                f"doctor: daemon did NOT reject a form-encoded mutating POST (v4 section 8a "
                f"control 4) -- got status {status}"
            )

        # Control 4: missing token must 403.
        status, _ = _probe_request(
            base_url, "/nodes/999999999/start", method="POST",
            json_body={"owner": "doctor-probe"},
        )
        if status != 403:
            issues.append(
                f"doctor: daemon did NOT reject a mutating POST with no session token (v4 "
                f"section 8a control 4) -- got status {status}"
            )

        # Control 4: a token-shaped query string param must not be honored.
        status, _ = _probe_request(
            base_url, "/nodes/999999999/start?token=doctor-probe-fake-token", method="POST",
            json_body={"owner": "doctor-probe"},
        )
        if status != 403:
            issues.append(
                f"doctor: daemon accepted a token-shaped query-string param as auth (v4 "
                f"section 8a control 4 requires the Authorization header, never a query "
                f"string) -- got status {status}"
            )
    finally:
        if own_proc is not None:
            own_proc.terminate()
            try:
                own_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                own_proc.kill()
                own_proc.wait(timeout=5)
        if scratch_dir is not None:
            scratch_dir.cleanup()

    return issues, notes


PROVIDERS_DOC = "docs/providers.md"
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)+)")
_PIN_RE = re.compile(r"^\s*(>=|<=|==|>|<|=)?\s*(\d+(?:\.\d+)*)\s*$")
_AUTH_CHECK_TIMEOUT_S = 15


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def version_satisfies(version_output: str, pin: str) -> bool | None:
    """Does the first dotted version in `version_output` satisfy `pin`
    (comma-separated clauses such as `>=2.0,<3`)? None if no version
    can be read or the pin doesn't parse."""
    found = _VERSION_RE.search(version_output)
    if found is None:
        return None
    have = _version_tuple(found.group(1))
    for clause in pin.split(","):
        match = _PIN_RE.match(clause)
        if match is None:
            return None
        op, want_text = match.group(1) or "==", match.group(2)
        want = _version_tuple(want_text)
        width = max(len(have), len(want))
        a, b = have + (0,) * (width - len(have)), want + (0,) * (width - len(want))
        ok = {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "==": a == b, "=": a == b}[op]
        if not ok:
            return False
    return True


def _check_drivers(config, report: "DoctorReport") -> None:
    """v4 section 6: run each driver's `auth_check` (a failure usually
    means logged out or an expired session) and compare the version it
    prints against `pinned_version`. Warnings, not errors: another
    driver may still be routable."""
    for name, agent in config.agents.items():
        if not agent.auth_check.strip():
            continue
        try:
            check = subprocess.run(
                agent.auth_check, shell=True, capture_output=True, text=True,
                timeout=_AUTH_CHECK_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            report.warn(
                f"agents.{name}: auth_check timed out after {_AUTH_CHECK_TIMEOUT_S}s "
                f"(see {PROVIDERS_DOC})"
            )
            continue
        output = (check.stdout + check.stderr).strip()
        if check.returncode != 0:
            report.warn(
                f"agents.{name}: auth_check failed (exit {check.returncode}): "
                f"{output[:200] or 'no output'} -- not installed, logged out or session "
                f"expired? See {PROVIDERS_DOC}"
            )
            continue
        found = _VERSION_RE.search(output)
        version = found.group(1) if found else "unknown version"
        if agent.pinned_version.strip():
            ok = version_satisfies(output, agent.pinned_version)
            if ok is not True:
                report.warn(
                    f"agents.{name}: {version} does not satisfy pinned_version "
                    f"{agent.pinned_version!r}; vendor CLI output formats change between "
                    f"versions (see {PROVIDERS_DOC})"
                )
                continue
        report.info.append(
            f"agents.{name}: {version}"
            + (f" (pinned {agent.pinned_version})" if agent.pinned_version.strip() else "")
        )


def _list_orphan_worktrees(repo_root: Path, db_path: Path, report: "DoctorReport") -> None:
    """`doctor --repair` lists worktrees no active node owns (plan
    section 5, "Leases"). Listing only: a worktree can hold uncommitted
    work, so removing it is left to the human."""
    from . import strict as strict_mod

    root = strict_mod.worktrees_root(repo_root)
    if not root.exists():
        return
    conn = core_db.connect(db_path)
    try:
        for path in sorted(root.iterdir()):
            match = re.fullmatch(r"node-(\d+)", path.name)
            if match is None:
                continue
            row = core_db.query_one(
                conn, "SELECT status, deleted_at FROM nodes WHERE id = ?", (int(match.group(1)),),
            )
            if row is None:
                reason = "no such node"
            elif row["deleted_at"] is not None:
                reason = "node deleted"
            elif row["status"] in ("done", "failed"):
                reason = f"node {row['status']}"
            else:
                continue
            report.info.append(f"orphan worktree: {path} ({reason})")
    finally:
        conn.close()


def run_doctor(
    repo_root: Path, *, repair: bool = False,
    skip_security_probes: bool = False, daemon_port: int | None = None,
) -> DoctorReport:
    repo_root = Path(repo_root)
    report = DoctorReport()
    muvue_dir = repo_root / ".muvue"

    if not muvue_dir.exists():
        report.fail(f"{muvue_dir} does not exist; run `muvue init`")
        return report

    config_path = muvue_dir / "config.toml"
    config = None
    if not config_path.exists():
        report.fail(f"{config_path} missing")
    else:
        try:
            config = load_config(config_path)
        except ConfigError as e:
            report.fail(str(e))

    # v4 section 2: "`doctor` errors if `budget.unit` is not producible by
    # that driver's `cost_model`." Mirrors decision #72's existing
    # cost_model -> agent_spend-unit mapping (core.runner.
    # EXPECTED_BUDGET_UNIT), not a new one.
    if config is not None:
        for agent_name, agent_cfg in config.agents.items():
            if agent_cfg.budget is None:
                continue
            expected_unit = runner_mod.EXPECTED_BUDGET_UNIT.get(agent_cfg.cost_model)
            if agent_cfg.budget.unit != expected_unit:
                report.fail(
                    f"agents.{agent_name}.budget.unit is {agent_cfg.budget.unit!r}, which "
                    f"cost_model {agent_cfg.cost_model!r} cannot produce (expected "
                    f"{expected_unit!r})"
                )

    db_path = muvue_dir / "muvue.db"
    if not db_path.exists():
        report.fail(f"{db_path} missing")
    else:
        conn = core_db.connect(db_path)
        try:
            version = core_db.get_schema_version(conn)
            if version == 0:
                report.fail(f"{db_path} has no schema_version recorded")
        finally:
            conn.close()

    # v4 section 4a: hook fast-path queue depth (`.muvue/queue.jsonl`,
    # `muvue._hook`'s spool -- see src/muvue/_hook.py). A deep queue
    # means drain (absent-daemon CLI-callback drain, or the daemon's
    # continuous loop) is falling behind the spool rate.
    depth = hooks_mod._pending_line_count(repo_root)
    report.queue_depth = depth
    report.info.append(f"hook queue depth: {depth}")
    if depth > QUEUE_DEPTH_WARN_THRESHOLD:
        report.warn(
            f"hook queue depth is {depth} (> {QUEUE_DEPTH_WARN_THRESHOLD}) in "
            f"{muvue_dir}; drain is falling behind"
        )

    husky_dir = repo_root / ".husky"
    for name in HOOK_NAMES:
        path = husky_dir / name if husky_dir.is_dir() else repo_root / ".git" / "hooks" / name
        begin, _ = _hook_marker(name)
        if not path.exists() or begin not in path.read_text():
            if repair:
                _install_hook_shim(path, name, {})
                report.repaired.append(f"reinstalled hook shim: {path}")
            else:
                report.fail(f"hook shim missing or not installed: {path} (--repair to fix)")
        else:
            content = path.read_text()
            # Verify the shim uses an absolute interpreter path (plan
            # section 5). v4 section 4a: current shims are
            # `PYTHONPATH=<dir> <python> -S -m muvue._hook NAME` (the
            # `PYTHONPATH=` prefix is required for `-S` to still find
            # the `muvue` package -- see core.repo_init.
            # hook_fast_path_command) -- the interpreter is the token
            # right before `-S`, not simply the first token anymore.
            # `-m muvue hook` (no `PYTHONPATH=` prefix, first token IS
            # the interpreter) recognized too for a pre-v4 shim not yet
            # re-`init`/`doctor --repair`ed.
            for line in content.splitlines():
                if "-m muvue._hook " in line or " -m muvue hook " in line:
                    tokens = shlex.split(line)
                    interp = None
                    if "-S" in tokens:
                        idx = tokens.index("-S")
                        if idx > 0:
                            interp = tokens[idx - 1]
                    elif tokens and tokens[0].startswith("/"):
                        interp = tokens[0]
                    if interp is None or not Path(interp).is_absolute():
                        report.fail(f"hook shim in {path} does not use an absolute path")
                    break

    # Adapter protocol_version drift (plan section 7: "doctor warns on
    # mismatch"), across every adapter file muvue writes.
    if config is not None:
        if config.protocol_version != PROTOCOL_VERSION:
            report.warn(
                f"config.toml says protocol_version={config.protocol_version}, this muvue "
                f"speaks protocol_version={PROTOCOL_VERSION}; see CHANGELOG.md, set it to "
                f"{PROTOCOL_VERSION}, then re-run `muvue adapter install`"
            )
        for rel, installed in adapters_mod.installed_protocol_versions(repo_root).items():
            if installed != config.protocol_version:
                report.warn(
                    f"{rel} was installed for protocol_version={installed}, repo is now "
                    f"protocol_version={config.protocol_version}; re-run `muvue adapter install`"
                )
        _check_drivers(config, report)

    if repair and db_path.exists():
        _list_orphan_worktrees(repo_root, db_path, report)

    # Strict mode (plan section 5, P4): "Agent never holds a `main`
    # checkout" -- a node actively being worked (`in_progress`/`review`)
    # must have a bound worktree that still exists on disk, distinct from
    # `repo_root` itself. A missing binding or a worktree directory
    # that's disappeared (removed by hand, or `worktree_setup` never
    # completed for a pre-P4 in-progress node) is exactly the state
    # `doctor --repair` orphan-worktree handling (plan section 5,
    # "Leases") is meant to catch; P4 only adds detection, `--repair`
    # for worktrees is left for the daemon reconcile work that owns
    # leases (out of P4 scope).
    if config is not None and config.mode == "strict" and db_path.exists():
        conn = core_db.connect(db_path)
        try:
            rows = core_db.query_all(
                conn,
                "SELECT id, status, worktree FROM nodes "
                "WHERE status IN ('in_progress', 'review') AND deleted_at IS NULL",
            )
        finally:
            conn.close()
        for row in rows:
            worktree = row["worktree"]
            if worktree is None:
                report.fail(
                    f"node {row['id']} is {row['status']} in strict mode with no bound "
                    "worktree (agent would be holding the main checkout)"
                )
            elif not Path(worktree).exists():
                report.fail(
                    f"node {row['id']} is bound to worktree {worktree}, which no longer "
                    "exists on disk"
                )
            elif Path(worktree).resolve() == repo_root.resolve():
                report.fail(
                    f"node {row['id']}'s bound worktree is the main checkout {repo_root} "
                    "(strict mode must never point a node at main)"
                )

    # v4 section 5: branch coherence. "doctor and every start compare
    # current HEAD against the branch recorded at project start. On
    # divergence: warn in light mode, refuse in strict mode." `doctor`
    # doesn't itself refuse anything (it's a diagnostic, not a gate) --
    # the analogous outcome is `report.fail` (flips `ok` False, same as
    # every other strict-mode-only hard check above) in strict mode vs.
    # `report.warn` in light mode. Checked per active/executing/paused
    # project with a recorded branch (a project created without a
    # repo_root recorded none, and has nothing to compare).
    if config is not None and db_path.exists():
        conn = core_db.connect(db_path)
        try:
            coherence_rows = core_db.query_all(
                conn,
                "SELECT id, branch FROM projects WHERE phase IN "
                "('planning', 'executing', 'paused') AND branch IS NOT NULL",
            )
        finally:
            conn.close()
        if coherence_rows:
            current_branch = gitutil.current_branch(repo_root)
            if current_branch is not None:
                for row in coherence_rows:
                    if current_branch != row["branch"]:
                        msg = (
                            f"project {row['id']} started on branch "
                            f"{row['branch']!r}, repo is currently on "
                            f"{current_branch!r} (branch coherence, v4 section 5)"
                        )
                        if config.mode == "strict":
                            report.fail(msg)
                        else:
                            report.warn(msg)

    # v4 section 8a control 7: live daemon-security probes. Only makes
    # sense once config/DB are known-good (skipped above already fails
    # the report for a broken install) -- there's nothing meaningful to
    # spin a throwaway daemon up against otherwise.
    if config is not None and db_path.exists() and not skip_security_probes:
        probe_issues, probe_notes = run_security_probes(repo_root, port=daemon_port)
        for issue in probe_issues:
            report.fail(issue)
        for note in probe_notes:
            report.warn(note)

    return report
