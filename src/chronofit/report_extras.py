"""日次レポートの上半分に載せるものを集める（GitHub の成果・作業の割り付け・予定）。

どれも外部（gh・会話ログ・カレンダー）に頼るので、1つ読めなくてもレポート全体は出す。
読めなかったものは `{"error": 理由}` にして、画面に理由を出す。
"""
from datetime import date as date_type, datetime, time
from pathlib import Path

from . import cli_outing, paths
from .model import rollup, work
from .sources import claude_sessions, github_done

_EXPECTED = (RuntimeError, OSError, ValueError, KeyError, TypeError)


def _guard(read):
    try:
        return read()
    except _EXPECTED as error:
        return {"error": f"{type(error).__name__}: {error}"}


def work_groups(day, settings, bounds=None, records=None):
    """その日のスパンを「リポジトリ × 作業」へ割り付ける。

    `bounds` と `records`（その区間に切った生レコード）を渡すと生活の1日で数える。
    """
    if records is None:
        records = rollup.read_day(paths.raw_dir() / f"{day}.jsonl")
    spans = [record for record in records if record["t"] == "span"]
    since = (bounds[0] if bounds else
             datetime.combine(date_type.fromisoformat(day), time()).astimezone())
    roots = [Path(root).expanduser() for root in settings.get("claude_dirs") or []] or None
    sessions = claude_sessions.load(since, roots)
    return work.by_repo(work.attribute(spans, sessions, claude_sessions.cwds_near))


def agenda(day, today=None):
    """`today` は生活の1日の今日（深夜0〜5時は暦の前日）。暦の今日も取り直す。"""
    if day in {today, date_type.today().isoformat()}:
        cli_outing.refresh_calendar(day)   # 今日消した・動かした予定を反映する
    checked, upcoming = cli_outing.agenda_for_day(day)
    return {"checked": checked, "upcoming": upcoming}


def gather(day, settings, bounds=None, records=None, today=None):
    return {
        "done": _guard(lambda: github_done.fetch(date_type.fromisoformat(day), bounds=bounds)),
        "work": _guard(lambda: work_groups(day, settings, bounds, records)),
        "agenda": _guard(lambda: agenda(day, today)),
    }
