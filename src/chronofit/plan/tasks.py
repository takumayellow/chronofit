"""やることの一覧そのもの。読み書きだけを持ち、進捗は持たない。

進捗をここに書かない理由: チェック欄を持たせると、**付け忘れた瞬間に嘘になる**。
「終わったか」は所要時間DB（`chronofit done` が書く）から数えれば、記録を二重に
付ける必要が無い。一覧は「何を何本やるつもりか」だけを持つ。

1タスクの形:

    {"subject": "確率統計2", "kind": "過去問", "count": 5,
     "due": "2026-09-20", "priority": "S", "target": "…", "assumed_hours": 3.0}

`subject` と `kind` が鍵。同じ組を2度書かない（後から書いたほうで上書きする）。
"""
import json


def key(task):
    return (task.get("subject"), task.get("kind"), task.get("target") or "")


def load(path):
    """一覧を読む。無ければ空。壊れていたら黙って空にせず、例外を上げる。"""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks") if isinstance(data, dict) else data
    return [t for t in (tasks or []) if t.get("subject") and t.get("kind")]


def save(path, task_list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"tasks": task_list}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def upsert(task_list, task):
    """同じ鍵があれば差し替え、無ければ足す。追加のたびに重複が増えないように。"""
    existing = {key(t): index for index, t in enumerate(task_list)}
    index = existing.get(key(task))
    if index is None:
        return task_list + [task]
    return [{**t, **task} if position == index else t
            for position, t in enumerate(task_list)]


def remove(task_list, subject, kind=None, target=None):
    """消した件数と残りを返す。何も消えなかったことを呼び出し側が言えるように。"""
    kept = [t for t in task_list
            if not (t.get("subject") == subject
                    and (kind is None or t.get("kind") == kind)
                    and (target is None or (t.get("target") or "") == target))]
    return len(task_list) - len(kept), kept
