"""複数日サイト（日次ページの部品・一覧ページ・書き出し）のテスト。データはすべて架空。"""
from argparse import Namespace
from datetime import datetime

import pytest

from chronofit import cli, cli_site, config, report_extras
from chronofit import paths
from chronofit.model import context as context_model
from chronofit.model import rollup
from chronofit.ui import report_projects, site_index, timeline


def at(d, h, m=0):
    return datetime(2026, 3, d, h, m).astimezone()


def seg(kind, a, b, active=None, reason=None):
    out = {"kind": kind, "start": a, "end": b, "sec": (b - a).total_seconds(),
           "active_sec": (b - a).total_seconds() if active is None else active,
           "proc": "Editor.exe", "title": "memo"}
    if reason:
        out["reason"] = reason
    return out


# --- 1日の流れ -------------------------------------------------------------

def test_区間を渡すと行は区切りの時刻から始まり記録の最後の時間で終わる():
    rows = timeline.hour_rows([seg("present", at(10, 9), at(10, 10, 30))],
                              start=at(10, 5), end=at(11, 5))
    assert [row["hour"].hour for row in rows] == [5, 6, 7, 8, 9, 10]
    assert rows[0]["pieces"] == [] and rows[4]["pieces"]


def test_翌朝の区切りを越える行は作らない():
    rows = timeline.hour_rows([seg("present", at(11, 3), at(11, 6))],
                              start=at(10, 5), end=at(11, 5))
    assert rows[-1]["hour"] == at(11, 4)


def test_重ねる帯はその1時間に入る部分だけ():
    spans = [(at(10, 8, 30), at(10, 10, 15))]
    assert timeline.overlay(spans, at(10, 9)) == [(0.0, 100.0, at(10, 9), at(10, 10))]
    left, width, lo, hi = timeline.overlay(spans, at(10, 10))[0]
    assert (left, round(width, 3), lo, hi) == (0.0, 25.0, at(10, 10), at(10, 10, 15))
    assert timeline.overlay(spans, at(10, 11)) == []


def test_自動プレイの帯と凡例が出る():
    summary = {"segments": [seg("present", at(10, 9), at(10, 10))],
               "auto_spans": [(at(10, 9, 10), at(10, 9, 40))]}
    html = timeline.render(summary, at(10, 5), at(11, 5))
    assert "class='auto'" in html and "自動プレイ" in html


# --- やったこと --------------------------------------------------------------

DONE = {"prs": [{"repo": "school-notes", "number": 3, "title": "第2回のまとめ",
                 "closed": "2026-03-10T03:00:00Z"}],
        "issues": [{"repo": "side-app", "number": 7, "title": "画面を直す",
                    "closed": "2026-03-10T04:00:00Z"}],
        "commits": {"side-app": 2, "lonely-repo": 1}}
WORK = [{"repo": "side-app", "active_sec": 1800.0,
         "tasks": [{"task": "画面", "active_sec": 1800.0, "first": None, "last": None}]},
        {"repo": "school-notes", "active_sec": 600.0, "tasks": []},
        {"repo": "tiny", "active_sec": 30.0, "tasks": []}]
GROUPS = {"大学": ["school-*"]}


def test_括り_プロジェクトの2段に寄せて時間の多い順():
    groups = report_projects.collect(DONE, WORK, GROUPS, "開発")
    assert [g["name"] for g in groups] == ["開発", "大学"]
    dev = groups[0]
    assert [p["repo"] for p in dev["projects"]] == ["side-app", "lonely-repo"]
    assert dev["sec"] == 1800.0
    side = dev["projects"][0]
    assert (len(side["issues"]), side["commits"]) == (1, 2)
    assert groups[1]["projects"][0]["prs"][0]["number"] == 3


def test_1分未満で成果も無いプロジェクトは出さない():
    groups = report_projects.collect(DONE, WORK, GROUPS, "開発")
    repos = {p["repo"] for g in groups for p in g["projects"]}
    assert "tiny" not in repos and "lonely-repo" in repos


def test_括りのパターンは大文字小文字を区別しない():
    assert report_projects.group_of("School-Notes", GROUPS, "開発") == "大学"
    assert report_projects.group_of("other", GROUPS, "開発") == "開発"


def test_節はプロジェクトごとに畳み_時間の無いものはダッシュ():
    html = report_projects.section(DONE, WORK, 3600.0, GROUPS, "開発")
    assert html.count("<details class='proj'>") == 3
    assert "30分" in html and "―" in html
    assert "入力のあった時間のうち 67%" in html


def test_読めなかった源は理由を出す():
    html = report_projects.section({"error": "gh が無い"}, None, 0.0)
    assert "GitHub: 読めなかった（gh が無い）" in html
    assert "作業の割り付け: 読んでいない" in html


def test_成果のタイトルはエスケープする():
    done = {"prs": [{"repo": "x", "number": 1, "title": "<script>", "closed": None}]}
    html = report_projects.section(done, [], 0.0)
    assert "<script>" not in html and "&lt;script&gt;" in html


# --- 一覧ページ --------------------------------------------------------------

def _summary(day):
    return {"segments": [seg("present", at(day, 9), at(day, 10)),
                         seg("present", at(day, 10, 1), at(day, 11))],
            "wall_sec": 7200.0, "net_sec": 5400.0}


def test_近いスパンはつないで1本にする():
    parts = site_index.merged(_summary(10)["segments"])
    assert len(parts) == 1 and parts[0]["end"] == at(10, 11)


def test_一覧は日付から日次ページへ飛び_記録の無い日は数字をダッシュ():
    days = [("2026-03-11", at(11, 5), _summary(11)), ("2026-03-10", at(10, 5), None)]
    html = site_index.render(days, "2026-03-11")
    assert "href='2026-03-11.html'" in html and "今日" in html
    assert "href='2026-03-10.html'" not in html
    assert "<span>2.0</span><span>1.5</span>" in html
    assert "最近の2日" in html and "05:00" in html


def test_記録が1日も無い一覧():
    assert "まだ1日ぶんの記録も無い" in site_index.render([], None)


# --- 書き出し ----------------------------------------------------------------

def _record(a, b):
    sec = (b - a).total_seconds()
    return {"t": "span", "start": a.isoformat(timespec="seconds"),
            "end": b.isoformat(timespec="seconds"), "sec": sec, "active_sec": sec,
            "media_sec": 0.0, "proc": "Editor.exe", "title": "memo"}


def _day_summary(day):
    return rollup.summarize_day([_record(at(day, 9), at(day, 10)),
                                 _record(at(day, 10, 30), at(day, 11))])


@pytest.fixture
def site(tmp_path, monkeypatch):
    known = {"2026-03-10": _day_summary(10), "2026-03-11": _day_summary(11)}
    synced = []
    monkeypatch.setattr(config, "load", lambda: {"day_start": "05:00"})
    monkeypatch.setattr(paths, "report_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_load_summary",
                        lambda day, bounds=None, settings=None: known.get(day))
    monkeypatch.setattr(cli, "_read_records", lambda day, bounds=None: [])
    monkeypatch.setattr(cli, "_board_for", lambda day, settings, tasks=None: ([], None, None))
    monkeypatch.setattr(cli, "sync_issues", lambda quiet=False: synced.append(True))
    monkeypatch.setattr(context_model, "annotate", lambda summary, rules: None)
    monkeypatch.setattr(report_extras, "gather",
                        lambda day, settings, bounds=None, records=None, today=None:
                        {"done": None, "work": None, "agenda": None})
    monkeypatch.setattr(cli_site, "logical_today", lambda settings: "2026-03-11")
    return tmp_path, synced


def args(**kw):
    base = {"date": None, "tasks": None, "rebuild": False, "days": 14, "no_open": True}
    return Namespace(**{**base, **kw})


def test_今日を書くと前の日と一覧も書き直し_Issueを数え直す(site):
    out, synced = site
    assert cli_site.run(args(), lambda path: None) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "2026-03-10.html", "2026-03-11.html", "index.html"]
    assert synced == [True]
    today = (out / "2026-03-11.html").read_text(encoding="utf-8")
    assert "2026-03-10.html" in today and "2026-03-12.html" not in today


def test_過去の日のページには翌日へのリンクがある(site):
    out, synced = site
    assert cli_site.run(args(date="2026-03-10"), lambda path: None) == 0
    assert "2026-03-11.html" in (out / "2026-03-10.html").read_text(encoding="utf-8")
    assert synced == []


def test_生ログの無い日は1を返す(site):
    assert cli_site.run(args(date="2026-03-01"), lambda path: None) == 1


def test_一覧は記録を始める前の日を並べない(site):
    out, _ = site
    cli_site.write_index({}, "2026-03-11", count=5)
    html = (out / "index.html").read_text(encoding="utf-8")
    assert "最近の2日" in html


def test_まとめて書き直す(site, capsys):
    out, _ = site
    assert cli_site.run(args(rebuild=True, days=4), lambda path: None) == 0
    assert "2 日ぶん書き直した" in capsys.readouterr().out
    assert (out / "index.html").exists()


def test_開く指定があればページを開く(site):
    opened = []
    cli_site.run(args(no_open=False), opened.append)
    assert opened and opened[0].name == "2026-03-11.html"


def test_昨日は生活の1日で数える(monkeypatch):
    monkeypatch.setattr(cli_site, "logical_today", lambda settings: "2026-03-11")
    assert cli_site.resolve("yesterday", {}) == "2026-03-10"
    assert cli_site.resolve(None, {}) == "2026-03-11"
    assert cli_site._shift("2026-03-31", 1) == "2026-04-01"


def test_同じ実行の中では同じ日のサマリを2度作らない(site, monkeypatch):
    calls = []
    original = cli._load_summary
    monkeypatch.setattr(cli, "_load_summary",
                        lambda day, bounds=None, settings=None:
                        calls.append(day) or original(day, bounds, settings))
    cli_site.run(args(), lambda path: None)
    assert calls.count("2026-03-11") == 1 and calls.count("2026-03-10") == 1


def test_深夜は生活の今日の予定を取り直す(monkeypatch):
    refreshed = []
    monkeypatch.setattr(report_extras.cli_outing, "refresh_calendar", refreshed.append)
    monkeypatch.setattr(report_extras.cli_outing, "agenda_for_day", lambda day: ([], []))
    report_extras.agenda("2000-01-01", today="2000-01-01")
    report_extras.agenda("2000-01-02", today="2000-01-01")
    assert refreshed == ["2000-01-01"]


def test_ページだけを配り_フォルダの一覧は出さない(tmp_path, monkeypatch):
    import threading
    import urllib.error
    import urllib.request
    (tmp_path / "index.html").write_text("<p>一覧</p>", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    monkeypatch.setattr(paths, "report_dir", lambda: tmp_path)
    server = cli_site.make_server(port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as response:
            assert "一覧" in response.read().decode("utf-8")
            assert response.headers["X-Robots-Tag"] == "noindex"
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + "/sub/")
        assert caught.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
    assert server.server_address[0] == "127.0.0.1"


def test_使用中のポートには重ねて起動しない(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "report_dir", lambda: tmp_path)
    first = cli_site.make_server(port=0)
    try:
        with pytest.raises(OSError):
            cli_site.make_server(port=first.server_address[1])
    finally:
        first.server_close()
