"""種別ごとの見積もり方のテスト。

固定したいのは3点: 一点物に学習曲線を当てないこと、習慣を需要に積まないこと、
そして習慣や一点物の実測が学習曲線を汚さないこと。
"""
import pytest

from chronofit.estimate import curve, db, kinds


def instance(subject, kind, index, net_hours, mode="series"):
    return db.make(subject, kind, f"{index}", index, net_hours, mode=mode)


# --- モードの判定 -------------------------------------------------------------

def test_設定に無い種別は繰り返しものとみなす():
    assert kinds.mode_for("過去問", {}) == kinds.SERIES


def test_設定でモードを差し替えられる():
    settings = {"kind_modes": {"レポート": "oneoff", "ピアノ": "habit"}}
    assert kinds.mode_for("レポート", settings) == kinds.ONEOFF
    assert kinds.mode_for("ピアノ", settings) == kinds.HABIT


def test_知らないモード名は既定へ落とす():
    assert kinds.mode_for("レポート", {"kind_modes": {"レポート": "typo"}}) == kinds.SERIES


# --- 一点物 -------------------------------------------------------------------

def test_一点物は中央値とp80で出す():
    rows = [instance("情報理論", "レポート", 1, 2.0),
            instance("情報理論", "レポート", 2, 3.0),
            instance("情報理論", "レポート", 3, 9.0)]
    result = kinds.estimate_oneoff(rows, "情報理論", "レポート")
    assert result["median"] == pytest.approx(3.0)
    assert result["hours"] > result["median"]      # 予定には余裕のある側を使う
    assert result["basis"] == curve.BASIS_MEASURED


def test_一点物に逓減を当てない():
    # 3件目が1件目より重くても「やるほど速くなる」補正を掛けない
    rows = [instance("情報理論", "レポート", 1, 2.0),
            instance("情報理論", "レポート", 2, 6.0)]
    assert kinds.estimate_oneoff(rows, "情報理論", "レポート")["b"] is None


def test_一点物も科目が無ければ同じ種別を流用する():
    rows = [instance("物理", "レポート", 1, 4.0), instance("物理", "レポート", 2, 6.0)]
    result = kinds.estimate_oneoff(rows, "情報理論", "レポート")
    assert result["basis"] == curve.BASIS_BORROWED
    assert "実測なし" in result["note"]


def test_一点物も根拠が無ければ数字を出さない():
    assert kinds.estimate_oneoff([], "情報理論", "レポート")["hours"] is None


def test_分位点は線形補間():
    assert kinds.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)


# --- 習慣 ---------------------------------------------------------------------

def test_習慣は容量から引く():
    settings = {"habits": [{"name": "ピアノ", "hours_per_day": 2.0}]}
    assert kinds.available_hours(10.0, settings) == pytest.approx(8.0)


def test_習慣が容量を超えても負にしない():
    settings = {"habits": [{"name": "ピアノ", "hours_per_day": 12.0}]}
    assert kinds.available_hours(10.0, settings) == 0.0


def test_時間の入っていない習慣は数えない():
    settings = {"habits": [{"name": "ピアノ"}, {"hours_per_day": 1.0}]}
    assert kinds.habit_hours_per_day(settings) == 0.0


# --- 曲線の汚染防止 -----------------------------------------------------------

def test_一点物の実績が学習曲線に混ざらない():
    rows = [instance("物理", "過去問", 1, 6.0),
            instance("物理", "過去問", 2, 4.0),
            instance("物理", "過去問", 3, 40.0, mode="oneoff")]   # 別物が同じ種別名で入った
    assert curve.estimate(rows, "物理", "過去問", 3)["samples"] == 2


def test_古い行はモード欠落でも繰り返しものとして読む():
    row = db.make("物理", "過去問", "2024", 1, 6.0)
    del row["mode"]
    assert curve.estimate([row], "物理", "過去問", 1)["samples"] == 1
