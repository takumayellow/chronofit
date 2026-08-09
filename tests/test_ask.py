"""離席ブロックのラベル付け対話のテスト。

確かめるのは打鍵の少なさそのもの。ここで質問が1つ増えるだけで続かなくなり、
続かなければ実測が貯まらず、習慣を実測から引くという前提ごと崩れる。
"""
from datetime import datetime

from chronofit.ui import ask


def block(start="2026-08-09T20:00:00", sec=7200.0):
    begin = datetime.fromisoformat(start)
    return {"start": begin, "end": begin, "sec": sec, "reason_label": "無入力"}


def keys(monkeypatch, sequence):
    pending = list(sequence)
    monkeypatch.setattr(ask, "_read_key", lambda: pending.pop(0))


def test_中身の決まった選択肢は1打で科目まで決まる(monkeypatch):
    # ピアノは毎回同じ。ここで科目を聞き直すと、2時間の練習を測るのに
    # 毎日2段目の入力が要ることになり、実測が貯まらない
    keys(monkeypatch, ["4"])
    categories = [{"key": "4", "label": "ピアノ", "subject": "ピアノ", "kind": "練習"}]
    answers = ask.ask_blocks([block()], categories, lambda b: None,
                             detail_for=lambda b: {"subject": "聞いてはいけない"})
    label, detail = answers["2026-08-09T20:00:00"]
    assert label == "ピアノ"
    assert (detail["subject"], detail["kind"]) == ("ピアノ", "練習")


def test_種別を省けばラベルを種別にする(monkeypatch):
    keys(monkeypatch, ["4"])
    categories = [{"key": "4", "label": "ピアノ", "subject": "ピアノ"}]
    _, detail = ask.ask_blocks([block()], categories,
                               lambda b: None)["2026-08-09T20:00:00"]
    assert detail["kind"] == "ピアノ"


def test_中身の決まっていない選択肢はこれまで通り聞く(monkeypatch):
    keys(monkeypatch, ["3"])
    categories = [{"key": "3", "label": "オフPC作業", "study": True}]
    _, detail = ask.ask_blocks([block()], categories, lambda b: None,
                               detail_for=lambda b: {"subject": "情報理論",
                                                     "kind": "参考書"})["2026-08-09T20:00:00"]
    assert detail["subject"] == "情報理論"


def test_中身を持たない離席では何も聞かない(monkeypatch):
    keys(monkeypatch, ["2"])
    categories = [{"key": "2", "label": "食事・休憩"}]
    label, detail = ask.ask_blocks([block()], categories, lambda b: None,
                                   detail_for=lambda b: {"subject": "聞いてはいけない"},
                                   )["2026-08-09T20:00:00"]
    assert (label, detail) == ("食事・休憩", None)
