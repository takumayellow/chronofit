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

    名前は origin の URL の末尾（GitHub のリポジトリ名）。origin が無ければフォルダ名。
    worktree・submodule は `.git` がファイルで別の場所の git ディレクトリを指すので、
    そちらの設定を読んで元のリポジトリの名前に揃える。
    """
    if not cwd:
        return UNKNOWN_REPO, False
    found = repo_root(cwd)
    if found:
        return found[0], True
    return Path(cwd).name or UNKNOWN_REPO, False


def repo_root(path):
    """`path` を含む git リポジトリの `(名前, 置き場所)`。git の外なら None。"""
    path = Path(path)
    for candidate in (path, *path.parents):
        marker = candidate / ".git"
        if marker.is_dir():
            return _origin_name(marker) or candidate.name, candidate
        if marker.is_file():
            gitdir = _linked_gitdir(marker)
            name = (_origin_name(_common_dir(gitdir)) or _worktree_folder(gitdir)
                    if gitdir else None)
            return name or candidate.name, candidate
    return None


def _linked_gitdir(marker):
    try:
        text = marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    gitdir = Path(text.removeprefix("gitdir:").strip())
    return gitdir if gitdir.is_absolute() else marker.parent / gitdir


def _common_dir(gitdir):
    """worktree の git ディレクトリは `commondir` で元の git ディレクトリを指す。"""
    try:
        common = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return gitdir
    common = Path(common)
    return common if common.is_absolute() else gitdir / common


def _worktree_folder(gitdir):
    return gitdir.parents[2].name if gitdir.parent.name == "worktrees" else None


_ORIGIN_URL = re.compile(r'\[remote "origin"\][^\[]*?^\s*url\s*=\s*(\S+)', re.M | re.S)


def _origin_name(gitdir):
    """git ディレクトリの設定にある origin の URL の末尾。読めなければ None。"""
    try:
        text = (gitdir / "config").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = _ORIGIN_URL.search(text)
    if not match:
        return None
    name = match.group(1).rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return name.removesuffix(".git") or None


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
