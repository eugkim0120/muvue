"""SQLite schema for muvue.core (P0).

Process graph tables per plan section 3, plus empty structure-graph tables
(created now, no logic yet, needed from P6 onward).

- SCHEMA_VERSION 2 -> 3 (P7): `notes.archived_at` (lesson decay soft
  delete). `core.migrate.run_migrate` `ALTER TABLE`s it into pre-existing
  databases; new databases get it straight from this `CREATE TABLE`.
- SCHEMA_VERSION 3 -> 4 (v4 handoff plan section 3, foundational
  txn/schema slice): `projects.closed_at` (`created_at` already
  existed); new `agent_spend` table (per-driver budget accounting,
  section 2/6); `nodes.attempts` split from the new `nodes.
  lease_expiries` (a daemon-reclaim/crash-timeout counter that must
  never drive `failed` -- see core.daemon.reconcile_leases and
  docs/decisions.md); `events.actor_evidence` (how the actor was
  determined -- `tty`/`dashboard_token`/`mcp`/`hook`/`subprocess`,
  purely additive/observational this phase, see docs/decisions.md);
  new `actual_touches` table (written from commits by
  core.hooks.handle_post_commit, drift-vs-`predicted_touches` KPI is
  future work).
- SCHEMA_VERSION 4 -> 5 (v4 section 5, branch-coherence check):
  `projects.branch` -- the branch `core.gitutil.current_branch` reports
  at `core.projects.create_project` time, `NULL` when the project wasn't
  created with a `repo_root` (e.g. most unit tests, or a repo with no
  git branch at all). `core.nodes.start` and `core.doctor.run_doctor`
  both compare the repo's *current* branch against this recorded one.
- SCHEMA_VERSION 5 -> 6 (v4 section 2 budget rule): drops the v3
  per-project `projects.budget_unit/budget_limit/spent` columns. v4
  budgets are per driver (`[agents.<x>.budget]`, `agent_spend`); nothing
  ever enforced the old columns.
"""

SCHEMA_VERSION = 6

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'planning'
        CHECK (phase IN ('planning', 'executing', 'paused', 'closed')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_at TEXT,
    branch TEXT
);

-- v4 section 2/3: one row per (project, driver) -- "Budget is per driver,
-- in that driver's unit." `unit` mirrors that driver's `[agents.<x>.
-- budget].unit` (validated against `cost_model` at config-load time,
-- future phase); this table only accumulates `spent`, it does not judge
-- it against a limit (no budget-check logic lands this phase -- see
-- docs/decisions.md).
CREATE TABLE IF NOT EXISTS agent_spend (
    project_id INTEGER NOT NULL REFERENCES projects(id),
    agent TEXT NOT NULL,
    unit TEXT NOT NULL,
    spent REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, agent, unit)
);

CREATE TABLE IF NOT EXISTS plan_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    n INTEGER NOT NULL,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    parent_id INTEGER REFERENCES nodes(id),
    kind TEXT NOT NULL CHECK (kind IN ('spec', 'task', 'subtask')),
    title TEXT NOT NULL,
    body_md TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    block_reason TEXT,
    criteria_json TEXT NOT NULL DEFAULT '[]',
    criteria_hash TEXT,
    criteria_mode TEXT NOT NULL DEFAULT 'manual'
        CHECK (criteria_mode IN ('auto', 'external', 'manual')),
    risk_tier TEXT NOT NULL DEFAULT 'low',
    version INTEGER NOT NULL DEFAULT 1,
    owner TEXT,
    lease_until TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    lease_expiries INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    summary TEXT,
    worktree TEXT,
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS deps (
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    depends_on INTEGER NOT NULL REFERENCES nodes(id),
    PRIMARY KEY (node_id, depends_on)
);

CREATE TABLE IF NOT EXISTS predicted_touches (
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    path_glob TEXT NOT NULL,
    PRIMARY KEY (node_id, path_glob)
);

-- v4 section 3: "written from commits; drift vs predicted is a KPI."
-- One row per (node, real file path) touched by a commit linked to that
-- node -- core.hooks.handle_post_commit writes these alongside
-- `node_commits`. The prediction-vs-actual drift KPI computation itself
-- is future (P6/P7-scoped) work; this phase only gets the raw data
-- flowing in.
CREATE TABLE IF NOT EXISTS actual_touches (
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    path TEXT NOT NULL,
    PRIMARY KEY (node_id, path)
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    kind TEXT NOT NULL CHECK (kind IN ('discovery', 'decision', 'lesson', 'feedback')),
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    last_retrieved_at TEXT,
    -- P7 lesson decay (plan section 9): a lesson note not retrieved by
    -- enough distinct projects gets archived unless pinned. Soft-delete,
    -- same `*_at` convention as `nodes.deleted_at` -- see docs/decisions.md.
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    text, content='notes', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO notes_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    project_id INTEGER REFERENCES projects(id),
    node_id INTEGER REFERENCES nodes(id),
    actor TEXT NOT NULL CHECK (actor IN ('agent', 'human', 'hook', 'daemon')),
    -- v4 section 3: "records how the actor was determined (tty,
    -- dashboard_token, mcp, hook, subprocess) so a human verb issued by
    -- a non-TTY process is visible rather than merely disallowed."
    -- Purely additive/observational this phase -- nothing enforces on
    -- it yet (see docs/decisions.md); nullable so an old row (pre-v4,
    -- or a call site this phase missed) reads back as "unknown" rather
    -- than a fabricated value.
    actor_evidence TEXT,
    type TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    request_id TEXT,
    acked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_request_id ON events(request_id);
CREATE INDEX IF NOT EXISTS idx_events_node_id ON events(node_id);

CREATE TABLE IF NOT EXISTS node_commits (
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    sha TEXT NOT NULL,
    files TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (node_id, sha)
);

CREATE TABLE IF NOT EXISTS node_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    agent TEXT NOT NULL,
    model TEXT,
    in_tokens INTEGER NOT NULL DEFAULT 0,
    out_tokens INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    rate_limited INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS plan_revision_nodes (
    revision_id INTEGER NOT NULL REFERENCES plan_revisions(id),
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    criteria_hash TEXT NOT NULL,
    PRIMARY KEY (revision_id, node_id)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    project_id INTEGER NOT NULL REFERENCES projects(id),
    text TEXT NOT NULL,
    default_answer TEXT,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'answered', 'timed_out')),
    answer TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS external_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    system TEXT NOT NULL,
    ext_id TEXT NOT NULL,
    url TEXT
);

-- Structure graph: empty schema for now, no logic until P6+.
CREATE TABLE IF NOT EXISTS components (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT,
    purpose TEXT,
    anchors_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'current'
        CHECK (status IN ('current', 'stale', 'deprecated')),
    verified_sha TEXT
);

CREATE TABLE IF NOT EXISTS component_edges (
    src INTEGER NOT NULL REFERENCES components(id),
    dst INTEGER NOT NULL REFERENCES components(id),
    kind TEXT NOT NULL,
    PRIMARY KEY (src, dst, kind)
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    context TEXT,
    choice TEXT,
    rejected_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'current',
    superseded_by INTEGER REFERENCES decisions(id),
    source_node_id INTEGER REFERENCES nodes(id)
);

-- P6: `brief`'s structure-aware ranking (plan section 4) searches these
-- two FTS5 tables in addition to notes_fts, now that `close` (core/close.py)
-- actually populates `decisions`/`components` (see docs/decisions.md).
CREATE VIRTUAL TABLE IF NOT EXISTS decisions_fts USING fts5(
    title, context, choice, content='decisions', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS decisions_ai AFTER INSERT ON decisions BEGIN
    INSERT INTO decisions_fts(rowid, title, context, choice)
        VALUES (new.id, new.title, new.context, new.choice);
END;

CREATE TRIGGER IF NOT EXISTS decisions_ad AFTER DELETE ON decisions BEGIN
    INSERT INTO decisions_fts(decisions_fts, rowid, title, context, choice)
        VALUES ('delete', old.id, old.title, old.context, old.choice);
END;

CREATE TRIGGER IF NOT EXISTS decisions_au AFTER UPDATE ON decisions BEGIN
    INSERT INTO decisions_fts(decisions_fts, rowid, title, context, choice)
        VALUES ('delete', old.id, old.title, old.context, old.choice);
    INSERT INTO decisions_fts(rowid, title, context, choice)
        VALUES (new.id, new.title, new.context, new.choice);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS components_fts USING fts5(
    name, purpose, content='components', content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS components_ai AFTER INSERT ON components BEGIN
    INSERT INTO components_fts(rowid, name, purpose) VALUES (new.id, new.name, new.purpose);
END;

CREATE TRIGGER IF NOT EXISTS components_ad AFTER DELETE ON components BEGIN
    INSERT INTO components_fts(components_fts, rowid, name, purpose)
        VALUES ('delete', old.id, old.name, old.purpose);
END;

CREATE TRIGGER IF NOT EXISTS components_au AFTER UPDATE ON components BEGIN
    INSERT INTO components_fts(components_fts, rowid, name, purpose)
        VALUES ('delete', old.id, old.name, old.purpose);
    INSERT INTO components_fts(rowid, name, purpose) VALUES (new.id, new.name, new.purpose);
END;

CREATE TABLE IF NOT EXISTS invariants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    component_id INTEGER REFERENCES components(id)
);

CREATE TABLE IF NOT EXISTS node_touches (
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    component_id INTEGER NOT NULL REFERENCES components(id),
    PRIMARY KEY (node_id, component_id)
);

CREATE TABLE IF NOT EXISTS project_links (
    src INTEGER NOT NULL REFERENCES projects(id),
    dst INTEGER NOT NULL REFERENCES projects(id),
    kind TEXT NOT NULL CHECK (kind IN ('follows', 'supersedes', 'depends')),
    PRIMARY KEY (src, dst, kind)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
