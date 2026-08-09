"""需要（見積もり）を、実際に使える容量へ週単位で割り付ける。

ここが計測と予定の接点になる。要点は3つ:

- **同じ単位で比べる。** 見積もりは「入力のあった時間（net）」で出る。カレンダーの空きは
  暦時間なので、そのままでは比べられない。容量側に slack 率を掛けて net へ落としてから
  引き算する。率が無い日タイプは**換算せず、換算できないと報告する**。
- **習慣は容量から引く。** 毎日やるものを需要側に積むと「毎日2時間×33日＝66時間のタスク」
  という無意味な数字になる。引き算は `kinds.available_hours` が持つ。
- **割り付けは週単位。** 実測で「カレンダー通りには全く動けない」ことが分かっている以上、
  時刻まで割り付けた計画は作った瞬間に嘘になる。守れる粒度は週のゴールまで。

入りきらなかったものは黙って落とさない。落ちたものを名指しして返すのが、この関数の
一番大事な出力である（容量を超えているという事実こそが、計画で分かるべきことなので）。
"""
from datetime import date, timedelta

from ..estimate import curve, kinds, slack


def days_between(start, end):
    """start から end まで（両端を含む）の日付。"""
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def week_key(day):
    """ISO 週（月曜始まり）の識別子。表示にもそのまま使う。"""
    monday = day - timedelta(days=day.weekday())
    return monday.isoformat()


def capacity(days, hours_by_type, settings=None, ratios=None,
             weekend_days=(5, 6), workdays=()):
    """日ごとの容量を net 時間で。率が無ければ `net` は None にして暦のまま残す。"""
    result = []
    for day in days:
        kind = slack.day_type(day, weekend_days, workdays)
        calendar = hours_by_type.get(kind, hours_by_type.get("平日", 0.0))
        available = kinds.available_hours(calendar, settings)
        ratio = (ratios or {}).get(kind, {}).get("ratio")
        result.append({"date": day, "day_type": kind, "calendar": available,
                       "net": available * ratio if ratio else None,
                       "ratio": ratio})
    return result


def weekly_capacity(day_capacity):
    """日ごとの容量を週へ畳む。率の無い日が混ざる週は `net` を持たない。"""
    weeks = {}
    for entry in day_capacity:
        key = week_key(entry["date"])
        week = weeks.setdefault(key, {"week": key, "calendar": 0.0,
                                      "net": 0.0, "unknown_days": 0})
        week["calendar"] += entry["calendar"]
        if entry["net"] is None:
            week["unknown_days"] += 1
        else:
            week["net"] += entry["net"]
    return [weeks[key] for key in sorted(weeks)]


def expand(tasks, instances, settings=None):
    """タスク定義を1本ずつの需要へ展開する。

    `count` を2以上にすると、通し番号を進めながら並べる。**ここが学習曲線の効くところ**で、
    「過去問5本」を平らな5倍として置かないためにこの展開がある。
    """
    items = []
    for task in tasks:
        subject, kind = task.get("subject"), task.get("kind")
        if not subject or not kind:
            continue
        mode = kinds.mode_for(kind, settings)
        if mode == kinds.HABIT:
            continue   # 習慣は需要ではない。容量から引く側
        start = task.get("index") or curve.next_index(instances, subject, kind)
        for offset in range(max(1, int(task.get("count") or 1))):
            index = start + offset
            if mode == kinds.ONEOFF:
                estimate = kinds.estimate_oneoff(instances, subject, kind,
                                                 task.get("assumed_hours"))
            else:
                estimate = curve.estimate(instances, subject, kind, index,
                                          task.get("assumed_hours"))
            items.append({"subject": subject, "kind": kind, "mode": mode,
                          "target": task.get("target"), "index": index,
                          "due": task.get("due"),
                          "hours": estimate.get("hours"),
                          "basis": estimate.get("basis"),
                          "note": estimate.get("note")})
    return items


def order(items):
    """締切の早い順。締切の無いものは後ろへ、同じ締切なら軽い本数から。"""
    return sorted(items, key=lambda item: (item["due"] or "9999-99-99", item["index"]))


def allocate(items, weeks):
    """週の容量へ順に詰める。入らなかったものは overflow で返す。

    1本を週にまたいで割らない。またいで割った計画は、実行時にどちらの週の分なのかが
    分からなくなって守れない。
    """
    remaining = [{"week": week["week"], "left": week["net"], "items": []}
                 for week in weeks if week["net"] is not None]
    overflow = []
    for item in order(items):
        if item["hours"] is None:
            overflow.append({**item, "reason": "見積もりが出せない（実測も仮値も無い）"})
            continue
        for week in remaining:
            if item["hours"] <= week["left"]:
                week["left"] -= item["hours"]
                week["items"].append(item)
                break
        else:
            overflow.append({**item, "reason": "容量に入らない"})
    return remaining, overflow


def make(tasks, instances, hours_by_type, until, settings=None, ratios=None,
         today=None, weekend_days=(5, 6), workdays=()):
    """需要と容量を突き合わせて計画にする。"""
    start = today or date.today()
    days = days_between(start, until)
    day_capacity = capacity(days, hours_by_type, settings, ratios,
                            weekend_days, workdays)
    weeks = weekly_capacity(day_capacity)
    items = expand(tasks, instances, settings)
    placed, overflow = allocate(items, weeks)
    return {"weeks": weeks, "placed": placed, "overflow": overflow,
            "demand": sum(item["hours"] or 0.0 for item in items),
            "supply": sum(week["net"] for week in weeks),
            "unknown_days": sum(week["unknown_days"] for week in weeks)}
