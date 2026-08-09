"""種別ごとの見積もり方。「何本目」を持つのは過去問だけではないし、全部が持つわけでもない。

学習曲線は強力だが、**繰り返しに意味がある種別にしか当たらない**。全部を曲線で
扱おうとすると、繰り返さないタスクに架空の逓減が付き、習慣に架空の締切が付く。
種別を3つのモードに分ける:

| モード | 何が起きるか | 単位と通し番号 | 見積もり方 |
|---|---|---|---|
| `series` | 繰り返すほど速くなる | 1年度分 / 1章 / 1本 / 1問 に通し番号 | 学習曲線（`curve.py`） |
| `oneoff` | 繰り返さない | 単位はあるが通し番号に意味が無い | 同種の実測の中央値と p80 |
| `habit`  | 時間が入力であって出力ではない | 単位が無い | 見積もらない。**容量から先に引く** |

3つ目が「過去問以外をどう包括的に扱うか」の要になる。毎日2時間のピアノをタスクとして
積むと、需要が 60 時間ぶん膨らんだように見える。実際に起きているのは
**1日の使える時間が2時間減ること**だけで、これは見積もりではなく容量の問題である。
需要側に置くと、他のタスクを削る判断が「ピアノを削るか」と混ざって決まらなくなる。

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
    """毎日決まって時間を取るもの。見積もりではなく容量側の定数。"""
    entries = (settings or {}).get("habits") or []
    return [h for h in entries
            if h.get("name") and isinstance(h.get("hours_per_day"), (int, float))]


def habit_hours_per_day(settings=None):
    return sum(h["hours_per_day"] for h in habits(settings))


def available_hours(calendar_hours, settings=None):
    """習慣を引いた、実際にタスクへ割り当てられる暦時間。

    負にはしない。習慣が容量を食い尽くしているなら、それは「予定が組めない」
    ではなく「習慣の設定が現実と合っていない」ことを意味する。
    """
    return max(0.0, calendar_hours - habit_hours_per_day(settings))
