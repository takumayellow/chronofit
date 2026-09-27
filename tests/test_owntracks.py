"""スマホ（OwnTracks）から届く位置の受け取りと、滞在への畳み込みのテスト。

座標はすべて架空（赤道・本初子午線付近の海上）。
"""
import base64
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from chronofit.sources import location, owntracks

JST = timezone(timedelta(hours=9))
HOME = {"name": "家", "lat": 0.0, "lng": 0.0, "radius_m": 100}
SHOP = {"name": "店", "lat": 0.01, "lng": 0.0, "radius_m": 80}


def at(hour, minute=0, day=27):
    return datetime(2026, 9, day, hour, minute, tzinfo=JST)


def point(moment, lat, lon, acc=10):
    return {"_type": "location", "lat": lat, "lon": lon, "tst": int(moment.timestamp()),
            "acc": acc, "batt": 80}


def pings(start, end, lat, lon):
    """止まっている間も15分ごとに届く点（OwnTracks の ping）。"""
    result, moment = [], start
    while moment <= end:
        result.append(point(moment, lat, lon))
        moment += timedelta(minutes=15)
    return result


def test_位置のメッセージだけを点として読む():
    body = json.dumps([point(at(12), 0.0, 0.0), {"_type": "transition"},
                       {"_type": "location", "lat": 999, "lon": 0, "tst": 1}]).encode()
    points, others = owntracks.parse_payload(body)
    assert len(points) == 1 and len(others) == 2
    assert owntracks.parse_payload(b"not json") == ([], [])


def test_点を測位日ごとに足し再送の重複は1つにする(tmp_path):
    owntracks.append([point(at(12), 0.0, 0.0), point(at(23, 59, day=26), 0.0, 0.0)], tmp_path)
    owntracks.append([point(at(12), 0.0, 0.0)], tmp_path)
    assert len(owntracks.load_points("2026-09-27", tmp_path)) == 1
    assert len(owntracks.load_points("2026-09-26", tmp_path)) == 1


def test_近い点の並びを滞在に_その間を移動にする():
    points = [point(at(12), 0.0, 0.0), point(at(12, 20), 0.0001, 0.0),       # 家
              point(at(12, 25), 0.005, 0.0),                                  # 途中
              *pings(at(12, 40), at(14, 25), 0.01, 0.0),
              point(at(14, 30), 0.01, 0.0001)]                                # 店
    segments = owntracks.to_segments(points)
    assert [(s[0], s[1], s[2] is None) for s in segments] == [
        (at(12), at(12, 20), False), (at(12, 20), at(12, 40), True),
        (at(12, 40), at(14, 30), False)]
    stays = location.to_stays(segments, [HOME, SHOP])
    assert [s["place"] for s in stays] == ["家", location.MOVING, "店"]


def test_精度の悪い点は捨てる():
    points = [point(at(12), 0.0, 0.0), point(at(12, 10), 0.05, 0.0, acc=1500),
              point(at(12, 30), 0.0, 0.0)]
    assert len(owntracks.to_segments(points)) == 1


def test_前日から続く滞在も当日に重なる(tmp_path):
    owntracks.append(pings(at(23, day=26), at(8), 0.0, 0.0), tmp_path)
    stays = owntracks.stays_for("2026-09-27", tmp_path, [HOME])
    assert [s["place"] for s in stays] == ["家"]


def test_タイムラインの書き出しと重なる時間は書き出しを取る():
    imported = [{"start": at(12).isoformat(), "end": at(13).isoformat(), "place": "店"}]
    received = [{"start": at(12, 30).isoformat(), "end": at(12, 50).isoformat(), "place": "家"},
                {"start": at(11).isoformat(), "end": at(15).isoformat(), "place": "家"}]
    merged = location.merge_sources(imported, received)
    assert [(s["place"], s["start"], s["end"]) for s in merged] == [
        ("家", at(11).isoformat(), at(12).isoformat()),
        ("店", at(12).isoformat(), at(13).isoformat()),
        ("家", at(13).isoformat(), at(15).isoformat())]


def test_ゆっくり歩いた経路を1つの滞在にしない():
    # 1分ごとに約55m ずつ進む。どの点も直前の平均からは100m 以内だが、全体は1km を超える
    walk = [point(at(12) + timedelta(minutes=i), 0.0005 * i, 0.0) for i in range(20)]
    segments = owntracks.to_segments(walk)
    assert all((s[1] - s[0]).total_seconds() < 5 * 60 for s in segments if s[2] is not None)


def test_長い空白は家に数えずどこにも数えない():
    points = [point(at(8), 0.0, 0.0), point(at(8, 10), 0.0, 0.0),
              point(at(20), 0.0, 0.0), point(at(20, 10), 0.0, 0.0)]
    segments = owntracks.to_segments(points)
    assert [(s[0], s[1]) for s in segments] == [(at(8), at(8, 10)), (at(20), at(20, 10))]


def test_Basic認証を照合する():
    good = "Basic " + base64.b64encode(b"chronofit:secret").decode()
    bad = "Basic " + base64.b64encode(b"chronofit:wrong").decode()
    assert owntracks.authorized(good, "chronofit", "secret")
    assert not owntracks.authorized(bad, "chronofit", "secret")
    assert not owntracks.authorized("Basic !!!", "chronofit", "secret")
    assert not owntracks.authorized(None, "chronofit", "secret")
    assert not owntracks.authorized(good, "chronofit", "")


def test_全インターフェースでは待ち受けない(tmp_path):
    with pytest.raises(ValueError):
        owntracks.serve("0.0.0.0", 0, tmp_path, "u", "t")


def _post(url, body, auth):
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Authorization": auth})
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read()


def test_受け口は認証を通った位置だけ保存する(tmp_path):
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 owntracks.make_handler(tmp_path, "chronofit", "secret"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/pub"
    body = json.dumps(point(at(12), 0.0, 0.0)).encode()
    try:
        good = "Basic " + base64.b64encode(b"chronofit:secret").decode()
        assert _post(url, body, good) == (200, b"[]")
        with pytest.raises(urllib.error.HTTPError) as denied:
            _post(url, body, "Basic " + base64.b64encode(b"chronofit:nope").decode())
        assert denied.value.code == 401
    finally:
        server.shutdown()
        server.server_close()
    assert len(owntracks.load_points("2026-09-27", tmp_path)) == 1


def test_setupは受け口の認証をgitの外に作る(tmp_path, monkeypatch, capsys):
    from chronofit import cli
    monkeypatch.setenv("CHRONOFIT_HOME", str(tmp_path))
    assert cli.main(["location", "setup", "--host", "127.0.0.1"]) == 0
    receiver = json.loads((tmp_path / "location" / "receiver.json").read_text(encoding="utf-8"))
    assert receiver["host"] == "127.0.0.1" and len(receiver["token"]) >= 24
    assert cli.main(["location", "setup"]) == 0
    again = json.loads((tmp_path / "location" / "receiver.json").read_text(encoding="utf-8"))
    assert again["token"] == receiver["token"]            # 作り直すのは --rotate のときだけ
