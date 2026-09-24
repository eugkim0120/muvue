"""FastAPI + SSE daemon API (plan section 8): `create_app` is the only
public entry point. The daemon holds no in-memory state (plan section 1,
principle 1) -- every route opens a fresh `sqlite3.Connection` through
`muvue.core.db` and closes it before returning; nothing here issues raw
SQL that `muvue.core` doesn't already own (plan working rule 3)."""

from .app import create_app

__all__ = ["create_app"]
