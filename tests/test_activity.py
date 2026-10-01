"""やったことの全時間割り付けのテスト。アプリ・URL・リポジトリ名・場所はすべて架空。"""
from datetime import datetime, timedelta, timezone

from chronofit.model import activity, work
from chronofit.sources import claude_sessions
from chronofit.ui import report_projects

JST = timezone(timedelta(hours=9))


def at(clock):
    return datetime.fromisoformat(f"2026-01-05T{clock}+09:00")


def span(clock, title, sec=600, proc="WindowsTerminal.exe"):
    start = at(clock)
    return {"t": "span", "start": start.isoformat(),
            "end": (start + timedelta(seconds=sec)).isoformat(),
            "sec": sec, "active_sec": sec, "proc": proc, "title": title}


def visit(clock, url, title):
    """閲覧履歴の1行（naive UTC）。"""
    return (at(clock).astimezone(timezone.utc).replace(tzinfo=None), url, title)


def session(title, *marks):
    return {"titles": {title}, "marks": [(at(clock), cwd) for clock, cwd in marks]}


def repo_of(cwd):
    return cwd.rsplit("/", 1)[-1], True


def run(spans, sessions=None, **kwargs):
    return activity.attribute(spans, sessions or {}, claude_sessions.cwds_near,
                              repo_of=repo_of, **kwargs)


def projects(result):
    return {row["repo"]: [(task["task"], task["active_sec"]) for task in row["tasks"]]
            for row in result["projects"]}


def activities(result):
    return {(row["group"], row["label"]): row["active_sec"] for row in result["activities"]}


class TestブラウザのURL:
    def test_タイトルが同じ訪問のうち一番近い時刻のURLを使う(self):
        index = activity.url_index([visit("09:00", "https://a.example/old", "記事"),
                                    visit("10:01", "https://a.example/new", "記事")])
        assert activity.url_for(span("10:00:00", "記事 - Vivaldi", proc="vivaldi.exe"),
                                index) == "https://a.example/new"

    def test_離れすぎた訪問は使わない(self):
        index = activity.url_index([visit("01:00", "https://a.example/", "記事")])
        assert activity.url_for(span("23:00:00", "記事 - Vivaldi", proc="vivaldi.exe"),
                                index) is None

    def test_GitHubのURLはそのリポジトリへ_予約されたパスは除く(self):
        assert activity.github_url_repo("https://github.com/someone/notes/pull/3") == "notes"
        assert activity.github_url_repo("https://github.com/settings/profile") is None
        result = run([span("10:00:00", "差分 - Vivaldi", proc="vivaldi.exe")],
                     visits=[visit("10:00", "https://github.com/someone/notes/pull/3", "差分")])
        assert projects(result) == {"notes": [(work.GITHUB_VIEW, 600.0)]}


class Test規則:
    def test_プロジェクトと括りへ入れ_当たらなければ未分類(self):
        rules = [{"match": r"^game\.exe", "project": "toy-game", "label": "動かして確認"},
                 {"match": r"chat\.example", "group": "連絡", "label": "チャット"},
                 {"match": "(壊れた"}]
        spans = [span("10:00:00", "Toy", proc="game.exe"),
                 span("10:10:00", "部屋 - Vivaldi", proc="vivaldi.exe"),
                 span("10:20:00", "無題", proc="paint.exe")]
        result = run(spans, rules=rules,
                     visits=[visit("10:10", "https://chat.example/room", "部屋")])
        assert projects(result) == {"toy-game": [("動かして確認", 600.0)]}
        assert activities(result) == {("連絡", "チャット"): 600.0}
        assert [(row["label"], row["tasks"][0]["task"]) for row in result["unclassified"]] == [
            ("paint", "無題")]
        assert result["sec"] == 1800.0

    def test_閲覧の規則は科目を学習へ_種別はその括りへ(self):
        browse = [{"match": "講義", "subject": "架空の講義"},
                  {"match": r"video\.example", "category": "娯楽"}]
        spans = [span("10:00:00", "第3回 講義 - Vivaldi", proc="vivaldi.exe"),
                 span("10:10:00", "猫 - Vivaldi", proc="vivaldi.exe")]
        result = run(spans, browse_rules=browse,
                     visits=[visit("10:10", "https://video.example/watch", "猫")])
        assert activities(result) == {(activity.STUDY_GROUP, "架空の講義"): 600.0,
                                      ("娯楽", "video.example"): 600.0}


class Test確認の画面:
    RULES = [{"match": r"localhost", "follow_claude": True, "group": "開発",
              "label": "手元の画面を確認"}]

    def test_直前に見ていたターミナルの作業へ寄せる_忙しい会話ではなく(self):
        sessions = {"道具の整理": [session("道具の整理", ("10:00", "/w/tools"))],
                    "大きな作業": [session("大きな作業", *[(f"10:{m:02d}", "/w/big")
                                                     for m in range(0, 30, 2)])]}
        spans = [span("10:00:00", "dev 2:道具の整理", sec=300),
                 span("10:05:00", "プレビュー - Vivaldi", proc="vivaldi.exe")]
        result = run(spans, sessions, rules=self.RULES,
                     visits=[visit("10:05", "http://localhost:8000/", "プレビュー")])
        assert dict(projects(result)["tools"]) == {"道具の整理": 300.0, "手元の画面を確認": 600.0}

    def test_ターミナルを見てから時間が空けば会話の多数決_それも無ければ括り(self):
        sessions = {"道具の整理": [session("道具の整理", ("10:00", "/w/tools"))]}
        spans = [span("08:00:00", "dev 2:道具の整理", sec=60),
                 span("10:00:00", "プレビュー - Vivaldi", proc="vivaldi.exe")]
        visits = [visit("10:00", "http://localhost:8000/", "プレビュー")]
        assert "手元の画面を確認" in dict(projects(run(spans, sessions, rules=self.RULES,
                                                       visits=visits))["tools"])
        alone = run(spans[1:], rules=self.RULES, visits=visits)
        assert activities(alone) == {("開発", "手元の画面を確認"): 600.0}


class Test名前の無い窓:
    def test_同じtmuxの窓に後から付いた作業名を使う(self):
        sessions = {"資料の整理": [session("資料の整理", ("10:20", "/w/notes"))],
                    "大きな作業": [session("大きな作業", *[(f"10:{m:02d}", "/w/big")
                                                     for m in range(0, 30, 2)])]}
        spans = [span("10:00:00", "dev 4:Claude Code"),
                 span("10:20:00", "dev 4:✳ 資料の整理")]
        assert projects(run(spans, sessions)) == {"notes": [("資料の整理", 1200.0)]}

    def test_同じ窓に名前が無ければ会話の多数決(self):
        sessions = {"大きな作業": [session("大きな作業", ("10:00", "/w/big"))]}
        result = run([span("10:00:00", "dev 7:Claude Code")], sessions)
        assert projects(result) == {"big": [(activity.UNNAMED_CLAUDE, 600.0)]}


class Test外出:
    BOUNDS = (at("05:00:00"), at("05:00:00") + timedelta(days=1))

    def stay(self, place, start, end, **extra):
        return {"place": place, "start": at(start), "end": at(end), **extra}

    def test_家と短い滞在を除き_重なった予定とスマホの時間を付ける(self):
        visits = [self.stay("家", "06:00", "12:00"),
                  self.stay("架空の店", "13:00", "14:00"),
                  self.stay("架空の駅", "14:10", "14:13")]
        events = [{"title": "買い物", "start": "2026-01-05T04:10:00Z",
                   "end": "2026-01-05T04:50:00Z"},
                  {"title": "一日中の予定", "start": "2026-01-05T00:00:00Z",
                   "end": "2026-01-05T14:00:00Z"}]
        phone = [(at("13:10"), at("13:20")), (at("15:00"), at("15:30"))]
        rows = activity.outings(visits, events, self.BOUNDS, {"家"}, phone)
        assert [(row["place"], row["sec"], row["events"], row["phone_sec"]) for row in rows] == [
            ("架空の店", 3600.0, ["買い物", "一日中の予定"], 600.0)]


class Test節:
    def test_外出_PCの括り_スマホ_未分類の順で_割合はPCだけ(self):
        result = run([span("10:00:00", "Toy", proc="game.exe"),
                      span("10:10:00", "部屋", proc="chat.exe"),
                      span("10:20:00", "無題", proc="paint.exe")],
                     rules=[{"match": "^game", "project": "toy-game"},
                            {"match": "^chat", "group": "連絡", "label": "チャット"}])
        outings = [{"place": "架空の店", "start": at("13:00"), "end": at("14:00"),
                    "sec": 3600.0, "activity": None, "events": [], "phone_sec": 0}]
        html = report_projects.section({}, result, project_groups={"遊び": ["toy-*"]},
                                       outings=outings, phone={"動画": 1200.0})
        place = {name: html.index(f"<h3>{name}") for name in ["外出", "遊び", "連絡", "スマホ", "未分類"]}
        assert place["外出"] < min(place["遊び"], place["連絡"])
        assert max(place["遊び"], place["連絡"]) < place["スマホ"] < place["未分類"]
        assert "PC で入力のあった 30分 のうち、プロジェクト 33%・ほかの用途 33%・未分類 33%" in html
        assert "用事の手がかり無し" in html

    def test_古い形の割り付けも読む(self):
        rows = [{"repo": "notes", "active_sec": 600.0, "tasks": [{"task": "整理",
                                                                  "active_sec": 600.0}]}]
        html = report_projects.section({}, rows, present_net_sec=1200.0)
        assert "50%" in html and "notes" in html

    def test_長い外出があってもPCの棒はPCの中で比べる(self):
        result = run([span("10:00:00", "Toy", sec=1200, proc="game.exe")],
                     rules=[{"match": "^game", "project": "toy-game"}])
        outings = [{"place": "架空の店", "start": at("06:00"), "end": at("12:00"),
                    "sec": 6 * 3600.0, "activity": None, "events": [], "phone_sec": 0}]
        html = report_projects.section({}, result, outings=outings)
        assert html.count("width:100.0%") == 2
