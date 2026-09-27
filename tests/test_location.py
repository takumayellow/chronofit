"""位置履歴で離席ブロックを場所に割るテスト。

座標はすべて架空（赤道・本初子午線付近の海上）。実在の住所や店の座標を
公開リポジトリへ入れないため、テストでも使わない。

確かめるのは、場所の割り当てが正しいことに加えて、**保存物から個人情報が
削られていること**（軌跡を持たない・登録済みの座標を持たない・期限で消える）。
"""
import json
from datetime import date, datetime, timedelta, timezone

from chronofit.sources import location

JST = timezone(timedelta(hours=9))
HOME = {"name": "家", "lat": 0.0, "lng": 0.0, "radius_m": 100}
SHOP = {"name": "店", "lat": 0.01, "lng": 0.0, "radius_m": 80, "away_label": "食事・休憩"}
PLACES = [HOME, SHOP]


def at(hour, minute=0, day=27):
    return datetime(2026, 9, day, hour, minute, tzinfo=JST)


def visit(start, end, latlng):
    return {"startTime": start.isoformat(), "endTime": end.isoformat(),
            "visit": {"topCandidate": {"placeLocation": {"latLng": latlng}}}}


def activity(start, end):
    return {"startTime": start.isoformat(), "endTime": end.isoformat(),
            "activity": {"topCandidate": {"type": "WALKING"}}}


def path_only(start, end):
    return {"startTime": start.isoformat(), "endTime": end.isoformat(),
            "timelinePath": [{"point": "0.005°, 0.0°", "time": start.isoformat()}]}


def export(*items):
    return {"semanticSegments": list(items)}


def block(start, end):
    return {"start": start, "end": end, "sec": (end - start).total_seconds()}


def test_座標表記はAndroidとiOSの両方を読む():
    assert location.parse_latlng("1.5°, 2.5°") == (1.5, 2.5)
    assert location.parse_latlng({"latLng": "-1.25°, 2.5°"}) == (-1.25, 2.5)
    assert location.parse_latlng("geo:1.5,2.5") == (1.5, 2.5)
    assert location.parse_latlng("geo:abc") is None
    assert location.parse_latlng("95.0°, 0.0°") is None


def test_軌跡だけの区間は読まない():
    data = export(visit(at(12), at(12, 30), "0.0°, 0.0°"),
                  path_only(at(12, 30), at(12, 40)),
                  activity(at(12, 30), at(12, 40)))
    segments = location.segments(data)
    assert [(s[0], s[2]) for s in segments] == [(at(12), (0.0, 0.0)), (at(12, 30), None)]


def test_壊れた区間は飛ばして残りを読む():
    data = export({"startTime": "garbage", "endTime": at(13).isoformat(), "visit": {}},
                  visit(at(13), at(12), "0.0°, 0.0°"),          # 終わりが先
                  visit(at(14), at(15), "not a coord"),
                  visit(at(15), at(16), "0.0°, 0.0°"))
    assert len(location.segments(data)) == 1


def test_Z付きの時刻も読む():
    data = export({"startTime": "2026-09-27T03:00:00.000Z",
                   "endTime": "2026-09-27T04:00:00.000Z",
                   "visit": {"topCandidate": {"placeLocation": {"latLng": "0°, 0°"}}}})
    assert location.segments(data)[0][0] == at(12)


def test_登録済みの場所は名前だけ残し座標を持たない():
    stays = location.to_stays([(at(12), at(13), (0.0002, 0.0001))], PLACES)
    assert stays == [{"start": at(12).isoformat(), "end": at(13).isoformat(), "place": "家"}]


def test_未登録の場所は丸めた座標だけ残す():
    stays = location.to_stays([(at(12), at(13), (0.123456, 0.654321))], PLACES)
    assert stays[0]["place"] == location.UNKNOWN
    assert (stays[0]["lat"], stays[0]["lng"]) == (0.123, 0.654)


def test_移動区間は区分と時刻だけ():
    stays = location.to_stays([(at(12), at(12, 20), None)], PLACES)
    assert stays == [{"start": at(12).isoformat(), "end": at(12, 20).isoformat(),
                      "place": location.MOVING}]


def test_半径が重なるときは近いほうに当てる():
    near = [{"name": "A", "lat": 0.0, "lng": 0.0, "radius_m": 500},
            {"name": "B", "lat": 0.002, "lng": 0.0, "radius_m": 500}]
    assert location.match((0.0015, 0.0), location.valid_places(near)) == "B"


def test_壊れた場所の設定は無視する():
    places = [{"name": "x"}, {"lat": 0, "lng": 0}, "bad", {"name": "ok", "lat": "0", "lng": 0}]
    assert [p["name"] for p in location.valid_places(places)] == ["ok"]


def test_同じ書き出しを二度取り込んでも増えない(tmp_path):
    stays = location.to_stays([(at(12), at(13), (0.0, 0.0))], PLACES)
    location.store(stays, tmp_path)
    location.store(stays, tmp_path)
    assert len(location.load_day("2026-09-27", tmp_path)) == 1


def test_離席ブロックを場所ごとに割る(tmp_path):
    raw = location.segments(export(
        visit(at(12), at(12, 23), "0.0°, 0.0°"),           # 家（ブロック前から）
        activity(at(12, 23), at(12, 40)),
        visit(at(12, 40), at(14, 30), "0.01°, 0.0°"),      # 店
        activity(at(14, 30), at(15, 0))))
    location.store(location.to_stays(raw, PLACES), tmp_path)
    stays = location.stays_around("2026-09-27", tmp_path)
    parts = location.overlay(block(at(12, 23), at(15, 11)), stays)
    assert parts == [{"place": "移動", "sec": 47 * 60.0}, {"place": "店", "sec": 110 * 60.0}]


def test_前日から続く滞在も拾う(tmp_path):
    location.store(location.to_stays([(at(23, 0, day=26), at(8, 0), (0.0, 0.0))], PLACES),
                   tmp_path)
    stays = location.stays_around("2026-09-27", tmp_path)
    assert location.overlay(block(at(0), at(1)), stays) == [{"place": "家", "sec": 3600.0}]


def test_大半を過ごした場所のラベルを提案する():
    target = block(at(12), at(13))
    target["places"] = [{"place": "店", "sec": 3000.0}, {"place": "移動", "sec": 600.0}]
    assert location.suggest(target, PLACES) == "食事・休憩"
    target["places"] = [{"place": "店", "sec": 2000.0}, {"place": "移動", "sec": 1600.0}]
    assert location.suggest(target, PLACES) is None


def test_ラベルの無い場所は提案しない():
    target = block(at(12), at(13))
    target["places"] = [{"place": "家", "sec": 3600.0}]
    assert location.suggest(target, PLACES) is None


def test_annotateは位置の無いブロックに何も付けない():
    summary = {"away_blocks": [block(at(1), at(2))]}
    location.annotate(summary, [])
    assert "places" not in summary["away_blocks"][0]


def test_後から登録した場所へ当て直す(tmp_path):
    stays = location.to_stays([(at(12), at(13), (0.3, 0.3))], PLACES)
    location.store(stays, tmp_path)
    later = PLACES + [{"name": "新しい店", "lat": 0.3, "lng": 0.3}]
    rematched = location.stays_around("2026-09-27", tmp_path, later)
    assert rematched[0]["place"] == "新しい店"
    assert "lat" not in rematched[0]


def test_保持期間を過ぎた未登録座標を消す(tmp_path):
    old = location.to_stays([(at(12, day=1), at(13, day=1), (0.3, 0.3))], PLACES)
    new = location.to_stays([(at(12), at(13), (0.3, 0.3))], PLACES)
    location.store(old + new, tmp_path)
    removed = location.purge(tmp_path, date(2026, 9, 27), retention_days=20)
    assert removed == 1
    kept_old = location.load_day("2026-09-01", tmp_path)[0]
    assert "lat" not in kept_old and kept_old["place"] == location.UNKNOWN
    assert "lat" in location.load_day("2026-09-27", tmp_path)[0]


def test_保存物に軌跡も登録済みの座標も入らない(tmp_path):
    raw = location.segments(export(visit(at(12), at(13), "0.0°, 0.0°"),
                                   path_only(at(13), at(13, 5)),
                                   activity(at(13), at(13, 5))))
    location.store(location.to_stays(raw, PLACES), tmp_path)
    text = (tmp_path / "2026-09-27.json").read_text(encoding="utf-8")
    assert "timelinePath" not in text and "point" not in text
    assert "lat" not in json.loads(text)[0]


def test_未登録の場所を長い順に並べる(tmp_path):
    stays = location.to_stays([(at(9), at(10), (0.3, 0.3)),
                               (at(11), at(14), (0.5, 0.5)),
                               (at(15), at(16), (0.3, 0.3))], PLACES)
    location.store(stays, tmp_path)
    spots = location.unknown_spots(tmp_path)
    assert [(s["lat"], s["sec"], s["visits"]) for s in spots] == [(0.5, 10800.0, 1),
                                                                  (0.3, 7200.0, 2)]


def test_取り込みコマンドは書き出しを消せる(tmp_path, monkeypatch, capsys):
    from chronofit import cli
    monkeypatch.setenv("CHRONOFIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHRONOFIT_CONFIG", str(tmp_path / "config.json"))
    (tmp_path / "config.json").write_text(json.dumps({"places": PLACES}), encoding="utf-8")
    source = tmp_path / "Timeline.json"
    source.write_text(json.dumps(export(visit(at(12), at(13), "0.0°, 0.0°"))),
                      encoding="utf-8")
    assert cli.main(["location", "import", str(source), "--delete-source"]) == 0
    assert not source.exists()
    stored = location.load_day("2026-09-27", tmp_path / "home" / "location")
    assert stored[0]["place"] == "家"


def test_読めない書き出しは消さない(tmp_path, monkeypatch):
    from chronofit import cli
    monkeypatch.setenv("CHRONOFIT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHRONOFIT_CONFIG", str(tmp_path / "none.json"))
    source = tmp_path / "wrong.json"
    source.write_text(json.dumps({"unrelated": []}), encoding="utf-8")
    assert cli.main(["location", "import", str(source), "--delete-source"]) == 0
    assert source.exists()


def test_保持日数0は既定値へ戻さない():
    from chronofit import cli
    assert cli._retention({"location_retention_days": 0}) == 0
    assert cli._retention({}) == location.RETENTION_DAYS
    assert cli._retention({"location_retention_days": "x"}) == location.RETENTION_DAYS


def test_数日続いた滞在も当日の離席に重ねる(tmp_path):
    location.store(location.to_stays([(at(22, day=24), at(8), (0.0, 0.0))], PLACES), tmp_path)
    stays = location.stays_around("2026-09-27", tmp_path)
    assert location.overlay(block(at(6), at(7)), stays) == [{"place": "家", "sec": 3600.0}]


def test_日付でない名前のファイルはpurgeで触らない(tmp_path):
    (tmp_path / "backup.json").write_text('[{"lat": 1}]', encoding="utf-8")
    assert location.purge(tmp_path, date(2026, 9, 27), retention_days=0) == 0
    assert "lat" in (tmp_path / "backup.json").read_text(encoding="utf-8")
