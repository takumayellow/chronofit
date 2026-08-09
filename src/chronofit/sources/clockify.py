"""Clockify のエクスポート CSV を取り込む（過去データのバックフィル）。

2025-12-17〜2026-02-04 の手動タイマー運用で 188 entries / 201h が残っている。
死んだ計測だが、学習曲線の初期値としては今ある唯一の実測なので捨てない。

ただしそのままでは使えない。**4時間以上のエントリが10件あり、それだけで全体の34%
（67.4h）を占める**（10.2h「ブックマーク整理」等）。これは止め忘れであって作業時間では
ないので、捨てずに `contaminated` を立てて別集計にする。混ぜると学習曲線が壊れる。
"""
import csv
from datetime import datetime

# これを超える連続エントリはタイマー止め忘れとみなす。
# 根拠: 実測分布で 188件中 178件が4時間未満、超える10件は開始時刻から見て
# 休憩・食事・就寝をまたいでいる。
CONTAMINATION_HOURS = 4.0


def _parse_start(text):
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def read_normalized(csv_path, contamination_hours=CONTAMINATION_HOURS):
    """`task_entries_normalized.csv` を読み、エントリの辞書リストを返す。

    期待する列: group_id, task, start, duration_seconds
    """
    entries = []
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            start = _parse_start((row.get("start") or "").strip())
            if start is None:
                continue  # "no-date" 行はタイムラインに載せられない
            try:
                seconds = int(float(row["duration_seconds"]))
            except (KeyError, TypeError, ValueError):
                continue
            hours = seconds / 3600
            entries.append({
                "source": "clockify",
                "group_id": row.get("group_id", ""),
                "task": (row.get("task") or "").strip(),
                "start": start,
                "hours": hours,
                "contaminated": hours >= contamination_hours,
            })
    entries.sort(key=lambda item: item["start"])
    return entries


def summarize(entries):
    """取り込み結果の要約。バックフィルの健全性を目視するため。"""
    clean = [e for e in entries if not e["contaminated"]]
    dirty = [e for e in entries if e["contaminated"]]
    total = sum(e["hours"] for e in entries)
    return {
        "entries": len(entries),
        "clean_entries": len(clean),
        "contaminated_entries": len(dirty),
        "total_hours": total,
        "clean_hours": sum(e["hours"] for e in clean),
        "contaminated_hours": sum(e["hours"] for e in dirty),
        "contaminated_share": (sum(e["hours"] for e in dirty) / total) if total else 0.0,
        "first": entries[0]["start"] if entries else None,
        "last": entries[-1]["start"] if entries else None,
    }
