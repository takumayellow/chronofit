"""自分がマージした PR を、作成とマージの時刻つきで読む（`gh search` を読むだけ）。

作業時間の区切りに使う。時刻は GitHub の UTC をそのまま aware な datetime にする。
"""
from datetime import datetime

from .todo_issues import gh_json

LIMIT = 1000


def _time(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def parse(found):
    """検索結果 → `[{"repo", "number", "created", "merged"}]`。形の崩れた行は捨てる。"""
    rows = []
    for row in found or []:
        if not isinstance(row, dict):
            continue
        repo = (row.get("repository") or {}).get("name")
        created, merged = _time(row.get("createdAt")), _time(row.get("closedAt"))
        if repo and isinstance(row.get("number"), int) and created and merged:
            rows.append({"repo": repo, "number": row["number"],
                         "created": created, "merged": merged})
    return sorted(rows, key=lambda row: row["merged"])


def fetch_merged(since):
    """`since`（date）以降にマージした自分の PR。"""
    found = gh_json("search", "prs", "--author", "@me", "--merged",
                    "--merged-at", f">={since.isoformat()}",
                    "--json", "repository,number,createdAt,closedAt",
                    "--limit", str(LIMIT))
    if len(found or []) >= LIMIT:
        raise RuntimeError(f"マージした PR が {LIMIT} 件を超えた。--since を縮めて測り直す")
    return parse(found)
