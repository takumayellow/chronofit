"""外出の区間・滞在の実測と、予定した外出の見積もりのテスト。

座標はすべて架空（赤道・本初子午線付近の海上）。
"""
from datetime import datetime, timedelta, timezone

import pytest

from chronofit.estimate import outing
from chronofit.sources import location

JST = timezone(timedelta(hours=9))
PLACES = [{"name": "家", "lat": 0.0, "lng": 0.0, "radius_m": 100, "home": True},
          {"name": "駅", "lat": 0.01, "lng": 0.0, "radius_m": 100},
          {"name": "店", "lat": 0.011, "lng": 0.0, "radius_m": 80},
          {"name": "遠方", "lat": 0.3, "lng": 0.0, "radius_m": 100}]


def at(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=JST)


def stay(place, start, end, **extra):
    return {"place": place, "start": start.isoformat(), "end": end.isoformat(), **extra}


def trip(day, go=10, shop=40, back=12, unknown=0):
    """家 → 店 → 家。`unknown` 分だけ帰りに未登録の場所へ寄る。"""
    out = at(day, 12)
    arrive = out + timedelta(minutes=go)
    leave = arrive + timedelta(minutes=shop)
    items = [stay("家", at(day, 8), out),
             stay(location.MOVING, out, arrive),
             stay("店", arrive, leave)]
    cursor = leave
    if unknown:
        items.append(stay(location.UNKNOWN, cursor, cursor + timedelta(minutes=unknown)))
        cursor += timedelta(minutes=unknown)
    home = cursor + timedelta(minutes=back)
    items += [stay(location.MOVING, cursor, home), stay("家", home, at(day, 23))]
    return items


def test_登録済みの場所どうしの移動を区間にする():
    legs = outing.legs(trip(1))
    assert [(leg["from"], leg["to"], leg["sec"] / 60) for leg in legs] == [
        ("家", "店", 10), ("店", "家", 12)]


def test_寄り道を挟んだ移動は区間に数えない():
    legs = outing.legs(trip(1, unknown=30))
    assert [(leg["from"], leg["to"]) for leg in legs] == [("家", "店")]


def test_記録の抜けた移動は区間に数えない():
    items = [stay("家", at(1, 8), at(1, 12)), stay("店", at(1, 13), at(1, 14))]
    assert outing.legs(items) == []


def test_短く割れた同じ場所の滞在は1つに戻す():
    items = [stay("店", at(1, 12), at(1, 12, 20)), stay("店", at(1, 12, 25), at(1, 13))]
    visits = outing.visits(items)
    assert len(visits) == 1 and visits[0]["end"] == at(1, 13)


def test_要約は中央値とp80():
    s = outing.summarize([60 * m for m in (10, 10, 11, 12, 30)])
    assert s["n"] == 5 and s["median"] == 11 and 12 <= s["p80"] <= 30
    assert outing.summarize([]) is None


def test_件数が足りなければ逆向き_それも無ければ距離から仮置き():
    stays = trip(1) + trip(2) + trip(3, back=14)
    table = outing.leg_table(outing.legs(stays))
    found, basis = outing.leg_estimate("家", "店", table)
    assert found["median"] == 10 and basis == "実測3件"
    table.pop(("店", "家"))
    _, basis = outing.leg_estimate("店", "家", table)
    assert basis.startswith("逆向き")
    guessed, basis = outing.leg_estimate("駅", "遠方", {}, PLACES)
    assert basis.startswith("仮") and guessed["median"] > 60       # 約32km は電車で1時間強
    walk, _ = outing.leg_estimate("駅", "店", {}, PLACES)
    assert walk["median"] < 5                                     # 約110m は徒歩で数分
    assert outing.leg_estimate("家", "どこか", {}, PLACES) == (None, "不明")


def test_予定の外出を区間と滞在の実測で組み立てる():
    stays = trip(1) + trip(2) + trip(3)
    result = outing.plan(outing.parse_steps(["家", "店", "家"]),
                         outing.leg_table(outing.legs(stays)),
                         outing.dwell_table(outing.visits(stays)), PLACES, depart=at(4, 12))
    assert [row["basis"] for row in result["rows"]] == ["実測3件"] * 3
    assert result["median"] == 10 + 40 + 12
    assert result["rows"][1]["start"] == at(4, 12, 10)
    assert result["unknown"] == []


def test_時間を指定した滞在は指定を根拠にする():
    result = outing.plan(outing.parse_steps(["家", "店:25", "家"]), {}, {}, PLACES)
    assert result["rows"][1]["median"] == 25 and result["rows"][1]["basis"] == "指定"


def test_用事の実測が無ければ場所全体に落とす():
    visits = outing.visits(trip(1) + trip(2) + trip(3))
    table = outing.dwell_table(visits)
    found, basis = outing.dwell_estimate("店", "食事", table)
    assert found["median"] == 40 and basis == "用事を問わず実測3件"


def test_場所の指定を読む():
    assert outing.parse_steps(["家", "店@食事:30", "家"]) == [
        {"place": "家"}, {"place": "店", "activity": "食事", "minutes": 30.0}, {"place": "家"}]


def test_長すぎる滞在は滞在の分布に入れない():
    visits = [{"place": "家", "start": at(1, 0), "end": at(1, 23)}]
    assert outing.dwell_table(visits) == {}


def test_その日を除いた実測で過去の外出を見積もり直す():
    stays = trip(1) + trip(2) + trip(3) + trip(4) + trip(5, unknown=30)
    results = outing.backtest(stays, {"家"}, PLACES)
    assert len(results) == 5
    clean = outing.score(results, "legs_only", clean_only=True)
    assert clean["n"] == 4 and clean["mae"] == pytest.approx(0)
    everything = outing.score(results, "legs_only")
    assert everything["n"] == 5 and everything["bias"] == pytest.approx(0)
    assert results[-1]["detour"] == pytest.approx(42)            # 寄り道30分 + 帰り12分


def test_場所の登録はgitの外のファイルを設定より優先する(tmp_path):
    location.save_place(tmp_path, {"name": "家", "lat": 1.0, "lng": 1.0})
    places = location.load_places(tmp_path, [{"name": "家", "lat": 0, "lng": 0},
                                             {"name": "店", "lat": 0.01, "lng": 0}])
    assert {(p["name"], p["lat"]) for p in places} == {("家", 1.0), ("店", 0.01)}
    location.save_place(tmp_path, {"name": "家", "lat": 2.0, "lng": 2.0})
    assert [p["lat"] for p in location.load_places(tmp_path)] == [2.0]
    (tmp_path / location.PLACES_FILE).write_text("{broken", encoding="utf-8")
    assert location.load_places(tmp_path) == []


def test_用事を問わない見積もりは場所の全部の滞在から():
    visits = [{"place": "店", "activity": "食事", "start": at(d, 12), "end": at(d, 12, 50)}
              for d in (1, 2, 3, 4)]
    visits += [{"place": "店", "activity": "買い物", "start": at(d, 15), "end": at(d, 15, 20)}
               for d in (1, 2, 3, 4, 5)]
    table = outing.dwell_table(visits)
    pooled, basis = outing.dwell_estimate("店", None, table)
    assert pooled["n"] == 9 and basis == "実測9件"
    fallback, basis = outing.dwell_estimate("店", "映画", table)
    assert fallback["n"] == 9 and basis == "用事を問わず実測9件"
    assert outing.dwell_estimate("店", "食事", table)[0]["median"] == 50


def overnight(day):
    """22時に家を出て、日付が変わってから店を出て帰る。"""
    nxt = day + 1
    return [stay("家", at(day, 8), at(day, 22)), stay(location.MOVING, at(day, 22), at(day, 22, 10)),
            stay("店", at(day, 22, 10), at(nxt, 0, 20)),
            stay(location.MOVING, at(nxt, 0, 20), at(nxt, 1)), stay("家", at(nxt, 1), at(nxt, 9))]


def test_日を跨ぐ外出は触れた日を全部除いて見積もる():
    stays = overnight(1) + trip(3) + trip(4) + trip(5) + overnight(7)
    result = next(r for r in outing.backtest(stays, {"家"}, PLACES) if r["day"] == "2026-09-01")
    back = result["legs_only"]["rows"][-1]
    assert back["basis"] == "実測4件"             # 自分の帰り道（9/2 発）は数えない


def test_検証では決済で当てた用事を予定の指定に使う():
    stays = trip(1) + trip(2) + trip(3) + trip(4)

    def label(visit_list):
        return [{**v, "activity": "食事"} if v["place"] == "店" else v for v in visit_list]

    results = outing.backtest(stays, {"家"}, PLACES, label=label)
    assert results[0]["full"]["rows"][1]["label"] == "店（食事）"


def test_見積もれない行から先は時刻を出さない():
    result = outing.plan(outing.parse_steps(["家", "どこか", "家"]), {}, {}, PLACES,
                         depart=at(1, 12))
    assert all("start" not in row for row in result["rows"])


def test_分の指定が数でなければ分かる文面で断る():
    for token in ("店:xx", "店:0"):
        with pytest.raises(ValueError, match="店"):
            outing.parse_steps(["家", token, "家"])
