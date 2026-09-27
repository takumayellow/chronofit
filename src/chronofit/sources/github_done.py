"""その日に片付いたもの（マージした PR・閉じた Issue・コミット）を GitHub の検索で読む。

時間の記録だけでは「何が終わったか」が分からない。終わったものは GitHub に残っているので、
日次レポートではそれを並べる。`gh search` を読むだけで、書き込みはしない。

日付の境目は現地時刻（JST なら +09:00）の0時。日付だけで検索すると UTC で切られ、
朝9時より前の作業が前の日に入る。
"""
from collections import Counter
from datetime import datetime, time, timedelta

from .todo_issues import gh_json

LIMIT = 200


def day_range(day, tz=None):
    """GitHub の検索に渡す `開始..終了`（現地時刻の0時から翌0時の1秒前）。"""
    tz = tz or datetime.now().astimezone().tzinfo
    start = datetime.combine(day, time(), tz)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return f"{start.isoformat()}..{end.isoformat()}"


def fetch(day, tz=None):
    """`{"prs": [...], "issues": [...], "commits": {リポジトリ名: 件数}, "errors": [...]}`。

    3つの検索は別々に失敗しうる（コミット検索だけ制限に当たる等）。1つ落ちても
    取れた分は捨てず、落ちた検索の理由を `errors` に残す。
    """
    window = day_range(day, tz)
    errors = []

    def search(kind, *args):
        try:
            return gh_json("search", kind, "--author", "@me", *args,
                           "--limit", str(LIMIT)) or []
        except (RuntimeError, OSError, ValueError) as error:
            errors.append(f"{kind}: {error}")
            return []

    prs = search("prs", "--merged-at", window, "--json", "repository,number,title")
    issues = search("issues", "--closed", window, "--json", "repository,number,title")
    commits = search("commits", "--author-date", window, "--json", "repository,sha")
    return {"prs": _rows(prs), "issues": _rows(issues), "commits": _count(commits),
            "errors": errors}


def _rows(found):
    rows = [{"repo": (row.get("repository") or {}).get("name") or "",
             "number": row.get("number"), "title": row.get("title") or ""}
            for row in found if isinstance(row, dict)]
    return sorted(rows, key=lambda row: (row["repo"], row["number"] or 0))


def _count(commits):
    return dict(Counter((row.get("repository") or {}).get("name") or ""
                        for row in commits if isinstance(row, dict)).most_common())
