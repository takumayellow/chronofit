"""1日の HTML のうち、睡眠のカードとスマホの使い道の部分。

スマホのイベントがある日にだけ出す。無い日に「睡眠 0h」と書くと、寝ていない日に読める。
アプリ名は出さず、設定の対応表で丸めたカテゴリだけを出す。
"""
import html as html_escape


def _e(text):
    return html_escape.escape(str(text), quote=True)


def _hours(seconds):
    return f"{seconds / 3600:.1f}h"


def has_phone(summary):
    return "sleep_sec" in summary


def cards(summary):
    """在席・離席の横に並べるカード。(見出し, 値, 注) の列。"""
    if not has_phone(summary):
        return []
    return [
        ("睡眠", _hours(summary["sleep_sec"]), _night(summary)),
        ("離席(睡眠除く)", _hours(summary["away_awake_sec"]), "離席から睡眠を引いた残り"),
        ("スマホ", _hours(summary["phone_sec"]),
         f"うち離席中 {_hours(summary.get('phone_away_sec', 0.0))}"),
    ]


def _clock(moment, with_date=False):
    if not moment:
        return "―"
    return f"{moment:%m/%d %H:%M}" if with_date else f"{moment:%H:%M}"


def _night(summary):
    """睡眠カードの注。前の夜に寝た時刻 → 起きた時刻と、その日の夜に寝た時刻。"""
    wake = summary.get("wake")
    fell = next((a for a, b in summary.get("sleep_spans") or [] if b == wake), None)
    if wake:
        note = (f"{_clock(fell)} 就寝 → " if fell else "") + f"{_clock(wake)} 起床"
    else:
        note = "PC もスマホも触っていない夜の最長区間"
    if summary.get("bed"):
        note += f"（次は {_clock(summary['bed'])} 就寝）"
    return note


def section(summary):
    """人が触っていたスマホの時間を、アプリの種類ごとに。"""
    if not has_phone(summary):
        return "<p class='muted'>スマホの記録が無い（<code>chronofit phone pull</code>）。</p>"
    auto = summary.get("phone_auto_sec") or 0.0
    head = (f"<p class='sub'>手で触っていた {_e(_hours(summary.get('phone_sec') or 0.0))} を"
            "アプリの種類ごとに分けた"
            + (f"（自動プレイの {_e(_hours(auto))} は除く）" if auto else "") + "。</p>")
    categories = summary.get("phone_categories") or {}
    if not categories:
        return head + "<p class='muted'>アプリの前面化の記録が無い。</p>"
    rows = "".join(f"<tr><td class='n'>{sec / 60:.0f}分</td><td>{_e(name)}</td></tr>"
                   for name, sec in categories.items())
    return head + "<table><tr><th>時間</th><th>カテゴリ</th></tr>" + rows + "</table>"


def away_head(summary):
    return "<th>睡眠</th><th>スマホ</th>" if has_phone(summary) else ""


def away_cells(summary, block):
    if not has_phone(summary):
        return ""
    sleep, used = block.get("sleep_sec", 0.0), block.get("phone_sec", 0.0)
    return (f"<td class='n'>{f'{sleep / 60:.0f}分' if sleep else ''}</td>"
            f"<td class='n'>{f'{used / 60:.0f}分' if used else ''}</td>")
