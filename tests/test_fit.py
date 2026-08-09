"""需要を容量へ割り付けるところのテスト。

ここが間違うと「間に合う」という嘘の結論が出る。落ちたものを黙って消さないことと、
単位（net / 暦）を混ぜないことを重点的に見る。
"""
from datetime import date

from chronofit.plan import fit

SETTINGS = {
    "kind_modes": {"過去問": "series", "レポート": "oneoff", "ピアノ": "habit"},
    "habits": [{"name": "ピアノ"}],
}

# 実測から出した1日あたりの目減り（kinds.habit_load の出力の形）
HABIT_LOAD = [{"name": "ピアノ", "hours_per_day": 1.0, "basis": "実測", "samples": 7}]

RATIOS = {"平日": {"ratio": 0.5, "days": 10}, "休日": {"ratio": 0.5, "days": 5}}

INSTANCES = [
    {"subject": "応用数学B", "kind": "過去問", "index": 1, "net_hours": 4.0, "mode": "series"},
    {"subject": "応用数学B", "kind": "過去問", "index": 2, "net_hours": 3.0, "mode": "series"},
    {"subject": "応用数学B", "kind": "過去問", "index": 4, "net_hours": 2.0, "mode": "series"},
]


def test_習慣は需要に積まない():
    # 毎日やるものをタスクとして積むと「2h×33日」の巨大タスクが生まれる
    items = fit.expand([{"subject": "ピアノ", "kind": "ピアノ", "count": 30}],
                       INSTANCES, SETTINGS)
    assert items == []


def test_本数を展開すると通し番号が進む():
    items = fit.expand([{"subject": "応用数学B", "kind": "過去問", "count": 3}],
                       INSTANCES, SETTINGS)
    assert [item["index"] for item in items] == [5, 6, 7]


def test_後の本ほど軽く見積もられる():
    items = fit.expand([{"subject": "応用数学B", "kind": "過去問", "count": 3}],
                       INSTANCES, SETTINGS)
    assert items[0]["hours"] > items[-1]["hours"]


def test_容量から習慣の実測ぶんを引く():
    days = [date(2026, 8, 10)]
    entry = fit.capacity(days, {"平日": 8.0}, HABIT_LOAD, RATIOS)[0]
    assert entry["calendar"] == 7.0          # 8h - 実測 1h/日
    assert entry["net"] == 3.5               # slack 率 0.5 で net へ


def test_実測の無い習慣では容量を削らない():
    # 「毎日2時間ピアノ」と宣言しただけで容量を削ると、弾かなかった日の時間が消える
    days = [date(2026, 8, 10)]
    entry = fit.capacity(days, {"平日": 8.0}, None, RATIOS)[0]
    assert entry["calendar"] == 8.0


def test_slack率の無い日は供給に数えない():
    # 数えてしまうと、実際には無い時間を当てにした計画になる
    days = [date(2026, 8, 11)]               # 出社日
    entry = fit.capacity(days, {"平日": 8.0, "出社": 3.0}, HABIT_LOAD, RATIOS,
                         workdays=("2026-08-11",))[0]
    assert entry["day_type"] == "出社"
    assert entry["net"] is None
    week = fit.weekly_capacity([entry])[0]
    assert week["net"] == 0.0 and week["unknown_days"] == 1


def test_入りきらないものを黙って落とさない():
    weeks = [{"week": "2026-08-10", "net": 5.0, "unknown_days": 0}]
    items = [{"subject": "A", "kind": "過去問", "index": 1, "hours": 4.0, "due": None},
             {"subject": "B", "kind": "過去問", "index": 1, "hours": 4.0, "due": None}]
    placed, overflow = fit.allocate(items, weeks)
    assert len(placed[0]["items"]) == 1
    assert overflow[0]["subject"] == "B" and overflow[0]["reason"]


def test_見積もりの無いものは容量に関わらず溢れる():
    weeks = [{"week": "2026-08-10", "net": 100.0, "unknown_days": 0}]
    items = [{"subject": "A", "kind": "過去問", "index": 1, "hours": None, "due": None}]
    placed, overflow = fit.allocate(items, weeks)
    assert placed[0]["items"] == []
    assert "見積もり" in overflow[0]["reason"]


def test_締切の早いものから詰める():
    weeks = [{"week": "2026-08-10", "net": 4.0, "unknown_days": 0},
             {"week": "2026-08-17", "net": 4.0, "unknown_days": 0}]
    items = [{"subject": "遅", "kind": "過去問", "index": 1, "hours": 4.0, "due": "2026-09-01"},
             {"subject": "早", "kind": "過去問", "index": 1, "hours": 4.0, "due": "2026-08-15"}]
    placed, _ = fit.allocate(items, weeks)
    assert placed[0]["items"][0]["subject"] == "早"


def test_週は月曜始まりでまとまる():
    assert fit.week_key(date(2026, 8, 9)) == "2026-08-03"    # 日曜は前の週
    assert fit.week_key(date(2026, 8, 10)) == "2026-08-10"


def test_通しで計画にすると需要と供給が並ぶ():
    plan = fit.make([{"subject": "応用数学B", "kind": "過去問", "count": 2}],
                    INSTANCES, {"平日": 8.0}, date(2026, 8, 16), SETTINGS, RATIOS,
                    today=date(2026, 8, 10))
    assert plan["demand"] > 0 and plan["supply"] > 0
    assert len(plan["weeks"]) == 1
