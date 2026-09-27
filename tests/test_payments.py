"""決済の通知メールの読み取りと、滞在の用事への当て込みのテスト。

メール本文は形式だけを実物に合わせた架空のもの（氏名・カード番号・店は架空）。
"""
import base64
from datetime import datetime, timedelta, timezone

import pytest

from chronofit.sources import gmail, payments

JST = timezone(timedelta(hours=9))

SOURCES = [{"from": "notice@card.example", "subject": "ご利用のお知らせ", "format": "card_notice"},
           {"from": "order@shop.example", "subject": "ご注文", "format": "mobile_order",
            "prefix": "テストバーガー"}]

CARD = """山田　様

いつもテストカードをご利用頂きありがとうございます。
ご利用カード：テスト／クレジット

◇利用日：2026/09/24 09:25
◇利用先：テスト書店
◇利用取引：買物
◇利用金額：1,980円
"""

CARD_USD = CARD.replace("テスト書店", "EXAMPLE* SUB").replace("1,980円", "110.28 USD")

ORDER_HTML = """<html><style>td{color:red}</style><table>
<tr><td>ご注文日時 ：</td><td>2026/09/22 13:07:09</td></tr>
<tr><td>店舗名 ：</td><td>テスト駅前店</td></tr>
<tr><td>ご請求金額 ：</td><td>¥850</td></tr></table></html>"""


def test_カード利用のお知らせから時刻と店と金額を読む():
    row = payments.parse_message("テストカード <Notice@card.example>",
                                 "ご利用のお知らせ【テストカード】", CARD, SOURCES)
    assert row == {"time": "2026-09-24T09:25:00+09:00", "merchant": "テスト書店",
                   "amount": 1980.0, "currency": "JPY"}


def test_外貨の利用は通貨ごと読む():
    row = payments.parse_card_notice(CARD_USD)
    assert row["amount"] == 110.28 and row["currency"] == "USD"


def test_コロンの前の空白と秒のある通知も読む():
    text = "◇利用日 ：2026/07/25 11:06:50\n◇利用先 ：テスト店\n◇利用金額：4000円\n"
    assert payments.parse_card_notice(text) == {
        "time": "2026-07-25T11:06:50+09:00", "merchant": "テスト店", "amount": 4000.0,
        "currency": "JPY"}

def test_モバイルオーダーのHTMLから注文時刻と店舗を読む():
    text = payments.html_to_text(ORDER_HTML)
    assert "color" not in text
    row = payments.parse_message("order@shop.example", "【モバイルオーダー】ご注文ありがとうございます",
                                 text, SOURCES)
    assert row == {"time": "2026-09-22T13:07:09+09:00", "merchant": "テストバーガー テスト駅前店",
                   "amount": 850.0, "currency": "JPY"}


def test_対象外の送信元と件名は読まない():
    assert payments.parse_message("someone@example.com", "ご利用のお知らせ", CARD, SOURCES) is None
    assert payments.parse_message("notice@card.example", "お支払い金額のお知らせ", CARD,
                                  SOURCES) is None
    assert payments.parse_message("notice@card.example", "ご利用のお知らせ", CARD, []) is None
    assert payments.parse_card_notice("本文なし") is None


def test_読む通知はgitの外に登録し_検索を壊す値は受け付けない(tmp_path):
    payments.save_source(tmp_path, "Notice@Card.example", "ご利用のお知らせ", "card_notice")
    payments.save_source(tmp_path, "notice@card.example", "ご利用のお知らせ", "card_notice",
                         prefix="X")                                   # 同じ組は置き換える
    assert payments.load_sources(tmp_path) == [
        {"from": "notice@card.example", "subject": "ご利用のお知らせ", "format": "card_notice",
         "prefix": "X"}]
    assert payments.gmail_queries(payments.load_sources(tmp_path)) == [
        'from:notice@card.example subject:"ご利用のお知らせ"']
    for bad in (("a b@x.example", "件名", "card_notice"), ("a@x.example", 'x" OR "', "card_notice"),
                ("a@x.example", "件名", "unknown")):
        with pytest.raises(ValueError):
            payments.save_source(tmp_path, *bad)
    (tmp_path / payments.SOURCES).write_text('[{"from": "a@x.example", "subject": 1, '
                                             '"format": "card_notice"}]', encoding="utf-8")
    assert payments.load_sources(tmp_path) == []


def test_同じメッセージは2回足さない(tmp_path):
    row = {"id": "m1", **payments.parse_card_notice(CARD)}
    assert payments.append([row], tmp_path) == 1
    assert payments.append([row, {**row, "id": "m2"}], tmp_path) == 1
    assert [r["id"] for r in payments.load(tmp_path)] == ["m1", "m2"]


def test_店名の規則で用事を決め_決まっていない店を数える(tmp_path):
    payments.save_rule(tmp_path, "マクドナルド", "食事")
    payments.save_rule(tmp_path, "マクドナルド", "外食")          # 同じ語は置き換える
    rules = payments.load_rules(tmp_path)
    assert payments.activity_of("マクドナルド テスト駅前店", rules) == "外食"
    rows = [{"merchant": "テスト書店"}, {"merchant": "テスト書店"},
            {"merchant": "マクドナルド 某店"}]
    assert payments.unmapped(rows, rules) == [("テスト書店", 2)]


def _visit(place, start, end):
    return {"place": place, "start": start, "end": end}


def test_滞在中の決済でその滞在の用事を決める():
    rules = [{"match": "マクドナルド", "activity": "食事"}, {"match": "書店", "activity": "買い物"},
             {"match": "通販", "activity": payments.IGNORE}]
    t = lambda h, m=0: datetime(2026, 9, 22, h, m, tzinfo=JST)  # noqa: E731
    visits = [_visit("モール", t(12, 30), t(13, 30)), _visit("モール", t(15), t(16)),
              _visit("駅", t(17), t(17, 10))]
    rows = [{"time": t(13, 7).isoformat(), "merchant": "マクドナルド 某店"},
            {"time": t(15, 10).isoformat(), "merchant": "マクドナルド 某店"},
            {"time": t(15, 40).isoformat(), "merchant": "テスト書店"},
            {"time": t(17, 13).isoformat(), "merchant": "テスト書店"},    # 端から3分後も拾う
            {"time": t(12, 40).isoformat(), "merchant": "テスト通販"}]    # 場所と無関係は数えない
    labeled = payments.label_visits(visits, rows, rules)
    assert [v.get("activity") for v in labeled] == ["食事", None, "買い物"]
    assert "activity" not in visits[0]                                   # 元の列は変えない


def _part(mime, text):
    return {"mimeType": mime, "body": {"data": base64.urlsafe_b64encode(text.encode()).decode()}}


def test_本文はtext_plainを優先し_無ければHTMLを平文にする():
    both = {"mimeType": "multipart/alternative",
            "parts": [_part("text/html", "<b>html</b>"), _part("text/plain", "plain")]}
    assert gmail.body_text(both) == "plain"
    assert "html" in gmail.body_text({"mimeType": "multipart/alternative",
                                      "parts": [_part("text/html", "<b>html</b>")]})


def test_トークンはDPAPIで暗号化して置く(tmp_path):
    gmail.save_token(tmp_path, '{"refresh_token": "secret-value"}')
    assert b"secret-value" not in (tmp_path / gmail.TOKEN_FILE).read_bytes()
    assert gmail.load_token(tmp_path) == '{"refresh_token": "secret-value"}'
    assert gmail.load_token(tmp_path / "none") is None


def test_求める権限は読み取り専用だけ():
    assert gmail.SCOPES == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_日次の取得は最新の決済の数日前から():
    from chronofit import cli_outing
    assert cli_outing._fetch_since([]) is None
    rows = [{"time": "2026-09-10T12:00:00+09:00"}, {"time": "2026-09-24T09:25:00+09:00"}]
    assert cli_outing._fetch_since(rows) == "2026-09-21"
