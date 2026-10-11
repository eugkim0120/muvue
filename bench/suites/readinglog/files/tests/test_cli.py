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
