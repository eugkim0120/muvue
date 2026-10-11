"""Benchmark suites: task metadata, fixture files and hidden acceptance tests."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

SUITES_DIR = Path(__file__).resolve().parent / "suites"


@dataclass(frozen=True)
class Task:
    key: str
    title: str
    criteria: str
    predicted_touches: tuple[str, ...]
    depends_on: tuple[str, ...]
    carries: tuple[str, ...]
    files: tuple[str, ...]
    requires: tuple[str, ...]
    usage: dict


@dataclass(frozen=True)
class Suite:
    name: str
    root: Path
    goal: str
    spec_title: str
    spec_body: str
    tasks: tuple[Task, ...]

    def task(self, key: str) -> Task:
        for t in self.tasks:
            if t.key == key:
                return t
        raise KeyError(f"suite {self.name} has no task {key!r}")


def load_suite_dir(root: Path) -> Suite:
    data = tomllib.loads((root / "tasks.toml").read_text())
    tasks = tuple(
        Task(
            key=t["key"], title=t["title"], criteria=t["criteria"],
            predicted_touches=tuple(t["predicted_touches"]), depends_on=tuple(t["depends_on"]),
            carries=tuple(t["carries"]), files=tuple(t["files"]), requires=tuple(t["requires"]),
            usage=dict(t["usage"]),
        )
        for t in data["task"]
    )
    return Suite(
        name=root.name, root=root, goal=data["suite"]["goal"], spec_title=data["suite"]["spec_title"],
        spec_body=(root / "spec.md").read_text().strip(), tasks=tasks,
    )


def load_suite(name: str) -> Suite:
    return load_suite_dir(SUITES_DIR / name)


def task_order(suite: Suite) -> list[Task]:
    done: list[Task] = []
    remaining = list(suite.tasks)
    while remaining:
        ready = [t for t in remaining if all(d in {x.key for x in done} for d in t.depends_on)]
        if not ready:
            raise ValueError(f"suite {suite.name} has a dependency cycle among {[t.key for t in remaining]}")
        done.append(ready[0])
        remaining.remove(ready[0])
    return done


def run_acceptance(suite: Suite, repo: Path) -> tuple[int, int]:
    """Run the suite's hidden tests against `repo`; return (passed, total)."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={report}",
             str(suite.root / "acceptance")],
            env={**os.environ, "BENCH_REPO": str(repo)}, capture_output=True, text=True, cwd=tmp,
        )
        if not report.exists():
            raise RuntimeError(f"acceptance run for {suite.name} wrote no report")
        node = ET.parse(report).getroot()
        node = node if node.tag == "testsuite" else node.find("testsuite")
        total = int(node.get("tests"))
        failed = int(node.get("failures")) + int(node.get("errors")) + int(node.get("skipped"))
        return total - failed, total
