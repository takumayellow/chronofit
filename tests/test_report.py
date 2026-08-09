"""1日の分析画面のテスト。

見た目のテストはしない。ここで守りたいのは3つだけ:

- **測っていない値を描かない**（在席ゼロの日に slack 率 0% と出さない）。
- **空いていた時間を詰めない**（記録の無い時間帯が見た目から消えない）。
- **タイトルをそのまま HTML に流し込まない**（`<script>` を含む題名のページを
  自分のブラウザで開くことになる）。
"""
from datetime import datetime, timedelta

import pytest

from chronofit.ui import report


def span(hour, minute, minutes, kind="present", active_ratio=1.0, **over):
    start = datetime(2026, 8, 9, hour, minute)
    sec = minutes * 60.0
    return {"start": start, "end": start + timedelta(seconds=sec), "sec": sec,
            "kind": kind, "reason": None if kind == "present" else "idle",
            "active_sec": sec * active_ratio, "media_sec": 0.0,
            "proc": "code.exe", "title": "chronofit", **over}


def summary(**over):
    return {"wall_sec": 3600.0, "net_sec": 1800.0, "away_sec": 0.0, "passive_sec": 0.0,
            "slack_ratio": 0.5, "away_blocks": [], "passive_blocks": [],
            "titles": [], "segments": [], **over}


class Testタイムライン:
    def test_時刻の境界でスパンを切る(self):
        rows = report.hour_rows([span(9, 30, 60)])
        assert [f"{row['hour']:%H:%M}" for row in rows] == ["09:00", "10:00"]
        assert rows[0]["pieces"][0]["left"] == 50.0
        assert rows[0]["pieces"][0]["width"] == 50.0

    def test_空いていた時間も行として残す(self):
        """詰めると、間が空いていた事実が見た目から消える。"""
        rows = report.hour_rows([span(9, 0, 10), span(12, 0, 10)])
        assert [f"{row['hour']:%H:%M}" for row in rows] == \
            ["09:00", "10:00", "11:00", "12:00"]
        assert rows[1]["pieces"] == []

    def test_離席は理由ごとに分ける(self):
        assert report.kind_key({"kind": "away", "reason": "sleep"}) == "away:sleep"
        assert report.kind_key({"kind": "passive"}) == "passive"

    def test_入力の割合は高さで持ち位置は持たない(self):
        """スパンの中で「いつ」手が動いたかは測っていない。"""
        piece = report.hour_rows([span(9, 0, 60, active_ratio=0.25)])[0]["pieces"][0]
        assert piece["active_ratio"] == 0.25
        assert (piece["left"], piece["width"]) == (0.0, 100.0)

    def test_日を跨いでも2行に割れる(self):
        rows = report.hour_rows([span(23, 50, 20)])
        assert [f"{row['hour']:%m-%d %H:%M}" for row in rows] == \
            ["08-09 23:00", "08-10 00:00"]
        assert rows[0]["pieces"][0]["left"] == pytest.approx(83.333, abs=0.01)
        assert rows[1]["pieces"][0]["left"] == 0.0

    def test_ゼロ長のスパンは何も描かない(self):
        assert report.hour_rows([span(9, 0, 0)]) == []

    def test_終わりが始まりより前でも落ちない(self):
        broken = span(9, 0, 10)
        broken["end"] = broken["start"] - timedelta(minutes=5)
        assert report.hour_rows([broken]) == []

    def test_秒が壊れていても実時刻から割合を出す(self):
        """`sec` と実時刻が食い違うのは gap レコードで起こる。実時刻のほうを信じる。"""
        zero = span(9, 0, 10, active_ratio=0.5)
        zero["sec"] = 0.0
        piece = report.hour_rows([zero])[0]["pieces"][0]
        assert piece["active_ratio"] == pytest.approx(0.5)
        assert piece["width"] == pytest.approx(16.667, abs=0.01)

    def test_入力割合は1を超えない(self):
        assert report.hour_rows([span(9, 0, 10, active_ratio=1.4)])[0] \
            ["pieces"][0]["active_ratio"] == 1.0

    def test_長すぎる空白は行を詰めて飛ばした時間を明示する(self):
        """数ヶ月ぶりの起動で数千行を吐かない。ただし黙って詰めない。"""
        far = span(9, 0, 10, kind="away")
        far["start"] = datetime(2026, 9, 9, 9, 0)
        far["end"] = far["start"] + timedelta(minutes=10)
        rows = report.hour_rows([span(9, 0, 10), far])
        assert len(rows) == 2
        assert rows[1]["skipped"] > 700
        assert "時間 記録なし" in report.render(summary(segments=[span(9, 0, 10), far]),
                                                "2026-08-09")

    def test_記録が無い日でも落ちない(self):
        assert report.hour_rows([]) == []
        assert "記録が無い" in report.render(summary(), "2026-08-09")


class Test数字の出し方:
    def test_在席ゼロの日は0パーセントと書かない(self):
        html = report.render(summary(wall_sec=0.0, net_sec=0.0, slack_ratio=None),
                             "2026-08-09")
        body = html.split("</style>")[1]      # CSS の 100% を拾わない
        assert "データ無し" in body
        assert "0%" not in body

    def test_主要な数字が出ている(self):
        html = report.render(summary(), "2026-08-09")
        assert "1.0h" in html and "0.5h" in html and "50%" in html


class Testエスケープ:
    def test_タイトルをそのまま流し込まない(self):
        html = report.render(
            summary(titles=[{"proc": "chrome.exe", "title": "<script>alert(1)</script>",
                             "net_sec": 60.0, "passive_sec": 0.0, "wall_sec": 60.0}]),
            "2026-08-09")
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html

    def test_タイムラインの吹き出しもエスケープする(self):
        html = report.render(summary(segments=[span(9, 0, 10, title="a' onerror='x")]),
                             "2026-08-09")
        assert "onerror='x" not in html


class Test離席と進捗:
    def test_未ラベルの離席が分かる(self):
        block = {"start": datetime(2026, 8, 9, 12, 0), "end": datetime(2026, 8, 9, 13, 0),
                 "sec": 3600.0, "reason": "idle", "reason_label": "無入力"}
        html = report.render(summary(away_blocks=[block]), "2026-08-09")
        assert "未ラベル" in html and "12:00-13:00" in html

    def test_ラベルがあればそれを出す(self):
        block = {"start": datetime(2026, 8, 9, 12, 0), "end": datetime(2026, 8, 9, 13, 0),
                 "sec": 3600.0, "reason": "idle", "reason_label": "無入力", "label": "食事"}
        html = report.render(summary(away_blocks=[block]), "2026-08-09")
        assert "食事" in html and "未ラベル" not in html

    def test_推定は推定と明記する(self):
        """実測の顔をさせない。そのまま所要時間DBへ入る値ではない。"""
        block = {"start": datetime(2026, 8, 9, 12, 0), "end": datetime(2026, 8, 9, 13, 0),
                 "sec": 3600.0, "reason": "idle", "reason_label": "無入力",
                 "guess": {"subject": "応用数学B", "kind": "過去問", "via": "直前"}}
        html = report.render(summary(away_blocks=[block]), "2026-08-09")
        assert "推定 応用数学B 過去問（直前）" in html

    def test_見積もれない残りを0時間として出さない(self):
        row = {"subject": "応用数学A", "kind": "レポート", "target": None,
               "priority": "A", "due": None, "goal": 1, "done": 0, "left": 1,
               "spent_hours": 0.0, "remaining_hours": None, "basis": None,
               "days_left": None, "hours_per_day": None, "state": "未着手",
               "overdue": False}
        html = report.render(summary(), "2026-08-09", [row])
        assert "0.0h" not in html.split("<h2>進捗</h2>")[1]

    def test_一覧が空でも進捗の欄は出る(self):
        assert "task add" in report.render(summary(), "2026-08-09", [])
