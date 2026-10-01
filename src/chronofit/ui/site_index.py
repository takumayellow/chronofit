"""複数日を並べる一覧ページ（index.html）。1行 = 生活の1日（朝の区切りから翌朝まで）。

横軸は24時間を1本にしたもの。PC の在席と受動（再生中）だけを色で置き、
離席は地の色のまま残す。睡眠は斜線、スマホは上端の細い帯で重ねる（日次ページと同じ記号）。
行の右には在席・入力・睡眠・スマホの合計を出し、日付から日次ページへ飛べる。
"""
from datetime import timedelta

from . import style
from .style import e
from .timeline import KIND_STYLE

WEEKDAYS = "月火水木金土日"
SHOWN_KINDS = ("present", "passive")
TICK_HOURS = 3


def _pct(moment, start):
    return (moment - start).total_seconds() / 864.0          # 86400秒 = 100%


def merged(segments, kinds=SHOWN_KINDS, gap_sec=120):
    """同じ種類の区間で、間が `gap_sec` 以内のものをつなぐ（細切れの線を減らす）。"""
    out = []
    for segment in sorted((s for s in segments or [] if s["kind"] in kinds),
                          key=lambda s: s["start"]):
        if (out and out[-1]["kind"] == segment["kind"]
                and (segment["start"] - out[-1]["end"]).total_seconds() <= gap_sec):
            out[-1]["end"] = max(out[-1]["end"], segment["end"])
        else:
            out.append({"kind": segment["kind"], "start": segment["start"],
                        "end": segment["end"]})
    return out


def _bar(css, start, a, b, extra=""):
    end = start + timedelta(days=1)
    lo, hi = max(a, start), min(b, end)
    if hi <= lo:
        return ""
    return (f"<div class='{css}' style='left:{_pct(lo, start):.3f}%;"
            f"width:{max(_pct(hi, start) - _pct(lo, start), 0.08):.3f}%{extra}'"
            f" title='{lo:%H:%M}-{hi:%H:%M}'></div>")


def _track(summary, start):
    parts = [_bar("piece", start, s["start"], s["end"],
                  f";background:{KIND_STYLE[s['kind']][0]}")
             for s in merged(summary.get("segments"))]
    parts += [_bar("sleep", start, a, b) for a, b in summary.get("sleep_spans") or []]
    parts += [_bar("auto", start, a, b) for a, b in summary.get("auto_spans") or []]
    parts += [_bar("phone", start, a, b) for a, b in summary.get("phone_spans") or []]
    return f"<div class='track'>{''.join(parts)}</div>"


def _h(seconds):
    return f"{seconds / 3600:.1f}"


def _nums(summary):
    values = [_h(summary.get("wall_sec") or 0), _h(summary.get("net_sec") or 0)]
    if "sleep_sec" in summary:
        values += [_h(summary["sleep_sec"]) if summary.get("sleep_sec") else "―",
                   _h(summary.get("phone_sec") or 0)]
    else:
        values += ["―", "―"]
    return "<div class='nums'>" + "".join(f"<span>{v}</span>" for v in values) + "</div>"


def _hours_axis(start):
    marks = "".join(
        f"<span style='left:{n / 24 * 100:.3f}%'>{(start + timedelta(hours=n)):%H}</span>"
        for n in range(0, 24, TICK_HOURS))
    return f"<div class='hours'>{marks}</div>"


def render(days, today=None):
    """`days` は新しい順の [(日付, 開始時刻, summary または None)]。"""
    if not days:
        body = "<p class='muted'>まだ1日ぶんの記録も無い。</p>"
        return style.page("chronofit", body, style.topbar([]))
    first_start = days[0][1]
    head = (f"<div class='dayrow head'><div class='d'></div>{_hours_axis(first_start)}"
            "<div class='nums'><span>在席h</span><span>入力h</span><span>睡眠h</span>"
            "<span>スマホh</span></div></div>")
    rows = []
    for day, start, summary in days:
        weekday = WEEKDAYS[start.weekday()]
        label = f"{start.month}/{start.day}<small>{weekday}</small>"
        mark = " <small>今日</small>" if day == today else ""
        link = f"<a href='{e(day)}.html'>{label}</a>{mark}"
        if summary is None:
            rows.append(f"<div class='dayrow'><div class='d'>{label}</div>"
                        "<div class='track'></div><div class='nums muted'>"
                        "<span>―</span><span>―</span><span>―</span><span>―</span></div></div>")
            continue
        rows.append(f"<div class='dayrow'><div class='d'>{link}</div>"
                    f"{_track(summary, start)}{_nums(summary)}</div>")
    legend = ("<div class='legend'>"
              f"<span><i style='background:{KIND_STYLE['present'][0]}'></i>在席</span>"
              f"<span><i style='background:{KIND_STYLE['passive'][0]}'></i>受動（再生中）</span>"
              "<span><i class='swatch-sleep'></i>睡眠</span>"
              "<span><i class='swatch-phone'></i>スマホ</span>"
              "<span><i class='swatch-auto'></i>自動プレイ</span>"
              "<span>地の色 = 離席・記録なし</span></div>")
    clock = f"{first_start:%H:%M}"
    body = (f"<header class='day'><h1>最近の{len(days)}日</h1>"
            f"<div class='range'>1行 = {clock} から翌 {clock} まで。日付から各日のページへ。</div>"
            f"</header><div class='days'>{head}{''.join(rows)}</div>{legend}")
    top = style.topbar([("今日", f"{today}.html" if today else None)])
    return style.page("chronofit", body, top)
