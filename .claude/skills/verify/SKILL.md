---
name: verify
description: Build/launch/drive recipe for muvue (Typer CLI over a per-repo SQLite state machine). Use before claiming any muvue change works.
---

# Verifying muvue

## Build

```bash
uv sync
```

No compiled artifacts. `uv run muvue ...` picks up source changes immediately (editable install).

## Get a throwaway target repo

muvue operates on an *external* git repo, never on itself. Never verify against
`/home/eugene/projects/muvue`'s own `.muvue/` — always scaffold a scratch repo:

```bash
mkdir -p /tmp/muvue-demo-repo && cd /tmp/muvue-demo-repo
git init -q && git config user.email t@t.com && git config user.name test
echo hi > README.md && git add -A && git commit -qm init
```

## Drive the full loop (smallest path that exercises the state machine end to end)

```bash
uv run muvue init /tmp/muvue-demo-repo
uv run muvue doctor /tmp/muvue-demo-repo                 # sanity + live security probes
uv run muvue project create --goal "..." --path /tmp/muvue-demo-repo   # -> project id
uv run muvue spec <project_id> --title "..." --body "..." --path /tmp/muvue-demo-repo   # -> spec node id
uv run muvue approve spec:<spec_id> --path /tmp/muvue-demo-repo
uv run muvue decompose <spec_id> --title "..." --criteria "..." --path /tmp/muvue-demo-repo  # -> task node id
uv run muvue approve gate2:<project_id> --path /tmp/muvue-demo-repo
uv run muvue run --path /tmp/muvue-demo-repo              # drives the `fake` driver (default routing)
uv run muvue approve review:<node_id> --path /tmp/muvue-demo-repo
uv run muvue close <project_id> --yes --path /tmp/muvue-demo-repo
uv run muvue uninit /tmp/muvue-demo-repo                  # must leave zero files behind vs. pre-init snapshot
```

`config.toml` written by `init` already routes every kind to the `fake` agent — no
vendor CLI login needed to exercise the runner.

## Gotchas

- The Bash tool's cwd resets between calls in some hosts — `cd` inside every
  command, don't rely on a persisted cwd.
- `project create --path` (and every other verb) defaults `--path` to `.`; when
  invoking from a different cwd than the target repo, `--path` is required.
- `doctor` starts a throwaway daemon on a scratch repo (not the target) to run
  live security probes, then tears it down — this is expected, not a leak.
- Round-trip proof for `init`/`uninit` is a real `find` snapshot diff, not
  `git status` alone (`.muvue/` is gitignored, so `git status` won't show it).
