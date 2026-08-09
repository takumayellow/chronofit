"""Clockify バックフィルのテスト。

汚染判定（タイマー止め忘れ）を取り違えると学習曲線の初期値が壊れるので、
境界と除外条件を固定する。
"""
import textwrap

import pytest

from chronofit.sources import clockify


@pytest.fixture
def csv_path(tmp_path):
    path = tmp_path / "entries.csv"
    path.write_text(textwrap.dedent("""\
        group_id,task,start,duration_seconds,duration_hm
        G001,過去問 応用数学A 2024,2026-01-05 09:00,7200,2h 0m
        G002,ブックマーク整理,2026-01-02 10:26,36720,10h 12m
        G003,休憩,no-date,600,0h 10m
        G004,ちょうど4時間,2026-01-06 09:00,14400,4h 0m
        """), encoding="utf-8")
    return path


def test_no_dateの行は落とす(csv_path):
    entries = clockify.read_normalized(csv_path)
    assert [e["task"] for e in entries if e["task"] == "休憩"] == []


def test_4時間以上を汚染として印を付ける(csv_path):
    entries = clockify.read_normalized(csv_path)
    flags = {e["task"]: e["contaminated"] for e in entries}
    assert flags["過去問 応用数学A 2024"] is False
    assert flags["ブックマーク整理"] is True
    # 境界は「以上」。ちょうど4時間は汚染側に倒す（安全寄り）
    assert flags["ちょうど4時間"] is True


def test_汚染分は捨てずに残す(csv_path):
    entries = clockify.read_normalized(csv_path)
    report = clockify.summarize(entries)
    assert report["entries"] == 3
    assert report["clean_entries"] == 1
    assert report["contaminated_entries"] == 2
    assert report["total_hours"] == pytest.approx(2 + 10.2 + 4)
    assert report["clean_hours"] == pytest.approx(2)


def test_時刻順に並べる(csv_path):
    entries = clockify.read_normalized(csv_path)
    assert [e["start"] for e in entries] == sorted(e["start"] for e in entries)


def test_しきい値は変更できる(csv_path):
    entries = clockify.read_normalized(csv_path, contamination_hours=1.0)
    assert all(e["contaminated"] for e in entries)
