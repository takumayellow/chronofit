"""スマホのイベント列を、使用区間・アプリ別の時間・睡眠へ畳み、離席を割る。

PC だけを見ていると、夜の離席は「寝ていた」「寝る前にスマホを触っていた」「朝 PC の
前に座るまで起きていた」の区別が付かず、1日の離席が 16〜19h という値になる。
ここではスマホの操作を足して、PC もスマホも触っていない夜の最長区間を睡眠とする。

- 閾値（夜の時間帯・最短の長さ）は利用側の設定で渡す。ロジックに個人の生活を焼き込まない
- スマホの記録が届いていない時間を「無操作」と読まない。記録が覆っている範囲の中の
  空白だけを睡眠の候補にする（記録が無いのに寝ていたことにすると、嘘の実測になる）
- アプリ名はカテゴリへ丸めてから外へ出す。対応表は利用側の設定にある
"""
from datetime import datetime, time, timedelta

MAX_USE_SEC = 6 * 3600          # 閉じる記録を欠いたまま、これより長く続いた使用は信じない
MAX_OPEN_USE_SEC = 30 * 60      # 終わりが分からない使用は、ここまでで打ち切る
UNCATEGORIZED = "未分類"
SLEEP_DEFAULTS = {
    # 夜の時間帯。開始が終了より遅ければ、開始は前の日の時刻
    "window": ["20:00", "14:00"],
    "min_hours": 3.0,           # これより短い無操作は睡眠にしない（仮眠・外出と区別できない）
    "bridge_minutes": 15.0,     # 夜中にこれ以下だけスマホを触っても、睡眠を2つに割らない
    "bridge_min_gap_hours": 1.0,  # 割らずにつなぐのは、両側がこれ以上の無操作のときだけ
}

_OPENERS = {"KEYGUARD_HIDDEN", "ACTIVITY_RESUMED"}
_CLOSERS = {"SCREEN_NON_INTERACTIVE", "KEYGUARD_SHOWN", "DEVICE_SHUTDOWN", "DEVICE_STARTUP"}
_APP_CLOSERS = {"SCREEN_NON_INTERACTIVE", "KEYGUARD_SHOWN", "DEVICE_SHUTDOWN",
                "DEVICE_STARTUP"}


def _moment(event):
    return datetime.fromisoformat(event["time"]).astimezone()


def _close(start, end, moments=()):
    """1回の使用を区間にする。閉じる記録を欠いて長すぎる使用は、イベントのあった所だけ残す。

    画面を点けたまま寝ると（自動プレイなど）、点いた時刻から朝に消すまでが1回の使用に
    なる。そのときは中のイベントが `MAX_OPEN_USE_SEC` より長く途切れた所で割り、
    途切れた間は使っていないとみなす（朝に手で触った分を落とさない）。
    """
    if (end - start).total_seconds() <= MAX_USE_SEC:
        return [(start, end)]
    gap = timedelta(seconds=MAX_OPEN_USE_SEC)
    edges = [start] + sorted(m for m in moments if start < m < end) + [end]
    pieces, since = [], start
    for before, after in zip(edges, edges[1:]):
        if after - before > gap:
            pieces.append((since, before if before > since else since + gap))
            since = after
    pieces.append((since, end))
    return [(a, b) for a, b in pieces if b > a]


def usage_intervals(events, until=None):
    """画面が点いていて、ロックが外れていた（か、アプリが前面に出た）区間の列。

    通知で画面が点いただけの時間は数えない。使用は画面が点いた時刻から数える
    （顔認証などで解除するまでの数秒も、手に取っている時間なので）。
    """
    result = []
    screen_on = None        # 画面が点いた時刻
    confirmed = False       # 点いている間に解除かアプリの前面化があったか
    moments = []            # 点いている間のイベントの時刻
    for event in events:
        moment, kind = _moment(event), event["type"]
        if screen_on is not None:
            moments.append(moment)
        if kind == "SCREEN_INTERACTIVE":
            if screen_on is None:
                screen_on, confirmed, moments = moment, False, []
        elif kind in _OPENERS:
            if screen_on is not None:
                confirmed = True
        elif kind in _CLOSERS:
            if screen_on is not None and confirmed and moment > screen_on:
                result += _close(screen_on, moment, moments)
            moments = []
            if kind == "KEYGUARD_SHOWN":
                # ロックをかけて画面はまだ点いている。次の解除で使用が再開する
                screen_on = moment if screen_on is not None else None
                confirmed = False
            else:
                screen_on, confirmed = None, False
    if screen_on is not None and confirmed:
        end = screen_on + timedelta(seconds=MAX_OPEN_USE_SEC)
        result.append((screen_on, min(end, until) if until else end))
    return [(a, b) for a, b in result if b > a]


def _intersect(xs, ys):
    """2つの区間列の共通部分。"""
    out = []
    for a, b in xs:
        for c, d in ys:
            start, end = max(a, c), min(b, d)
            if end > start:
                out.append((start, end))
    return out


def clip(intervals, start, end):
    """区間列を start〜end に切り詰める。"""
    return _intersect(intervals, [(start, end)])


def _total(intervals):
    return sum((b - a).total_seconds() for a, b in intervals)


def _merge(intervals):
    merged = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _subtract(xs, ys):
    """区間列 xs から ys を除いた残り。"""
    out = []
    for a, b in _merge(xs):
        pieces = [(a, b)]
        for c, d in _merge(ys):
            pieces = [part for s, e in pieces
                      for part in ((s, min(e, c)), (max(s, d), e)) if part[1] > part[0]]
        out += pieces
    return out


def _app_pieces(events, end):
    """(パッケージ, 前へ出た時刻, 切れた時刻) の列。切り方は `app_seconds` の説明どおり。"""
    pieces = []
    current, since = None, None
    for event in events:
        moment, kind, package = _moment(event), event["type"], event["package"]
        if kind == "ACTIVITY_RESUMED":
            if current is not None:
                pieces.append((current, since, moment))
            current, since = package, moment
        elif kind in _APP_CLOSERS and current is not None:
            pieces.append((current, since, moment))
            current = None
    if current is not None and since < end:
        pieces.append((current, since, end))
    return pieces


def automated_intervals(events, packages, until=None):
    """自動で動かしているアプリが前面にいた区間（使用区間の中だけ）。

    自動プレイは画面を点けたまま端末がタップを送るので、イベントだけ見ると人が
    触っているのと区別が付かない。利用側が「自動で動かしている」と指定したアプリの
    前面時間は人の操作に数えない（その代わり、同じアプリを手で触った時間も外れる）。
    """
    packages = set(packages or ())
    if not packages:
        return []
    usage = _merge(usage_intervals(events, until=until))
    horizon = usage[-1][1] if usage else None
    if horizon is None:
        return []
    spans = [(a, b) for package, a, b in _app_pieces(events, horizon) if package in packages]
    return _merge(_intersect(_merge(spans), usage))


def human_usage(events, packages=(), until=None):
    """人が触っていた使用区間（自動で動かしているアプリの前面を除く）。"""
    usage = usage_intervals(events, until=until)
    return _subtract(usage, automated_intervals(events, packages, until=until))


def night_sec(night, usage):
    """1晩の睡眠の長さ。夜中に少し触った時間は除く。"""
    if not night:
        return 0.0
    span = [(night["start"], night["end"])]
    return _total(span) - _total(_intersect(span, _merge(usage)))


def app_seconds(events, start, end, until=None):
    """start〜end の間の、アプリ（パッケージ）ごとの前面時間（秒）。

    前面は「最後に前へ出たアプリ」とし、次のアプリが前へ出るか、画面が消えた・ロック
    された時点で切る。使用区間の外（ロック画面の上など）の時間は数えない。
    閉じる記録がまだ無い最後のアプリは、使用区間の終わり（`until` で打ち切る）まで数える。

    PAUSED / STOPPED では切らない。同じアプリの中で画面を移るたびに、古い画面の
    PAUSED・STOPPED が新しい画面の RESUMED の後に届く端末があり、それで切ると
    使っている最中のアプリの時間が消える。
    """
    pieces = _app_pieces(events, end)
    usage = _intersect(_merge(usage_intervals(events, until=until)), [(start, end)])
    totals = {}
    for package, a, b in pieces:
        seconds = _total(_intersect([(a, b)], usage))
        if seconds > 0:
            totals[package] = totals.get(package, 0.0) + seconds
    return totals


def by_category(apps, mapping):
    """パッケージ名ごとの秒を、対応表のカテゴリへ丸める。表に無いものは未分類。"""
    totals = {}
    for package, seconds in apps.items():
        category = (mapping or {}).get(package) or UNCATEGORIZED
        totals[category] = totals.get(category, 0.0) + seconds
    return totals


def _clock(text, fallback):
    try:
        return time.fromisoformat(str(text))
    except ValueError:
        return fallback


def night_window(day, settings=None):
    """`day` の朝に終わる夜の時間帯 (開始, 終了)。"""
    options = {**SLEEP_DEFAULTS, **(settings or {})}
    window = options.get("window") or SLEEP_DEFAULTS["window"]
    begin = _clock(window[0], time(20))
    finish = _clock(window[1], time(14))
    start_day = day - timedelta(days=1) if begin > finish else day
    return (datetime.combine(start_day, begin).astimezone(),
            datetime.combine(day, finish).astimezone())


def _inside(gap, covered):
    return any(c <= gap[0] and gap[1] <= d for c, d in _merge(covered))


def detect_sleep(day, activity, covered, settings=None):
    """`day` の朝に終わる夜の睡眠。見つからなければ None。

    `activity` は PC の入力とスマホの使用の区間。`covered` はスマホの記録が届いている
    範囲の列。夜の時間帯の中で、どちらも触っていない最長の区間を睡眠とする。
    返り値の `sec` は、夜中に少し触った時間を除いた長さ。
    """
    options = {**SLEEP_DEFAULTS, **(settings or {})}
    begin, finish = night_window(day, options)
    busy = _merge(_intersect(activity, [(begin, finish)]))
    edges = [begin] + [t for span in busy for t in span] + [finish]
    gaps = [(a, b) for a, b in zip(edges[::2], edges[1::2]) if b > a and _inside((a, b), covered)]

    bridge = float(options["bridge_minutes"]) * 60
    min_gap = float(options["bridge_min_gap_hours"]) * 3600
    runs = []
    for gap in gaps:
        if runs:
            last = runs[-1]
            between = (gap[0] - last["end"]).total_seconds()
            if (between <= bridge and last["last_gap"] >= min_gap
                    and _total([gap]) >= min_gap and _inside((last["end"], gap[0]), covered)):
                last.update(end=gap[1], sec=last["sec"] + _total([gap]),
                            last_gap=_total([gap]))
                continue
        runs.append({"start": gap[0], "end": gap[1], "sec": _total([gap]),
                     "last_gap": _total([gap])})
    if not runs:
        return None
    best = max(runs, key=lambda run: run["sec"])
    if best["sec"] < float(options["min_hours"]) * 3600:
        return None
    return {"start": best["start"], "end": best["end"], "sec": best["sec"]}


def wake_and_bed(night_before, night_after):
    """その日の起床（前の夜の終わり）と就寝（次の夜の始まり）。"""
    return {"wake": night_before["end"] if night_before else None,
            "bed": night_after["start"] if night_after else None}


def annotate(summary, sleeps, usage, categories, bounds=None):
    """1日の要約に、睡眠・スマホの使用・カテゴリ別の時間を足す。

    `usage` はその日に切り詰めたスマホの使用区間。スマホの記録が1つも無い日は
    何も足さない（0 と書くと「スマホを触らず、寝てもいない日」に読めてしまう）。

    `bounds`（その日の 0 時〜翌 0 時）があれば、睡眠はその日に入る全部を数える。
    PC が無入力のまま「在席」と判定された時間も、寝ていれば睡眠になる。
    離席から引くのは、離席と重なった睡眠だけ。
    """
    if not sleeps and not usage and not categories:
        return summary
    away = [(s["start"], s["end"]) for s in summary.get("segments") or []
            if s.get("kind") == "away"]
    usage = _merge(usage)
    asleep = _merge((s["start"], s["end"]) for s in sleeps)

    def split(spans):
        slept = _intersect(asleep, spans)
        # 夜中に少し触った時間は、睡眠の区間の中にあっても睡眠に数えない
        sleep_sec = _total(slept) - _total(_intersect(slept, usage))
        return sleep_sec, _total(_intersect(usage, spans))

    away_sleep, phone_away = split(away)
    summary["sleep_sec"] = split([bounds])[0] if bounds else away_sleep
    summary["away_awake_sec"] = max(0.0, summary.get("away_sec", 0.0) - away_sleep)
    summary["phone_sec"] = _total(usage)
    summary["phone_away_sec"] = phone_away
    summary["phone_categories"] = dict(sorted(categories.items(), key=lambda kv: -kv[1]))
    for block in summary.get("away_blocks") or []:
        spans = _intersect(away, [(block["start"], block["end"])])
        block["sleep_sec"], block["phone_sec"] = split(spans)
    return summary


def format_lines(summary):
    """`rollup` の標準出力に足す行。スマホの記録が無い日は何も出さない。"""
    if "sleep_sec" not in summary:
        return []
    wake, bed = summary.get("wake"), summary.get("bed")
    lines = [f"  睡眠 {summary['sleep_sec'] / 3600:.1f}h"
             f"  離席(睡眠を除く) {summary['away_awake_sec'] / 3600:.1f}h"
             f"  スマホ {summary['phone_sec'] / 3600:.1f}h"
             f"（離席中 {summary['phone_away_sec'] / 3600:.1f}h）"
             f"  起床 {f'{wake:%H:%M}' if wake else '―'}"
             f"  就寝 {f'{bed:%m/%d %H:%M}' if bed else '―'}"]
    if summary.get("phone_categories"):
        lines.append("    " + " / ".join(f"{name} {sec / 60:.0f}分"
                                         for name, sec in summary["phone_categories"].items()))
    return lines
