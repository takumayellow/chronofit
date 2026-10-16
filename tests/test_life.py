"""生活時間の実測から1日の持ち時間を出すところのテスト。データはすべて架空。"""
import argparse
import json
from datetime import date

from chronofit import cli
from chronofit.estimate import life

H = 3600.0


def row(day, sleep, away, nap=0.0, **extra):
    return {"date": day, "sleep_sec": sleep * H, "away_awake_sec": away * H,
            "nap_sec": nap * H, "wake": f"{day}T07:30:00+09:00",
            "bed": f"{day}T23:30:00+09:00", **extra}


# 2026-09-21〜25 は月〜金、26・27 は土日
WEEK = [row("2026-09-21", 7.0, 9.0), row("2026-09-22", 8.0, 9.0, nap=1.0),
        row("2026-09-23", 6.0, 10.0), row("2026-09-26", 9.0, 4.0)]


def test_目標の睡眠を引けば持ち時間が出る():
    assert life.target_hours({"bed": "23:30", "wake": "07:30"}) == 8.0
    assert life.target_hours({"bed": "01:00", "wake": "08:30"}) == 7.5
    assert life.target_hours({"bed": "23:30"}) is None
    assert life.target_hours({"bed": "x", "wake": "07:30"}) is None
    assert life.target_hours(None) is None


def test_休む時間は実測と目標の長いほう():
    result = life.hours_by_type(WEEK, target=8.0)
    weekday = result["平日"]
    # 休む時間: max(7, 8)=8, max(8+1, 8)=9, max(6, 8)=8 → 平均 25/3
    assert weekday["days"] == 3
    assert abs(weekday["rest"] - 25 / 3) < 1e-9
    assert abs(weekday["away"] - 28 / 3) < 1e-9
    assert abs(weekday["hours"] - (24 - 25 / 3 - 28 / 3)) < 1e-9
    assert abs(weekday["nap"] - 1 / 3) < 1e-9
    assert "休日" not in result   # 1日しか無い日タイプは実測を使わない


def test_目標が無ければ実測のまま引く():
    weekday = life.hours_by_type(WEEK)["平日"]
    assert abs(weekday["rest"] - 22 / 3) < 1e-9


def test_PCの外の習慣は離席から除き_二重に引かない():
    weekday = life.hours_by_type(
        WEEK, habit_hours={"2026-09-21": 1.5, "2026-09-22": 20.0})["平日"]
    # 9/22 は離席 9h より長い習慣を書いても、離席を超えては除かない
    assert abs(weekday["away"] - (7.5 + 0.0 + 10.0) / 3) < 1e-9
    assert abs(weekday["habit"] - (1.5 + 9.0) / 3) < 1e-9


def test_夜が取れていない日と窓の外と壊れた行は使わない():
    rows = WEEK + [row("2026-09-24", 1.0, 20.0, wake=None), row("2026-09-24", 4.0, 13.0, bed=None),
                   row("2026-09-25", 7.0, 9.0),
                   {"date": "2026-09-25"}, {"date": "bad", "sleep_sec": H, "away_awake_sec": H},
                   {"date": "2026-09-24", "sleep_sec": "7", "away_awake_sec": H}]
    assert life.hours_by_type(rows)["平日"]["days"] == 4
    assert life.hours_by_type(rows, since="2026-09-22", until="2026-09-25")["平日"]["days"] == 3


def test_二度寝の秒が壊れていれば二度寝なしとして読む():
    rows = [dict(entry, nap_sec="5") for entry in WEEK]
    assert life.hours_by_type(rows)["平日"]["nap"] == 0.0


def test_設定の持ち時間は数値だけ使う(tmp_path, monkeypatch):
    flags = argparse.Namespace(hours=6.0, workday_hours=None, window=14)
    for index, broken in enumerate(("8", ["平日"], {"平日": "8", "休日": True})):
        folder = tmp_path / str(index)
        folder.mkdir()
        settings = _settings(folder, monkeypatch, {"capacity_hours": broken}, [])
        assert cli._capacity(settings, flags, today=date(2026, 9, 28))[0]["平日"] == 6.0


def test_出社日は日タイプを分ける():
    result = life.hours_by_type(WEEK, workdays=("2026-09-21",), min_days=1)
    assert result["出社"]["days"] == 1 and result["平日"]["days"] == 2


def test_内訳の行に出どころを出す():
    weekday = life.hours_by_type(WEEK, target=8.0)["平日"]
    line = life.format_entry("平日", weekday, 8.0)
    assert line.startswith("持ち時間 平日 6.3h = 24h − 休む 8.3h")
    assert "二度寝 0.3h" in line and "目標 8h" in line and "実測 3日の平均" in line


def _settings(tmp_path, monkeypatch, settings, rollups):
    home = tmp_path / "home"
    monkeypatch.setenv("CHRONOFIT_HOME", str(home))
    monkeypatch.setenv("CHRONOFIT_CONFIG", str(tmp_path / "config.json"))
    (tmp_path / "config.json").write_text(json.dumps(settings), encoding="utf-8")
    folder = home / "rollup"
    folder.mkdir(parents=True)
    for entry in rollups:
        (folder / f"{entry['date']}.json").write_text(json.dumps(entry), encoding="utf-8")
    return settings


def test_持ち時間は設定_引数_実測_既定の順に効く(tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch,
                         {"sleep_target": {"bed": "23:30", "wake": "07:30"},
                          "capacity_hours": {"休日": 5.0}}, WEEK)
    today = date(2026, 9, 28)
    flags = argparse.Namespace(hours=None, workday_hours=None, window=14)
    hours, notes = cli._capacity(settings, flags, today=today)
    assert abs(hours["平日"] - (24 - 25 / 3 - 28 / 3)) < 1e-9   # 実測
    assert hours["休日"] == 5.0                                   # 設定
    assert hours["出社"] == 3.0                                   # 実測が無いので既定
    assert len(notes) == 1 and notes[0].startswith("持ち時間 平日")   # 出社日は使っていない
    hours, notes = cli._capacity(settings, argparse.Namespace(hours=6.0, workday_hours=None,
                                                              window=14), today=today)
    assert hours["平日"] == 6.0 and hours["休日"] == 5.0 and notes == []


def test_今日と窓より前の日は実測に入れない(tmp_path, monkeypatch):
    settings = _settings(tmp_path, monkeypatch, {}, WEEK)
    flags = argparse.Namespace(hours=None, workday_hours=None, window=14)
    # 9/23 当日は途中なので 9/21・9/22 の2日だけ → 日数が足りず既定
    hours, notes = cli._capacity(settings, flags, today=date(2026, 9, 23))
    assert hours["平日"] == 8.0
    assert "持ち時間 平日 8h（既定。生活時間の実測が3日に満たない）" in notes
    # 窓 2 日（9/22・9/23）でも足りない
    narrow = argparse.Namespace(hours=None, workday_hours=None, window=2)
    assert cli._capacity(settings, narrow, today=date(2026, 9, 24))[0]["平日"] == 8.0
