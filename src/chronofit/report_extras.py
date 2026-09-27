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


def work_groups(day, settings):
    """その日のスパンを「リポジトリ × 作業」へ割り付ける。"""
    raw = paths.raw_dir() / f"{day}.jsonl"
    spans = [record for record in rollup.read_day(raw) if record["t"] == "span"]
    since = datetime.combine(date_type.fromisoformat(day), time()).astimezone()
    roots = [Path(root).expanduser() for root in settings.get("claude_dirs") or []] or None
    sessions = claude_sessions.load(since, roots)
    return work.by_repo(work.attribute(spans, sessions, claude_sessions.cwds_near))


def agenda(day):
    checked, upcoming = cli_outing.agenda_for_day(day)
    return {"checked": checked, "upcoming": upcoming}


def gather(day, settings):
    return {
        "done": _guard(lambda: github_done.fetch(date_type.fromisoformat(day))),
        "work": _guard(lambda: work_groups(day, settings)),
        "agenda": _guard(lambda: agenda(day)),
    }
