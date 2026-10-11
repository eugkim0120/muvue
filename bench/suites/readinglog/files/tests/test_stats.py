from readinglog.cli import main


def test_stats_after_one_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("READINGLOG_FILE", str(tmp_path / "log.json"))
    main(["add", "Dune", "--pages", "400"])
    main(["log", "Dune", "25"])
    capsys.readouterr()
    assert main(["stats"]) == 0
    assert capsys.readouterr().out.splitlines() == ["Pages this week: 25", "Current streak: 1 day"]
