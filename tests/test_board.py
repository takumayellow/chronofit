"""進捗の数え方のテスト。

ここが間違うと、**終わったものをもう一度計画に載せる**か、逆に終わっていないものを
終わったことにする。どちらも「予定を立て直す」という board の唯一の用途を壊す。
"""
from datetime import date

import pytest

from chronofit.plan import board, tasks as tasks_store

SETTINGS = {
    "kind_modes": {"過去問": "series", "レポート": "oneoff", "ピアノ": "habit"},
    "habits": [{"name": "ピアノ"}],
}

INSTANCES = [
    {"subject": "確率統計2", "kind": "過去問", "target": "2024期末", "index": 1,
     "net_hours": 4.0, "mode": "series"},
    {"subject": "確率統計2", "kind": "過去問", "target": "2023期末", "index": 2,
     "net_hours": 3.0, "mode": "series"},
]

TODAY = date(2026, 8, 9)


def task(**over):
    return {"subject": "確率統計2", "kind": "過去問", "count": 5, **over}


class Test進捗の数え方:
    def test_終わった本数は所要時間DBから数える(self):
        row = board.progress(task(), INSTANCES, SETTINGS, TODAY)
        assert (row["done"], row["goal"], row["left"]) == (2, 5, 3)

    def test_実測が無ければ未着手(self):
        row = board.progress(task(subject="応用数学A"), INSTANCES, SETTINGS, TODAY)
        assert row["state"] == board.NOT_STARTED
        assert row["done"] == 0

    def test_本数に達したら完了(self):
        row = board.progress(task(count=2), INSTANCES, SETTINGS, TODAY)
        assert row["state"] == board.DONE
        assert row["left"] == 0
        assert row["remaining_hours"] == 0.0

    def test_目標を超えて終わっていても残りは負にならない(self):
        assert board.progress(task(count=1), INSTANCES, SETTINGS, TODAY)["left"] == 0

    def test_投入済み時間は実測の合計(self):
        assert board.progress(task(), INSTANCES, SETTINGS, TODAY)["spent_hours"] == 7.0

    def test_target_を指定したタスクはその対象だけ数える(self):
        row = board.progress(task(target="2024期末"), INSTANCES, SETTINGS, TODAY)
        assert row["done"] == 1


class Test残り時間:
    def test_残りは1本ずつ見積もって足す(self):
        """学習曲線があるので「1本の見積もり×本数」にはならない。"""
        row = board.progress(task(), INSTANCES, SETTINGS, TODAY)
        assert row["remaining_hours"] is not None
        # 3・4・5本目。逓減があるので 1本目の 4.0h × 3 より小さい
        assert 0 < row["remaining_hours"] < 12.0

    def test_見積もれない本があれば合計を出さない(self):
        """足せるぶんだけ足すと、見積もれない本が 0 時間として計画に混ざる。"""
        row = board.progress(task(subject="応用数学A", kind="レポート"),
                             INSTANCES, SETTINGS, TODAY)
        assert row["remaining_hours"] is None

    def test_仮値があれば見積もれる(self):
        row = board.progress(task(subject="応用数学A", kind="レポート", count=2,
                                  assumed_hours=3.0), INSTANCES, SETTINGS, TODAY)
        assert row["remaining_hours"] == pytest.approx(6.0)


class Test締切:
    def test_締切までの日数と1日あたりの必要量を出す(self):
        row = board.progress(task(due="2026-08-19"), INSTANCES, SETTINGS, TODAY)
        assert row["days_left"] == 10
        assert row["hours_per_day"] == pytest.approx(row["remaining_hours"] / 10, abs=0.01)

    def test_締切が無ければ日あたりも出さない(self):
        row = board.progress(task(), INSTANCES, SETTINGS, TODAY)
        assert row["days_left"] is None and row["hours_per_day"] is None

    def test_締切を過ぎて残っていれば超過(self):
        assert board.progress(task(due="2026-08-01"), INSTANCES, SETTINGS, TODAY)["overdue"]

    def test_終わっていれば締切を過ぎていても超過にしない(self):
        row = board.progress(task(count=2, due="2026-08-01"), INSTANCES, SETTINGS, TODAY)
        assert row["overdue"] is False


class Test壊れた入力:
    def test_読めない締切は締切なしとして扱う(self):
        """毎晩の自動実行を、書き間違えた1行で止めない。"""
        row = board.progress(task(due="9/14/2026"), INSTANCES, SETTINGS, TODAY)
        assert row["days_left"] is None and row["overdue"] is False

    def test_count_0_は1本に化けない(self):
        assert board.goal_count({"count": 0}) == 0
        assert board.goal_count({}) == 1

    def test_count_0_のタスクは計画へ渡さない(self):
        assert board.remaining_tasks([task(subject="応用数学A", count=0)],
                                     INSTANCES, SETTINGS) == []


class Test一覧:
    def test_習慣は進捗を持たない(self):
        """時間が入力であって出力なので、「何本終わった」が定義できない。"""
        rows = board.rows([task(), {"subject": "ピアノ", "kind": "ピアノ", "count": 30}],
                          INSTANCES, SETTINGS, TODAY)
        assert [row["subject"] for row in rows] == ["確率統計2"]

    def test_科目か種別が欠けた行は捨てる(self):
        assert board.rows([{"subject": "確率統計2"}], INSTANCES, SETTINGS, TODAY) == []

    def test_遅れているものが先頭に来る(self):
        rows = board.rows([task(due="2026-12-01"), task(subject="応用数学A",
                                                        due="2026-08-01")],
                          INSTANCES, SETTINGS, TODAY)
        assert board.order(rows)[0]["subject"] == "応用数学A"

    def test_完了は最後に回す(self):
        rows = board.rows([task(count=2), task(subject="応用数学A")],
                          INSTANCES, SETTINGS, TODAY)
        assert board.order(rows)[-1]["state"] == board.DONE

    def test_合計は見積もれない件数を隠さない(self):
        summary = board.summarize(board.rows(
            [task(), task(subject="応用数学A", kind="レポート")],
            INSTANCES, SETTINGS, TODAY))
        assert summary["unestimated"] == 1
        assert summary["units_left"] == 3 + 5


class Test計画へ渡す形:
    def test_残り本数に置き換える(self):
        """一覧をそのまま渡すと、終わったぶんまでもう一度計画に載る。"""
        remaining = board.remaining_tasks([task()], INSTANCES, SETTINGS)
        assert remaining[0]["count"] == 3

    def test_終わったタスクは渡さない(self):
        assert board.remaining_tasks([task(count=2)], INSTANCES, SETTINGS) == []

    def test_習慣は渡さない(self):
        assert board.remaining_tasks([{"subject": "ピアノ", "kind": "ピアノ", "count": 30}],
                                     INSTANCES, SETTINGS) == []

    def test_他の項目は保つ(self):
        remaining = board.remaining_tasks([task(due="2026-09-01", priority="S")],
                                          INSTANCES, SETTINGS)
        assert remaining[0]["due"] == "2026-09-01" and remaining[0]["priority"] == "S"


class Test一覧の読み書き:
    def test_保存して読み直せる(self, tmp_path):
        path = tmp_path / "tasks.json"
        tasks_store.save(path, [task()])
        assert tasks_store.load(path)[0]["subject"] == "確率統計2"

    def test_無ければ空(self, tmp_path):
        assert tasks_store.load(tmp_path / "none.json") == []

    def test_同じ鍵は増やさず差し替える(self):
        updated = tasks_store.upsert([task()], task(count=8))
        assert len(updated) == 1 and updated[0]["count"] == 8

    def test_target_が違えば別のタスク(self):
        assert len(tasks_store.upsert([task(target="A")], task(target="B"))) == 2

    def test_消した件数を返す(self):
        removed, kept = tasks_store.remove([task(), task(subject="応用数学A")], "確率統計2")
        assert removed == 1 and [t["subject"] for t in kept] == ["応用数学A"]

    def test_無いものを消しても壊れない(self):
        removed, kept = tasks_store.remove([task()], "存在しない科目")
        assert removed == 0 and len(kept) == 1

    def test_科目か種別が欠けた行は読まない(self, tmp_path):
        path = tmp_path / "tasks.json"
        tasks_store.save(path, [task(), {"subject": "科目だけ"}])
        assert len(tasks_store.load(path)) == 1
