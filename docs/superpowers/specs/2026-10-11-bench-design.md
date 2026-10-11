# Benchmark harness (Phase 0) design

Date: 2026-10-11. Status: approved by the user in chat ("Yes, go").
Context: the evaluation in the plan "Making muvue world-class" (published as the "muvue Audit" artifact). That evaluation asked whether muvue increases or reduces transparency, flexibility and token use. Phase 0 builds the harness that answers those questions with numbers on every release.

## Goal

One command drives the real muvue CLI through a fixed project on a scratch repository and writes a dated result. The result:
- scores muvue on truth, outcome, tokens and flexibility;
- compares it with a "plain" arm that does the same work without muvue.

Today's baseline must show the known failures, in particular a task marked done with no commit. That proves the harness detects them.

## Constraints

- **No paid calls.** The user ruled that the bench never calls the Claude API. A scripted shim agent stands in for `claude -p`.
- **The bench drives muvue only through its public CLI**, `python -m muvue ...` from the current source tree. It never imports `muvue.core` to change state. It reads `.muvue/muvue.db` read-only to score.
- **Nothing from the user's environment is committed.** The recorded readinglog logs contain the user's tool list, MCP server names and home paths. Only per-task usage numbers are copied into fixtures.
- **The bench lives at the repo root in `bench/`** and is not shipped in the wheel. Its tests live in `tests/` with the rest of the suite.
- **Every number in the report is labelled measured or replayed.**

## Pieces

### `bench/shim_agent.py`, the stand-in for `claude -p`

It is invoked as the agent `command`, with the prompt on stdin and the task worktree as cwd.

1. **Find the task.** Parse the node id from the prompt, the same way the runner states it. Map it to a suite task through a JSON file the bench writes once it has created the tasks.
2. **Check dependencies.** Every path listed in the task's `requires` must exist in the worktree. If any is missing, write nothing and emit a result whose text begins `Blocked. No commit made.` and names the missing files, with `is_error: false`. This is what real Claude did on task #4.
3. **Otherwise, do the work.** Copy the task's fixture files into the worktree, then `git add` and `git commit` with the `Muvue-Node: <id>` trailer (the exact key comes from `muvue.core.trailers`). Emit a summary naming the files.
4. **Output.** Write stream-json that `parse_claude_stream_json` accepts: one `system`/`init` line and one `result` line. The result carries:
   - the task's recorded `usage` (input, cache-creation, cache-read and output tokens);
   - `total_cost_usd`;
   - `num_turns`.

   The agent is configured with `usage_parser = "claude_stream_json"`, so muvue's real Claude parsing and spend recording run.
5. **Plain mode.** `--plain` applies every task's files in dependency order in the repo's main checkout and makes one commit per task. That is one session. The bench runs no muvue in this arm.

### `bench/suites/readinglog/`

- **`spec.md`:** the spec text used in the real run.
- **`tasks.toml`:** the six tasks, with the real titles, criteria, predicted touches, `depends_on` and `carries` from the readinglog run. Each task also has:
  - `files`: fixture paths;
  - `requires`: paths that must exist first;
  - a `usage` block with the numbers recorded in `~/projects/readinglog` `node_usage`.
- **`files/`:** hand-written fixture source that composes cleanly.
  - `cli.py` (task 4) dispatches subcommands it discovers in a `readinglog/commands/` package.
  - Tasks 4, 6 and 7 each add a module there and edit no shared file.
  - That way a correct orchestrator produces no merge conflicts, and any conflict is muvue's fault.
- **Task 4 is written fresh,** because the real run produced nothing for it.
- **`acceptance/test_acceptance.py`:** the hidden tests. They run `readinglog add`, `log`, `progress` and `stats` against a temporary `READINGLOG_FILE` on the final `main`. They live outside the scratch repo, so no agent sees them.

### `bench/run.py`, entry point `python -m bench`

`python -m bench run [--suite readinglog] [--arm muvue|plain|both]`

**muvue arm:**
1. Create a scratch git repo with the suite's starting files.
2. Run `muvue init`.
3. Write a config with:
   - the shim agent routed for every kind;
   - `worktree_mode = "per_node"`;
   - checks `python -m pytest -q` and lint `true`;
   - `worktree_setup = ""`;
   - default risk globs.
4. Create the project, the spec and the approval, `decompose` each task, then approve gate2.
5. Loop until no task is ready or the step cap is reached: `muvue run`, then `approve review:N` for each task in review.
6. Finish with `muvue merge` if anything is left unmerged.

**Isolation:** each run's scratch repos go under a fresh temporary directory. Every muvue subprocess runs with `HOME` pointed at a directory inside it, so worktrees (`~/.muvue/worktrees/<hash>`) and daemon files never touch the user's home, and deleting the temporary directory removes them.

**Plain arm:** create the same scratch repo, then run the shim once with `--plain`.

**Scoring, for both arms:** run the hidden acceptance tests on the final `main`, then compute the metrics below.

**Output:**
- `bench/results/<YYYY-MM-DD>-<suite>.json`, which holds both arms, the muvue git sha and the bench version;
- a markdown table on stdout.

The results directory is committed. Each run adds a file, so baselines stay comparable.

## Metrics

| Group | Metric | Source | Label |
|---|---|---|---|
| Truth | done tasks with at least one linked commit, out of done tasks | `nodes`, `node_commits` | measured |
| Truth | done tasks whose stored summary says blocked or no commit | `nodes.summary` matched against `^Blocked` or `no commit` | measured |
| Truth | check runs with stored output, out of check runs | events (today: none stored) | measured |
| Truth | approvals by actor (human, agent or the bench's script) | events `actor`, `actor_evidence` | measured |
| Outcome | hidden acceptance tests passed / total | pytest on final `main` | measured |
| Outcome | merge conflicts, rebase subtasks, failed tasks | events, `nodes` | measured |
| Outcome | wall clock per arm | bench timer | measured |
| Tokens | agent sessions launched | events `node.start` / spend rows | measured |
| Tokens | prompt bytes muvue sent per session | stdin bytes, recorded by the shim | measured |
| Tokens | tokens and USD per session | fixture `usage` replayed through muvue's parser | replayed |
| Flexibility | muvue commands the bench had to issue, split into human verbs and others | bench step log | measured |

The **truth score** is the share of done tasks that have a commit and do not claim to be blocked. A world-class muvue scores 100%. Today's expected baseline is below 100%, because of the dependency-merge bug and zero-commit done.

## Error handling

- **A muvue command fails unexpectedly:** record its exit code and stderr in the result. Mark the arm `errored`, still write the JSON, and exit non-zero. No retries and no fallbacks.
- **The step cap is reached:** default 30 muvue commands per arm. Mark the arm `stalled` and score what exists.
- **The shim gets a prompt it can't map to a task:** emit `is_error: true` with the reason. That is a bench bug and must be visible.

## Testing

Tests go in `tests/`, written first:
- **Shim:**
  - writes and commits the fixture files when `requires` are present;
  - emits a blocked summary and commits nothing when they are absent;
  - its stream-json parses with `parse_claude_stream_json` to the fixture usage.
- **Scoring:** a seeded sqlite file, or a tiny real run, produces the expected truth numbers, including a zero-commit done task.
- **Plain arm:** passes all acceptance tests. This proves the fixtures and the hidden tests are consistent.
- **End to end:** `python -m bench run --arm muvue` on the suite completes without `errored` and writes the JSON. It is marked slow if it exceeds about 60 s.

## Out of scope

- Other suites (a bugfix suite and a refactor suite), until this one is used.
- Any fix to muvue itself; Phase A does those.
- Real agent runs, and measuring the effect of lean-session flags (Phase C needs paid runs, which require the user's go-ahead).
