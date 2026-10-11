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
