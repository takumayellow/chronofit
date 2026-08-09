"""ブラウザ履歴を時間へ直すところのテスト。

訪問は「点」なので、長さの復元の仕方がそのまま数字の意味になる。
ここが緩むと、開きっぱなしのタブが何時間もの作業に化ける。
"""
from datetime import datetime, timedelta

from chronofit.sources import history

BASE = datetime(2026, 8, 9, 13, 0)

RULES = [
    {"match": r"atcoder\.jp", "subject": "AtCoder", "kind": "精進"},
    {"match": r"youtube\.com", "category": "娯楽"},
]


def visit(offset_min, url, title=""):
    return (BASE + timedelta(minutes=offset_min), url, title)


def test_次の訪問までを閲覧時間とみなす():
    spans = history.spans([visit(0, "https://a.example/1"), visit(5, "https://a.example/2")])
    assert spans[0][1] == 300.0


def test_間が空いたら開きっぱなしとみなさない():
    # 3時間後の訪問までを閲覧時間にすると、寝ている間が作業時間になる
    spans = history.spans([visit(0, "https://a.example/1"), visit(180, "https://a.example/2")])
    assert spans[0][1] == history.TAIL_SEC


def test_最後の訪問にも長さを与える():
    assert history.spans([visit(0, "https://a.example/1")])[0][1] == history.TAIL_SEC


def test_URLでもタイトルでも分類できる():
    # 科目名は URL に出ず、タイトルにしか出ないことが多い
    assert history.classify("https://atcoder.jp/contests/abc", "", RULES)["subject"] == "AtCoder"
    rules = [{"match": "応用数学B", "subject": "応用数学B"}]
    assert history.classify("https://letus.example/mod/1", "応用数学B 第3回", rules)


def test_当たらなければ分類しない():
    assert history.classify("https://unknown.example/", "", RULES) is None


def test_壊れた正規表現があっても分類を止めない():
    rules = [{"match": "([unclosed"}] + RULES
    assert history.classify("https://youtube.com/watch", "", rules)["category"] == "娯楽"


def test_日ごとに分類済みと未分類を分ける():
    visits = [visit(0, "https://atcoder.jp/a"), visit(10, "https://atcoder.jp/b"),
              visit(20, "https://unknown.example/x"), visit(25, "https://unknown.example/y")]
    days = history.daily(visits, RULES)
    assert len(days) == 1
    assert days[0]["by_label"]["AtCoder"] == 600 + 600
    assert "unknown.example" in days[0]["unknown"]


def test_未分類は重い順で規則づくりの材料になる():
    visits = [visit(0, "https://light.example/"), visit(2, "https://heavy.example/"),
              visit(12, "https://light.example/2")]
    top = history.unclassified(history.daily(visits, RULES))
    assert top[0][0] == "heavy.example"


def test_複数スナップショットの重複訪問を落とす():
    same = visit(0, "https://a.example/1")
    assert len(history.dedupe([same, same, visit(1, "https://a.example/2")])) == 2


def test_www_は同じドメインとして数える():
    assert history.domain("https://www.youtube.com/watch") == "youtube.com"


def test_日付をまたぐ訪問を別の日として数える():
    visits = [(datetime(2026, 8, 9, 23, 58), "https://a.example/1", ""),
              (datetime(2026, 8, 10, 0, 1), "https://a.example/2", "")]
    assert [day["date"] for day in history.daily(visits, RULES)] == ["2026-08-09", "2026-08-10"]
