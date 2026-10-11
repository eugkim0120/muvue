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
