"""スマホで再生していたもの（タイトル・チャンネル）を、再生中のセッションから読む。

usagestats が分かるのは「どのアプリが前にいたか」までで、動画アプリの中で何を見ていた
かは残らない。Android はメディアの再生セッションごとにタイトルと副題（動画なら
チャンネル）を持っていて、`dumpsys media_session` でその時点のものを読める。

- 読めるのは「読んだ瞬間に」セッションが持っているものだけ。過去へは遡れないので、
  短い間隔で読み続ける（`chronofit phone watch`）
- 残すのは時刻・パッケージ・再生状態・タイトル・副題だけ。git の外（`phone/media/`）に置く
"""
import json
import re
from datetime import date as date_type, datetime, timedelta

from . import phone

# PlaybackState の STATE_PLAYING と STATE_BUFFERING。止めたまま残っているセッションは
# 「いま見ているもの」ではないので残さない（一覧を眺めている時間をその動画に数えてしまう）
PLAYING = frozenset({3, 6})
_STATE = re.compile(r"^\s+state=PlaybackState \{state=(-?\d+)")
_DESCRIPTION = re.compile(r"^\s+metadata: size=\d+, description=(.*)$")


def media_dir(root):
    return root / "media"


MAX_TEXT = 200                  # 題・副題はこの長さで切る（端末から来る自由な文字列）


def _text(value):
    value = re.sub(r"[\x00-\x1f\x7f]", " ", value or "").strip()[:MAX_TEXT]
    return None if value in ("", "null") else value


def parse_sessions(text):
    """`dumpsys media_session` から、タイトルを持つセッションを読む。

    1つのセッションは `package=` の行から始まり、`state=PlaybackState {state=N, …}` と
    `metadata: size=N, description=タイトル, 副題, 説明` が続く。説明の3つは ", " で
    つながっているので、タイトルに ", " が入っても崩れないよう後ろから2つ切り離す。
    返すのは再生中で、タイトルのあるものだけの {"package", "state", "title", "subtitle"} の列。
    """
    sessions, current = [], None
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("package="):
            current = {"package": stripped[len("package="):], "state": None}
            sessions.append(current)
            continue
        if current is None:
            continue
        match = _STATE.match(line)
        if match:
            current["state"] = int(match.group(1))
            continue
        match = _DESCRIPTION.match(line)
        if match:
            parts = match.group(1).rsplit(", ", 2)
            parts += [None] * (3 - len(parts))
            current["title"], current["subtitle"] = _text(parts[0]), _text(parts[1])
    return [{"package": s["package"], "state": s["state"], "title": s.get("title"),
             "subtitle": s.get("subtitle")}
            for s in sessions if s.get("title") and s["state"] in PLAYING
            and re.fullmatch(r"[\w.]+", s["package"])]


def fetch(serials=None, adb="adb"):
    """使える端末の `dumpsys media_session` を読む。どれも読めなければ RuntimeError。"""
    for serial in phone.ready_serials(serials, adb):
        code, out = phone._adb("-s", serial, "shell", "dumpsys", "media_session", adb=adb)
        if code == 0 and "Sessions Stack" in out:
            return out
    raise RuntimeError("media_session を読める端末が無い（無線 adb が切れていないか確かめる）")


def append(sessions, moment, root):
    """読んだ時刻とともに日ごとの JSONL に足す。返り値は足した件数。"""
    if not sessions:
        return 0
    directory = media_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = moment.astimezone().isoformat(timespec="seconds")
    with (directory / f"{moment.astimezone().date()}.jsonl").open("a", encoding="utf-8") as handle:
        for session in sessions:
            handle.write(json.dumps({"time": stamp, **session}, ensure_ascii=False) + "\n")
    return len(sessions)


def _valid(item):
    if not isinstance(item, dict) or not isinstance(item.get("package"), str):
        return False
    if not isinstance(item.get("title"), str):
        return False
    try:
        item["time"] = datetime.fromisoformat(str(item["time"]))
    except (KeyError, ValueError):
        return False
    return item["time"].tzinfo is not None


def load(day, root, before=1, after=1):
    """その日の前後を含む読み取りを時刻順に。時刻は aware な datetime にして返す。"""
    current = date_type.fromisoformat(day)
    samples = []
    for offset in range(-before, after + 1):
        path = media_dir(root) / f"{current + timedelta(days=offset)}.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if _valid(item):
                samples.append(item)
    return sorted(samples, key=lambda item: item["time"])


def purge(root, today, retention_days=None):
    """保持期間を過ぎた日の読み取りを消す。返り値は消した日数。"""
    directory = media_dir(root)
    if retention_days is None or not directory.is_dir():
        return 0
    cutoff = (today - timedelta(days=retention_days)).isoformat()
    removed = 0
    for path in sorted(directory.glob("*.jsonl")):
        try:
            date_type.fromisoformat(path.stem)
        except ValueError:
            continue
        if path.stem < cutoff:
            path.unlink()
            removed += 1
    return removed
