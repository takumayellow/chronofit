"""生活の1日（朝5時区切り）のテスト。データはすべて架空。"""
from datetime import datetime, time

import pytest

from chronofit.model import day


def at(d, h, m=0):
    return datetime(2026, 3, d, h, m).astimezone()


def rec(a, b, sec=None, active=None):
    sec = (b - a).total_seconds() if sec is None else sec
    return {"t": "span", "start": a.isoformat(timespec="seconds"),
            "end": b.isoformat(timespec="seconds"), "sec": sec,
            "active_sec": sec if active is None else active, "media_sec": 0.0,
            "proc": "Editor.exe", "title": "memo"}


def test_区切りの時刻は設定で変えられて_壊れていれば5時():
    assert day.start_clock({}) == time(5)
    assert day.start_clock({"day_start": "04:30"}) == time(4, 30)
    assert day.start_clock({"day_start": "夜明け"}) == time(5)


def test_1日は5時から翌朝5時():
    start, end = day.bounds("2026-03-10", time(5))
    assert (start, end) == (at(10, 5), at(11, 5))


def test_夜中は前の日に入る():
    assert day.logical_date(at(11, 2), time(5)) == "2026-03-10"
    assert day.logical_date(at(11, 5), time(5)) == "2026-03-11"
    assert day.logical_date(at(10, 23), time(5)) == "2026-03-10"


def test_区切りをまたぐスパンは長さの割合で秒を割る():
    start, end = day.bounds("2026-03-10", time(5))
    records = [rec(at(10, 4), at(10, 6), active=1800.0),   # 半分だけ入る
               rec(at(11, 1), at(11, 2)),                  # 夜中は入る
               rec(at(11, 6), at(11, 7))]                  # 翌日は入らない
    clipped = day.clip_records(records, start, end)
    assert [r["start"][11:16] for r in clipped] == ["05:00", "01:00"]
    assert clipped[0]["sec"] == pytest.approx(3600.0)
    assert clipped[0]["active_sec"] == pytest.approx(900.0)
    assert records[0]["sec"] == 7200.0                     # 元のレコードは書き換えない


def test_長さ0のレコードは区間の中にあるときだけ残す():
    start, end = day.bounds("2026-03-10", time(5))
    inside, outside = rec(at(10, 9), at(10, 9)), rec(at(10, 4), at(10, 4))
    assert day.clip_records([inside, outside], start, end) == [inside]
