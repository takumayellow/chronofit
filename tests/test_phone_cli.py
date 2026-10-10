"""スマホの使用状況を 1日の要約・rollup・report へ通すテスト。データはすべて架空。"""
import json
from datetime import datetime

from chronofit import cli, cli_phone
from chronofit.sources import phone
from chronofit.ui import report

from test_phone import DUMP


def at(hour, minute=0, day=27):
    return datetime(2026, 9, day, hour, minute).astimezone()


def span(start, end, active):
    sec = (end - start).total_seconds()
    return {"t": "span", "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"), "sec": sec,
            "active_sec": sec if active else 0.0, "media_sec": 0.0,
            "proc": "Editor.exe", "title": "notes.txt"}


def _setup(tmp_path, monkeypatch, pulled=True):
    home = tmp_path / "home"
    monkeypatch.setenv("CHRONOFIT_HOME", str(home))
    monkeypatch.setenv("CHRONOFIT_CONFIG", str(tmp_path / "config.json"))
    (tmp_path / "config.json").write_text(json.dumps(
        {"phone": {"categories": {"com.example.chat": "連絡"}}}), encoding="utf-8")
    raw = home / "raw"
    raw.mkdir(parents=True)
    days = {"2026-09-26": [span(at(21, day=26), at(22, 30, day=26), True),
                           span(at(22, 30, day=26), at(0), False)],
            "2026-09-27": [span(at(0), at(9), False), span(at(9), at(12), True)]}
    for day, spans in days.items():
        (raw / f"{day}.jsonl").write_text(
            "".join(json.dumps(s) + "\n" for s in spans), encoding="utf-8")
    root = home / "phone"
    phone.append(phone.parse_usagestats(DUMP), root)
    if pulled:
        phone.record_pull(root, at(12))
    return home


def test_rollupに睡眠とスマホとカテゴリだけが出る(tmp_path, monkeypatch, capsys):
    home = _setup(tmp_path, monkeypatch)
    assert cli.main(["rollup", "--date", "2026-09-27"]) == 0
    out = capsys.readouterr().out
    assert "睡眠 7.0h" in out and "起床 07:00" in out
    written = (home / "rollup" / "2026-09-27.json").read_text(encoding="utf-8")
    shared = json.loads(written)
    # 前の夜 23:50〜7:00 のうち、この日に入るのは 0:00〜7:00
    assert shared["sleep_sec"] == 7 * 3600
    assert shared["phone_sec"] == 15 * 60 + 2
    assert shared["phone_categories"] == {"連絡": 900.0}
    assert "phone_apps" not in shared   # アプリ名・題は共有できる形に入れない
    assert shared["wake"].startswith("2026-09-27T07:00:00")
    assert shared["away_blocks"][0]["sleep_sec"] == 7 * 3600
    assert "com.example" not in written            # アプリ名は共有の粒度に出さない


def test_記録が夜を覆っていなければ睡眠は出さずスマホだけ出す(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch, pulled=False)
    summary = cli._load_summary("2026-09-27")
    assert summary["sleep_sec"] == 0.0
    assert summary["wake"] is None
    assert summary["phone_sec"] == 15 * 60 + 2


def test_スマホのイベントが無い日は何も足さない(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    summary = cli._load_summary("2026-09-26")
    assert "sleep_sec" in summary                  # 26日の夜のイベントはある
    (tmp_path / "home" / "phone" / "events" / "2026-09-26.jsonl").unlink()
    (tmp_path / "home" / "phone" / "events" / "2026-09-27.jsonl").unlink()
    assert "sleep_sec" not in cli._load_summary("2026-09-26")


def test_reportに睡眠とカテゴリの表が出る(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    summary = cli._load_summary("2026-09-27")
    page = report.render(summary, "2026-09-27")
    assert "スマホの使い道" in page and "離席(睡眠除く)" in page
    assert "就寝 → 07:00 起床" in page and "アプリの種類ごとに分けた" in page
    assert "連絡" in page and "com.example" not in page
    assert "<th>睡眠</th><th>スマホ</th>" in page


def test_pullは件数だけをログに残す(tmp_path, monkeypatch):
    home = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(phone, "fetch", lambda serials, adb="adb", **_: ("host.example:5555", DUMP))
    assert cli.main(["phone", "pull"]) == 0
    log = (home / "phone" / "pull.log").read_text(encoding="utf-8")
    assert "ok events=13 added=0" in log
    assert "host.example" not in log and "com.example" not in log


def test_pullはphone_linkのしたことだけを残す(tmp_path, monkeypatch):
    home = _setup(tmp_path, monkeypatch)

    def reopened(serials, adb="adb", notes=None):
        notes += ["reopened", "待受が閉じている", "wireless_debugging_on", "reopened"]
        return "host.example:5555", DUMP

    monkeypatch.setattr(phone, "fetch", reopened)
    assert cli.main(["phone", "pull"]) == 0
    log = (home / "phone" / "pull.log").read_text(encoding="utf-8")
    assert log.rstrip().endswith("purged_days=0 reopened wireless_debugging_on")
    assert "host.example" not in log


def test_pullが失敗したら1を返してログに残す(tmp_path, monkeypatch):
    home = _setup(tmp_path, monkeypatch)

    def broken(serials, adb="adb", **_):
        raise RuntimeError("usagestats を読める端末が無い")

    monkeypatch.setattr(phone, "fetch", broken)
    assert cli.main(["phone", "pull"]) == 1
    assert "failed" in (home / "phone" / "pull.log").read_text(encoding="utf-8")


def test_appsはパッケージ名を画面にだけ出す(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    assert cli_phone.show_apps(days=2, today=at(0).date()) == 0
    out = capsys.readouterr().out
    assert "com.example.video" in out and "(未分類)" in out


def _logical(date):
    from datetime import time
    from chronofit.model import day
    return day.bounds(date, time(5))


def test_生活の1日では夜中の作業は前の日に入り_睡眠はその朝に終わった1晩を数える(
        tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    evening = cli._load_summary("2026-09-26", bounds=_logical("2026-09-26"))
    assert evening["net_sec"] == 1.5 * 3600            # 21:00〜22:30 の入力
    assert evening["phone_sec"] == 40 * 60             # 23:10〜23:50 は前の日の夜
    morning = cli._load_summary("2026-09-27", bounds=_logical("2026-09-27"))
    assert morning["net_sec"] == 3 * 3600              # 9:00〜12:00。0〜5時は入らない
    assert morning["sleep_sec"] == 7 * 3600 + 600      # 23:50〜7:00 を割らずに数える
    assert morning["sleep_spans"][0] == (at(23, 50, day=26), at(7))


def test_自動プレイの時間は別に持ち_スマホの時間に入れない(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(json.dumps(
        {"phone": {"categories": {"com.example.chat": "連絡"},
                   "automated": ["com.example.video"]}}), encoding="utf-8")
    summary = cli._load_summary("2026-09-26", bounds=_logical("2026-09-26"))
    assert summary["phone_auto_sec"] == 30 * 60 - 3
    assert summary["phone_sec"] == 10 * 60 + 3
    assert "com.example.video" not in str(summary["phone_categories"])


def test_点けたまま置くアプリは使用に入れず_自動プレイにも出さない(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    (tmp_path / "config.json").write_text(json.dumps(
        {"phone": {"categories": {"com.example.chat": "連絡"},
                   "ignored": "com.example.video"}}), encoding="utf-8")   # 1つなら文字列でも
    summary = cli._load_summary("2026-09-26", bounds=_logical("2026-09-26"))
    assert summary["phone_auto_sec"] == 0
    assert summary["phone_sec"] == 10 * 60 + 3
    assert "com.example.video" not in str(summary["phone_categories"])
