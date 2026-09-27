"""外出の所要時間を、位置履歴の実測から組み立てる。

外出1回の時間を「店に何分」の1つの数で持つと、別の組み合わせの外出を予定するときに
使えない。登録済みの場所を節にして、次の2種類に分けて持つ:

- **区間**（場所 → 場所）: 移動は行為ではなく経路で決まる。向きごとに分布を持つ
- **滞在**（場所での時間）: 用事の種類で長さが違うので、分かるときは用事ごとに持つ
  （用事は決済の記録などから後で当てる。`activity` キー）

予定は「節の列」として与え、区間と滞在の実測を足して見積もる。見積もりには必ず根拠
（実測 n 件 / 逆向きを流用 / 距離からの仮置き / 指定）を付ける。
"""
import statistics
from datetime import timedelta

from ..sources import location

MIN_LEG_SEC = 2 * 60            # これより短い区間は、同じ場所の滞在が割れただけとみなす
MAX_LEG_SEC = 4 * 60 * 60       # これより長い空白は、途中で別の用事をしていたとみなす
MAX_DETOUR_SEC = 10 * 60        # 区間の途中にこれ以上の未登録の滞在があれば区間にしない
MIN_COVERAGE = 0.6             # 区間のこれ以上が移動などの記録で埋まっていなければ欠測とみなす
MERGE_GAP_SEC = 10 * 60         # 同じ場所の滞在がこの間隔で割れていたら1つに戻す
MIN_SAMPLES = 3                 # これ未満の件数は「実測」と呼ばない
WALK_M_PER_MIN = 70.0           # 仮置き: 徒歩（直線距離に 1.3 倍の迂回を見込む）
TRANSIT_M_PER_MIN = 400.0       # 仮置き: 電車（直線距離基準、乗り換え待ちを別に足す）
TRANSIT_OVERHEAD_MIN = 12.0
WALK_LIMIT_M = 1500.0
DETOUR = 1.3


def _named(stay):
    return stay.get("place") not in (None, location.MOVING, location.UNKNOWN)


def _sec(stay):
    return (location._parse_time(stay["end"])
            - location._parse_time(stay["start"])).total_seconds()


def visits(stays):
    """登録済みの場所での滞在。短い間隔で割れた同じ場所の滞在は1つに戻す。"""
    result = []
    for stay in sorted(stays, key=lambda s: location._parse_time(s["start"])):
        if not _named(stay):
            continue
        start, end = location._parse_time(stay["start"]), location._parse_time(stay["end"])
        last = result[-1] if result else None
        if (last and last["place"] == stay["place"]
                and (start - last["end"]).total_seconds() <= MERGE_GAP_SEC):
            last["end"] = max(last["end"], end)
            continue
        result.append({"place": stay["place"], "start": start, "end": end})
    return result


def legs(stays):
    """登録済みの場所から別の登録済みの場所への移動の実例。

    区間の途中に未登録の場所での長い滞在（寄り道）があれば数えない。寄り道込みの
    時間を区間の所要時間にすると、区間の分布が寄り道の有無で二山になる。
    記録の抜けた区間（電池切れ・圏外）も数えない。抜けは所要時間を長く見せる。
    """
    ordered = sorted(stays, key=lambda s: location._parse_time(s["start"]))
    result, last, detour, covered = [], None, 0.0, 0.0
    for stay in ordered:
        if not _named(stay):
            if stay.get("place") == location.UNKNOWN:
                detour = max(detour, _sec(stay))
            covered += _sec(stay)
            continue
        start = location._parse_time(stay["start"])
        if last and last["place"] != stay["place"]:
            gap = (start - last["end"]).total_seconds()
            if (MIN_LEG_SEC <= gap <= MAX_LEG_SEC and detour < MAX_DETOUR_SEC
                    and covered >= gap * MIN_COVERAGE):
                result.append({"from": last["place"], "to": stay["place"],
                               "start": last["end"], "end": start, "sec": gap})
        end = location._parse_time(stay["end"])
        if last and last["place"] == stay["place"] and end < last["end"]:
            continue
        last, detour, covered = {"place": stay["place"], "end": end}, 0.0, 0.0
    return result


def summarize(seconds):
    """所要時間（秒）の列を分で要約する。p80 は予定に置くときの「余裕込み」の値。"""
    values = sorted(seconds)
    if not values:
        return None
    minutes = [value / 60 for value in values]
    p80 = (statistics.quantiles(minutes, n=10, method="inclusive")[7]
           if len(minutes) > 1 else minutes[0])
    return {"n": len(minutes), "median": statistics.median(minutes), "p80": p80,
            "min": minutes[0], "max": minutes[-1]}


ANY = "*"                   # dwell_table で用事を問わない場所全体の分布の鍵

def _since(items, since):
    if not since:
        return items
    return [item for item in items if item["start"].date().isoformat() >= since]


def _days(start, end):
    """start から end までに触れる日付（ISO 文字列）の集合。"""
    days, day = set(), start.date()
    while day <= end.date():
        days.add(day.isoformat())
        day += timedelta(days=1)
    return days


def leg_table(leg_list, since=None, exclude_days=()):
    """(出発地, 到着地) → 要約。`exclude_days` の日は除く（見積もりの検証用）。"""
    groups = {}
    for leg in _since(leg_list, since):
        if leg["start"].date().isoformat() in exclude_days:
            continue
        groups.setdefault((leg["from"], leg["to"]), []).append(leg["sec"])
    return {key: summarize(values) for key, values in groups.items()}


def dwell_table(visit_list, since=None, exclude_days=(), max_hours=8):
    """(場所, 用事) → 滞在の要約。用事が分からない滞在は用事 None に入れ、
    用事を問わない場所全体の分布を (場所, ANY) に持つ。

    家のような一晩いる場所は予定の部品にならないので、`max_hours` を超える滞在は除く。
    """
    groups = {}
    for visit in _since(visit_list, since):
        if visit["start"].date().isoformat() in exclude_days:
            continue
        seconds = (visit["end"] - visit["start"]).total_seconds()
        if seconds > max_hours * 3600:
            continue
        groups.setdefault((visit["place"], visit.get("activity")), []).append(seconds)
        groups.setdefault((visit["place"], ANY), []).append(seconds)
    return {key: summarize(values) for key, values in groups.items()}


def guess_leg(origin, destination, places):
    """実測の無い区間を直線距離から仮置きする。場所が分からなければ None。"""
    known = {place["name"]: place for place in location.valid_places(places)}
    if origin not in known or destination not in known:
        return None
    meters = location.distance_m((known[origin]["lat"], known[origin]["lng"]),
                                 (known[destination]["lat"], known[destination]["lng"]))
    if meters <= WALK_LIMIT_M:
        minutes = meters * DETOUR / WALK_M_PER_MIN
    else:
        minutes = meters / TRANSIT_M_PER_MIN + TRANSIT_OVERHEAD_MIN
    return {"n": 0, "median": minutes, "p80": minutes * 1.25, "min": minutes, "max": minutes}


def leg_estimate(origin, destination, table, places=None):
    """区間1つの見積もりと根拠。"""
    found = table.get((origin, destination))
    if found and found["n"] >= MIN_SAMPLES:
        return found, f"実測{found['n']}件"
    reverse = table.get((destination, origin))
    if reverse and reverse["n"] >= MIN_SAMPLES:
        return reverse, f"逆向きを流用{reverse['n']}件"
    if found:
        return found, f"実測{found['n']}件（少ない）"
    guessed = guess_leg(origin, destination, places or [])
    if guessed:
        return guessed, "仮（直線距離から）"
    return None, "不明"


def dwell_estimate(place, activity, table):
    """滞在1つの見積もりと根拠。用事の実測が無ければ場所全体の実測に落とす。"""
    if activity:
        found = table.get((place, activity))
        if found and found["n"] >= MIN_SAMPLES:
            return found, f"実測{found['n']}件"
    pooled = table.get((place, ANY))
    if pooled and pooled["n"] >= MIN_SAMPLES:
        label = "用事を問わず" if activity else ""
        return pooled, f"{label}実測{pooled['n']}件"
    return None, "不明"


def plan(steps, legs_by_pair, dwell_by_key, places=None, depart=None):
    """節の列を見積もる。

    `steps` は [{"place", "minutes"(任意), "activity"(任意)}]。最初と最後の節は
    出発地と帰着地なので滞在を足さない。`minutes` を渡した節はその値（根拠「指定」）。
    返り値は行の列と合計。合計の p80 は各行の p80 の和で、ずれが同じ向きに重なった
    場合の上限寄りの値になる。
    """
    rows, clock = [], depart
    total = {"median": 0.0, "p80": 0.0}

    def push(kind, label, summary, basis):
        nonlocal clock
        median = summary["median"] if summary else None
        p80 = summary["p80"] if summary else None
        row = {"kind": kind, "label": label, "median": median, "p80": p80, "basis": basis}
        if clock is not None and median is None:
            clock = None
        if clock is not None:
            row["start"], clock = clock, clock + timedelta(minutes=median)
            row["end"] = clock
        if median is not None:
            total["median"] += median
            total["p80"] += p80
        rows.append(row)

    for index, step in enumerate(steps):
        if index:
            previous = steps[index - 1]["place"]
            summary, basis = leg_estimate(previous, step["place"], legs_by_pair, places)
            push("leg", f"{previous} → {step['place']}", summary, basis)
        if 0 < index < len(steps) - 1:
            if step.get("minutes") is not None:
                fixed = float(step["minutes"])
                summary, basis = {"median": fixed, "p80": fixed}, "指定"
            else:
                summary, basis = dwell_estimate(step["place"], step.get("activity"),
                                                dwell_by_key)
            label = step["place"] + (f"（{step['activity']}）" if step.get("activity") else "")
            push("stay", label, summary, basis)
    return {"rows": rows, "median": total["median"], "p80": total["p80"],
            "unknown": [row["label"] for row in rows if row["median"] is None]}


def parse_steps(tokens):
    """CLI の `家 駅前:45 駅前@食事 家` を節の列へ。"""
    steps = []
    for token in tokens:
        place, _, minutes = token.partition(":")
        place, _, activity = place.partition("@")
        step = {"place": place}
        if minutes:
            try:
                step["minutes"] = float(minutes)
            except ValueError:
                raise ValueError(f"「{token}」の :の後ろは分の数値") from None
            if step["minutes"] <= 0:
                raise ValueError(f"「{token}」の分は正の数")
        if activity:
            step["activity"] = activity
        steps.append(step)
    return steps


def outings(visit_list, homes):
    """家を出てから家へ戻るまでを1回の外出として切り出す（検証用）。

    返り値は [{"steps": [場所...], "visits": [...], "start", "end"}]。途中に家以外の
    節が無いもの・1日を超えるものは外出として扱わない。
    """
    result, current = [], None
    for visit in visit_list:
        if visit["place"] in homes:
            if current and len(current["visits"]) > 1:
                current["visits"].append(visit)
                span = (visit["start"] - current["visits"][0]["end"]).total_seconds()
                if span <= 24 * 3600:
                    result.append({"visits": current["visits"],
                                   "start": current["visits"][0]["end"],
                                   "end": visit["start"]})
            current = {"visits": [visit]}
        elif current:
            current["visits"].append(visit)
    return result


def backtest(stays, homes, places=None, since=None, label=None):
    """過去の外出を、その日を除いた実測だけで見積もり直して実績と比べる。

    2通り比べる: 滞在を実績で与えて区間だけ予測したもの（区間の精度）と、
    滞在も実測から予測したもの（外出全体の精度）。`label` は滞在の列に用事を付ける
    関数（決済から当てる）。付いた用事は、予定を立てるときの `@用事` の指定として使う。
    外出が日を跨ぐときは、触れた日を全部除いて見積もる（自分自身で自分を当てない）。
    """
    all_visits = label(visits(stays)) if label else visits(stays)
    all_legs = legs(stays)
    measured = {(leg["from"], leg["start"]) for leg in all_legs}
    results = []
    for trip in outings(all_visits, homes):
        day = trip["start"].date().isoformat()
        if since and day < since:
            continue
        excluded = _days(trip["visits"][0]["start"], trip["visits"][-1]["end"])
        legs_by_pair = leg_table(all_legs, exclude_days=excluded)
        dwell_by_key = dwell_table(all_visits, exclude_days=excluded)
        actual = (trip["end"] - trip["start"]).total_seconds() / 60
        steps_given = [{"place": v["place"]} for v in trip["visits"]]
        for step, visit in zip(steps_given[1:-1], trip["visits"][1:-1]):
            step["minutes"] = (visit["end"] - visit["start"]).total_seconds() / 60
        steps_free = [{"place": v["place"], **({"activity": v["activity"]}
                                                if v.get("activity") else {})}
                      for v in trip["visits"]]
        given = plan(steps_given, legs_by_pair, dwell_by_key, places)
        free = plan(steps_free, legs_by_pair, dwell_by_key, places)
        # 区間として数えられなかった間（寄り道・長い空白）は、区間の実測では説明できない
        detour = sum((b["start"] - a["end"]).total_seconds() / 60
                     for a, b in zip(trip["visits"], trip["visits"][1:])
                     if (a["place"], a["end"]) not in measured)
        results.append({"day": day, "start": trip["start"],
                        "route": [v["place"] for v in trip["visits"]],
                        "actual": actual, "detour": detour, "clean": detour == 0,
                        "legs_only": given, "full": free})
    return results


def score(results, key, clean_only=False):
    """予測の誤差の要約。見積もれない節を含む外出は数えない。

    `clean_only` は寄り道の無かった外出だけを数える（区間の実測で説明できる範囲の精度）。
    """
    usable = [r for r in results if not r[key]["unknown"] and (r["clean"] or not clean_only)]
    if not usable:
        return None
    errors = [r[key]["median"] - r["actual"] for r in usable]
    inside = sum(1 for r in usable if r["actual"] <= r[key]["p80"])
    return {"n": len(usable), "mae": statistics.mean(abs(e) for e in errors),
            "bias": statistics.median(errors), "within_p80": inside / len(usable)}
