"""作業名とリポジトリへの割り付けのテスト。パス・作業名・リポジトリ名はすべて架空。"""
import json
from datetime import datetime, timezone

from chronofit.model import work
from chronofit.sources import claude_sessions
from chronofit.ui import report_work


def at(clock):
    return datetime.fromisoformat(f"2026-01-05T{clock}+09:00")


def span(clock, title, sec=600, proc="WindowsTerminal.exe"):
    start = f"2026-01-05T{clock}+09:00"
    return {"t": "span", "start": start, "end": start, "sec": sec, "active_sec": sec,
            "proc": proc, "title": title}


def session(title, *marks):
    return {"titles": {title}, "marks": [(at(clock), cwd) for clock, cwd in marks]}


def by_name(repos):
    return lambda cwd: (repos.get(cwd, cwd.rsplit("/", 1)[-1]), cwd in repos)


class Test作業名:
    def test_tmuxの窓番号と処理中マークを外す(self):
        assert work.task_name("dev 3:✳ 資料の整理") == "資料の整理"
        assert work.task_name("dev 3:◐ 資料の整理") == "資料の整理"
        assert work.task_name("⠋ 資料の整理") == "資料の整理"

    def test_GitHubのページはリポジトリ名を取る(self):
        assert work.github_repo("直す · Issue #3 · someone/notes - Browser") == "notes"
        assert work.github_repo("検索結果 - Browser") is None


class Test割り付け:
    def test_処理中マーク違いは同じ作業にまとめ_前後の作業ディレクトリの多数決で決める(self):
        sessions = {"資料の整理": [session("資料の整理", ("10:00", "/w/notes"),
                                        ("10:05", "/w/notes/sub"), ("10:08", "/w/tmp"))]}
        spans = [span("10:03:00", "dev 3:✳ 資料の整理"), span("10:13:00", "dev 3:◑ 資料の整理")]
        rows = work.attribute(spans, sessions, claude_sessions.cwds_near,
                              by_name({"/w/notes": "notes", "/w/notes/sub": "notes"}))
        assert [(row["repo"], row["task"], row["active_sec"]) for row in rows] == [
            ("notes", "資料の整理", 1200.0)]

    def test_gitの外しか無ければその名前_会話に無い作業名とアプリは扱わない(self):
        sessions = {"下書き": [session("下書き", ("10:00", "/w/scratch"))]}
        spans = [span("10:01:00", "下書き"), span("10:02:00", "無関係の窓"),
                 span("10:03:00", "メモ", proc="notepad.exe")]
        rows = work.attribute(spans, sessions, claude_sessions.cwds_near, by_name({}))
        assert [(row["repo"], row["task"]) for row in rows] == [("scratch", "下書き")]

    def test_GitHubの閲覧はそのリポジトリへ寄せる(self):
        rows = work.attribute([span("10:00:00", "PR · someone/notes - Browser",
                                    proc="vivaldi.exe")], {}, claude_sessions.cwds_near)
        assert rows[0]["repo"] == "notes" and rows[0]["task"] == work.GITHUB_VIEW

    def test_worktreeは元のリポジトリ名に揃える(self, tmp_path):
        main = tmp_path / "notes"
        (main / ".git" / "worktrees" / "notes-extra").mkdir(parents=True)
        tree = tmp_path / "notes-extra"
        tree.mkdir()
        (tree / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'notes-extra'}\n",
                                   encoding="utf-8")
        assert work.repo_name(str(tree / "src")) == ("notes", True)
        assert work.repo_name(str(main)) == ("notes", True)

    def test_リポジトリ別の合計(self):
        rows = [{"repo": "a", "task": "x", "active_sec": 60.0},
                {"repo": "b", "task": "y", "active_sec": 300.0},
                {"repo": "a", "task": "z", "active_sec": 30.0}]
        groups = work.by_repo(rows)
        assert [(g["repo"], g["active_sec"], len(g["tasks"])) for g in groups] == [
            ("b", 300.0, 1), ("a", 90.0, 2)]


class Test会話ログ:
    def test_作業名と作業ディレクトリだけを読む_壊れた行は捨てる(self, tmp_path):
        log = tmp_path / "projects" / "p" / "s.jsonl"
        log.parent.mkdir(parents=True)
        lines = [{"type": "ai-title", "aiTitle": "資料の整理"},
                 {"type": "user", "cwd": "/w/notes", "timestamp": "2026-01-05T01:00:00Z"},
                 {"type": "assistant", "message": "本文は読まない"}]
        log.write_text("\n".join(map(json.dumps, lines)) + "\n{壊れた\n", encoding="utf-8")
        found = claude_sessions.load(datetime(2000, 1, 1, tzinfo=timezone.utc), [tmp_path])
        marks = found["資料の整理"][0]["marks"]
        assert marks == [(datetime(2026, 1, 5, 1, tzinfo=timezone.utc), "/w/notes")]

    def test_指定した日より前に止まったログは開かない(self, tmp_path):
        log = tmp_path / "projects" / "p" / "old.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text("{}\n", encoding="utf-8")
        future = datetime(2999, 1, 1, tzinfo=timezone.utc)
        assert claude_sessions.transcript_files([tmp_path], future) == []

    def test_幅の中に記録が無ければ一番近い記録を使う(self):
        sessions = [session("x", ("08:00", "/w/early"), ("12:00", "/w/late"))]
        assert claude_sessions.cwds_near(sessions, at("11:00"), window_sec=600) == ["/w/late"]

    def test_遠すぎる記録は使わない(self):
        sessions = [session("x", ("08:00", "/w/early"))]
        assert claude_sessions.cwds_near(sessions, at("20:00"), window_sec=600) == []


class Test画面:
    def test_1分未満の作業は行に出さず_割り付けた割合を出す(self):
        groups = [{"repo": "notes", "active_sec": 1800.0,
                   "tasks": [{"task": "整理", "active_sec": 1800.0,
                              "first": "2026-01-05T10:00:00+09:00",
                              "last": "2026-01-05T10:30:00+09:00"}]},
                  {"repo": "tiny", "active_sec": 20.0, "tasks": []}]
        html = report_work.work_section(groups, 3600.0)
        assert "notes" in html and "tiny" not in html and "51%" in html

    def test_読めなかったものは理由を出す(self):
        assert "gh が失敗" in report_work.done_section({"error": "gh が失敗"})
        assert "無い" in report_work.done_section({"prs": [], "issues": [], "commits": {}})

    def test_検索が1つ落ちても取れた分は出す(self):
        html = report_work.done_section({
            "prs": [{"repo": "notes", "number": 3, "title": "直す"}], "issues": [],
            "commits": {}, "errors": ["commits: 制限"]})
        assert "notes#3" in html and "commits: 制限" in html

    def test_1分未満の作業しか無いリポジトリは空の一覧にしない(self):
        groups = [{"repo": "notes", "active_sec": 120.0,
                   "tasks": [{"task": "a", "active_sec": 30.0, "first": "2026-01-05T10:00:00+09:00",
                              "last": "2026-01-05T10:00:30+09:00"}] * 4}]
        html = report_work.work_section(groups, 600.0)
        assert "<ul class='tasks'></ul>" not in html and "1分未満" in html


def test_GitHubの検索は現地時刻の1日で切る():
    from datetime import date, timedelta
    from chronofit.sources import github_done
    tz = timezone(timedelta(hours=9))
    assert github_done.day_range(date(2026, 1, 5), tz) == (
        "2026-01-05T00:00:00+09:00..2026-01-05T23:59:59+09:00")


def test_GitHubの成果はURLも持つ():
    from chronofit.sources import github_done
    rows = github_done._rows([{"repository": {"name": "x"}, "number": 3, "title": "t",
                               "closedAt": "", "url": "https://github.com/someone/x/pull/3"}])
    assert rows[0]["url"] == "https://github.com/someone/x/pull/3"


def test_これからの予定に出る時刻と終わる時刻の目安を出す():
    event = {"title": "散髪", "start": "2026-09-29T15:00:00+09:00",
             "end": "2026-09-29T16:00:00+09:00"}
    guess = {"place": "店", "n": 3, "arrive": -8, "leave": -11, "done": 72}
    html = report_work.agenda_section({"checked": [], "upcoming": [
        {"event": event, "forecast": guess}, {"event": {**event, "title": "ゼミ"},
                                              "forecast": None}]})
    assert "これから 店・14:49 ごろ出る・16:12 ごろ終わる（過去3回）" in html
    assert "<td class='muted'>これから</td>" in html
