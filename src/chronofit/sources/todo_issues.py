"""GitHub の Issue（と Project の日付）を、やることの一覧の正本として読む。

一覧を手で書くと、終わったものを消し忘れた瞬間に「締切を過ぎた」と嘘をつく。
Issue なら閉じた時点で一覧から消えるので、終わったかどうかを二重に付けなくて済む。

- 読むのは `gh`（GitHub CLI）経由で、Project の項目と、そのリポジトリの開いている Issue だけ。
  書き込みはしない
- 1 Issue = 1 タスク（`count` は 1）。日付は Project の `Start` / `Target`、
  見積もりは `見積もり`（時間）から取る
- どの Project を読むかは設定 `todo_project`（`{"owner": ..., "number": ...}`）で渡す。
  ここには焼き込まない
"""
import json
import re
import subprocess

KIND = "Issue"
# 優先度ラベルの頭 → 一覧の優先度（S が一番上）
PRIORITIES = {"P1": "S", "P2": "A", "P3": "B"}
DEFAULT_SUBJECT = "todo"
TIMEOUT = 60
# gh の引数に渡す前に形を確かめる（`-` で始まる値をフラグとして読ませない）
_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
_PRIORITY = re.compile(r"(P[123])(?::|$)")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._][A-Za-z0-9._-]*")


def gh_json(*args):
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True,
                              encoding="utf-8", timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"gh を実行できなかった: {type(error).__name__}") from error
    if done.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1]} が失敗した（`gh auth status` を確かめる）")
    return json.loads(done.stdout or "null")


def fetch(owner, number, limit=500):
    """Project の項目と、項目のあるリポジトリで開いている Issue 番号を読む。"""
    if not _OWNER.fullmatch(str(owner)) or not str(number).isdigit():
        raise ValueError("todo_project の owner / number の形が違う")
    items = (gh_json("project", "item-list", str(number), "--owner", owner,
                 "--format", "json", "--limit", str(limit)) or {}).get("items") or []
    repos = {repo for repo in map(_repo, items) if repo and _REPO.fullmatch(repo)}
    open_numbers = {}
    for repo in sorted(repos):
        rows = gh_json("issue", "list", "-R", repo, "--state", "open",
                   "--json", "number", "--limit", str(limit)) or []
        open_numbers[repo] = {row["number"] for row in rows}
    return items, open_numbers


def _repo(item):
    url = (item.get("content") or {}).get("repository") or item.get("repository") or ""
    return url.removeprefix("https://github.com/").strip("/") or None


def to_tasks(items, open_numbers):
    """開いている Issue の項目だけを一覧の形にする。下書きや閉じた Issue は入れない。"""
    result = []
    # 複数のリポジトリにまたがる Project では、番号だけだと別の Issue と見分けられない。
    many = len({repo for repo in open_numbers if open_numbers[repo]}) > 1
    for item in items:
        content = item.get("content") or {}
        repo, number = _repo(item), content.get("number")
        if content.get("type") != "Issue" or number not in open_numbers.get(repo, set()):
            continue
        labels = item.get("labels") or []
        prefix = repo.rsplit("/", 1)[-1] if many else ""
        title = content.get("title") or item.get("title") or ""
        task = {"subject": _subject(labels), "kind": KIND, "count": 1,
                "target": f"{prefix}#{number} {title}".strip(),
                "issue": f"{repo}#{number}"}
        priority = next((PRIORITIES[_priority(label)] for label in labels if _priority(label)),
                        None)
        if priority:
            task["priority"] = priority
        if item.get("target"):
            task["due"] = item["target"]
        if item.get("start"):
            task["start"] = item["start"]
        hours = item.get("見積もり")
        if isinstance(hours, (int, float)) and hours > 0:
            task["assumed_hours"] = float(hours)
        result.append(task)
    return result


def _priority(label):
    """`P1:必須` / `P1` の形だけを優先度とみなす（`P2P` のような領域ラベルは違う）。"""
    match = _PRIORITY.match(label)
    return match.group(1) if match else None


def _subject(labels):
    """優先度以外の最初のラベル（領域）を科目にする。無ければ既定の名前。"""
    return next((label for label in labels if not _priority(label)), DEFAULT_SUBJECT)


def merge(current, issue_tasks):
    """手で書いたタスクは残し、Issue 由来のものは丸ごと取り替える（閉じたものが消える）。"""
    return [task for task in current if not task.get("issue")] + list(issue_tasks)
