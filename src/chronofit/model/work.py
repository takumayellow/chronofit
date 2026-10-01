"""在席中の時間を「リポジトリ × 作業」に割り付ける。

ターミナルのタイトルは `<tmux セッション> <窓>:<処理中マーク> <作業名>` の形で、
処理中マーク（✳ ◐ ◑ …）が変わるたびに別の行になる。これを作業名だけに揃え、
Claude の会話ログで同じ作業名を引いて、その時刻の作業ディレクトリからリポジトリを決める。

ブラウザで GitHub のページ（`… · owner/repo`）を見ていた時間もそのリポジトリへ寄せる。
どちらにも当たらない時間はここでは扱わない（アプリ別の表が別にある）。
"""
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

TERMINALS = {"WindowsTerminal.exe", "wezterm-gui.exe", "alacritty.exe", "mintty.exe"}
BROWSERS = {"vivaldi.exe", "chrome.exe", "msedge.exe", "firefox.exe"}
GITHUB_VIEW = "GitHub で確認"
UNKNOWN_REPO = "（不明）"

_TMUX_PREFIX = re.compile(r"^\S+ \d+:")
_MARKS = re.compile(r"^[\s⠀-⣿✳✻✽✶✢·◐◑◒◓*●•]+")
_GITHUB = re.compile(r"· ([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+)(?: - [^·]+)?$")


def task_name(title):
    """ターミナルのタイトルから tmux の窓番号と処理中マークを外した作業名。"""
    text = _TMUX_PREFIX.sub("", title or "", count=1)
    return _MARKS.sub("", text).strip()


def github_repo(title):
    """GitHub のページのタイトル末尾にある `owner/repo`。無ければ None。"""
    match = _GITHUB.search(title or "")
    return match.group(1).rsplit("/", 1)[-1] if match else None


def repo_name(cwd):
    """作業ディレクトリを含む git リポジトリの名前と、git の中だったか。

    worktree は `.git` がファイルで、元のリポジトリの `.git/worktrees/<名前>` を指すので、
    元のリポジトリの名前に揃える。
    """
    if not cwd:
        return UNKNOWN_REPO, False
    path = Path(cwd)
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        if marker.is_dir():
            return candidate.name, True
        if marker.is_file():
            return _worktree_origin(marker) or candidate.name, True
    return path.name or UNKNOWN_REPO, False


def _worktree_origin(marker):
    try:
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.removeprefix("gitdir:").strip())
    return gitdir.parents[2].name if gitdir.parent.name == "worktrees" else None


def attribute(spans, sessions, cwds_near, repo_of=repo_name):
    """スパン列を (リポジトリ, 作業) ごとの入力あり時間へまとめ、多い順に並べる。

    - `sessions`: 作業名 → 会話の列（`claude_sessions.load` の戻り値）
    - `cwds_near(会話の列, 時刻)`: その時刻の前後の作業ディレクトリ
    - `repo_of(作業ディレクトリ)`: (リポジトリ名, git の中だったか)
    """
    table, repo_cache = {}, {}
    for span in spans:
        seconds = float(span.get("active_sec") or 0)
        if seconds <= 0:
            continue
        key = span_key(span, sessions, cwds_near, repo_of, repo_cache)
        if not key:
            continue
        row = table.setdefault(key, {"repo": key[0], "task": key[1], "active_sec": 0.0,
                                     "first": span["start"], "last": span["end"]})
        row["active_sec"] += seconds
        row["first"] = min(row["first"], span["start"])
        row["last"] = max(row["last"], span["end"])
    return sorted(table.values(), key=lambda row: -row["active_sec"])


def span_key(span, sessions, cwds_near, repo_of, repo_cache):
    """スパン1本の (リポジトリ, 作業)。会話にも GitHub のページにも当たらなければ None。"""
    proc, title = span.get("proc"), span.get("title") or ""
    if proc in BROWSERS:
        repo = github_repo(title)
        return (repo, GITHUB_VIEW) if repo else None
    if proc not in TERMINALS:
        return None
    task = task_name(title)
    if task not in sessions:
        return None
    cwds = cwds_near(sessions[task], datetime.fromisoformat(span["start"]))
    return (vote(cwds, repo_of, repo_cache) or UNKNOWN_REPO, task)


def vote(cwds, repo_of, repo_cache):
    """作業ディレクトリの列から、いちばん多いリポジトリ。列が空なら None。"""
    votes = Counter()
    for cwd in cwds:
        if cwd not in repo_cache:
            repo_cache[cwd] = repo_of(cwd)
        name, in_git = repo_cache[cwd]
        # git の外（ホームや作業用の一時フォルダ）は、git の中の記録が1つも無いときだけ使う。
        votes[(in_git, name)] += 1
    if not votes:
        return None
    in_git = any(key[0] for key in votes)
    return max((key for key in votes if key[0] == in_git), key=lambda key: votes[key])[1]


def by_repo(rows):
    """リポジトリごとの合計と、その中の作業（多い順）。"""
    groups = {}
    for row in rows:
        group = groups.setdefault(row["repo"], {"repo": row["repo"], "active_sec": 0.0,
                                                "tasks": []})
        group["active_sec"] += row["active_sec"]
        group["tasks"].append(row)
    return sorted(groups.values(), key=lambda group: -group["active_sec"])
