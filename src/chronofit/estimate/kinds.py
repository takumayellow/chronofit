"""種別ごとの見積もり方。「何本目」を持つのは過去問だけではないし、全部が持つわけでもない。

学習曲線は強力だが、**繰り返しに意味がある種別にしか当たらない**。全部を曲線で
扱おうとすると、繰り返さないタスクに架空の逓減が付き、習慣に架空の締切が付く。
種別を3つのモードに分ける:

| モード | 何が起きるか | 単位と通し番号 | 見積もり方 |
|---|---|---|---|
| `series` | 繰り返すほど速くなる | 1年度分 / 1章 / 1本 / 1問 に通し番号 | 学習曲線（`curve.py`） |
| `oneoff` | 繰り返さない | 単位はあるが通し番号に意味が無い | 同種の実測の中央値と p80 |
| `habit`  | 時間が入力であって出力ではない | 単位が無い | 見積もらない。**実測ぶんを容量から引く** |

3つ目が「過去問以外をどう包括的に扱うか」の要になる。毎日2時間のピアノをタスクとして
積むと、需要が 60 時間ぶん膨らんだように見える。実際に起きているのは
**1日の使える時間が減ること**だけで、これは見積もりではなく容量の問題である。
需要側に置くと、他のタスクを削る判断が「ピアノを削るか」と混ざって決まらなくなる。

**ただし「毎日2時間」と宣言して引くことはしない。** 宣言した枠は、やらなかった日も
容量を食い、やり過ぎた日は容量を食わない。どちらも実際とずれる。引くのは
**直近の実測から出した1日あたりの平均**で、実測が無ければ**何も引かない**（引けないと
書く）。習慣も他のタスクと同じく、まず測る対象であって、先に確保する枠ではない。

`oneoff` を平均でなく**中央値と p80** で出すのは、一点物の分布が右に長い尾を引くため。
平均は数件の長引いた例に引きずられ、中央値だけでは楽観になる。両方を出して、
予定には p80 を、見通しには中央値を使う。
"""
SERIES = "series"
ONEOFF = "oneoff"
HABIT = "habit"
DEFAULT_MODE = SERIES

MODES = (SERIES, ONEOFF, HABIT)


def mode_for(kind, settings=None):
    """その種別のモード。設定に無ければ series とみなす。

    どの種別がどのモードかは個人固有（科目構成で変わる）なので、この
    リポジトリには表を持たず、利用側の `kind_modes` から引く。
    """
    table = (settings or {}).get("kind_modes") or {}
    mode = table.get(kind)
    return mode if mode in MODES else DEFAULT_MODE


def _sorted_hours(instances, kind, subject=None):
    return sorted(row["net_hours"] for row in instances
                  if row.get("net_hours", 0) > 0
                  and row.get("kind") == kind
                  and (subject is None or row.get("subject") == subject))


def percentile(values, q):
    """線形補間の分位点。件数が少ない前提なので、素直な実装で足りる。"""
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    position = q * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def estimate_oneoff(instances, subject, kind, assumed_hours=None):
    """一点物の見積もり。逓減を当てず、同種の実測の散らばりで出す。"""
    from . import curve   # 根拠ラベルを共有するためだけの参照

    for scope, note_suffix in ((subject, ""), (None, f"（{subject} の実測なし）")):
        hours = _sorted_hours(instances, kind, scope)
        if not hours:
            continue
        basis = curve.BASIS_MEASURED if scope else curve.BASIS_BORROWED
        return {
            "hours": percentile(hours, 0.8),
            "median": percentile(hours, 0.5),
            "basis": basis, "samples": len(hours), "b": None,
            "note": f"{kind} の実測 {len(hours)}件の p80{note_suffix}",
        }

    if assumed_hours is None:
        return {"hours": None, "median": None, "basis": curve.BASIS_ASSUMED,
                "samples": 0, "b": None,
                "note": f"{kind} は一点物で実績も無い。実測が要る"}
    return {"hours": assumed_hours, "median": assumed_hours,
            "basis": curve.BASIS_ASSUMED, "samples": 0, "b": None,
            "note": f"仮値。{kind} の実測なし"}


def habits(settings=None):
    """習慣として扱う対象の一覧。時間は持たない（実測から出すため）。

    `assumed_hours_per_day` は実測が貯まるまでの仮値としてだけ使い、
    仮値であることを basis に残す。
    """
    entries = (settings or {}).get("habits") or []
    return [h for h in entries if h.get("name")]


def _habit_rows(rows, entry):
    """その習慣に該当する実測行。既定では科目名で引く。"""
    subject = entry.get("subject") or entry["name"]
    kind = entry.get("kind")
    return [row for row in rows
            if row.get("subject") == subject
            and (kind is None or row.get("kind") == kind)
            and row.get("date")]


def _span_days(rows):
    """行が並んでいる日数。窓を指定されなかったときの分母にする。

    測った日だけを分母にすると「やった日の平均」になり、容量から引く値としては
    大きすぎる。最初と最後の間にある、やらなかった日も数える。
    """
    dates = sorted({row["date"] for row in rows if row.get("date")})
    if not dates:
        return 0
    from datetime import date as date_type
    return (date_type.fromisoformat(dates[-1]) - date_type.fromisoformat(dates[0])).days + 1


def habit_load(rows, settings=None, since=None, until=None):
    """習慣ごとの「1日あたり実際にかかっている時間」。

    分母は窓の日数（`since`〜`until`）にする。やらなかった日を分母から外すと
    「やった日の平均」になり、容量から引く値としては大きすぎる。知りたいのは
    **1日あたり平均でどれだけ持っていかれるか**なので、やらなかった日も数える。
    """
    from . import curve

    window = None
    if since and until:
        from datetime import date as date_type
        span = (date_type.fromisoformat(until) - date_type.fromisoformat(since)).days + 1
        window = max(1, span)

    result = []
    for entry in habits(settings):
        matched = [row for row in _habit_rows(rows, entry)
                   if not since or since <= row["date"] <= (until or "9999-99-99")]
        total = sum(row.get("net_hours", 0.0) for row in matched)
        days = window or _span_days(matched)
        if matched and days:
            result.append({"name": entry["name"], "hours_per_day": total / days,
                           "basis": curve.BASIS_MEASURED, "samples": len(matched),
                           "days": days})
            continue
        assumed = entry.get("assumed_hours_per_day")
        result.append({"name": entry["name"],
                       "hours_per_day": assumed if assumed else 0.0,
                       "basis": curve.BASIS_ASSUMED if assumed else None,
                       "samples": 0, "days": days})
    return result


def habit_hours_per_day(load=None):
    """容量から引く合計。実測も仮値も無ければ 0（＝勝手に引かない）。"""
    return sum(entry.get("hours_per_day") or 0.0 for entry in (load or []))


def available_hours(calendar_hours, load=None):
    """習慣ぶんを引いた、実際にタスクへ割り当てられる暦時間。

    引くのは**実測（無ければ明示した仮値）だけ**。宣言された枠では引かない。
    負にはしない。習慣が容量を食い尽くしているなら、それは「予定が組めない」ではなく
    「その日の持ち時間の置き方が現実と合っていない」ことを意味する。
    """
    return max(0.0, calendar_hours - habit_hours_per_day(load))
