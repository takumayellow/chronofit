"""見積もりのテスト。

ここが壊れると、間違った数字が「実測」の顔をして予定に入る。根拠のラベルと
下限の扱いを特に固定する。
"""
import pytest

from chronofit.estimate import curve, db, slack


def instance(subject, kind, index, net_hours):
    return db.make(subject, kind, f"{index}本目", index, net_hours)


# --- 学習曲線 ---------------------------------------------------------------

def test_逓減を実測から学ぶ():
    # 1本目6h → 5本目1.5h という実感どおりの形を入れて、間を補間できるか
    rows = [instance("応用数学B", "過去問", 1, 6.0),
            instance("応用数学B", "過去問", 2, 3.5),
            instance("応用数学B", "過去問", 5, 1.8)]
    result = curve.estimate(rows, "応用数学B", "過去問", 3)
    assert result["basis"] == curve.BASIS_MEASURED
    assert 1.8 < result["hours"] < 3.5
    assert result["samples"] == 3


def test_やるほど速くなる形になっている():
    rows = [instance("物理", "過去問", 1, 6.0), instance("物理", "過去問", 4, 2.0)]
    later = curve.estimate(rows, "物理", "過去問", 6)["hours"]
    earlier = curve.estimate(rows, "物理", "過去問", 2)["hours"]
    assert later < earlier


def test_何本やっても下限を割らない():
    # 素の冪則は n を増やすと 0 へ漸近する。現実には最低限かかる時間がある
    rows = [instance("物理", "過去問", 1, 6.0), instance("物理", "過去問", 4, 2.0)]
    assert curve.estimate(rows, "物理", "過去問", 100)["hours"] >= 2.0 * curve.FLOOR_RATIO


def test_実績1件でも既定の逓減で外挿する():
    rows = [instance("情報理論", "過去問", 1, 4.0)]
    result = curve.estimate(rows, "情報理論", "過去問", 1)
    assert result["hours"] == pytest.approx(4.0)
    assert result["samples"] == 1


def test_科目の実績が無ければ同じ種別を流用し流用と明示する():
    rows = [instance("物理", "過去問", 1, 6.0), instance("物理", "過去問", 3, 2.5)]
    result = curve.estimate(rows, "確率統計2", "過去問", 1)
    assert result["basis"] == curve.BASIS_BORROWED
    assert result["hours"] > 0
    assert "流用" in result["note"]


def test_流用元も無ければ仮値と明示する():
    result = curve.estimate([], "情報理論", "レポート", 1, assumed_hours=3.0)
    assert result["basis"] == curve.BASIS_ASSUMED
    assert result["hours"] == 3.0
    assert "実測なし" in result["note"]


def test_仮値すら渡されなければ数字を出さない():
    # 根拠の無い数字を実測の顔で返さない
    result = curve.estimate([], "情報理論", "レポート", 1)
    assert result["hours"] is None


def test_逆行する実績でも逓減を負にしない():
    # たまたま後半が重い年度に当たっても「やるほど遅くなる」外挿はしない
    rows = [instance("物理", "過去問", 1, 2.0), instance("物理", "過去問", 5, 6.0)]
    model = curve.fit([(r["index"], r["net_hours"]) for r in rows])
    assert model["b"] >= 0.0


def test_次は何本目か():
    rows = [instance("物理", "過去問", 1, 6.0), instance("物理", "過去問", 2, 4.0)]
    assert curve.next_index(rows, "物理", "過去問") == 3
    assert curve.next_index(rows, "物理", "マセマ") == 1


# --- slack 率 ---------------------------------------------------------------

def rollup(date, wall_h, net_h):
    return {"date": date, "wall_sec": wall_h * 3600, "net_sec": net_h * 3600}


def test_平日と休日を分ける():
    rows = [rollup("2026-08-07", 8.0, 4.0),   # 金
            rollup("2026-08-08", 8.0, 3.2)]   # 土
    buckets = slack.ratios(rows)
    assert buckets["平日"]["ratio"] == pytest.approx(0.5)
    assert buckets["休日"]["ratio"] == pytest.approx(0.4)


def test_率は日ごとの平均でなく合計の比():
    # 30分の日と8時間の日を同じ重みにしない
    rows = [rollup("2026-08-07", 1.0, 1.0), rollup("2026-08-06", 9.0, 3.0)]
    assert slack.ratios(rows)["平日"]["ratio"] == pytest.approx(4.0 / 10.0)


def test_ほとんど触っていない日は混ぜない():
    rows = [rollup("2026-08-07", 0.2, 0.2), rollup("2026-08-06", 8.0, 4.0)]
    assert slack.ratios(rows)["平日"]["days"] == 1


def test_出社日は週末より優先する():
    assert slack.day_type(__import__("datetime").date(2026, 8, 8),
                          workdays=("2026-08-08",)) == "出社"


def test_暦時間へ直す():
    assert slack.calendar_hours(3.0, 0.5) == pytest.approx(6.0)


def test_率が無ければ勝手に換算しない():
    assert slack.calendar_hours(3.0, None) is None


# --- DB ---------------------------------------------------------------------

def test_壊れた行を捨てて読み続ける(tmp_path):
    path = tmp_path / "instances.jsonl"
    good = db.make("物理", "過去問", "2024", 1, 6.0)
    path.write_text('{"broken"\n' + __import__("json").dumps(good, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    assert len(db.load(path)) == 1


def test_カバレッジは件数順(tmp_path):
    rows = [instance("物理", "過去問", 1, 6.0), instance("物理", "過去問", 2, 4.0),
            instance("情報理論", "レポート", 1, 3.0)]
    assert db.coverage(rows)[0]["subject"] == "物理"
    assert db.coverage(rows)[0]["max_index"] == 2
