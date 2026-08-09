"""所要時間DB。タスク1インスタンス = 1行。

ここが chronofit の出力であり、既存のどのツールも持っていないもの。市販の
トラッカーは「今週 12 時間」は出すが、「応用数学B の過去問の3本目」は出さない。
予定を組むのに要るのは後者のほうである。

JSONL で持つのは、追記しかしない・行単位で人が読める・壊れた行を捨てて続行できる、
の3つが欲しいため。
"""
import json

FIELDS = ("subject", "kind", "target", "index", "net_hours", "wall_hours",
          "sessions", "date", "source")


def default_path(data_root):
    return data_root / "instances.jsonl"


def load(path):
    """壊れた行は捨てて読む。1行の欠損で全履歴を失わない。"""
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("subject") and row.get("kind") and row.get("index"):
            rows.append(row)
    return rows


def append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({k: row.get(k) for k in FIELDS},
                                ensure_ascii=False) + "\n")
    return row


def make(subject, kind, target, index, net_hours, wall_hours=None,
         sessions=1, date=None, source="chronofit"):
    """1インスタンスを組み立てる。wall が無ければ net と同じとみなす。"""
    return {
        "subject": subject, "kind": kind, "target": target, "index": index,
        "net_hours": round(net_hours, 2),
        "wall_hours": round(wall_hours if wall_hours is not None else net_hours, 2),
        "sessions": sessions, "date": date, "source": source,
    }


def coverage(rows):
    """(科目, 種別) ごとに何件・何本目まであるか。見積もりの信頼度そのもの。"""
    table = {}
    for row in rows:
        key = (row["subject"], row["kind"])
        entry = table.setdefault(key, {"subject": row["subject"], "kind": row["kind"],
                                       "count": 0, "max_index": 0, "net_hours": 0.0})
        entry["count"] += 1
        entry["max_index"] = max(entry["max_index"], row["index"])
        entry["net_hours"] += row.get("net_hours", 0.0)
    return sorted(table.values(), key=lambda e: (-e["count"], e["subject"]))
