"""タイトル正規化のテスト。

ここが緩いとスパンが数千行に膨らみ、厳しすぎると作業対象（ファイル名・年度）を
取り落とす。両側を固定する。
"""
from chronofit.model.title import normalize


def test_スピナーを落とす():
    # 実測: claude-work のタイトルは1サンプルごとにブライユ文字が変わる
    first = normalize("claude-work  2:⠐ 夏休みのタスク優先順位付け")
    second = normalize("claude-work  2:⠂ 夏休みのタスク優先順位付け")
    assert first == second
    assert first == "claude-work 2: 夏休みのタスク優先順位付け"


def test_経過秒表示を落とす():
    assert normalize("Claude (12s · esc to interrupt)") == "Claude"
    assert normalize("Claude (3m)") == "Claude"


def test_未読件数を落とす():
    assert normalize("(3) 受信トレイ - Gmail") == normalize("(7) 受信トレイ - Gmail")


def test_再生位置を落とす():
    assert normalize("動画 12:34 / 1:02:33 - YouTube") == normalize("動画 / - YouTube")


def test_未保存マークを落とす():
    assert normalize("● main.py - VSCode") == normalize("main.py - VSCode")


def test_作業対象は残す():
    # 科目・年度・拡張子は学習曲線のキーそのものなので絶対に落とさない
    title = normalize("応用数学A_2024_期末.pdf - SumatraPDF")
    assert "応用数学A" in title
    assert "2024" in title
    assert "期末" in title
    assert ".pdf" in title


def test_空とNoneに耐える():
    assert normalize("") == ""
    assert normalize(None) == ""


def test_異なる作業は別物のまま():
    assert normalize("応用数学A_2024.pdf") != normalize("応用数学A_2023.pdf")
