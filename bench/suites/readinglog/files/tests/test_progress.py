from readinglog.cli import main


def test_progress_sorted_by_percent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("READINGLOG_FILE", str(tmp_path / "log.json"))
    main(["add", "Dune", "--pages", "400"])
    main(["add", "Emma", "--pages", "100"])
    main(["log", "Emma", "50"])
    capsys.readouterr()
    assert main(["progress"]) == 0
    assert capsys.readouterr().out.splitlines() == ["Emma: 50/100 pages (50%)", "Dune: 0/400 pages (0%)"]
