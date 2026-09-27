"""Issue を一覧の正本にするときの変換のテスト。リポジトリ名・件名はすべて架空。"""
import subprocess

from chronofit.sources import todo_issues


def item(number, title, labels, repo="https://github.com/someone/todo", **fields):
    return {"content": {"type": "Issue", "number": number, "title": title, "repository": repo},
            "labels": labels, "title": title, **fields}


def test_開いているIssueだけを一覧にする_閉じたものと下書きは入れない():
    items = [item(1, "レポートを出す", ["P1:必須", "授業"], target="2026-09-27",
                  start="2026-09-26"),
             item(2, "終わった用事", ["P2:通常", "買い物"], target="2026-09-20"),
             {"content": {"type": "DraftIssue", "title": "思いつき"}, "labels": []}]
    tasks = todo_issues.to_tasks(items, {"someone/todo": {1}})
    assert tasks == [{"subject": "授業", "kind": "Issue", "count": 1,
                      "target": "#1 レポートを出す", "issue": "someone/todo#1",
                      "priority": "S", "due": "2026-09-27", "start": "2026-09-26"}]


def test_領域ラベルが無ければ既定の科目_見積もりがあれば使う():
    tasks = todo_issues.to_tasks([item(3, "片付け", ["P3:任意"], **{"見積もり": 1.5})],
                                 {"someone/todo": {3}})
    assert tasks[0]["subject"] == "todo" and tasks[0]["priority"] == "B"
    assert tasks[0]["assumed_hours"] == 1.5 and "due" not in tasks[0]


def test_手で書いたタスクは残しIssue由来は取り替える():
    manual = {"subject": "数学", "kind": "過去問", "count": 3}
    stale = {"subject": "授業", "kind": "Issue", "count": 1, "issue": "someone/todo#9"}
    fresh = {"subject": "授業", "kind": "Issue", "count": 1, "issue": "someone/todo#1"}
    assert todo_issues.merge([manual, stale], [fresh]) == [manual, fresh]


def test_P始まりの領域ラベルは優先度と取り違えない():
    tasks = todo_issues.to_tasks([item(4, "調べもの", ["P2P", "P1"])], {"someone/todo": {4}})
    assert tasks[0]["subject"] == "P2P" and tasks[0]["priority"] == "S"


def test_複数リポジトリのProjectでは件名にリポジトリ名を付ける():
    items = [item(5, "準備", []), item(5, "準備", [], repo="https://github.com/someone/other")]
    tasks = todo_issues.to_tasks(items, {"someone/todo": {5}, "someone/other": {5}})
    assert [task["target"] for task in tasks] == ["todo#5 準備", "other#5 準備"]


def test_ghは窓を作らずに起動する(monkeypatch):
    seen = {}

    def run(args, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="{}", stderr="")

    monkeypatch.setattr(todo_issues.subprocess, "run", run)
    todo_issues.gh_json("project", "item-list")
    assert seen.get("creationflags", 0) & todo_issues.NO_WINDOW == todo_issues.NO_WINDOW
