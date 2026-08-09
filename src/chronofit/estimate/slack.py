"""slack 率 — 見積もりを暦時間へ直すための実測定数。

見積もりが外れる原因は2つある。「作業そのものが重かった」のと「作業以外に時間が
溶けた」のである。この2つを1つのバッファに混ぜて積むと、外れたときにどちらが
原因か分からず、次の見積もりが改善しない。

    必要な暦時間 = est_net ÷ slack_ratio(日タイプ)

分けて持てば、est_net が外れたのか slack 率が外れたのかが毎回わかる。
日タイプごとに分けるのは、平日夜と休日で率が体感2倍近く違うため。
"""
import json
from datetime import date as date_type

MIN_WALL_HOURS = 1.0   # これ未満しか在席していない日は率が暴れるので混ぜない


def day_type(day, weekend_days=(5, 6), workdays=()):
    """日タイプ。まず出社日、次に週末、残りが平日。

    出社日を先に見るのは、出社日は週末に当たっても平日とも週末とも違う
    使い方になるため。
    """
    key = day.isoformat()
    if key in set(workdays):
        return "出社"
    return "休日" if day.weekday() in tuple(weekend_days) else "平日"


def load_rollups(rollup_dir):
    """日次ロールアップ JSON を読む。集計値だけなので生タイトルは触らない。"""
    rows = []
    if not rollup_dir.is_dir():
        return rows
    for path in sorted(rollup_dir.glob("????-??-??.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if row.get("date"):
            rows.append(row)
    return rows


def ratios(rollups, weekend_days=(5, 6), workdays=(), min_wall_hours=MIN_WALL_HOURS):
    """日タイプごとの slack 率。

    率の平均ではなく **合計 net ÷ 合計 wall** を取る。日ごとの率を平均すると、
    30分しか触らなかった日と8時間の日が同じ重みになってしまう。
    """
    buckets = {}
    for row in rollups:
        wall = row.get("wall_sec", 0) / 3600.0
        if wall < min_wall_hours:
            continue
        try:
            day = date_type.fromisoformat(row["date"])
        except (ValueError, KeyError):
            continue
        bucket = buckets.setdefault(day_type(day, weekend_days, workdays),
                                    {"wall_hours": 0.0, "net_hours": 0.0, "days": 0})
        bucket["wall_hours"] += wall
        bucket["net_hours"] += row.get("net_sec", 0) / 3600.0
        bucket["days"] += 1

    for bucket in buckets.values():
        bucket["ratio"] = (bucket["net_hours"] / bucket["wall_hours"]
                           if bucket["wall_hours"] else None)
    return buckets


def calendar_hours(net_hours, ratio):
    """net 時間を暦時間へ。率が無ければ換算しない（勝手に仮定を置かない）。"""
    if net_hours is None or not ratio:
        return None
    return net_hours / ratio


def format_ratios(buckets):
    if not buckets:
        return "slack 率を出せる日がまだ無い（在席1時間以上の日が要る）。"
    lines = ["日タイプ   日数   在席      入力あり   slack率"]
    for name in ("平日", "休日", "出社"):
        bucket = buckets.get(name)
        if not bucket:
            continue
        lines.append(f"{name:8} {bucket['days']:4}日 {bucket['wall_hours']:7.1f}h "
                     f"{bucket['net_hours']:8.1f}h  {bucket['ratio']:6.0%}")
    return "\n".join(lines)
