"""繰り返し行った未登録の場所の自動登録のテスト。座標・件名はすべて架空（赤道上）。"""
from datetime import datetime, timedelta, timezone

from chronofit.estimate import agenda, auto_places
from chronofit.sources import location

JST = timezone(timedelta(hours=9))
SHOP = (0.0, 0.0)
PARK = (0.0, 0.05)            # 店から約 5.5km


def at(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=JST)


def stay(coords, start, end, place=location.UNKNOWN):
    return {"place": place, "start": start.isoformat(), "end": end.isoformat(),
            "lat": coords[0], "lng": coords[1]}


def event(title, start, end):
    return {"id": f"{title}-{start:%d%H%M}", "title": title,
            "start": start.isoformat(), "end": end.isoformat()}


def near(coords, metres):
    """北へ `metres` ずらした座標（赤道上で 1度 ≒ 111km）。"""
    return (coords[0] + metres / 111_000, coords[1])


def test_別の日に同じ件名で2回行った場所を件名で登録する():
    stays = [stay(SHOP, at(1, 10), at(1, 11)), stay(near(SHOP, 30), at(8, 15), at(8, 16, 10))]
    events = [event("散髪", at(1, 10, 5), at(1, 11)), event("散髪", at(8, 15, 5), at(8, 16))]
    found = auto_places.discover(stays, events)
    assert [p["name"] for p in found] == ["散髪"]
    assert found[0]["auto"] and found[0]["visit_days"] == 2
    assert location.match(near(SHOP, 20), location.valid_places(found)) == "散髪"


def test_件名が1回しか重ならない場所は登録しない():
    stays = [stay(SHOP, at(1, 10), at(1, 11)), stay(SHOP, at(8, 15), at(8, 16))]
    events = [event("洗濯物", at(1, 10), at(1, 10, 30))]
    assert auto_places.discover(stays, events) == []


def test_1日しか行っていない場所と通り過ぎただけの滞在は数えない():
    stays = [stay(SHOP, at(1, 10), at(1, 11)), stay(SHOP, at(1, 14), at(1, 15)),
             stay(SHOP, at(8, 9), at(8, 9, 3))]        # 3分: バスで通過
    events = [event("散髪", at(1, 10), at(1, 11)), event("散髪", at(1, 14), at(1, 15)),
              event("散髪", at(8, 9), at(8, 9, 3))]
    assert auto_places.discover(stays, events) == []


def test_離れた場所は別に束ね_登録済みの名前とは重ねない():
    stays = [stay(SHOP, at(1, 10), at(1, 11)), stay(SHOP, at(8, 10), at(8, 11)),
             stay(PARK, at(2, 10), at(2, 11)), stay(PARK, at(9, 10), at(9, 11)),
             stay(PARK, at(3, 10), at(3, 11), place="家")]
    events = [event("散歩", at(d, 10), at(d, 11)) for d in (1, 8, 2, 9)]
    found = auto_places.discover(stays, events, known=[{"name": "散歩"}])
    assert sorted(p["name"] for p in found) == ["散歩 2", "散歩 3"]


def test_滞在の一部に重なっただけの長い予定は名前にしない():
    stays = [stay(SHOP, at(d, 10), at(d, 11)) for d in (1, 8)]
    events = [event("試験期間", at(d, 0), at(d, 5, 59)) for d in (1, 8)]
    assert auto_places.discover(stays, events) == []


def test_手の登録が自動登録より優先される(tmp_path):
    location.save_place(tmp_path, {"name": "店", "lat": 0.0, "lng": 0.0, "radius_m": 50})
    location.save_auto_places(tmp_path, [
        {"name": "店", "lat": 1.0, "lng": 1.0, "radius_m": 100, "auto": True},
        {"name": "散髪", "lat": 0.0, "lng": 0.05, "radius_m": 100, "auto": True}])
    places = {p["name"]: p for p in location.load_places(tmp_path)}
    assert places["店"]["lat"] == 0.0 and "散髪" in places
    assert [p["name"] for p in location.load_places(tmp_path, auto=False)] == ["店"]
    # 手で足すときに自動登録を places.json へ書き写さない
    location.save_place(tmp_path, {"name": "駅", "lat": 0.0, "lng": 0.1, "radius_m": 50})
    assert [p["name"] for p in location.load_places(tmp_path, auto=False)] == ["店", "駅"]


def _visit(d, booked, arrive_min, done_min, title):
    """家を出て、予約の `arrive_min` 分後に着き、`done_min` 分後に店を出る1日。"""
    start = at(d, booked)
    arrive = start + timedelta(minutes=arrive_min)
    leave_home = arrive - timedelta(minutes=3)
    done = start + timedelta(minutes=done_min)
    stays = [{"place": "家", "start": at(d, 0).isoformat(), "end": leave_home.isoformat()},
             {"place": location.MOVING, "start": leave_home.isoformat(),
              "end": arrive.isoformat()},
             {"place": "店", "start": arrive.isoformat(), "end": done.isoformat()},
             {"place": "家", "start": (done + timedelta(minutes=3)).isoformat(),
              "end": at(d, 23).isoformat()}]
    events = [event(title, start, start + timedelta(hours=1)),
              # 滞在の途中に始まるだけのリマインダは予約ではない
              event("解約する", start + timedelta(minutes=40), start + timedelta(minutes=50))]
    return stays, events


def test_件名が違っても行った場所で過去の予定をつなぎ_出る時刻と終わる時刻を見積もる():
    stays, events = [], []
    for d, title, done in ((1, "美容室", 70), (8, "美容室", 72), (15, "髪切る 店", 80)):
        s, e = _visit(d, 13, -8, done, title)
        stays, events = stays + s, events + e
    rows = agenda.check(events, stays, {"家"}, now=at(30, 0))
    guess = agenda.forecast(event("髪切る", at(29, 15), at(29, 16)), rows, stays)
    assert guess["place"] == "店" and guess["n"] == 3
    assert guess["arrive"] == -8 and guess["leave"] == -11 and guess["done"] == 72


def test_行った場所が分からない件名は見積もらない():
    stays, events = _visit(1, 13, -8, 70, "美容室")
    rows = agenda.check(events, stays, {"家"}, now=at(30, 0))
    assert agenda.forecast(event("ゼミ", at(29, 15), at(29, 16)), rows, stays) is None
    # 場所は分かっても、過去の予約が1回だけなら見積もらない
    assert agenda.forecast(event("美容室", at(29, 15), at(29, 16)), rows, stays) is None
