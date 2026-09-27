"""sync_issues は daily / report の途中で呼ばれるので、壊れた入力でも例外を外へ出さない。"""
from chronofit import cli


def test_一覧ファイルが壊れていても例外を出さず1を返す(tmp_path, monkeypatch):
    broken = tmp_path / "tasks.json"
    broken.write_text("{壊れた", encoding="utf-8")
    monkeypatch.setattr(cli.config, "load", lambda: {"todo_project": {"owner": "someone", "number": 1}})
    monkeypatch.setattr(cli.todo_issues, "fetch", lambda owner, number: ([], {}))
    monkeypatch.setattr(cli.paths, "tasks_path", lambda: broken)
    assert cli.sync_issues(quiet=True) == 1
    assert broken.read_text(encoding="utf-8") == "{壊れた"
