"""日次レポートの上半分に載せるものを集める（GitHub の成果・作業の割り付け・外出・予定）。

どれも外部（gh・会話ログ・カレンダー）に頼るので、1つ読めなくてもレポート全体は出す。
読めなかったものは `{"error": 理由}` にして、画面に理由を出す。
"""
import os
from datetime import date as date_type, datetime, time, timedelta, timezone
from pathlib import Path

from . import cli_outing, paths
from .model import activity, rollup
from .sources import browser, claude_sessions, gcal, github_done, history, location

_EXPECTED = (RuntimeError, OSError, ValueError, KeyError, TypeError)


def _guard(read):
    try:
        return read()
    except _EXPECTED as error:
        return {"error": f"{type(error).__name__}: {error}"}


def _visits(since):
    """`since` の少し前からの閲覧履歴（ブラウザのタイトルから URL を引くため）。"""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    utc = (since - timedelta(hours=6)).astimezone(timezone.utc).replace(tzinfo=None)
    return history.collect(browser.find_histories(Path(local)), utc)


def work_groups(day, settings, bounds=None, records=None):
    """その日のスパンを全部、プロジェクト・活動・未分類へ割り付ける（`activity.attribute`）。

    `bounds` と `records`（その区間に切った生レコード）を渡すと生活の1日で数える。
    """
    if records is None:
        records = rollup.read_day(paths.raw_dir() / f"{day}.jsonl")
    spans = [record for record in records if record["t"] == "span"]
    since = (bounds[0] if bounds else
             datetime.combine(date_type.fromisoformat(day), time()).astimezone())
    roots = [Path(root).expanduser() for root in settings.get("claude_dirs") or []] or None
    sessions = claude_sessions.load(since, roots)
    browse_rules = (settings.get("title_rules") or []) + (settings.get("url_rules") or [])
    return activity.attribute(spans, sessions, claude_sessions.cwds_near,
                              visits=_visits(since),
                              rules=settings.get("activity_rules") or [],
                              browse_rules=browse_rules)


def outings(day, bounds=None, phone_spans=()):
    """その日の外出（家と移動を除く滞在）。"""
    if bounds is None:
        start = datetime.combine(date_type.fromisoformat(day), time()).astimezone()
        bounds = (start, start + timedelta(days=1))
    stays, places = cli_outing._stays_and_places()
    visits = cli_outing._labeled_visits(stays)
    skip = cli_outing._homes(places) | {location.MOVING}
    events = gcal.load(paths.calendar_dir())
    return activity.outings(visits, events, bounds, skip, phone_spans)


def agenda(day, today=None):
    """`today` は生活の1日の今日（深夜0〜5時は暦の前日）。暦の今日も取り直す。"""
    if day in {today, date_type.today().isoformat()}:
        cli_outing.refresh_calendar(day)   # 今日消した・動かした予定を反映する
    checked, upcoming = cli_outing.agenda_for_day(day)
    return {"checked": checked, "upcoming": upcoming}


def gather(day, settings, bounds=None, records=None, today=None, phone_spans=()):
    return {
        "done": _guard(lambda: github_done.fetch(date_type.fromisoformat(day), bounds=bounds)),
        "work": _guard(lambda: work_groups(day, settings, bounds, records)),
        "outings": _guard(lambda: outings(day, bounds, phone_spans)),
        "agenda": _guard(lambda: agenda(day, today)),
    }
