"""生活時間の実測から、日タイプごとの1日の持ち時間を出す。

    持ち時間 = 24h − 休む時間 − 起きていた離席
    休む時間 = max(夜の睡眠 + 二度寝, 目標の睡眠)

持ち時間を勘で置くと、寝不足の日も二度寝した日も同じ8時間として計画に入る。rollup が
持っている睡眠と離席から直に出せば、実際に机へ向かえた時間と揃う。

目標の睡眠を下限にするのは、削った睡眠を容量に数えないため。実測が目標より短い日も、
目標どおり寝たものとして引く。

PC の外でした習慣（楽器の練習等）は離席に入っているが、容量からは習慣の実測として別に
引かれる。二重に引かないよう、離席からは除いておく。
"""
from datetime import date as date_type, datetime

from chronofit.estimate import slack

DAY_HOURS = 24.0
MIN_DAYS = 3   # これより少ない日タイプは実測を使わない（1日の外れ値でそのまま決まる）


def target_hours(target):
    """`{"bed": "23:30", "wake": "07:30"}` を睡眠の時間（8.0）へ。読めなければ None。"""
    if not isinstance(target, dict):
        return None
    try:
        bed = datetime.strptime(str(target["bed"]), "%H:%M")
        wake = datetime.strptime(str(target["wake"]), "%H:%M")
    except (KeyError, ValueError):
        return None
    minutes = (wake - bed).total_seconds() / 60 % (24 * 60)
    return minutes / 60 or None


def _day(row, since, until):
    """持ち時間に使える行なら日付を返す。

    起床と就寝の両方が出ている日だけを使う。どちらかの夜が取れていないと、その夜の睡眠が
    離席に入り、持ち時間が大きく減って見える。
    """
    key = row.get("date")
    sleep, away = row.get("sleep_sec"), row.get("away_awake_sec")
    if not isinstance(key, str) or (since and key < since) or (until and key > until):
        return None
    if not isinstance(sleep, (int, float)) or not isinstance(away, (int, float)):
        return None
    if not row.get("wake") or not row.get("bed"):
        return None
    try:
        return date_type.fromisoformat(key)
    except ValueError:
        return None


def hours_by_type(rollups, target=None, habit_hours=None, weekend_days=(5, 6), workdays=(),
                  since=None, until=None, min_days=MIN_DAYS):
    """日タイプごとの持ち時間と、その内訳（すべて1日平均の時間）。

    `habit_hours` は日付 → PC の外でした習慣の時間。`min_days` 日に満たない日タイプは
    返さない。内訳の平均は足し引きが持ち時間と一致するよう、中央値ではなく平均を取る。
    """
    buckets = {}
    for row in rollups:
        day = _day(row, since, until)
        if not day:
            continue
        sleep = row["sleep_sec"] / 3600
        nap = row.get("nap_sec")
        nap = nap / 3600 if isinstance(nap, (int, float)) else 0.0
        habit = min((habit_hours or {}).get(row["date"], 0.0), row["away_awake_sec"] / 3600)
        away = row["away_awake_sec"] / 3600 - habit
        rest = max(sleep + nap, target or 0.0)
        kind = slack.day_type(day, weekend_days, workdays)
        buckets.setdefault(kind, []).append((sleep, nap, rest, away, habit))
    result = {}
    for kind, days in buckets.items():
        if len(days) < min_days:
            continue
        sleep, nap, rest, away, habit = (sum(column) / len(days) for column in zip(*days))
        result[kind] = {"hours": max(0.0, DAY_HOURS - rest - away), "days": len(days),
                        "sleep": sleep, "nap": nap, "rest": rest, "away": away,
                        "habit": habit}
    return result


def format_entry(kind, entry, target=None):
    """plan の頭に出す1行。どこから持ち時間が出たかを読めるようにする。"""
    measured = entry["sleep"] + entry["nap"]
    goal = f"・目標 {target:g}h" if target else ""
    nap = f" + 二度寝 {entry['nap']:.1f}h" if entry["nap"] else ""
    return (f"持ち時間 {kind} {entry['hours']:.1f}h = 24h − 休む {entry['rest']:.1f}h"
            f"（睡眠 {entry['sleep']:.1f}h{nap} = {measured:.1f}h{goal}）"
            f" − 離席 {entry['away']:.1f}h（実測 {entry['days']}日の平均）")
