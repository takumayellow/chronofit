"""スマホで再生していたもの（media_session）の取り込みと、アプリ・タイトル別の内訳のテスト。

アプリ名・タイトル・チャンネルはすべて架空。
"""
from datetime import datetime, timedelta

from chronofit import cli_phone
from chronofit.model import phone as phone_model
from chronofit.sources import media
from chronofit.ui import report_projects

SESSIONS = """\
MEDIA SESSION SERVICE (dumpsys media_session)

  Sessions Stack - have 3 sessions:
    Player org.example.tube/Player (userId=0)
      ownerPid=100, ownerUid=10001, userId=0
      package=org.example.tube
      active=true
      state=PlaybackState {state=3, position=1000, buffered position=0, speed=1.0}
      metadata: size=7, description=猫の動画, その2, 架空チャンネル, null
    Music org.example.music/Music (userId=0)
      package=org.example.music
      active=false
      state=PlaybackState {state=2, position=5, buffered position=0, speed=0.0}
      metadata: size=4, description=止めた曲, 架空の歌手, null
    Call org.example.call/Call (userId=0)
      package=org.example.call
      active=false
      state=null
      metadata: null
"""


def at(minute, second=0):
    return datetime(2026, 1, 5, 10, minute, second).astimezone()


def ev(minute, kind, package="android"):
    return {"time": at(minute).replace(tzinfo=None).isoformat(timespec="seconds"),
            "type": kind, "package": package}


def sample(minute, title, package="org.example.tube", subtitle="架空チャンネル"):
    return {"time": at(minute), "package": package, "state": 3, "title": title,
            "subtitle": subtitle}


# 10:00 に画面を点けて動画アプリ、10:20 にチャット、10:30 に画面を消す
EVENTS = [ev(0, "SCREEN_INTERACTIVE"), ev(0, "KEYGUARD_HIDDEN"),
          ev(0, "ACTIVITY_RESUMED", "org.example.tube"),
          ev(20, "ACTIVITY_RESUMED", "org.example.chat"),
          ev(30, "SCREEN_NON_INTERACTIVE")]


class Test読み取り:
    def test_再生中でタイトルのあるセッションだけ_タイトルの読点は崩さない(self):
        assert media.parse_sessions(SESSIONS) == [
            {"package": "org.example.tube", "state": 3, "title": "猫の動画, その2",
             "subtitle": "架空チャンネル"}]

    def test_制御文字は空白にし_長すぎる題は切る(self):
        text = SESSIONS.replace("猫の動画, その2", "猫	" + "あ" * 300)
        title = media.parse_sessions(text)[0]["title"]
        assert title.startswith("猫 あ") and len(title) == media.MAX_TEXT

    def test_足して日ごとに読み戻す(self, tmp_path):
        rows = media.parse_sessions(SESSIONS)
        assert media.append(rows, at(5), tmp_path) == 1
        assert media.append([], at(6), tmp_path) == 0
        (tmp_path / "media" / f"{at(0).date()}.jsonl").open("a", encoding="utf-8").write("壊れ\n")
        loaded = media.load(at(0).date().isoformat(), tmp_path)
        assert [(row["time"], row["title"]) for row in loaded] == [(at(5), "猫の動画, その2")]

    def test_保持期間を過ぎた日は消す(self, tmp_path):
        row = {"package": "org.example.tube", "state": 3, "title": "猫", "subtitle": None}
        media.append([row], at(0) - timedelta(days=10), tmp_path)
        media.append([row], at(0), tmp_path)
        assert media.purge(tmp_path, at(0).date(), retention_days=7) == 1
        assert media.purge(tmp_path, at(0).date(), retention_days=None) == 0


class Test割り付け:
    def test_前面にいた時間を近い読み取りのタイトルへ_中点で分ける(self):
        samples = [sample(1, "猫"), sample(5, "犬"), sample(9, "犬"),
                   sample(25, "裏で流した", package="org.example.music")]
        played = phone_model.media_seconds(EVENTS, samples, at(0), at(59))
        # 1回は前後の中点まで、ただし 90 秒まで。間が空いた所は誰にも数えない
        # 猫: 10:00〜10:02:30 / 犬: 10:03:30〜10:06:30 と 10:07:30〜10:10:30
        assert played == {"org.example.tube": {("猫", "架空チャンネル"): 150.0,
                                               ("犬", "架空チャンネル"): 360.0}}

    def test_アプリが前面に無い時間はそのタイトルに数えない(self):
        samples = [sample(25, "チャット中に流れていた")]
        assert phone_model.media_seconds(EVENTS, samples, at(0), at(59)) == {}

    def test_呼び名は設定が優先_無ければ特徴的な部分(self):
        assert phone_model.app_label("org.example.tube", {"org.example.tube": "Tube"}) == "Tube"
        assert phone_model.app_label("com.example.android") == "example"
        assert phone_model.app_label("com.android") == "com.android"

    def test_カテゴリごとにアプリとタイトル_残りは記録なし(self):
        apps = {"org.example.tube": 1200.0, "org.example.chat": 600.0, "org.example.x": 90.0}
        played = {"org.example.tube": {("猫", "架空チャンネル"): 180.0, ("犬", None): 450.0}}
        out = phone_model.app_breakdown(apps, {"org.example.tube": "動画",
                                               "org.example.chat": "連絡"},
                                        {"org.example.tube": "Tube"}, played)
        assert out == {
            "動画": [{"task": f"Tube（{phone_model.NO_MEDIA}）", "active_sec": 570.0},
                     {"task": "Tube: 犬", "active_sec": 450.0},
                     {"task": "Tube: 猫（架空チャンネル）", "active_sec": 180.0}],
            "連絡": [{"task": "chat", "active_sec": 600.0}],
            phone_model.UNCATEGORIZED: [{"task": "x", "active_sec": 90.0}]}

    def test_タイトルの時間が前面時間を超えたら切り詰める(self):
        out = phone_model.app_breakdown({"org.example.tube": 100.0}, {}, None,
                                        {"org.example.tube": {("猫", None): 300.0}})
        assert out == {phone_model.UNCATEGORIZED: [{"task": "tube: 猫", "active_sec": 100.0}]}


class Test節:
    def test_スマホのカテゴリを開くとアプリと見ていたものが出る(self):
        html = report_projects.section(
            {}, {"projects": [], "activities": [], "unclassified": []},
            phone={"動画": 1200.0},
            phone_apps={"動画": [{"task": "Tube: 猫（架空チャンネル）", "active_sec": 900.0},
                                {"task": "Tube（記録なし）", "active_sec": 300.0}]})
        assert "アプリ・見ていたもの" in html
        assert "Tube: 猫（架空チャンネル）" in html and "15分" in html
        assert "（–）" not in html


class Test常駐:
    def test_読めた回は足し_失敗が続くと間隔を広げてログは1回だけ(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CHRONOFIT_HOME", str(tmp_path))
        answers = iter([SESSIONS, RuntimeError("切れた"), OSError("想定外"), SESSIONS])

        def fetch(serials=None, adb="adb"):
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        monkeypatch.setattr(media, "fetch", fetch)
        waits = []
        assert cli_phone.watch(settings={"phone": {}}, interval=60, rounds=4, sleep=waits.append) == 0
        assert waits == [60, 120, 240]
        log = (tmp_path / "phone" / "pull.log").read_text(encoding="utf-8")
        assert log.count("media failed") == 1 and log.count("media ok") == 1
        written = list((tmp_path / "phone" / "media").glob("*.jsonl"))
        assert sum(len(p.read_text(encoding="utf-8").splitlines()) for p in written) == 2
