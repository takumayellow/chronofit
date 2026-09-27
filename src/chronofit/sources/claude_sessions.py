"""Claude Code の会話ログから、作業名と作業ディレクトリの移り変わりを読む。

ターミナルのウィンドウタイトルには Claude が付けた作業名（`ai-title`）しか出ない。
同じ作業名を会話ログから引けば、その時刻にどのディレクトリ（リポジトリ）で
動いていたかが分かる。

- 読むのは作業名・時刻・作業ディレクトリだけ。発言の中身は読まない
- ログの置き場所は `~/.claude*/projects/*/*.jsonl`（設定ディレクトリを複数持てる）。
  設定 `claude_dirs` で差し替えられる
- 指定した日より前に更新が止まったファイルは開かない
"""
import json
from datetime import datetime, timezone
from pathlib import Path

TITLE_TYPES = {"ai-title": "aiTitle", "custom-title": "customTitle"}


def default_roots():
    return sorted(path for path in Path.home().glob(".claude*") if (path / "projects").is_dir())


def transcript_files(roots, since):
    """`since` 以降に更新された会話ログ。リンクで同じ実体を指すものは1つにする。"""
    seen, result = set(), []
    for root in roots:
        for path in Path(root, "projects").glob("*/*.jsonl"):
            try:
                real, mtime = path.resolve(), path.stat().st_mtime
            except OSError:
                continue
            if real in seen or mtime < since.timestamp():
                continue
            seen.add(real)
            result.append(path)
    return result


def _time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def read_session(path):
    """1本の会話ログから `{"titles": {...}, "marks": [(時刻, 作業ディレクトリ), ...]}`。"""
    titles, marks = set(), []
    try:
        lines = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return {"titles": titles, "marks": marks}
    with lines:
        for line in lines:
            if '"cwd"' not in line and 'Title"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            key = TITLE_TYPES.get(record.get("type"))
            if key and isinstance(record.get(key), str) and record[key].strip():
                titles.add(record[key].strip())
            moment = _time(record.get("timestamp")) if record.get("cwd") else None
            if moment and isinstance(record["cwd"], str):
                marks.append((moment, record["cwd"]))
    marks.sort(key=lambda mark: mark[0])
    return {"titles": titles, "marks": marks}


def index(sessions):
    """作業名 → その名前を持つ会話の列。"""
    by_title = {}
    for session in sessions:
        if not session["marks"]:
            continue
        for title in session["titles"]:
            by_title.setdefault(title, []).append(session)
    return by_title


FALLBACK_MAX_SEC = 3 * 3600   # これより離れた記録は別の作業とみなして使わない


def cwds_near(sessions, moment, window_sec=1800, fallback_max_sec=FALLBACK_MAX_SEC):
    """同じ作業名の会話で、`moment` の前後 `window_sec` 秒に記録された作業ディレクトリ。

    会話の途中で `cd` した先も記録に乗るので、1点で決めると揺れる。幅を取って多数決に回す。
    幅の中に記録が無ければ、`fallback_max_sec` 以内で一番近い記録1つを返す。
    会話ログは何日分も続くので、遠い記録まで拾うと別の日の作業ディレクトリに割り付けてしまう。
    """
    near, best = [], None
    for session in sessions:
        for mark_time, cwd in session["marks"]:
            gap = abs((mark_time - moment).total_seconds())
            if gap <= window_sec:
                near.append(cwd)
            if best is None or gap < best[0]:
                best = (gap, cwd)
    if near:
        return near
    return [best[1]] if best and best[0] <= fallback_max_sec else []


def load(since, roots=None):
    """`since` 以降に動いた会話を読んで、作業名の索引を返す。"""
    files = transcript_files(roots if roots is not None else default_roots(), since)
    return index(read_session(path) for path in files)
