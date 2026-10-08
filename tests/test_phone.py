"""スマホの使用状況（usagestats）の取り込みと、睡眠・アプリ別時間への畳み込みのテスト。

アプリ名・イベント列はすべて架空。
"""
from datetime import datetime, timedelta

from chronofit.model import phone as phone_model
from chronofit.sources import phone

DUMP = """\
user=0
  Last 24 hour events (timeRange="2026/9/26 12:00～2026/9/27 12:00" )
    time="2026-09-26 23:10:00" type=SCREEN_INTERACTIVE package=android flags=0x0
    time="2026-09-26 23:10:02" type=KEYGUARD_HIDDEN package=android flags=0x0
    time="2026-09-26 23:10:03" type=ACTIVITY_RESUMED package=com.example.chat class=com.example.chat.Main instanceId=1 taskRootPackage=com.example.chat taskRootClass=com.example.chat.Main flags=0x0
    time="2026-09-26 23:20:03" type=ACTIVITY_RESUMED package=com.example.video class=com.example.video.Player instanceId=2 taskRootPackage=com.example.video taskRootClass=com.example.video.Player flags=0x0
    time="2026-09-26 23:20:03" type=ACTIVITY_PAUSED package=com.example.chat class=com.example.chat.Main instanceId=1 flags=0x0
    time="2026-09-26 23:50:00" type=SCREEN_NON_INTERACTIVE package=android flags=0x0
    time="2026-09-26 23:50:00" type=KEYGUARD_SHOWN package=android flags=0x0
    time="2026-09-27 03:00:00" type=SCREEN_INTERACTIVE package=android flags=0x0
    time="2026-09-27 03:00:10" type=SCREEN_NON_INTERACTIVE package=android flags=0x0
    time="2026-09-27 03:00:10" type=NOTIFICATION_INTERRUPTION package=com.example.chat channelId=x flags=0x0
    time="2026-09-27 07:00:00" type=SCREEN_INTERACTIVE package=android flags=0x0
    time="2026-09-27 07:00:01" type=KEYGUARD_HIDDEN package=android flags=0x0
    time="2026-09-27 07:00:02" type=ACTIVITY_RESUMED package=com.example.chat class=com.example.chat.Main instanceId=1 taskRootPackage=com.example.chat taskRootClass=com.example.chat.Main flags=0x0
    time="2026-09-27 07:15:02" type=SCREEN_NON_INTERACTIVE package=android flags=0x0
  mDumpInitLastTimeSaved="2026-09-27 11:00:00"

  In-memory daily stats
    time="2026-09-25 01:00:00" type=ACTIVITY_RESUMED package=com.example.old class=x flags=0x0
"""


def at(hour, minute=0, day=27, second=0):
    return datetime(2026, 9, day, hour, minute, second).astimezone()


def test_直近24時間のイベントだけを読み_クラス名は捨てる():
    events = phone.parse_usagestats(DUMP)
    assert len(events) == 13          # 通知・日次統計の行は読まない
    assert events[0] == {"time": "2026-09-26T23:10:00", "type": "SCREEN_INTERACTIVE",
                         "package": "android"}
    assert all(set(event) == {"time", "type", "package"} for event in events)


def test_壊れた入力でも落ちない():
    assert phone.parse_usagestats("") == []
    assert phone.parse_usagestats("garbage\n  Last 24 hour events\n    time=\"x\" type=A") == []


def test_同じ窓を何度取り込んでも増えない(tmp_path):
    events = phone.parse_usagestats(DUMP)
    first = phone.append(events, tmp_path)
    again = phone.append(events, tmp_path)
    assert first == 13 and again == 0
    assert len(phone.load_events("2026-09-26", tmp_path)) == 7
    assert len(phone.load_events("2026-09-27", tmp_path)) == 6


def test_同じ秒に同じイベントが2回あれば2回とも残す(tmp_path):
    twice = [{"time": "2026-09-27T10:00:00", "type": "ACTIVITY_RESUMED",
              "package": "com.example.chat"}] * 2
    assert phone.append(twice, tmp_path) == 2
    assert phone.append(twice + twice[:1], tmp_path) == 1


def test_読み込みは壊れた行を飛ばす(tmp_path):
    phone.append(phone.parse_usagestats(DUMP), tmp_path)
    path = phone.events_dir(tmp_path) / "2026-09-27.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + "{broken\n[]\n", encoding="utf-8")
    assert len(phone.load_events("2026-09-27", tmp_path)) == 6


def test_保持期間を過ぎた日は消す(tmp_path):
    phone.append(phone.parse_usagestats(DUMP), tmp_path)
    removed = phone.purge(tmp_path, at(12).date() + timedelta(days=1), 1)
    assert removed == 1
    assert phone.load_events("2026-09-26", tmp_path) == []
    assert phone.load_events("2026-09-27", tmp_path)
    assert phone.purge(tmp_path, at(12).date(), None) == 0


def test_保持期間を過ぎた取得の記録も消す(tmp_path):
    phone.record_pull(tmp_path, at(12, day=25))
    phone.record_pull(tmp_path, at(12))
    phone.purge(tmp_path, at(12).date(), 1)
    assert phone.covered_spans(tmp_path) == [(at(12) - timedelta(hours=phone.WINDOW_HOURS), at(12))]


def test_画面が点いて解除されていた間だけを使用とする():
    usage = phone_model.usage_intervals(phone.parse_usagestats(DUMP))
    assert [(a.strftime("%d %H:%M"), b.strftime("%d %H:%M")) for a, b in usage] == [
        ("26 23:10", "26 23:50"), ("27 07:00", "27 07:15")]   # 3時の通知の点灯は数えない


def test_ロック画面の無い端末は画面ONから数える():
    events = [{"time": "2026-09-27T09:00:00", "type": "SCREEN_INTERACTIVE", "package": "android"},
              {"time": "2026-09-27T09:00:05", "type": "ACTIVITY_RESUMED", "package": "com.example.chat"},
              {"time": "2026-09-27T09:10:00", "type": "SCREEN_NON_INTERACTIVE", "package": "android"}]
    assert phone_model.usage_intervals(events) == [(at(9), at(9, 10))]


def test_終わりの無い使用は上限で打ち切る():
    events = [{"time": "2026-09-27T09:00:00", "type": "SCREEN_INTERACTIVE", "package": "android"},
              {"time": "2026-09-27T09:00:01", "type": "KEYGUARD_HIDDEN", "package": "android"}]
    usage = phone_model.usage_intervals(events, until=at(20))
    assert usage == [(at(9), at(9) + timedelta(seconds=phone_model.MAX_OPEN_USE_SEC))]


def test_点けたまま寝た夜は途切れた所で割り_朝の操作を残す():
    events = [{"time": "2026-09-27T00:30:00", "type": "SCREEN_INTERACTIVE", "package": "android"},
              {"time": "2026-09-27T00:30:05", "type": "KEYGUARD_HIDDEN", "package": "android"},
              {"time": "2026-09-27T00:40:00", "type": "ACTIVITY_RESUMED", "package": "com.example.game"},
              {"time": "2026-09-27T07:30:00", "type": "ACTIVITY_RESUMED", "package": "com.example.chat"},
              {"time": "2026-09-27T08:00:00", "type": "SCREEN_NON_INTERACTIVE", "package": "android"}]
    usage = phone_model.usage_intervals(events)
    assert usage == [(at(0, 30), at(0, 40)), (at(7, 30), at(8))]


def test_アプリ別の前面時間():
    apps = phone_model.app_seconds(phone.parse_usagestats(DUMP), at(0), at(0, day=28))
    assert apps == {"com.example.chat": 15 * 60}

    night = phone_model.app_seconds(phone.parse_usagestats(DUMP), at(0, day=26), at(0))
    assert night == {"com.example.chat": 10 * 60, "com.example.video": 30 * 60 - 3}


def test_同じアプリの画面移動で遅れて届く停止では切らない():
    def ev(clock, kind, package="com.example.chat"):
        return {"time": f"2026-09-27T{clock}", "type": kind, "package": package}
    events = [ev("09:00:00", "SCREEN_INTERACTIVE", "android"),
              ev("09:00:01", "KEYGUARD_HIDDEN", "android"),
              ev("09:00:02", "ACTIVITY_RESUMED"),
              ev("09:05:00", "ACTIVITY_PAUSED"),
              ev("09:05:00", "ACTIVITY_RESUMED"),
              ev("09:05:01", "ACTIVITY_STOPPED"),     # 古い画面の停止が後から届く
              ev("09:10:02", "SCREEN_NON_INTERACTIVE", "android")]
    assert phone_model.app_seconds(events, at(0), at(23)) == {"com.example.chat": 600.0}



def test_閉じる記録の無い最後のアプリも今の時刻まで数える():
    events = [{"time": "2026-09-27T09:00:00", "type": "SCREEN_INTERACTIVE", "package": "android"},
              {"time": "2026-09-27T09:00:00", "type": "ACTIVITY_RESUMED", "package": "com.example.chat"}]
    apps = phone_model.app_seconds(events, at(0), at(23), until=at(9, 10))
    assert apps == {"com.example.chat": 600.0}
    usage = phone_model.usage_intervals(events, until=at(9, 10))
    assert sum(apps.values()) == sum((b - a).total_seconds() for a, b in usage)

def test_アプリはカテゴリに丸め_無いものは未分類():
    rounded = phone_model.by_category({"com.example.chat": 600.0, "com.example.video": 300.0,
                                       "com.example.other": 60.0},
                                      {"com.example.chat": "連絡", "com.example.video": "動画"})
    assert rounded == {"連絡": 600.0, "動画": 300.0, phone_model.UNCATEGORIZED: 60.0}


def test_夜の無操作の最長区間を睡眠とする():
    usage = phone_model.usage_intervals(phone.parse_usagestats(DUMP))
    pc = [(at(21, day=26), at(22, 30, day=26)), (at(9), at(12))]
    sleep = phone_model.detect_sleep(at(0).date(), usage + pc, covered=[(at(12, day=26), at(12))])
    assert sleep == {"start": at(23, 50, day=26), "end": at(7), "sec": 7 * 3600 + 600}


def test_短すぎる無操作は睡眠にしない():
    activity = [(at(22, day=26), at(23, day=26)), (at(1), at(2)), (at(3), at(4)),
                (at(5), at(6)), (at(7), at(8))]
    assert phone_model.detect_sleep(at(0).date(), activity, covered=[(at(20, day=26), at(12))]) is None


def test_スマホの記録が夜を覆っていなければ睡眠を出さない():
    activity = [(at(22, day=26), at(23, day=26)), (at(8), at(9))]
    # PC だけ見ると23時〜8時は空いているが、スマホの記録が6時からしか無い
    assert phone_model.detect_sleep(at(0).date(), activity, covered=[(at(6), at(12))]) is None


def test_時間帯と最短の長さは設定で変えられる():
    activity = [(at(1), at(2)), (at(4), at(5))]
    settings = {"window": ["00:00", "06:00"], "min_hours": 1.5}
    sleep = phone_model.detect_sleep(at(0).date(), activity, covered=[(at(0), at(12))],
                                     settings=settings)
    assert sleep == {"start": at(2), "end": at(4), "sec": 7200}


def _summary():
    away = [{"start": at(0), "end": at(9), "sec": 9 * 3600.0, "kind": "away",
             "reason": "sleep"},
            {"start": at(12), "end": at(13), "sec": 3600.0, "kind": "away", "reason": "idle"}]
    return {"away_sec": 10 * 3600.0, "segments": away,
            "away_blocks": [{"start": at(0), "end": at(9), "sec": 9 * 3600.0},
                            {"start": at(12), "end": at(13), "sec": 3600.0}]}


def test_離席を睡眠とスマホとそれ以外に割る():
    summary = _summary()
    sleeps = [{"start": at(23, day=26), "end": at(7), "sec": 8 * 3600}]
    usage = [(at(7), at(7, 30)), (at(12, 10), at(12, 20))]
    phone_model.annotate(summary, sleeps, usage, {"連絡": 1500.0})
    assert summary["sleep_sec"] == 7 * 3600
    assert summary["away_awake_sec"] == 3 * 3600
    assert summary["phone_sec"] == 40 * 60
    assert summary["away_blocks"][0]["sleep_sec"] == 7 * 3600
    assert summary["away_blocks"][0]["phone_sec"] == 30 * 60
    assert summary["away_blocks"][1]["phone_sec"] == 10 * 60
    assert summary["phone_categories"] == {"連絡": 1500.0}


def test_スマホの記録が無い日は何も足さない():
    summary = _summary()
    phone_model.annotate(summary, [], [], {})
    assert "sleep_sec" not in summary and "away_awake_sec" not in summary


def test_起床と就寝の時刻():
    night_before = {"start": at(23, day=26), "end": at(7), "sec": 8 * 3600}
    night_after = {"start": at(0, 30, day=28), "end": at(8, day=28), "sec": 3600}
    times = phone_model.wake_and_bed(night_before, night_after)
    assert times == {"wake": at(7), "bed": at(0, 30, day=28)}
    assert phone_model.wake_and_bed(None, None) == {"wake": None, "bed": None}


def test_夜中に少しだけ触っても睡眠を割らない():
    activity = [(at(22, day=26), at(23, 30, day=26)), (at(3), at(3, 5)), (at(7), at(8))]
    sleep = phone_model.detect_sleep(at(0).date(), activity, covered=[(at(20, day=26), at(12))])
    assert (sleep["start"], sleep["end"]) == (at(23, 30, day=26), at(7))
    assert sleep["sec"] == 7.5 * 3600 - 300


def test_取得した時刻から記録の届いている範囲を出す(tmp_path):
    phone.record_pull(tmp_path, at(12))
    phone.record_pull(tmp_path, at(18))
    phone.record_pull(tmp_path, at(12, day=29))
    spans = phone.covered_spans(tmp_path)
    assert spans == [(at(12) - timedelta(hours=phone.WINDOW_HOURS), at(18)),
                     (at(12, day=29) - timedelta(hours=phone.WINDOW_HOURS), at(12, day=29))]


def test_設定した端末を優先し_形の怪しい指定は使わない(monkeypatch):
    calls = []

    def fake_adb(*args, adb="adb"):
        calls.append(args)
        if args[0] == "devices":
            return 0, "List of devices attached\nphone-a\tdevice\nphone-b\tdevice\nphone-c\toffline\n"
        return 0, DUMP

    monkeypatch.setattr(phone, "_adb", fake_adb)
    assert phone.fetch(["-bad", "phone-b"])[0] == "phone-b"
    assert all("-bad" not in call for call in calls)
    assert phone.fetch()[0] == "phone-a"


def test_読める端末が無ければ知らせる(monkeypatch):
    monkeypatch.setattr(phone, "_adb", lambda *args, adb="adb": (0, "List of devices attached\n"))
    import pytest
    with pytest.raises(RuntimeError):
        phone.fetch(["phone-a"])


def test_offlineの無線接続は切ってからつなぎ直す(monkeypatch):
    state = {"online": False}
    calls = []

    def fake_adb(*args, adb="adb"):
        calls.append(args[0])
        if args[0] == "devices":
            mark = "device" if state["online"] else "offline"
            return 0, f"List of devices attached\nhost.example:5555\t{mark}\n"
        if args[0] == "connect":
            state["online"] = "disconnect" in calls
        return 0, DUMP

    monkeypatch.setattr(phone, "_adb", fake_adb)
    assert phone.fetch(["host.example:5555"])[0] == "host.example:5555"
    assert calls.index("disconnect") < calls.index("connect")


class FakeLink:
    """phone-link の代わり。ensure に渡された notes へ書き足す。"""

    def __init__(self, configured=True, result="host.example:5555", notes=(), error=None):
        self._configured, self.result, self.notes, self.error = configured, result, notes, error
        self.calls = 0

    def configured(self):
        return self._configured

    def ensure(self, notes):
        self.calls += 1
        if self.error:
            raise self.error
        notes.extend(self.notes)
        return self.result


def test_phone_linkが無い_未設定なら何もしない(monkeypatch):
    assert phone.keep_link([]) is None
    link = FakeLink(configured=False)
    monkeypatch.setattr(phone, "phone_link", link)
    assert phone.keep_link([]) is None and link.calls == 0


def test_読む前にphone_linkで待受を保つ(monkeypatch):
    order = []
    link = FakeLink(notes=["reopened"])
    monkeypatch.setattr(phone, "phone_link", link)

    def fake_adb(*args, adb="adb"):
        order.append(args[0] if link.calls else "before-link")
        if args[0] == "devices":
            return 0, "List of devices attached\nhost.example:5555\tdevice\n"
        return 0, DUMP

    monkeypatch.setattr(phone, "_adb", fake_adb)
    notes = []
    assert phone.fetch(["host.example:5555"], notes=notes)[0] == "host.example:5555"
    assert notes == ["reopened"] and "before-link" not in order


def test_直せなかった理由を失敗の文に入れる(monkeypatch):
    import pytest
    monkeypatch.setattr(phone, "phone_link", FakeLink(result=None,
                                                      notes=["reopened", "端末に届かない"]))
    monkeypatch.setattr(phone, "_adb", lambda *args, adb="adb": (0, "List of devices attached\n"))
    with pytest.raises(RuntimeError, match="（端末に届かない）"):
        phone.fetch(["host.example:5555"])


def test_phone_linkがadbを起動できなくても取得は続ける(monkeypatch):
    monkeypatch.setattr(phone, "phone_link", FakeLink(error=RuntimeError("adb を実行できなかった")))
    notes = []
    assert phone.keep_link(notes) is None
    assert notes == ["adb を実行できなかった"]


def test_日の範囲を渡すと在席と判定された夜中も睡眠に数える():
    summary = _summary()
    summary["segments"][0]["end"] = at(4)          # 4時〜9時は PC が無入力の在席
    sleeps = [{"start": at(23, day=26), "end": at(7), "sec": 8 * 3600}]
    phone_model.annotate(summary, sleeps, [], {}, bounds=(at(0), at(0, day=28)))
    assert summary["sleep_sec"] == 7 * 3600
    assert summary["away_awake_sec"] == 10 * 3600 - 4 * 3600


def test_自動で動かしているアプリの前面は人の操作に数えない():
    events = phone.parse_usagestats(DUMP)
    auto = phone_model.automated_intervals(events, ["com.example.video"])
    assert auto == [(at(23, 20, day=26, second=3), at(23, 50, day=26))]
    human = phone_model.human_usage(events, ["com.example.video"])
    assert human == [(at(23, 10, day=26), at(23, 20, day=26, second=3)),
                     (at(7), at(7, 15, second=2))]
    assert phone_model.human_usage(events) == phone_model.usage_intervals(events)


def test_1晩の睡眠は夜中に触った時間を除く():
    night = {"start": at(0), "end": at(7), "sec": 7 * 3600.0}
    assert phone_model.night_sec(night, [(at(3), at(3, 10))]) == 7 * 3600 - 600
    assert phone_model.night_sec(None, []) == 0.0
