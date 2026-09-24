"""Real GitHub API access via the `gh` CLI subprocess -- the live-network
counterpart to `core.imports`'s injectable `fetch_fn` seam and
`core.pr`'s text-only body generation. P6 built both as documented stubs
because that build ran with no network access; this module plugs the
seam for good in an environment where `gh` is authenticated (see
docs/decisions.md). muvue never reads or stores GitHub credentials
itself (plan section 1 principle 7) -- it only shells out to the `gh`
CLI and relies on that CLI's own login, exactly like the vendor-agent
drivers in `core.drivers`.
"""

from __future__ import annotations

import json
import subprocess


class GithubError(Exception):
    """A `gh` CLI invocation failed (not authenticated, not found, no
    network, etc). Callers decide whether to fall back to a local
    `data`/`data_path` source."""


def _run_gh(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=30, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise GithubError(f"gh CLI unavailable: {exc}") from exc
    if proc.returncode != 0:
        raise GithubError(f"gh {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def fetch_issue_via_gh(number: int, *, repo: str | None = None) -> dict:
    """Real `fetch_fn` for `core.imports.import_github_issue`: fetches
    issue (or, failing that, PR) #`number` from `repo` (`"owner/name"`,
    or the current directory's `gh`-detected repo if omitted) via a real
    `gh issue view` / `gh pr view` call. Shape matches what
    `import_github_issue` expects: `{"number", "title", "url", "body"}`.
    """
    fields = "number,title,url,body"
    repo_args = ["--repo", repo] if repo else []
    try:
        out = _run_gh(["issue", "view", str(number), "--json", fields, *repo_args])
    except GithubError:
        out = _run_gh(["pr", "view", str(number), "--json", fields, *repo_args])
    return json.loads(out)


def create_pr_via_gh(
    *, head: str, base: str, title: str, body: str, repo: str | None = None,
) -> dict:
    """Real `gh pr create` call: the live counterpart to
    `core.pr.generate_pr_body`'s text-only output. Returns
    `{"url": ...}` on success; raises `GithubError` on failure (e.g. no
    commits between `base` and `head`, no push access)."""
    repo_args = ["--repo", repo] if repo else []
    url = _run_gh([
        "pr", "create", "--head", head, "--base", base,
        "--title", title, "--body", body, *repo_args,
    ]).strip()
    return {"url": url}
