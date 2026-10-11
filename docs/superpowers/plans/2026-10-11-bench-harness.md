# Benchmark Harness (Phase 0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m bench run` drives the real muvue CLI through a fixed six-task project with a scripted stand-in agent, and writes a dated JSON plus a markdown table. The output scores truth, outcome, tokens and flexibility for muvue against a plain single-session arm.

**Architecture:** A top-level `bench/` package (not shipped in the wheel) holds four pieces:
- a suite loader with composable fixture sources and hidden acceptance tests;
- a shim agent that speaks Claude's stream-json;
- a read-only scorer over `.muvue/muvue.db`;
- an arm runner that only shells out to `python -m muvue`.

Every muvue subprocess runs with `HOME` pointed at a temporary directory, so `~/.muvue/worktrees` and daemon files never touch the user's home.

**Tech Stack:** Python 3.12 stdlib (`argparse`, `subprocess`, `sqlite3`, `tomllib`, `json`, `xml.etree`), pytest, git.

**Spec:** `docs/superpowers/specs/2026-10-11-bench-design.md`

## Global Constraints

- No paid calls: the agent is always `bench/shim_agent.py`; nothing invokes `claude`.
- The bench drives muvue only through `[sys.executable, "-m", "muvue", ...]`. It never imports `muvue.core` to change state. Reading `.muvue/muvue.db` read-only (`sqlite3.connect(f"file:{db}?mode=ro", uri=True)`) is allowed for scoring and for listing review nodes.
- Every muvue subprocess gets `HOME=<work>/home` and `GIT_AUTHOR_NAME/EMAIL`, `GIT_COMMITTER_NAME/EMAIL` = `bench` / `bench@example.com`.
- Nothing from the user's environment is committed. Fixture usage numbers are copied below. Never copy `.muvue/logs/*.log` content.
- Every reported number is labelled `measured` or `replayed` in the JSON.
- Fail loudly: a muvue command that exits non-zero marks the arm `errored`, keeps its stderr, and still writes the JSON. No retries.
- Commit messages are plain prose and end with:
  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS
  ```

## File Structure

| Path | Responsibility |
|---|---|
| `bench/__init__.py` | Package marker with a docstring |
| `bench/__main__.py` | `python -m bench`, which calls `bench.run.main()` |
| `bench/suite.py` | Load a suite (`tasks.toml`), topological task order, run hidden acceptance tests |
| `bench/shim_agent.py` | The stand-in agent: task mode (stdin prompt) and `--plain` mode |
| `bench/score.py` | Read-only metrics from a muvue repo and the shim log |
| `bench/run.py` | Arms (muvue, plain), the step-capped muvue driver, results JSON and markdown |
| `bench/suites/readinglog/tasks.toml` | Suite metadata and six tasks |
| `bench/suites/readinglog/spec.md` | The spec body |
| `bench/suites/readinglog/start/…` | The scratch repo's first commit |
| `bench/suites/readinglog/files/…` | Fixture source each task writes |
| `bench/suites/readinglog/acceptance/test_acceptance.py` | Hidden end-to-end tests |
| `bench/results/.gitkeep` | Results directory (results are committed) |
| `tests/test_bench_suite.py`, `tests/test_bench_shim.py`, `tests/test_bench_score.py`, `tests/test_bench_run.py` | Tests |
| `pyproject.toml` | Add `[tool.pytest.ini_options] pythonpath = ["."]` so tests can `import bench` |

---

### Task 1: Suite fixtures, loader and hidden acceptance tests

**Files:**
- Create: `bench/__init__.py`, `bench/suite.py`, everything under `bench/suites/readinglog/`, `bench/results/.gitkeep`
- Modify: `pyproject.toml` (add the pytest section at the end of the file)
- Test: `tests/test_bench_suite.py`

**Interfaces:**
- Produces:
  - `bench.suite.Task` (frozen dataclass): `key: str`, `title: str`, `criteria: str`, `predicted_touches: tuple[str, ...]`, `depends_on: tuple[str, ...]`, `carries: tuple[str, ...]`, `files: tuple[str, ...]`, `requires: tuple[str, ...]`, `usage: dict`
  - `bench.suite.Suite` (frozen dataclass): `name: str`, `root: Path`, `goal: str`, `spec_title: str`, `spec_body: str`, `tasks: tuple[Task, ...]`, plus method `task(key: str) -> Task`
  - `load_suite(name: str) -> Suite`, which reads `bench/suites/<name>/`
  - `load_suite_dir(root: Path) -> Suite`
  - `task_order(suite: Suite) -> list[Task]`: dependencies first, ties in file order
  - `run_acceptance(suite: Suite, repo: Path) -> tuple[int, int]`: returns `(passed, total)`
- Consumes: nothing.

- [ ] **Step 1: Add the pytest path setting**

Append to `pyproject.toml`:

```toml

[tool.pytest.ini_options]
# The benchmark harness lives at the repo root (`bench/`), outside the
# package; its tests import it directly.
pythonpath = ["."]
```

- [ ] **Step 2: Write the failing test**

`tests/test_bench_suite.py`:

```python
"""The readinglog suite loads, orders its tasks, and its fixture files,
applied in order, pass the hidden acceptance tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from bench.suite import load_suite, run_acceptance, task_order

GIT_ENV = {
    "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.com",
    "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.com",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def test_suite_loads_six_tasks_with_usage():
    suite = load_suite("readinglog")
    assert [t.key for t in suite.tasks] == ["models", "store", "addlog", "streak", "progress", "stats"]
    assert suite.task("stats").depends_on == ("addlog", "streak")
    assert suite.task("models").usage["cache_creation_input_tokens"] == 43878
    for t in suite.tasks:
        for rel in t.files:
            assert (suite.root / "files" / rel).is_file(), rel
        assert len(t.carries) == len(t.depends_on)


def test_task_order_puts_dependencies_first():
    order = [t.key for t in task_order(load_suite("readinglog"))]
    for t in load_suite("readinglog").tasks:
        for dep in t.depends_on:
            assert order.index(dep) < order.index(t.key)


def test_all_fixture_files_pass_acceptance(tmp_path: Path):
    suite = load_suite("readinglog")
    repo = tmp_path / "repo"
    shutil.copytree(suite.root / "start", repo)
    for t in task_order(suite):
        for rel in t.files:
            dst = repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(suite.root / "files" / rel, dst)
    assert run_acceptance(suite, repo) == (4, 4)


def test_start_files_alone_fail_acceptance(tmp_path: Path):
    suite = load_suite("readinglog")
    repo = tmp_path / "repo"
    shutil.copytree(suite.root / "start", repo)
    passed, total = run_acceptance(suite, repo)
    assert (passed, total) == (0, 4)
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_bench_suite.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench'`.

- [ ] **Step 4: Write the suite data**

`bench/suites/readinglog/spec.md`:

```markdown
A small Python CLI, `readinglog`, that keeps a reading log in a JSON file (default ~/.readinglog.json, overridable with READINGLOG_FILE). Commands: `add TITLE --pages N` adds a book; `log TITLE PAGES` records pages read today; `progress` lists each book with pages read, total and percent; `stats` shows pages this week and the current daily streak. Standard library only. Every module has pytest tests.
```

`bench/suites/readinglog/tasks.toml`. The usage numbers were recorded from the real readinglog run on 2026-10-11 (claude-sonnet-5-5):

```toml
[suite]
goal = "readinglog: a command-line reading log with progress and streaks"
spec_title = "readinglog CLI"

[[task]]
key = "models"
title = "Book and reading-session models"
criteria = "src/readinglog/models.py defines Book(title, pages) and Session(title, day, pages) dataclasses that round-trip to and from dicts, with tests"
predicted_touches = ["src/readinglog/models.py", "tests/test_models.py"]
depends_on = []
carries = []
files = ["src/readinglog/models.py", "tests/test_models.py"]
requires = []
usage = { input_tokens = 10, cache_creation_input_tokens = 43878, cache_read_input_tokens = 227926, output_tokens = 1383, total_cost_usd = 0.2121546, num_turns = 9 }

[[task]]
key = "store"
title = "JSON storage with atomic save"
criteria = "src/readinglog/store.py loads and saves the log at READINGLOG_FILE or ~/.readinglog.json, writing via a temp file and os.replace, with tests"
predicted_touches = ["src/readinglog/store.py", "tests/test_store.py"]
depends_on = ["models"]
carries = ["Book, Session"]
files = ["src/readinglog/store.py", "tests/test_store.py"]
requires = ["src/readinglog/models.py"]
usage = { input_tokens = 14, cache_creation_input_tokens = 42297, cache_read_input_tokens = 331472, output_tokens = 4846, total_cost_usd = 0.2508232, num_turns = 11 }

[[task]]
key = "addlog"
title = "add and log commands"
criteria = "readinglog add TITLE --pages N and readinglog log TITLE PAGES update the store; logging an unknown title exits 1 with a message; tested"
predicted_touches = ["src/readinglog/cli.py", "src/readinglog/commands/*.py", "tests/test_cli.py"]
depends_on = ["store"]
carries = ["load, save"]
files = ["src/readinglog/cli.py", "src/readinglog/commands/__init__.py", "src/readinglog/commands/add.py", "src/readinglog/commands/log.py", "tests/test_cli.py"]
requires = ["src/readinglog/models.py", "src/readinglog/store.py"]
usage = { input_tokens = 14, cache_creation_input_tokens = 40046, cache_read_input_tokens = 326162, output_tokens = 1758, total_cost_usd = 0.2104082, num_turns = 7 }

[[task]]
key = "streak"
title = "Daily streak calculation"
criteria = "src/readinglog/streak.py computes the current daily streak and pages this week from sessions and a given today date, with tests for gaps and week boundaries"
predicted_touches = ["src/readinglog/streak.py", "tests/test_streak.py"]
depends_on = ["models"]
carries = ["Session"]
files = ["src/readinglog/streak.py", "tests/test_streak.py"]
requires = ["src/readinglog/models.py"]
usage = { input_tokens = 10, cache_creation_input_tokens = 39833, cache_read_input_tokens = 220854, output_tokens = 2632, total_cost_usd = 0.2077574, num_turns = 5 }

[[task]]
key = "progress"
title = "progress report"
criteria = "readinglog progress prints one line per book: title, pages read / total and percent, sorted by percent; tested"
predicted_touches = ["src/readinglog/commands/progress.py", "tests/test_progress.py"]
depends_on = ["addlog"]
carries = ["CLI entry point"]
files = ["src/readinglog/commands/progress.py", "tests/test_progress.py"]
requires = ["src/readinglog/cli.py", "src/readinglog/store.py"]
usage = { input_tokens = 16, cache_creation_input_tokens = 43592, cache_read_input_tokens = 391693, output_tokens = 4661, total_cost_usd = 0.2601793, num_turns = 9 }

[[task]]
key = "stats"
title = "stats command"
criteria = "readinglog stats prints pages this week and the current streak in days; tested"
predicted_touches = ["src/readinglog/commands/stats.py", "tests/test_stats.py"]
depends_on = ["addlog", "streak"]
carries = ["CLI entry point", "streak, week_pages"]
files = ["src/readinglog/commands/stats.py", "tests/test_stats.py"]
requires = ["src/readinglog/cli.py", "src/readinglog/store.py", "src/readinglog/streak.py"]
usage = { input_tokens = 14, cache_creation_input_tokens = 44127, cache_read_input_tokens = 338162, output_tokens = 3761, total_cost_usd = 0.2479622, num_turns = 9 }
```

- [ ] **Step 5: Write the starting repo files**

`bench/suites/readinglog/start/pyproject.toml`:

```toml
[project]
name = "readinglog"
version = "0.1.0"
description = "A command-line reading log"
requires-python = ">=3.12"
dependencies = []

[tool.pytest.ini_options]
pythonpath = ["src"]
```

`bench/suites/readinglog/start/README.md`:

```markdown
# readinglog

Track the books you read from the terminal.
```

`bench/suites/readinglog/start/.gitignore`:

```
__pycache__/
.pytest_cache/
```

`bench/suites/readinglog/start/src/readinglog/__init__.py`:

```python
"""A command-line reading log."""
```

- [ ] **Step 6: Write the fixture source, which composes with no shared-file edits**

`bench/suites/readinglog/files/src/readinglog/models.py`:

```python
"""Books and reading sessions, and their JSON-ready dict form."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Book:
    title: str
    pages: int

    def to_dict(self) -> dict:
        return {"title": self.title, "pages": self.pages}

    @classmethod
    def from_dict(cls, data: dict) -> Book:
        return cls(title=str(data["title"]), pages=int(data["pages"]))


@dataclass(frozen=True)
class Session:
    title: str
    day: date
    pages: int

    def to_dict(self) -> dict:
        return {"title": self.title, "day": self.day.isoformat(), "pages": self.pages}

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        return cls(title=str(data["title"]), day=date.fromisoformat(data["day"]), pages=int(data["pages"]))
```

`bench/suites/readinglog/files/tests/test_models.py`:

```python
from datetime import date

from readinglog.models import Book, Session


def test_book_round_trips():
    book = Book("Dune", 412)
    assert Book.from_dict(book.to_dict()) == book


def test_session_round_trips_with_iso_day():
    session = Session("Dune", date(2026, 10, 11), 30)
    assert session.to_dict()["day"] == "2026-10-11"
    assert Session.from_dict(session.to_dict()) == session
```

`bench/suites/readinglog/files/src/readinglog/store.py`:

```python
"""Load and save the reading log as JSON, replacing the file atomically."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from readinglog.models import Book, Session


def log_path() -> Path:
    return Path(os.environ.get("READINGLOG_FILE") or Path.home() / ".readinglog.json")


def load() -> tuple[list[Book], list[Session]]:
    path = log_path()
    if not path.exists():
        return [], []
    data = json.loads(path.read_text())
    books = [Book.from_dict(b) for b in data.get("books", [])]
    sessions = [Session.from_dict(s) for s in data.get("sessions", [])]
    return books, sessions


def save(books: list[Book], sessions: list[Session]) -> None:
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"books": [b.to_dict() for b in books], "sessions": [s.to_dict() for s in sessions]}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)
```

`bench/suites/readinglog/files/tests/test_store.py`:

```python
from datetime import date

from readinglog import store
from readinglog.models import Book, Session


def test_missing_file_loads_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("READINGLOG_FILE", str(tmp_path / "log.json"))
    assert store.load() == ([], [])


def test_save_then_load(tmp_path, monkeypatch):
    monkeypatch.setenv("READINGLOG_FILE", str(tmp_path / "log.json"))
    books = [Book("Dune", 412)]
    sessions = [Session("Dune", date(2026, 10, 11), 30)]
    store.save(books, sessions)
    assert store.load() == (books, sessions)
    assert [p.name for p in tmp_path.iterdir()] == ["log.json"]
```

`bench/suites/readinglog/files/src/readinglog/cli.py`:

```python
"""`readinglog` entry point. Subcommands are the modules in
`readinglog.commands`; each one defines `register(subparsers)`."""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys

import readinglog.commands as commands


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="readinglog")
    sub = parser.add_subparsers(dest="command", required=True)
    for info in sorted(pkgutil.iter_modules(commands.__path__), key=lambda m: m.name):
        importlib.import_module(f"readinglog.commands.{info.name}").register(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

`bench/suites/readinglog/files/src/readinglog/commands/__init__.py`:

```python
"""One module per `readinglog` subcommand."""
```

`bench/suites/readinglog/files/src/readinglog/commands/add.py`:

```python
from __future__ import annotations

from readinglog import store
from readinglog.models import Book


def register(sub) -> None:
    parser = sub.add_parser("add", help="add a book")
    parser.add_argument("title")
    parser.add_argument("--pages", type=int, required=True)
    parser.set_defaults(func=run)


def run(args) -> int:
    books, sessions = store.load()
    if any(b.title == args.title for b in books):
        print(f"already in the log: {args.title}")
        return 1
    books.append(Book(args.title, args.pages))
    store.save(books, sessions)
    print(f"added {args.title} ({args.pages} pages)")
    return 0
```

`bench/suites/readinglog/files/src/readinglog/commands/log.py`:

```python
from __future__ import annotations

from datetime import date

from readinglog import store
from readinglog.models import Session


def register(sub) -> None:
    parser = sub.add_parser("log", help="record pages read today")
    parser.add_argument("title")
    parser.add_argument("pages", type=int)
    parser.set_defaults(func=run)


def run(args) -> int:
    books, sessions = store.load()
    if not any(b.title == args.title for b in books):
        print(f"unknown book: {args.title}")
        return 1
    sessions.append(Session(args.title, date.today(), args.pages))
    store.save(books, sessions)
    print(f"logged {args.pages} pages of {args.title}")
    return 0
```

`bench/suites/readinglog/files/tests/test_cli.py`:

```python
from readinglog import store
from readinglog.cli import main


def test_add_then_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("READINGLOG_FILE", str(tmp_path / "log.json"))
    assert main(["add", "Dune", "--pages", "412"]) == 0
    assert main(["log", "Dune", "30"]) == 0
    books, sessions = store.load()
    assert [b.title for b in books] == ["Dune"]
    assert [s.pages for s in sessions] == [30]


def test_log_unknown_title_exits_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("READINGLOG_FILE", str(tmp_path / "log.json"))
    assert main(["log", "Missing", "5"]) == 1
    assert "unknown book: Missing" in capsys.readouterr().out
```

`bench/suites/readinglog/files/src/readinglog/streak.py`:

```python
"""Reading streak and weekly totals, computed from sessions and a given day."""

from __future__ import annotations

from datetime import date, timedelta

from readinglog.models import Session


def current_streak(sessions: list[Session], today: date) -> int:
    days = {s.day for s in sessions if s.pages > 0}
    streak, day = 0, today
    while day in days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def pages_this_week(sessions: list[Session], today: date) -> int:
    monday = today - timedelta(days=today.weekday())
    return sum(s.pages for s in sessions if monday <= s.day <= today)
```

`bench/suites/readinglog/files/tests/test_streak.py`:

```python
from datetime import date

from readinglog.models import Session
from readinglog.streak import current_streak, pages_this_week

SAT = date(2026, 10, 10)


def test_streak_counts_consecutive_days_back_from_today():
    sessions = [Session("Dune", date(2026, 10, d), 10) for d in (8, 9, 10)]
    assert current_streak(sessions, SAT) == 3


def test_gap_ends_streak():
    sessions = [Session("Dune", date(2026, 10, d), 10) for d in (7, 9, 10)]
    assert current_streak(sessions, SAT) == 2


def test_week_starts_on_monday():
    sessions = [Session("Dune", date(2026, 10, 4), 99), Session("Dune", date(2026, 10, 5), 20)]
    assert pages_this_week(sessions, SAT) == 20
```

`bench/suites/readinglog/files/src/readinglog/commands/progress.py`:

```python
from __future__ import annotations

from readinglog import store


def register(sub) -> None:
    parser = sub.add_parser("progress", help="pages read per book")
    parser.set_defaults(func=run)


def run(args) -> int:
    books, sessions = store.load()
    rows = []
    for book in books:
        read = min(sum(s.pages for s in sessions if s.title == book.title), book.pages)
        pct = round(100 * read / book.pages) if book.pages else 0
        rows.append((pct, book.title, read, book.pages))
    for pct, title, read, total in sorted(rows, key=lambda r: (-r[0], r[1])):
        print(f"{title}: {read}/{total} pages ({pct}%)")
    return 0
```

`bench/suites/readinglog/files/tests/test_progress.py`:

```python
from readinglog.cli import main


def test_progress_sorted_by_percent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("READINGLOG_FILE", str(tmp_path / "log.json"))
    main(["add", "Dune", "--pages", "400"])
    main(["add", "Emma", "--pages", "100"])
    main(["log", "Emma", "50"])
    capsys.readouterr()
    assert main(["progress"]) == 0
    assert capsys.readouterr().out.splitlines() == ["Emma: 50/100 pages (50%)", "Dune: 0/400 pages (0%)"]
```

`bench/suites/readinglog/files/src/readinglog/commands/stats.py`:

```python
from __future__ import annotations

from datetime import date

from readinglog import store
from readinglog.streak import current_streak, pages_this_week


def register(sub) -> None:
    parser = sub.add_parser("stats", help="pages this week and the current streak")
    parser.set_defaults(func=run)


def run(args) -> int:
    _, sessions = store.load()
    today = date.today()
    streak = current_streak(sessions, today)
    print(f"Pages this week: {pages_this_week(sessions, today)}")
    print(f"Current streak: {streak} day{'' if streak == 1 else 's'}")
    return 0
```

`bench/suites/readinglog/files/tests/test_stats.py`:

```python
from readinglog.cli import main


def test_stats_after_one_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("READINGLOG_FILE", str(tmp_path / "log.json"))
    main(["add", "Dune", "--pages", "400"])
    main(["log", "Dune", "25"])
    capsys.readouterr()
    assert main(["stats"]) == 0
    assert capsys.readouterr().out.splitlines() == ["Pages this week: 25", "Current streak: 1 day"]
```

- [ ] **Step 7: Write the hidden acceptance tests**

`bench/suites/readinglog/acceptance/test_acceptance.py`. It is never copied into the scratch repo. `BENCH_REPO` names the repo under test:

```python
"""Hidden end-to-end checks for the readinglog suite. They run the real
CLI from the repo under test (BENCH_REPO) with a throwaway log file."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(os.environ["BENCH_REPO"])


@pytest.fixture
def cli(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), "READINGLOG_FILE": str(tmp_path / "log.json")}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "readinglog.cli", *args],
                              cwd=REPO, env=env, capture_output=True, text=True)
    return run


def test_add_shows_in_progress(cli):
    assert cli("add", "Dune", "--pages", "300").returncode == 0
    out = cli("progress")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["Dune: 0/300 pages (0%)"]


def test_log_updates_progress(cli):
    cli("add", "Dune", "--pages", "300")
    assert cli("log", "Dune", "50").returncode == 0
    assert cli("progress").stdout.splitlines() == ["Dune: 50/300 pages (17%)"]


def test_log_unknown_book_fails(cli):
    out = cli("log", "Missing", "5")
    assert out.returncode == 1
    assert "unknown book" in out.stdout


def test_stats_counts_today(cli):
    cli("add", "Dune", "--pages", "300")
    cli("log", "Dune", "50")
    out = cli("stats")
    assert out.returncode == 0
    assert out.stdout.splitlines() == ["Pages this week: 50", "Current streak: 1 day"]
```

- [ ] **Step 8: Write the loader**

`bench/__init__.py`:

```python
"""muvue's benchmark harness: drives the real CLI through fixed suites
with a scripted stand-in agent and scores truth, outcome, tokens and
flexibility. Not shipped in the wheel."""
```

`bench/suite.py`:

```python
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
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bench_suite.py -q -p no:cacheprovider`
Expected: 4 passed.

Also confirm that the fixture's own tests pass on the fully applied tree. Copy `start/` plus every file into a temp dir, then run `uv run python -m pytest -q` there. Expected: 11 passed.

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml bench/__init__.py bench/suite.py bench/suites bench/results/.gitkeep tests/test_bench_suite.py
git commit -m "Add the readinglog benchmark suite and its loader" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 2: The shim agent

**Files:**
- Create: `bench/shim_agent.py`
- Test: `tests/test_bench_shim.py`

**Interfaces:**
- Consumes: `bench.suite.load_suite_dir`, `task_order`, `Suite.task`, `Task`.
- Produces:
  - **Invocation:** `python bench/shim_agent.py` (task mode) or `python bench/shim_agent.py --plain` (plain mode). Both work as a script, so `sys.path` gets the repo root.
  - **Environment it reads:**
    - `BENCH_SUITE_DIR`: the suite root;
    - `BENCH_TASK_MAP`: a JSON file of `{"<node id>": "<task key>"}`, used in task mode;
    - `BENCH_SHIM_LOG`: optional; append one JSON line per call, `{"node_id": int|None, "task": str|None, "prompt_bytes": int, "outcome": "committed"|"blocked"|"unmapped"|"plain", "missing": [str]}`.
  - **Output:** stdout is stream-json with one `system`/`init` line and one `result` line.
  - **Exit codes:** 0, or 1 for an unmapped prompt.
  - **Importable helpers:** `stream_json(text: str, usage: dict, *, is_error: bool = False) -> str` and `NODE_RE` (`re.compile(r"muvue node T(\d+)\b")`). The node id comes from `muvue.core.runner.agent_prompt`: "You are an agent working on muvue node T{node_id} in this repository."

- [ ] **Step 1: Write the failing tests**

`tests/test_bench_shim.py`:

```python
"""The shim commits fixture files when its dependencies are present,
reports blocked without committing when they are not, and its output
parses with muvue's Claude stream-json parser."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from bench.shim_agent import stream_json
from bench.suite import load_suite
from muvue.core.drivers import parse_claude_stream_json

SHIM = Path(__file__).resolve().parent.parent / "bench" / "shim_agent.py"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.com",
    "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.com",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}


def _repo(tmp_path: Path) -> Path:
    suite = load_suite("readinglog")
    repo = tmp_path / "repo"
    shutil.copytree(suite.root / "start", repo)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "start"]):
        subprocess.run(["git", *args], cwd=repo, env=GIT_ENV, check=True, capture_output=True)
    return repo


def _run(repo: Path, tmp_path: Path, prompt: str, task_map: dict, *args: str):
    (tmp_path / "map.json").write_text(json.dumps(task_map))
    env = {**GIT_ENV, "BENCH_SUITE_DIR": str(load_suite("readinglog").root),
           "BENCH_TASK_MAP": str(tmp_path / "map.json"), "BENCH_SHIM_LOG": str(tmp_path / "shim.jsonl")}
    return subprocess.run([sys.executable, str(SHIM), *args], cwd=repo, env=env, input=prompt,
                          capture_output=True, text=True)


def _log(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "shim.jsonl").read_text().splitlines()]


def test_commits_fixture_files_with_trailer(tmp_path):
    repo = _repo(tmp_path)
    out = _run(repo, tmp_path, "You are an agent working on muvue node T2 in this repository.\n", {"2": "models"})
    assert out.returncode == 0, out.stderr
    msg = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=repo, capture_output=True, text=True).stdout
    assert "Muvue-Node: 2" in msg
    assert (repo / "src/readinglog/models.py").is_file()
    result = parse_claude_stream_json(out.stdout)
    assert result.status == "done"
    assert result.in_tokens == 10 + 43878 + 227926
    assert result.out_tokens == 1383
    assert _log(tmp_path)[0]["outcome"] == "committed"


def test_missing_dependency_reports_blocked_and_commits_nothing(tmp_path):
    repo = _repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    out = _run(repo, tmp_path, "You are an agent working on muvue node T3 in this repository.\n", {"3": "store"})
    assert out.returncode == 0
    result = parse_claude_stream_json(out.stdout)
    assert result.status == "done"
    assert result.summary.startswith("Blocked. No commit made.")
    assert "src/readinglog/models.py" in result.summary
    assert subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout == head
    entry = _log(tmp_path)[0]
    assert entry["outcome"] == "blocked" and entry["missing"] == ["src/readinglog/models.py"]


def test_unmapped_prompt_is_an_error(tmp_path):
    repo = _repo(tmp_path)
    out = _run(repo, tmp_path, "no node here", {})
    assert out.returncode == 1
    assert parse_claude_stream_json(out.stdout).status == "failed"


def test_plain_mode_applies_every_task(tmp_path):
    repo = _repo(tmp_path)
    out = _run(repo, tmp_path, "", {}, "--plain")
    assert out.returncode == 0, out.stderr
    count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True, text=True).stdout
    assert int(count) == 1 + 6
    assert (repo / "src/readinglog/commands/stats.py").is_file()


def test_stream_json_error_flag():
    parsed = parse_claude_stream_json(stream_json("boom", {}, is_error=True))
    assert parsed.status == "failed" and parsed.error == "boom"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_bench_shim.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.shim_agent'`.

- [ ] **Step 3: Write the shim**

`bench/shim_agent.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bench_shim.py -q -p no:cacheprovider`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add bench/shim_agent.py tests/test_bench_shim.py
git commit -m "Add the benchmark's scripted stand-in agent" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 3: Read-only scoring

**Files:**
- Create: `bench/score.py`
- Test: `tests/test_bench_score.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. It reads a muvue repo's `.muvue/muvue.db`, which has these tables:
  - `nodes(id, kind, status, summary, deleted_at, …)`
  - `node_commits(node_id, sha, files)`
  - `events(id, ts, project_id, node_id, actor, actor_evidence, type, payload, …)`
  - `node_usage(node_id, agent, model, in_tokens, out_tokens, requests, cost, rate_limited)`
- Produces:
  - `BLOCKED_RE` (`re.compile(r"^\s*Blocked\b|\bno commit", re.IGNORECASE)`)
  - `node_ids_with_status(repo: Path, status: str) -> list[int]`: tasks and subtasks only, not deleted
  - `score_muvue(repo: Path, shim_log: Path | None) -> dict`, with keys `truth`, `outcome`, `tokens`
  - `read_shim_log(path: Path | None) -> list[dict]`

`score_muvue` returns this shape. Every leaf group carries `"label"`:

```python
{
  "truth": {"label": "measured", "done": int, "done_with_commit": int, "done_claiming_blocked": int,
            "truth_score": float | None, "check_runs": int, "check_runs_with_output": int,
            "approvals_by_actor": {"<event type>": {"<actor>/<actor_evidence>": int}}},
  "outcome": {"label": "measured", "statuses": {"<status>": int}, "failed": int,
              "subtasks": int, "conflict_events": int},
  "tokens": {"sessions": int, "sessions_label": "measured",
             "prompt_bytes": [int, ...], "prompt_bytes_label": "measured",
             "shim_outcomes": {"committed": int, "blocked": int, ...},
             "in_tokens": int, "out_tokens": int, "cost_usd": float, "usage_label": "replayed"},
}
```

Definitions:
- `truth_score` = (done tasks with ≥1 commit and a summary that doesn't match `BLOCKED_RE`) / done tasks, rounded to 3 places; `None` when there are no done tasks.
- `check_runs` = the number of `node.review` events.
- `check_runs_with_output` = events whose type contains `"check"` and whose JSON payload has the key `"output"`.
- `approvals_by_actor` covers the event types `spec.approved`, `gate2.approved`, `review.approved`, `review.auto_approved` and `node.done`, plus any type ending in `.approved`.

- [ ] **Step 1: Write the failing test**

This uses a real muvue repo driven by its CLI with the built-in fake agent. It commits nothing, so a done task with no commit must show up in the truth numbers.

`tests/test_bench_score.py`:

```python
"""score_muvue reads a real muvue repo: a fake-agent task that is done
with no commit lowers the truth score."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from bench.score import node_ids_with_status, read_shim_log, score_muvue


def _env(tmp_path: Path) -> dict:
    (tmp_path / "home").mkdir(exist_ok=True)
    return {**os.environ, "HOME": str(tmp_path / "home"),
            "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.com",
            "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.com"}


def _muvue(env: dict, *args: str):
    out = subprocess.run([sys.executable, "-m", "muvue", *args], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return out.stdout


def test_fake_agent_done_without_commit_is_caught(tmp_path):
    env = _env(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "start"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    p = str(repo)
    _muvue(env, "init", p)
    cfg = repo / ".muvue" / "config.toml"
    cfg.write_text(cfg.read_text().replace('test = "pytest -q"', 'test = "true"').replace('lint = "ruff check ."', 'lint = "true"'))
    pid = _muvue(env, "project", "create", "--goal", "g", "--path", p)["id"]
    sid = _muvue(env, "spec", str(pid), "--title", "s", "--body", "b", "--path", p)["id"]
    _muvue(env, "approve", f"spec:{sid}", "--path", p)
    _muvue(env, "decompose", str(sid), "--title", "t", "--criteria", "c", "--criteria-mode", "auto",
           "--predicted-touches", "src/a.py", "--path", p)
    _muvue(env, "approve", f"gate2:{pid}", "--path", p)
    _muvue(env, "run", "--path", p)

    result = score_muvue(repo, None)
    truth = result["truth"]
    assert truth["label"] == "measured"
    assert truth["done"] == 1
    assert truth["done_with_commit"] == 0
    assert truth["truth_score"] == 0.0
    assert truth["check_runs"] == 1
    assert result["tokens"]["sessions"] == 1
    assert result["tokens"]["usage_label"] == "replayed"
    assert node_ids_with_status(repo, "done") == [2]


def test_read_shim_log(tmp_path):
    log = tmp_path / "shim.jsonl"
    log.write_text('{"outcome": "blocked", "prompt_bytes": 10}\n{"outcome": "committed", "prompt_bytes": 12}\n')
    assert [e["outcome"] for e in read_shim_log(log)] == ["blocked", "committed"]
    assert read_shim_log(None) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_bench_score.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.score'`.

- [ ] **Step 3: Write the scorer**

`bench/score.py`:

```python
"""Read-only metrics from a muvue repo's database and the shim's log."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

BLOCKED_RE = re.compile(r"^\s*Blocked\b|\bno commit", re.IGNORECASE)
APPROVAL_TYPES = {"spec.approved", "gate2.approved", "review.approved", "review.auto_approved", "node.done"}


def _connect(repo: Path) -> sqlite3.Connection:
    db = repo / ".muvue" / "muvue.db"
    if not db.exists():
        raise FileNotFoundError(f"no muvue database at {db}")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def node_ids_with_status(repo: Path, status: str) -> list[int]:
    with _connect(repo) as c:
        rows = c.execute(
            "SELECT id FROM nodes WHERE status = ? AND kind IN ('task', 'subtask') AND deleted_at IS NULL ORDER BY id",
            (status,),
        ).fetchall()
    return [r[0] for r in rows]


def read_shim_log(path: Path | None) -> list[dict]:
    if path is None or not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _payload(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def score_muvue(repo: Path, shim_log: Path | None) -> dict:
    with _connect(repo) as c:
        tasks = c.execute(
            "SELECT id, status, summary FROM nodes WHERE kind IN ('task', 'subtask') AND deleted_at IS NULL"
        ).fetchall()
        commits = Counter(r[0] for r in c.execute("SELECT node_id FROM node_commits"))
        events = c.execute("SELECT type, actor, actor_evidence, payload FROM events").fetchall()
        usage = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(in_tokens), 0), COALESCE(SUM(out_tokens), 0), COALESCE(SUM(cost), 0) FROM node_usage"
        ).fetchone()

    done = [t for t in tasks if t[1] == "done"]
    with_commit = [t for t in done if commits[t[0]] > 0]
    claiming_blocked = [t for t in done if BLOCKED_RE.search(t[2] or "")]
    truthful = [t for t in with_commit if not BLOCKED_RE.search(t[2] or "")]

    approvals: dict[str, Counter] = {}
    for etype, actor, evidence, _ in events:
        if etype in APPROVAL_TYPES or etype.endswith(".approved"):
            approvals.setdefault(etype, Counter())[f"{actor}/{evidence}"] += 1

    log = read_shim_log(shim_log)
    return {
        "truth": {
            "label": "measured",
            "done": len(done),
            "done_with_commit": len(with_commit),
            "done_claiming_blocked": len(claiming_blocked),
            "truth_score": round(len(truthful) / len(done), 3) if done else None,
            "check_runs": sum(1 for e in events if e[0] == "node.review"),
            "check_runs_with_output": sum(1 for e in events if "check" in e[0] and "output" in _payload(e[3])),
            "approvals_by_actor": {k: dict(v) for k, v in sorted(approvals.items())},
        },
        "outcome": {
            "label": "measured",
            "statuses": dict(Counter(t[1] for t in tasks)),
            "failed": sum(1 for t in tasks if t[1] == "failed"),
            "subtasks": _count_subtasks(repo),
            "conflict_events": sum(1 for e in events if "conflict" in e[0]),
        },
        "tokens": {
            "sessions": int(usage[0]), "sessions_label": "measured",
            "prompt_bytes": [e["prompt_bytes"] for e in log], "prompt_bytes_label": "measured",
            "shim_outcomes": dict(Counter(e["outcome"] for e in log)),
            "in_tokens": int(usage[1]), "out_tokens": int(usage[2]), "cost_usd": round(float(usage[3]), 4),
            "usage_label": "replayed",
        },
    }


def _count_subtasks(repo: Path) -> int:
    with _connect(repo) as c:
        return c.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'subtask' AND deleted_at IS NULL").fetchone()[0]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bench_score.py -q -p no:cacheprovider`
Expected: 2 passed.

If `truth["check_runs"]` is not 1, read the events the fake run produced with `SELECT type FROM events`. Adjust the definition only if muvue does not emit `node.review` for a task that went through checks, and record what you found in the report. Do not loosen the truth assertions.

- [ ] **Step 5: Commit**

```bash
git add bench/score.py tests/test_bench_score.py
git commit -m "Add read-only benchmark scoring from the muvue database" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```

---

### Task 4: The arm runner, the CLI and the first baseline

**Files:**
- Create: `bench/run.py`, `bench/__main__.py`
- Test: `tests/test_bench_run.py`
- Create (from running it): `bench/results/2026-10-11-readinglog.json`

**Interfaces:**
- Consumes: `bench.suite.load_suite`, `task_order`, `run_acceptance`; `bench.score.score_muvue`, `node_ids_with_status`, `read_shim_log`; `bench/shim_agent.py` as a script.
- Produces:
  - `run_bench(suite_name: str, arms: tuple[str, ...], out_dir: Path, cap: int = 30) -> tuple[dict, Path]`: returns the result dict and the written JSON path
  - `render_markdown(result: dict) -> str`
  - `main(argv: list[str] | None = None) -> int`, which accepts `run [--suite readinglog] [--arm muvue|plain|both] [--cap 30] [--out-dir bench/results]`
- **Result shape:**
  ```
  {"suite", "date", "muvue_sha", "muvue_dirty": bool,
   "arms": {"muvue": ArmResult, "plain": ArmResult}}
  ```
  An `ArmResult` is:
  ```
  {"status": "ok"|"errored"|"stalled", "error": str|None, "wall_s": float,
   "acceptance": {"passed", "total", "label": "measured"},
   "flexibility": {"commands": int, "human_verbs": int, "label": "measured", "steps": [[argv..., returncode], ...]},
   plus the score_muvue keys for the muvue arm}
  ```
  The plain arm's `tokens` is `{"sessions": 1, "sessions_label": "measured", "usage_label": "not measured (shim)"}`.

- [ ] **Step 1: Write the failing test**

`tests/test_bench_run.py`:

```python
"""End to end: the bench drives real muvue through the readinglog suite
with the shim, scores both arms and writes the result file."""

from __future__ import annotations

import json

from bench.run import main, render_markdown, run_bench


def test_run_bench_both_arms(tmp_path):
    result, path = run_bench("readinglog", ("muvue", "plain"), tmp_path)
    assert path.exists() and json.loads(path.read_text()) == result
    plain = result["arms"]["plain"]
    assert plain["status"] == "ok", plain["error"]
    assert (plain["acceptance"]["passed"], plain["acceptance"]["total"]) == (4, 4)
    arm = result["arms"]["muvue"]
    assert arm["status"] in ("ok", "stalled"), arm["error"]
    assert arm["truth"]["done"] >= 1
    assert arm["tokens"]["sessions"] >= 6
    assert arm["flexibility"]["human_verbs"] >= 3
    assert all(isinstance(b, int) and b > 0 for b in arm["tokens"]["prompt_bytes"])
    table = render_markdown(result)
    assert "| truth score |" in table and "| acceptance |" in table


def test_main_rejects_unknown_suite(tmp_path, capsys):
    assert main(["run", "--suite", "nope", "--out-dir", str(tmp_path)]) == 2
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_bench_run.py -q -p no:cacheprovider`
Expected: FAIL with `ModuleNotFoundError: No module named 'bench.run'`.

- [ ] **Step 3: Write the runner**

`bench/run.py`:

```python
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
from pathlib import Path

from bench.score import node_ids_with_status, score_muvue
from bench.suite import SUITES_DIR, Suite, load_suite, run_acceptance, task_order

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
SHIM = BENCH_DIR / "shim_agent.py"
HUMAN_VERBS = {"approve", "reject", "merge", "close", "answer", "ack"}


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
        out = subprocess.run([sys.executable, "-m", "muvue", *argv], env=self.env, capture_output=True, text=True)
        self.steps.append([*args, out.returncode])
        if out.returncode != 0:
            raise ArmError(f"muvue {' '.join(args)} exited {out.returncode}: {out.stderr.strip()[-2000:]}")
        try:
            return json.loads(out.stdout)
        except json.JSONDecodeError:
            return out.stdout

    def flexibility(self) -> dict:
        return {"commands": len(self.steps), "human_verbs": sum(1 for s in self.steps if s[0] in HUMAN_VERBS),
                "label": "measured", "steps": self.steps}


def _env(work: Path, suite: Suite) -> dict:
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    return {**os.environ, "HOME": str(home),
            "GIT_AUTHOR_NAME": "bench", "GIT_AUTHOR_EMAIL": "bench@example.com",
            "GIT_COMMITTER_NAME": "bench", "GIT_COMMITTER_EMAIL": "bench@example.com",
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
    try:
        m("init", str(repo), path_flag=False)
        _configure(repo)
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
            in_review = node_ids_with_status(repo, "review")
            for node_id in in_review:
                m("approve", f"review:{node_id}")
            if not (isinstance(out, dict) and out.get("processed")) and not in_review:
                break
        m("merge")
    except Stalled as exc:
        status, error = "stalled", str(exc)
    except ArmError as exc:
        status, error = "errored", str(exc)
    wall = round(time.monotonic() - started, 2)
    passed, total = run_acceptance(suite, repo)
    scored = score_muvue(repo, work / "shim.jsonl") if (repo / ".muvue" / "muvue.db").exists() else {}
    return {"status": status, "error": error, "wall_s": wall,
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
            "tokens": {"sessions": 1, "sessions_label": "measured", "usage_label": "not measured (shim)"}}


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
        ("check runs with output", lambda a: f"{cell(a, 'truth', 'check_runs_with_output')}/{cell(a, 'truth', 'check_runs')}"),
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
```

`bench/__main__.py`:

```python
import sys

from bench.run import main

sys.exit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_bench_run.py -q -p no:cacheprovider`
Expected: 2 passed, in under about 120 s.

If the muvue arm is `errored`, read `arm["error"]` and fix the harness when the harness is at fault (for example a config regex or a command shape). If muvue itself misbehaves, that is a finding: record it in the result and the report. Do not hide it.

- [ ] **Step 5: Run the full suite and the first baseline**

Run: `uv run pytest -q -p no:cacheprovider`
Expected: all tests pass.

Then run: `uv run python -m bench run`
Expected: a markdown table on stdout, and `bench/results/2026-10-11-readinglog.json` written. Today's muvue should show a truth score below 1.0, because of the dependency-merge and zero-commit-done bugs. Copy the table into the task report.

- [ ] **Step 6: Commit**

```bash
git add bench/run.py bench/__main__.py tests/test_bench_run.py bench/results/2026-10-11-readinglog.json
git commit -m "Add the benchmark runner and record the first baseline" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01QM83ev6A1iYjgo8Me2X6FS"
```
