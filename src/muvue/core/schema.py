"""SQLite schema for muvue.core (P0).

Process graph tables per plan section 3, plus empty structure-graph tables
(created now, no logic yet, needed from P6 onward).
"""

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal TEXT NOT NULL,
    phase TEXT NOT NULL DEFAULT 'planning'
        CHECK (phase IN ('planning', 'executing', 'paused', 'closed')),
    budget_unit TEXT NOT NULL DEFAULT 'usd',
    budget_limit REAL NOT NULL DEFAULT 0,
    spent REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
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

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id INTEGER NOT NULL REFERENCES nodes(id),
    kind TEXT NOT NULL CHECK (kind IN ('discovery', 'decision', 'lesson', 'feedback')),
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    pinned INTEGER NOT NULL DEFAULT 0,
    last_retrieved_at TEXT,
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
