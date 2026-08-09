"""離席の中身を両隣から当てるところのテスト。

ここが「パソコンを触っていない時間をどう測るか」の答えの中心なので、
外れ方（隣が遠い・規則が壊れている）を特に固定する。
"""
from datetime import datetime, timedelta

from chronofit.model import context

RULES = [
    {"match": r"応用数学B.*\.pdf", "subject": "応用数学B", "kind": "過去問"},
    {"match": r"情報理論", "subject": "情報理論", "kind": "参考書"},
]

BASE = datetime(2026, 8, 9, 13, 0)


def present(offset_min, minutes, title):
    start = BASE + timedelta(minutes=offset_min)
    return {"kind": "present", "start": start,
            "end": start + timedelta(minutes=minutes), "title": title}


def away(offset_min, minutes):
    start = BASE + timedelta(minutes=offset_min)
    return {"kind": "away", "start": start,
            "end": start + timedelta(minutes=minutes),
            "sec": minutes * 60.0}


def test_直前に開いていたものを離席の中身とみなす():
    block = away(10, 90)
    segments = [present(0, 10, "応用数学B_2024期末.pdf - SumatraPDF"), block]
    assert context.guess(block, segments, RULES) == {
        "subject": "応用数学B", "kind": "過去問", "via": "直前",
        "title": "応用数学B_2024期末.pdf - SumatraPDF"}


def test_直前に手掛かりが無ければ直後を見る():
    # 紙で読み始めてから答え合わせで PC を開く順も同じくらい起きる
    block = away(10, 90)
    segments = [present(0, 10, "YouTube - Chrome"), block,
                present(100, 20, "情報理論 演習 - Chrome")]
    result = context.guess(block, segments, RULES)
    assert result["subject"] == "情報理論"
    assert result["via"] == "直後"


def test_遠すぎる前景はその離席の文脈とみなさない():
    # 朝に開いた PDF を、夜の離席の説明に使わない
    block = away(240, 60)
    segments = [present(0, 10, "応用数学B_2024期末.pdf"), block]
    assert context.guess(block, segments, RULES) is None


def test_規則に当たらなければ推定しない():
    # 当てずっぽうを既定値にすると、間違ったまま Enter で確定してしまう
    block = away(10, 60)
    segments = [present(0, 10, "Slack"), block]
    assert context.guess(block, segments, RULES) is None


def test_壊れた正規表現があっても推定を止めない():
    rules = [{"match": "([unclosed"}] + RULES
    block = away(10, 60)
    segments = [present(0, 10, "情報理論 第3章"), block]
    assert context.guess(block, segments, rules)["subject"] == "情報理論"


def test_ロールアップ全体に推定を付ける():
    block = away(10, 90)
    summary = {"segments": [present(0, 10, "応用数学B_2024期末.pdf"), block],
               "away_blocks": [block]}
    context.annotate(summary, RULES)
    assert summary["away_blocks"][0]["guess"]["subject"] == "応用数学B"


def test_タイトルの無い在席は隣にしない():
    # ロック画面など。空タイトルを隣として採ると推定が全部そこで止まる
    block = away(20, 60)
    segments = [present(0, 10, "情報理論 第3章"), present(12, 5, None), block]
    assert context.guess(block, segments, RULES)["subject"] == "情報理論"
