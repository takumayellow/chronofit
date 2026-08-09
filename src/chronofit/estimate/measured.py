"""習慣の実測を、日ごとの行として集める。

習慣には「終わった」が無い。ピアノは何本目でもないし、完了もしない。だから
所要時間DB（`done` で積む実績表）には一生1行も入らない。にもかかわらず容量は
確実に持っていかれるので、**DBを経由せず生の実測から直接**日ごとの時間を出す。

経路は2つで、どちらも既にある層をそのまま使う:

- **オフPC**（ピアノ・紙の練習）はラベル。`subject`/`kind` を持つ選択肢を1打で
  選ぶだけで、追加の質問なしに毎日貯まる。
- **PC上**（AtCoder 等）は前景タイトル。`match` の正規表現で拾う。

両方持つ習慣は両方足す。同じ日に紙とPCの両方で進めた時間は、どちらも実際に
使った時間だからである。
"""
from datetime import date as date_type

from . import attribute, offpc


def _offpc_rows(label_root, entry, subject, since, until):
    if not label_root or not label_root.is_dir():
        return []
    days = offpc.collect(label_root, subject, entry.get("kind"), None, since, until)
    return [{"date": day["date"], "hours": day["sec"] / 3600.0} for day in days]


def _onpc_rows(raw_dir, entry, since, until):
    pattern = entry.get("match")
    if not pattern or not raw_dir or not raw_dir.is_dir():
        return []
    days = attribute.collect(raw_dir, pattern, since, until)
    return [{"date": day["date"], "hours": day["net_hours"]} for day in days]


def habit_rows(entries, label_root=None, raw_dir=None, since=None, until=None):
    """習慣ごとの日別実測を、`kinds.habit_load` が読める行の形で返す。

    行の形（`subject`/`kind`/`date`/`net_hours`）を所要時間DBと揃えているのは、
    集計側に「習慣は別経路で来る」ことを知らせないため。集計は行の出どころを
    問わない。
    """
    until = until or date_type.today().isoformat()
    rows = []
    for entry in entries:
        subject = entry.get("subject") or entry["name"]
        kind = entry.get("kind")
        by_date = {}
        for found in (_offpc_rows(label_root, entry, subject, since, until)
                      + _onpc_rows(raw_dir, entry, since, until)):
            by_date[found["date"]] = by_date.get(found["date"], 0.0) + found["hours"]
        rows.extend({"date": date, "subject": subject, "kind": kind,
                     "net_hours": hours}
                    for date, hours in sorted(by_date.items()))
    return rows
