"""Scripted stand-in for `claude -p --output-format stream-json`.

Task mode reads muvue's prompt on stdin, finds the node id, and maps it
to a suite task. If any of the task's `requires` files are missing from
this checkout, it commits nothing and reports "Blocked. No commit made."
(with `is_error` false, as real Claude did on readinglog task #4).
Otherwise it copies the task's fixture files in and commits them with
the `Muvue-Node` trailer. Either way it replays the task's recorded
usage, so muvue's real Claude parser and spend recording run.

`--plain` applies every task in dependency order in the current checkout,
one commit per task: the single-session arm with no muvue."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.suite import Suite, Task, load_suite_dir, task_order  # noqa: E402

NODE_RE = re.compile(r"muvue node T(\d+)\b")
USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def stream_json(text: str, usage: dict, *, is_error: bool = False) -> str:
    init = {"type": "system", "subtype": "init", "model": "bench-shim"}
    result = {
        "type": "result", "subtype": "error" if is_error else "success", "is_error": is_error,
        "result": text, "usage": {k: int(usage.get(k, 0)) for k in USAGE_KEYS},
        "total_cost_usd": float(usage.get("total_cost_usd", 0.0)), "num_turns": int(usage.get("num_turns", 0)),
        "model": "bench-shim",
    }
    return json.dumps(init) + "\n" + json.dumps(result) + "\n"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _apply(suite: Suite, task: Task, cwd: Path, node_id: int | None) -> None:
    for rel in task.files:
        dst = cwd / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(suite.root / "files" / rel, dst)
    _git(cwd, "add", "--", *task.files)
    message = task.title if node_id is None else f"{task.title}\n\nMuvue-Node: {node_id}"
    _git(cwd, "commit", "-q", "-m", message)


def _log(entry: dict) -> None:
    path = os.environ.get("BENCH_SHIM_LOG")
    if path:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")


def run_task(suite: Suite, task_map: dict, prompt: str, cwd: Path) -> int:
    match = NODE_RE.search(prompt)
    node_id = int(match.group(1)) if match else None
    key = task_map.get(str(node_id)) if node_id is not None else None
    entry = {"node_id": node_id, "task": key, "prompt_bytes": len(prompt.encode()), "missing": []}
    if key is None:
        _log({**entry, "outcome": "unmapped"})
        sys.stdout.write(stream_json(f"bench shim: no suite task for node {node_id}", {}, is_error=True))
        return 1
    task = suite.task(key)
    missing = [r for r in task.requires if not (cwd / r).exists()]
    if missing:
        _log({**entry, "outcome": "blocked", "missing": missing})
        sys.stdout.write(stream_json(
            "Blocked. No commit made. Missing from this checkout: " + ", ".join(missing), task.usage))
        return 0
    _apply(suite, task, cwd, node_id)
    _log({**entry, "outcome": "committed"})
    sys.stdout.write(stream_json(f"Committed {', '.join(task.files)} for {task.title}.", task.usage))
    return 0


def run_plain(suite: Suite, cwd: Path) -> int:
    for task in task_order(suite):
        _apply(suite, task, cwd, None)
    _log({"node_id": None, "task": None, "prompt_bytes": 0, "missing": [], "outcome": "plain"})
    sys.stdout.write(stream_json(f"Applied {len(suite.tasks)} tasks in one session.", {}))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    suite = load_suite_dir(Path(os.environ["BENCH_SUITE_DIR"]))
    cwd = Path.cwd()
    if argv[:1] == ["--plain"]:
        return run_plain(suite, cwd)
    task_map = json.loads(Path(os.environ["BENCH_TASK_MAP"]).read_text())
    return run_task(suite, task_map, sys.stdin.read(), cwd)


if __name__ == "__main__":
    sys.exit(main())
