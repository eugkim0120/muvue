"""`python -m bench run`: drive muvue and a plain arm through a suite
with the scripted agent, score both, and write bench/results/<date>-<suite>.json."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

from bench.score import first_event_actor, node_ids_with_status, node_was_flagged, score_muvue
from bench.suite import SUITES_DIR, Suite, load_suite, run_acceptance, task_order

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
SHIM = BENCH_DIR / "shim_agent.py"
HUMAN_VERBS = {"approve", "reject", "merge", "close", "answer", "ack"}
# Verbs whose CLI command always prints a JSON result via `_echo_json`
# (src/muvue/cli/main.py); anything else on their stdout is a bug, not a
# human-readable message to fall back to silently.
JSON_VERBS = {"project", "spec", "decompose", "run", "merge"}


class ArmError(RuntimeError):
    pass


class Stalled(RuntimeError):
    pass


class Muvue:
    """Runs `python -m muvue` against one repo with an isolated HOME,
    logging every command; a non-zero exit raises ArmError."""

    def __init__(self, repo: Path, env: dict, cap: int):
        self.repo, self.env, self.cap = repo, env, cap
        self.steps: list[list] = []

    def __call__(self, *args: str, path_flag: bool = True):
        if len(self.steps) >= self.cap:
            raise Stalled(f"step cap {self.cap} reached")
        argv = [*args, "--path", str(self.repo)] if path_flag else list(args)
        out = subprocess.run(
            [sys.executable, "-m", "muvue", *argv], env=self.env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL,
        )
        redacted = [str(a).replace(str(self.repo), "<repo>") for a in args]
        self.steps.append([*redacted, out.returncode])
        if out.returncode != 0:
            raise ArmError(f"muvue {' '.join(args)} exited {out.returncode}: {out.stderr.strip()[-2000:]}")
        try:
            return json.loads(out.stdout)
        except json.JSONDecodeError:
            if args[0] in JSON_VERBS and out.stdout.strip():
                raise ArmError(
                    f"muvue {' '.join(args)} printed non-JSON stdout, so the run loop can't read its "
                    f"result: {out.stdout.strip()[:500]!r}"
                )
            return out.stdout

    def flexibility(self) -> dict:
        return {"commands": len(self.steps), "human_verbs": sum(1 for s in self.steps if s[0] in HUMAN_VERBS),
                "label": "measured", "steps": self.steps}


def _env(work: Path, suite: Suite) -> dict:
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    base = {k: v for k, v in os.environ.items() if not k.startswith("MUVUE_") and k != "CLAUDE_PROJECT_DIR"}
    return {**base, "HOME": str(home),
            "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.com",
            "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.com",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
            "BENCH_SUITE_DIR": str(suite.root), "BENCH_TASK_MAP": str(work / "task_map.json"),
            "BENCH_SHIM_LOG": str(work / "shim.jsonl")}


def _start_repo(suite: Suite, repo: Path, env: dict) -> None:
    shutil.copytree(suite.root / "start", repo)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", f"Start {suite.name}"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)


def _configure(repo: Path) -> None:
    cfg = repo / ".muvue" / "config.toml"
    text = cfg.read_text()
    py = sys.executable
    for pattern, value in (
        (r"(?m)^worktree_mode = .*$", 'worktree_mode = "per_node"'),
        (r"(?m)^worktree_setup = .*$", 'worktree_setup = ""'),
        (r"(?m)^test = .*$", f'test = "{py} -m pytest -q -p no:cacheprovider"'),
        (r"(?m)^lint = .*$", 'lint = "true"'),
    ):
        text, n = re.subn(pattern, value, text)
        if n != 1:
            raise ArmError(f"config.toml: expected one match for {pattern!r}, found {n}")
    text, n = re.subn(r"(?ms)^\[routing\]\n.*?(?=^\[|\Z)", "", text)
    if n != 1:
        raise ArmError("config.toml: expected one [routing] table")
    text += (
        f'\n[agents.shim]\ncommand = "{py} {SHIM}"\nusage_parser = "claude_stream_json"\ncost_model = "usd"\n'
        '\n[routing]\nspec = "shim"\ntask = "shim"\nsubtask = "shim"\n'
    )
    cfg.write_text(text)


def run_muvue_arm(suite: Suite, work: Path, cap: int) -> dict:
    env = _env(work, suite)
    repo = work / "repo"
    _start_repo(suite, repo, env)
    m = Muvue(repo, env, cap)
    started = time.monotonic()
    status, error = "ok", None
    run_merges: list[dict] = []
    final_merge: object = None
    approved_after_flag = 0
    try:
        m("init", str(repo), path_flag=False)
        _configure(repo)
        # muvue's `init` appends to the tracked .gitignore; commit that now,
        # as a user would, so the checkout is clean before any node work --
        # otherwise every per_node merge defers on `dirty_checkout` forever
        # (core.merge._merge_into_checkout) and nothing ever merges.
        subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "muvue init"], cwd=repo, env=env, check=True, capture_output=True)
        pid = m("project", "create", "--goal", suite.goal)["id"]
        sid = m("spec", str(pid), "--title", suite.spec_title, "--body", suite.spec_body)["id"]
        m("approve", f"spec:{sid}")
        ids: dict[str, int] = {}
        for task in task_order(suite):
            args = ["decompose", str(sid), "--title", task.title, "--criteria", task.criteria, "--criteria-mode", "auto"]
            for glob in task.predicted_touches:
                args += ["--predicted-touches", glob]
            for dep, carry in zip(task.depends_on, task.carries):
                args += ["--depends-on", str(ids[dep]), "--carries", carry]
            ids[task.key] = m(*args)["id"]
        (work / "task_map.json").write_text(json.dumps({str(v): k for k, v in ids.items()}))
        m("approve", f"gate2:{pid}")
        while True:
            out = m("run")
            if isinstance(out, dict) and out.get("merged"):
                run_merges.extend(out["merged"])
            in_review = node_ids_with_status(repo, "review")
            for node_id in in_review:
                if node_was_flagged(repo, node_id):
                    approved_after_flag += 1
                m("approve", f"review:{node_id}")
            if not (isinstance(out, dict) and out.get("processed")) and not in_review:
                break
        final_merge = m("merge")
    except Stalled as exc:
        status, error = "stalled", str(exc)
    except ArmError as exc:
        status, error = "errored", str(exc)
    except Exception as exc:  # noqa: BLE001 -- recorded, not swallowed; see item 6b
        tb = "\n".join(traceback.format_exc().splitlines()[-20:])
        status, error = "errored", f"{type(exc).__name__}: {exc}\n{tb}"
    wall = round(time.monotonic() - started, 2)
    passed, total = run_acceptance(suite, repo)
    scored = score_muvue(repo, work / "shim.jsonl") if (repo / ".muvue" / "muvue.db").exists() else {}
    if scored:
        scored.setdefault("truth", {})["approved_after_failed_check_or_flag"] = approved_after_flag
        unmerged_ids = scored.get("outcome", {}).get("done_unmerged_node_ids", [])
        if unmerged_ids and status == "ok":
            status = "errored"
            error = f"muvue called done nodes {unmerged_ids} unmerged on main: nothing actually merged"
    invoker = first_event_actor(repo, "gate2.approved") if (repo / ".muvue" / "muvue.db").exists() else None
    bench_approvals = sum(1 for s in m.steps if s[0] == "approve")
    return {"status": status, "error": error, "wall_s": wall,
            "reviewer": "approve-all", "invoker": invoker, "bench_approvals": bench_approvals,
            "merges": {"runs": run_merges, "final": final_merge},
            "acceptance": {"passed": passed, "total": total, "label": "measured"},
            "flexibility": m.flexibility(), **scored}


def run_plain_arm(suite: Suite, work: Path) -> dict:
    env = _env(work, suite)
    repo = work / "repo"
    _start_repo(suite, repo, env)
    started = time.monotonic()
    out = subprocess.run([sys.executable, str(SHIM), "--plain"], cwd=repo, env=env, capture_output=True, text=True)
    wall = round(time.monotonic() - started, 2)
    status = "ok" if out.returncode == 0 else "errored"
    passed, total = run_acceptance(suite, repo)
    return {"status": status, "error": None if status == "ok" else out.stderr.strip()[-2000:], "wall_s": wall,
            "acceptance": {"passed": passed, "total": total, "label": "measured"},
            "flexibility": {"commands": 0, "human_verbs": 0, "label": "measured", "steps": []},
            "tokens": {"sessions": 1, "sessions_label": "fixed (one shim session)",
                       "usage_label": "not measured (shim)"}}


def _muvue_sha() -> tuple[str, bool]:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip())
    return sha, dirty


def run_bench(suite_name: str, arms: tuple[str, ...], out_dir: Path, cap: int = 30) -> tuple[dict, Path]:
    suite = load_suite(suite_name)
    sha, dirty = _muvue_sha()
    today = dt.date.today().isoformat()
    result = {"suite": suite.name, "date": today, "muvue_sha": sha, "muvue_dirty": dirty, "arms": {}}
    with tempfile.TemporaryDirectory(prefix="muvue-bench-") as tmp:
        for arm in arms:
            work = Path(tmp) / arm
            work.mkdir()
            result["arms"][arm] = run_muvue_arm(suite, work, cap) if arm == "muvue" else run_plain_arm(suite, work)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{today}-{suite.name}.json"
    n = 2
    while path.exists():
        path = out_dir / f"{today}-{suite.name}-{n}.json"
        n += 1
    path.write_text(json.dumps(result, indent=2) + "\n")
    return result, path


def render_markdown(result: dict) -> str:
    arms = result["arms"]
    names = list(arms)

    def cell(arm: dict, *keys, default="n/a"):
        value = arm
        for k in keys:
            if not isinstance(value, dict) or k not in value:
                return default
            value = value[k]
        return value

    rows = [
        ("status", lambda a: a["status"]),
        ("acceptance", lambda a: f"{a['acceptance']['passed']}/{a['acceptance']['total']}"),
        ("truth score", lambda a: cell(a, "truth", "truth_score")),
        ("done with commit / done", lambda a: f"{cell(a, 'truth', 'done_with_commit')}/{cell(a, 'truth', 'done')}"),
        ("done claiming blocked", lambda a: cell(a, "truth", "done_claiming_blocked")),
        ("done unmerged", lambda a: cell(a, "outcome", "done_unmerged")),
        ("check runs with output (heuristic)",
         lambda a: f"{cell(a, 'truth', 'check_runs_with_output')}/{cell(a, 'truth', 'check_runs')}"),
        ("approved after failed check or flag", lambda a: cell(a, "truth", "approved_after_failed_check_or_flag")),
        ("agent sessions", lambda a: cell(a, "tokens", "sessions")),
        ("tokens in / out (replayed)", lambda a: f"{cell(a, 'tokens', 'in_tokens')} / {cell(a, 'tokens', 'out_tokens')}"),
        ("cost USD (replayed)", lambda a: cell(a, "tokens", "cost_usd")),
        ("muvue commands / human verbs", lambda a: f"{a['flexibility']['commands']} / {a['flexibility']['human_verbs']}"),
        ("wall s", lambda a: a["wall_s"]),
    ]
    lines = [f"Suite {result['suite']} · {result['date']} · muvue {result['muvue_sha'][:7]}"
             + (" (dirty)" if result["muvue_dirty"] else ""), "",
             "| metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(str(fn(arms[n])) for n in names) + " |")
    for n in names:
        if arms[n]["error"]:
            lines.append(f"\n{n} error: {arms[n]['error']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run a suite through muvue and the plain arm")
    run.add_argument("--suite", default="readinglog")
    run.add_argument("--arm", choices=("muvue", "plain", "both"), default="both")
    run.add_argument("--cap", type=int, default=30)
    run.add_argument("--out-dir", type=Path, default=BENCH_DIR / "results")
    args = parser.parse_args(argv)
    if not (SUITES_DIR / args.suite / "tasks.toml").exists():
        print(f"unknown suite: {args.suite}", file=sys.stderr)
        return 2
    arms = ("muvue", "plain") if args.arm == "both" else (args.arm,)
    result, path = run_bench(args.suite, arms, args.out_dir, args.cap)
    print(render_markdown(result))
    print(f"\nwrote {path}")
    return 1 if any(a["status"] == "errored" for a in result["arms"].values()) else 0
