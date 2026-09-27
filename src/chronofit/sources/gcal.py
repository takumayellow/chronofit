"""Google カレンダーの予定を読む（読み取り専用）。

- 権限は `calendar.readonly` だけ。予定の作成・変更はしない（登録は人が決める）
- 残すのは時刻・件名・場所欄・ID だけ。説明文や参加者は残さない
- 終日の予定は時刻を持たないので読まない（位置と突き合わせられない）
- トークンは Gmail と同じく DPAPI で暗号化して git の外に置く。同意は Gmail とは別に取る
"""
import json
from datetime import datetime, timedelta, timezone

from . import gmail

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
TOKEN_FILE = "calendar-token.bin"
EVENTS = "events.json"
AHEAD_DAYS = 14              # 取得は先の予定も少し持つ（見積もりの相手にする）
JST = timezone(timedelta(hours=9))


def _parse(value):
    """ISO 8601 の時刻。Python 3.10 の fromisoformat は末尾の Z を読めないので置き換える。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_event(item):
    """API の予定1件を記録1件へ。終日・取り消し済み・時刻の読めないものは None。"""
    if item.get("status") == "cancelled":
        return None
    start, end = (item.get("start") or {}).get("dateTime"), (item.get("end") or {}).get("dateTime")
    if not (start and end and item.get("id")):
        return None
    try:
        begin, finish = _parse(start), _parse(end)
    except ValueError:
        return None
    if finish <= begin:
        return None
    row = {"id": item["id"], "start": begin.isoformat(), "end": finish.isoformat(),
           "title": (item.get("summary") or "").strip()}
    if item.get("location"):
        row["location"] = item["location"].strip()
    return row


def load(root):
    try:
        rows = json.loads((root / EVENTS).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and _stored_ok(row)]


def _stored_ok(row):
    """保存済みの1件が、時刻の読める予定か（手で壊れたファイルでも落ちない）。"""
    try:
        return (isinstance(row.get("id"), str) and isinstance(row.get("title"), str)
                and datetime.fromisoformat(row["start"]) < datetime.fromisoformat(row["end"]))
    except (KeyError, TypeError, ValueError):
        return False


def replace_window(old_rows, new_rows, since, until):
    """取り直した期間 [since, until) の予定を丸ごと入れ替える（消された予定を残さない）。"""
    def inside(row):
        return since <= _parse(row["start"]) < until
    kept = [row for row in old_rows if not inside(row)]
    return sorted(kept + list(new_rows), key=lambda row: _parse(row["start"]))


def save(root, rows):
    root.mkdir(parents=True, exist_ok=True)
    (root / EVENTS).write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n",
                               encoding="utf-8")


def fetch(root, since, until):
    """主カレンダーの [since, until) の予定を読む。繰り返しは1回ずつに展開する。"""
    service = gmail.build_service(root, "calendar", "v3", SCOPES, TOKEN_FILE,
                                  "chronofit calendar auth")
    rows, token = [], None
    while True:
        response = service.events().list(
            calendarId="primary", timeMin=since.isoformat(), timeMax=until.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=2500, pageToken=token).execute()
        rows += [row for row in map(parse_event, response.get("items", [])) if row]
        token = response.get("nextPageToken")
        if not token:
            return rows


def window(since_day, today=None):
    """取得する期間。`since_day`（YYYY-MM-DD）の0時から、今日の AHEAD_DAYS 日先まで。"""
    today = today or datetime.now(JST).date()
    since = datetime.fromisoformat(since_day).replace(tzinfo=JST)
    until = datetime.combine(today + timedelta(days=AHEAD_DAYS), datetime.min.time(), JST)
    return since, until
