"""1日の流れ（1時間 = 1行）を描く。日次ページと一覧ページで同じ色を使う。

- **タイムラインは「時刻ごとの色」であって「集中度の位置」ではない。** 1つのスパンの
  中で、いつ手が動いたかは測っていない。だから active の割合は**位置ではなく濃い帯の
  高さ**で出す。横位置で描くと、測っていない情報を描いたことになる。
- 睡眠とスマホは PC の記録とは別の源なので、色を塗り替えずに重ねる（睡眠は斜線、
  スマホは行の上端の細い帯）。PC が「在席」と判定した夜中に寝ていた、という食い違いも
  そのまま見える。
"""
import html as html_escape
from datetime import timedelta

# 色は CSS の変数で持つ（明るい画面と暗い画面で値を変える）。
KIND_STYLE = {
    "present": ("var(--k-present)", "在席"),
    "passive": ("var(--k-passive)", "受動（再生中）"),
    "away:idle": ("var(--k-idle)", "無入力"),
    "away:locked": ("var(--k-locked)", "ロック"),
    "away:sleep": ("var(--k-sleep)", "PC スリープ"),
    "away:no_data": ("var(--k-nodata)", "記録なし"),
}
MAX_ROWS = 30


def _e(text):
    return html_escape.escape(str(text or ""), quote=True)


def kind_key(segment):
    """凡例と色を引くための鍵。離席は理由まで分ける（理由で意味が違うため）。"""
    if segment["kind"] == "away":
        return "away:" + (segment.get("reason") or "idle")
    return segment["kind"]


def _split_by_hour(start, end):
    """区間を時刻の境界で切る。1本が2時間にまたがっても行を跨げるように。"""
    cursor = start
    while cursor < end:
        hour = cursor.replace(minute=0, second=0, microsecond=0)
        piece_end = min(end, hour + timedelta(hours=1))
        yield hour, cursor, piece_end
        cursor = piece_end


def _hours_between(first, last):
    return int((last - first).total_seconds() // 3600) + 1


def hour_rows(segments, max_rows=MAX_ROWS, start=None, end=None):
    """1時間 = 1行のタイムライン。空の時間も行として残す。

    空行を詰めると、`09:00` の次が `14:00` という並びになり、**間が空いていた事実**が
    見た目から消える。1日の姿を見るのが目的なので、空白は空白のまま出す。

    `start`/`end`（生活の1日の区間）を渡すと、行はその区間の頭から始め、`end` の
    手前の時間で終える。渡さなければ記録のある最初の時間から最後の時間まで。
    区間なしで `max_rows` を超えたら中身のある時間だけを残し、**飛ばした時間数を
    行として明示する**（数ヶ月ぶりの起動で数百行の表にしない）。
    """
    filled = {}
    for segment in segments:
        for hour, a, b in _split_by_hour(segment["start"], segment["end"]):
            sec = segment.get("sec") or (b - a).total_seconds()
            active = segment.get("active_sec", 0.0) if segment["kind"] == "present" else 0.0
            filled.setdefault(hour, []).append({
                "left": (a - hour).total_seconds() / 36.0,      # % （3600秒 = 100%）
                "width": max((b - a).total_seconds() / 36.0, 0.15),
                "key": kind_key(segment),
                # 割合が 1 を超えるのはデータの異常。描画で丸めて、形が壊れないようにする。
                "active_ratio": min(active / sec, 1.0) if sec > 0 else 0.0,
                "start": a, "end": b,
                "proc": segment.get("proc", ""), "title": segment.get("title", ""),
            })
    if not filled:
        return []

    if start is not None and end is not None:
        first = start.replace(minute=0, second=0, microsecond=0)
        last = min(max(filled), end - timedelta(seconds=1)).replace(
            minute=0, second=0, microsecond=0)
        hours = [first + timedelta(hours=n) for n in range(_hours_between(first, last))]
    else:
        first, last = min(filled), max(filled)
        span_hours = _hours_between(first, last)
        hours = ([first + timedelta(hours=n) for n in range(span_hours)]
                 if span_hours <= max_rows else sorted(filled))

    rows, previous = [], None
    for hour in hours:
        skipped = 0 if previous is None else int((hour - previous).total_seconds() // 3600) - 1
        rows.append({"hour": hour, "skipped": max(0, skipped),
                     "pieces": sorted(filled.get(hour, []), key=lambda p: p["left"])})
        previous = hour
    return rows


def overlay(spans, hour):
    """区間の列のうち、その1時間に入る部分を (左%, 幅%, 開始, 終了) で。"""
    end = hour + timedelta(hours=1)
    out = []
    for a, b in spans or []:
        lo, hi = max(a, hour), min(b, end)
        if hi > lo:
            out.append(((lo - hour).total_seconds() / 36.0,
                        (hi - lo).total_seconds() / 36.0, lo, hi))
    return out


def _bands(spans, hour, css, label):
    return "".join(
        f"<div class='{css}' style='left:{left:.3f}%;width:{width:.3f}%'"
        f" title='{_e(f'{lo:%H:%M}-{hi:%H:%M} {label}')}'></div>"
        for left, width, lo, hi in overlay(spans, hour))


def _piece(piece):
    color, label = KIND_STYLE.get(piece["key"], ("var(--k-nodata)", piece["key"]))
    tip = (f"{piece['start']:%H:%M}-{piece['end']:%H:%M} {label}"
           + (f" / {piece['proc']} {piece['title']}" if piece["title"] else ""))
    inner = ""
    if piece["active_ratio"] > 0:
        inner = f"<div class='act' style='height:{piece['active_ratio'] * 100:.0f}%'></div>"
    return (f"<div class='piece' style='left:{piece['left']:.3f}%;"
            f"width:{piece['width']:.3f}%;background:{color}' title='{_e(tip)}'>{inner}</div>")


def legend(summary):
    items = [f"<span><i style='background:{color}'></i>{_e(label)}</span>"
             for color, label in KIND_STYLE.values()]
    items.append("<span><i class='swatch-act'></i>濃い部分の高さ = 入力のあった割合</span>")
    if summary.get("sleep_spans"):
        items.append("<span><i class='swatch-sleep'></i>睡眠（スマホ・PC から推定）</span>")
    if summary.get("phone_spans"):
        items.append("<span><i class='swatch-phone'></i>スマホを触っていた</span>")
    if summary.get("auto_spans"):
        items.append("<span><i class='swatch-auto'></i>自動プレイ</span>")
    return f"<div class='legend'>{''.join(items)}</div>"


def render(summary, start=None, end=None):
    rows = hour_rows(summary.get("segments") or [], start=start, end=end)
    if not rows:
        return "<p class='muted'>この日の記録が無い。</p>"
    lines = []
    for row in rows:
        if row.get("skipped"):
            lines.append(f"<div class='row gap'><div class='h'></div>"
                         f"<div class='muted'>… {row['skipped']}時間 記録なし …</div></div>")
        hour = row["hour"]
        layers = (_bands(summary.get("sleep_spans"), hour, "sleep", "睡眠")
                  + _bands(summary.get("auto_spans"), hour, "auto", "自動プレイ")
                  + _bands(summary.get("phone_spans"), hour, "phone", "スマホ"))
        lines.append(f"<div class='row'><div class='h'>{hour:%H}</div>"
                     f"<div class='track'>{''.join(_piece(p) for p in row['pieces'])}"
                     f"{layers}</div></div>")
    axis = ("<div class='row axis'><div class='h'></div><div class='scale'>"
            "<span>:00</span><span>:15</span><span>:30</span><span>:45</span></div></div>")
    return f"<div class='timeline'>{axis}{''.join(lines)}</div>{legend(summary)}"
