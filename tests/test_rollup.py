"""ロールアップのテスト。

ここの取り違えは見積もり全体を狂わせる。特に:
- 無入力を在席に数えてしまうと slack 率が実態より良く出て、予定が入らなくなる
- 記録の隙間を詰めてしまうと「PCが落ちていた時間」が消えて合計が実時間と合わなくなる
"""
from datetime import datetime, timedelta, timezone

import pytest

from chronofit.model import rollup

JST = timezone(timedelta(hours=9))


def _at(hour, minute, second=0):
    return datetime(2026, 8, 9, hour, minute, second, tzinfo=JST).isoformat(timespec="seconds")


def span(start, end, sec, active_sec, proc="Code.exe", title="main.py"):
    return {"t": "span", "start": start, "end": end, "sec": sec,
            "active_sec": active_sec, "proc": proc, "title": title}


def gap(start, end, sec):
    return {"t": "gap", "start": start, "end": end, "sec": sec, "reason": "clock_jump"}


def test_入力のない在席はwallにもnetにも乗せない():
    # 前景は PDF のまま、手は一切動いていない = 席にいたとは限らない
    records = [span(_at(9, 0), _at(9, 30), 1800, 0.0, "SumatraPDF.exe", "応用数学A_2024.pdf")]
    summary = rollup.summarize_day(records)
    assert summary["wall_sec"] == 0
    assert summary["net_sec"] == 0
    assert summary["away_sec"] == 1800


def test_slack率は在席に対する入力ありの割合():
    records = [span(_at(9, 0), _at(10, 0), 3600, 1800)]
    summary = rollup.summarize_day(records)
    assert summary["slack_ratio"] == pytest.approx(0.5)


def test_データが無い日のslack率はNone():
    # 0.0 だと「一切集中していない」と読めてしまう。データ無しと区別する
    assert rollup.summarize_day([])["slack_ratio"] is None


def test_記録の隙間はno_dataとして残す():
    records = [span(_at(9, 0), _at(9, 15), 900, 900),
               span(_at(11, 0), _at(11, 15), 900, 900)]
    segments = rollup.to_segments(records)
    holes = [s for s in segments if s["reason"] == "no_data"]
    assert len(holes) == 1
    assert holes[0]["sec"] == pytest.approx(105 * 60)


def test_サンプリングの揺らぎ程度の隙間は埋めない():
    records = [span(_at(9, 0), _at(9, 15), 900, 900),
               span(_at(9, 15, 15), _at(9, 30), 885, 885)]
    assert [s for s in rollup.to_segments(records) if s["reason"] == "no_data"] == []


def test_短い離席はラベルを聞かない():
    records = [span(_at(9, 0), _at(9, 15), 900, 900),
               span(_at(9, 15), _at(9, 20), 300, 0.0),   # 5分の無入力
               span(_at(9, 20), _at(9, 40), 1200, 1200)]
    assert rollup.summarize_day(records)["away_blocks"] == []


def test_割れた離席をひとつに束ねる():
    # ロック → スリープ → 復帰直後の無入力 は人間にとって1回の外出
    records = [span(_at(9, 0), _at(9, 15), 900, 900),
               span(_at(9, 15), _at(9, 20), 300, 0.0, rollup.LOCKED_PROC, ""),
               gap(_at(9, 20), _at(10, 10), 3000),
               span(_at(10, 10), _at(10, 15), 300, 0.0),
               span(_at(10, 15), _at(10, 45), 1800, 1800)]
    blocks = rollup.summarize_day(records)["away_blocks"]
    assert len(blocks) == 1
    assert blocks[0]["sec"] == pytest.approx(3600)
    # 一番長く占めたスリープを代表の理由にする
    assert blocks[0]["reason"] == "sleep"


def test_タイトル別の集計はnet順():
    records = [span(_at(9, 0), _at(9, 30), 1800, 600, "Code.exe", "a.py"),
               span(_at(9, 30), _at(10, 0), 1800, 1500, "chrome.exe", "調べもの")]
    titles = rollup.summarize_day(records)["titles"]
    assert [t["title"] for t in titles] == ["調べもの", "a.py"]


def test_ラベルは開始時刻で突き合わせる():
    records = [span(_at(9, 0), _at(9, 15), 900, 900),
               gap(_at(9, 15), _at(10, 0), 2700),
               span(_at(10, 0), _at(10, 30), 1800, 1800)]
    summary = rollup.summarize_day(records)
    labeled = rollup.merge_labels(summary, {_at(9, 15): "昼食"})
    assert labeled["away_blocks"][0]["label"] == "昼食"


def test_ラベルの無いブロックはNoneのまま():
    records = [span(_at(9, 0), _at(9, 15), 900, 900),
               gap(_at(9, 15), _at(10, 0), 2700),
               span(_at(10, 0), _at(10, 30), 1800, 1800)]
    summary = rollup.merge_labels(rollup.summarize_day(records), {})
    assert summary["away_blocks"][0]["label"] is None
