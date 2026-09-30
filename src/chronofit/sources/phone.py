"""スマホの使用状況を PC から取りに行く（Android の usagestats を無線 adb で読む）。

スマホ側に常駐アプリを足さずに済む経路を選んだ。Android は画面の点灯・消灯、
ロック解除、アプリの前面化を usagestats に秒単位で残しており、`dumpsys usagestats`
で直近24時間ぶんを読める。PC から定期的に読みに行けば操作ゼロで貯まる。

- 1回の取得は直近24時間の窓なので、同じイベントを何度も読む。足すときに重複を除く
- 取りこぼしは「24時間以上 adb が届かなかった」ときだけ起きる（再起動で無線 adb が
  切れた場合など）。その間は空白として残し、埋めない
- 持つのは時刻・種類・パッケージ名だけ。画面のクラス名は捨てる（アプリ内のどの画面に
  いたかまでは要らない）。パッケージ名も git の外にしか置かない
"""
import json
import re
import subprocess
from collections import Counter
from datetime import date as date_type, datetime, timedelta

# 定期実行は窓の無い pythonw から走るので、コンソールの adb を素で起動すると
# そのたびに窓が開く（Windows 以外では 0 で何もしない）
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TIMEOUT = 60

# 使用区間とアプリ別の時間を出すのに要る種類だけを残す
KEEP_TYPES = frozenset({
    "SCREEN_INTERACTIVE", "SCREEN_NON_INTERACTIVE",
    "KEYGUARD_SHOWN", "KEYGUARD_HIDDEN",
    "ACTIVITY_RESUMED", "ACTIVITY_PAUSED", "ACTIVITY_STOPPED",
    "DEVICE_SHUTDOWN", "DEVICE_STARTUP",
})
_SECTION = "Last 24 hour events"
_EVENT = re.compile(r'^\s+time="(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})"'
                    r"\s+type=([A-Z_]+)\s+package=(\S+)")
# adb の -s に渡す前に形を確かめる（`-` で始まる値をフラグとして読ませない）
_SERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")


def events_dir(root):
    return root / "events"


def parse_usagestats(text):
    """`dumpsys usagestats` の出力から、直近24時間のイベントを読む。

    日次・週次の統計の節にも同じ形の行があるが、そちらは集計の途中経過なので読まない。
    返すのは {"time": "YYYY-MM-DDTHH:MM:SS"（端末の現地時刻）, "type", "package"} の列。
    """
    events, inside = [], False
    for line in (text or "").splitlines():
        if _SECTION in line:
            inside = True
            continue
        if not inside:
            continue
        match = _EVENT.match(line)
        if not match:
            # 節はイベント行が途切れたところで終わる（次の見出しは字下げが浅い）
            if line.strip() and not line.startswith("    "):
                inside = False
            continue
        day, clock, kind, package = match.groups()
        if kind in KEEP_TYPES:
            events.append({"time": f"{day}T{clock}", "type": kind, "package": package})
    return events


def _key(event):
    return (event["time"], event["type"], event["package"])


def _valid(event):
    if not isinstance(event, dict):
        return False
    try:
        date_type.fromisoformat(str(event["time"])[:10])
    except (KeyError, ValueError):
        return False
    return isinstance(event.get("type"), str) and isinstance(event.get("package"), str)


def append(events, root):
    """イベントを日ごとの JSONL に足す。既にあるものは足さない。返り値は足した件数。

    同じ秒に同じイベントが2回起きることがある（アプリ内で画面を2つ開いた等）ので、
    集合ではなく回数で比べる。同じ窓を読み直しても回数は変わらないので増えない。
    """
    by_day = {}
    for event in events:
        by_day.setdefault(event["time"][:10], []).append(event)
    added = 0
    for day, items in by_day.items():
        have = Counter(_key(event) for event in load_events(day, root))
        want = Counter()
        fresh = []
        for event in items:
            want[_key(event)] += 1
            if want[_key(event)] > have[_key(event)]:
                fresh.append(event)
        if not fresh:
            continue
        directory = events_dir(root)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{day}.jsonl").open("a", encoding="utf-8") as handle:
            for event in fresh:
                handle.write(json.dumps(_trim(event), ensure_ascii=False) + "\n")
        added += len(fresh)
    return added


def _trim(event):
    return {"time": event["time"], "type": event["type"], "package": event["package"]}


def load_events(day, root):
    """その日のイベントを時刻順に。壊れた行は飛ばす。"""
    path = events_dir(root) / f"{day}.jsonl"
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if _valid(item):
            events.append(_trim(item))
    # 同じ秒の中は書いた順（＝端末が記録した順）を保つ
    return sorted(events, key=lambda event: event["time"])


def events_around(day, root, before=1, after=1):
    """その日の前後を含むイベント。夜は日付をまたぐので前後の日も読む。"""
    current = date_type.fromisoformat(day)
    events = []
    for offset in range(-before, after + 1):
        events += load_events((current + timedelta(days=offset)).isoformat(), root)
    return events


def purge(root, today, retention_days=None):
    """保持期間を過ぎた日のイベントと取得の記録を消す。None なら何もしない。返り値は消した日数。"""
    if retention_days is None:
        return 0
    cutoff = (today - timedelta(days=retention_days)).isoformat()
    _purge_pulls(root, cutoff)
    directory = events_dir(root)
    if not directory.is_dir():
        return 0
    removed = 0
    for path in sorted(directory.glob("*.jsonl")):
        try:
            date_type.fromisoformat(path.stem)
        except ValueError:
            continue            # 日付でない名前のファイルは chronofit のものではない
        if path.stem < cutoff:
            path.unlink()
            removed += 1
    return removed


def _adb(*args, adb="adb"):
    try:
        done = subprocess.run([adb, *args], capture_output=True, timeout=TIMEOUT,
                              check=False, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"adb を実行できなかった: {type(error).__name__}") from error
    return done.returncode, done.stdout.decode("utf-8", errors="replace")


def connected_serials(adb="adb"):
    """いま adb から見えている端末（状態が device のもの）。"""
    code, out = _adb("devices", adb=adb)
    if code != 0:
        return []
    return [line.split()[0] for line in out.splitlines()[1:]
            if len(line.split()) >= 2 and line.split()[1] == "device"]


def fetch(serials=None, adb="adb"):
    """使える端末を1台選んで `dumpsys usagestats` を読む。返り値は (端末, 出力)。

    `serials` は設定に書いた候補（`host:port` なら先に `adb connect` する）。
    無ければ見えている端末のうち最初のもの。どれも読めなければ RuntimeError。
    """
    candidates = [s for s in (serials or []) if _SERIAL.fullmatch(str(s))]
    for serial in candidates:
        if ":" in serial and serial not in connected_serials(adb):
            # 端末が眠って offline のまま残った接続は、connect し直しても戻らない。
            # いったん切ってからつなぎ直す
            _adb("disconnect", serial, adb=adb)
            _adb("connect", serial, adb=adb)
    visible = connected_serials(adb)
    order = [s for s in candidates if s in visible] or ([] if candidates else visible)
    for serial in order:
        code, out = _adb("-s", serial, "shell", "dumpsys", "usagestats", adb=adb)
        if code == 0 and _SECTION in out:
            return serial, out
    raise RuntimeError("usagestats を読める端末が無い（無線 adb が切れていないか確かめる）")


WINDOW_HOURS = 23   # 1回の取得が覆うとみなす長さ。端末の窓（24時間）より少し短く見る


def pulls_path(root):
    return root / "pulls.jsonl"


def record_pull(root, moment):
    """取得できた時刻を残す。睡眠の判定で「記録が届いている範囲」を知るのに使う。"""
    root.mkdir(parents=True, exist_ok=True)
    with pulls_path(root).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"time": moment.isoformat(timespec="seconds")}) + "\n")


def _purge_pulls(root, cutoff):
    path = pulls_path(root)
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    moments = [_pull_time(line) for line in lines]
    kept = [line for line, moment in zip(lines, moments)
            if moment and moment.date().isoformat() >= cutoff]
    if len(kept) < len(lines):
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.writelines(line + "\n" for line in kept)


def _pull_time(line):
    try:
        return datetime.fromisoformat(json.loads(line)["time"]).astimezone()
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def covered_spans(root):
    """スマホの記録が届いている範囲の列（重なりはまとめる）。"""
    path = pulls_path(root)
    if not path.is_file():
        return []
    spans = []
    for line in path.read_text(encoding="utf-8").splitlines():
        moment = _pull_time(line)
        if moment:
            spans.append((moment - timedelta(hours=WINDOW_HOURS), moment))
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
