"""カレンダーの予定と位置の記録の突き合わせのテスト。予定の件名・場所はすべて架空。"""
from datetime import datetime, timedelta, timezone

from chronofit.estimate import agenda
from chronofit.sources import gcal, location

JST = timezone(timedelta(hours=9))


def at(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=JST)


def stay(place, start, end, **extra):
    return {"place": place, "start": start.isoformat(), "end": end.isoformat(), **extra}


def event(title, start, end, uid=None):
    return {"id": uid or f"{title}-{start:%d%H%M}", "title": title,
            "start": start.isoformat(), "end": end.isoformat()}


def day(d, late=0):
    """家 → 学校（10時の予定に `late` 分遅れて着く）→ 家。"""
    arrive = at(d, 10) + timedelta(minutes=late)
    return [stay("家", at(d, 0), at(d, 9, 30)), stay(location.MOVING, at(d, 9, 30), arrive),
            stay("学校", arrive, at(d, 12)), stay(location.MOVING, at(d, 12), at(d, 12, 30)),
            stay("家", at(d, 12, 30), at(d, 23, 59))]


def test_予定の時間にいた場所と実際の時刻を返す():
    rows = agenda.check([event("ゼミ", at(1, 10), at(1, 12))], day(1, late=15), {"家"})
    assert rows[0]["status"] == "外出" and rows[0]["place"] == "学校"
    assert rows[0]["actual_start"] == at(1, 10, 15)


def test_家にいた予定と記録の無い予定を分ける():
    events = [event("ゼミ", at(1, 15), at(1, 16)), event("ゼミ", at(5, 10), at(5, 12))]
    rows = agenda.check(events, day(1), {"家"})
    assert [row["status"] for row in rows] == ["家", "記録なし"]


def test_まだ終わっていない予定は確かめない():
    events = [event("ゼミ", at(1, 10), at(1, 12)), event("ゼミ", at(2, 10), at(2, 12))]
    rows = agenda.check(events, day(1) + day(2), {"家"}, now=at(2, 11))
    assert len(rows) == 1


def test_同じ件名を同じ場所で2回こなせば件名の場所にする():
    stays = day(1) + day(2) + day(3)
    events = [event("ゼミ", at(1, 10), at(1, 12)), event("ゼミ", at(2, 10), at(2, 12)),
              event("面談", at(3, 10), at(3, 11))]
    places = agenda.title_places(agenda.check(events, stays, {"家"}))
    assert places == {"ゼミ": "学校"}


def test_繰り返しの予定が滞在の半分以上を覆えば用事にする():
    visits = [{"place": "学校", "start": at(d, 10), "end": at(d, 12)} for d in (1, 2, 3)]
    visits.append({"place": "学校", "start": at(4, 10), "end": at(4, 12), "activity": "食事"})
    events = [event("ゼミ", at(d, 10), at(d, 11, 30)) for d in (1, 2, 3, 4)]
    events.append(event("打刻", at(1, 11, 55), at(1, 12)))
    labeled = agenda.label_visits(visits, events)
    assert [v.get("activity") for v in labeled] == ["ゼミ", "ゼミ", "ゼミ", "食事"]
    assert "activity" not in visits[0]                                   # 元の列は変えない
    assert agenda.label_visits(visits[:1], events[:1])[0].get("activity") is None  # 1回きりは使わない


def test_未登録の場所に重なった予定の件名を手掛かりにする():
    stays = [stay(location.UNKNOWN, at(1, 19), at(1, 21), lat=0.12345, lng=0.0),
             stay(location.UNKNOWN, at(2, 19), at(2, 21), lat=0.12349, lng=0.0)]
    events = [event("食事会", at(1, 19), at(1, 21)), event("食事会", at(2, 19), at(2, 20))]
    assert agenda.spot_titles(stays, events)[(0.123, 0.0)]["食事会"] == 2


def test_APIの予定は時刻のあるものだけ件名と場所欄を残す():
    item = {"id": "e1", "status": "confirmed", "summary": " ゼミ ", "location": "学校",
            "description": "残さない", "start": {"dateTime": "2026-09-01T10:00:00+09:00"},
            "end": {"dateTime": "2026-09-01T12:00:00+09:00"}}
    assert gcal.parse_event(item) == {"id": "e1", "start": "2026-09-01T10:00:00+09:00",
                                      "end": "2026-09-01T12:00:00+09:00", "title": "ゼミ",
                                      "location": "学校"}
    assert gcal.parse_event({**item, "start": {"date": "2026-09-01"}}) is None      # 終日
    assert gcal.parse_event({**item, "status": "cancelled"}) is None
    assert gcal.parse_event({**item, "end": item["start"]}) is None


def test_取り直した期間の予定は丸ごと入れ替え_保存と読み直しが往復する(tmp_path):
    old = [event("消した予定", at(5, 10), at(5, 11)), event("前の予定", at(1, 10), at(1, 11))]
    new = [event("新しい予定", at(6, 10), at(6, 11))]
    rows = gcal.replace_window(old, new, at(3, 0), at(10, 0))
    assert [row["title"] for row in rows] == ["前の予定", "新しい予定"]
    gcal.save(tmp_path, rows)
    assert gcal.load(tmp_path) == rows
    (tmp_path / gcal.EVENTS).write_text('[{"id": 1}, "x"]', encoding="utf-8")
    assert gcal.load(tmp_path) == []


def test_カレンダーの権限は読み取り専用だけ():
    assert gcal.SCOPES == ["https://www.googleapis.com/auth/calendar.readonly"]


def test_UTCのZ表記も読み_時差が混ざっても時刻順に並べる():
    item = {"id": "e1", "summary": "ゼミ", "start": {"dateTime": "2026-09-01T01:00:00Z"},
            "end": {"dateTime": "2026-09-01T02:00:00Z"}}
    assert gcal.parse_event(item)["start"] == "2026-09-01T01:00:00+00:00"
    late = {"id": "a", "title": "後", "start": "2026-09-01T09:00:00+02:00",
            "end": "2026-09-01T10:00:00+02:00"}
    early = {"id": "b", "title": "先", "start": "2026-09-01T10:00:00+09:00",
             "end": "2026-09-01T11:00:00+09:00"}
    rows = gcal.replace_window([], [late, early], at(1, 0), at(2, 0))
    assert [row["title"] for row in rows] == ["先", "後"]
