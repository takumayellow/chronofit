"""1日の測定結果を、そのまま開いて読める1枚の HTML にする。

数字はコマンドの標準出力にも出ているが、**出力は流れて消える**。「今日はどうだった
のか」を後から見返す・人に見せる・週の頭に先週を眺める、といった用途には、
ファイルとして残って開けるものが要る。

設計の勘所:

- **外部に一切依存しない1枚もの。** CDN の CSS も JS も読まない。中身はウィンドウ
  タイトル（＝その日に開いた全部の題名）なので、開くたびに外へ通信が飛ぶ作りには
  しない。オフラインでも、10年後に開いても同じに見える。
- **置き場所は `data_root()` の下だけ。** 生タイトルを含むので、リポジトリには絶対に
  書かない（`paths.py` の方針そのまま）。
- **タイムラインは「時刻ごとの色」であって「集中度の位置」ではない。** 1つのスパンの
  中で、いつ手が動いたかは測っていない。だから active の割合は**位置ではなく濃い帯の
  高さ**で出す。横位置で描くと、測っていない情報を描いたことになる。
- **数字の無いところに 0 を描かない。** slack 率が None（在席ゼロ）のとき「0%」と
  書くと「一切集中していない日」に読める。「データ無し」と書く。
"""
import html as html_escape
from datetime import timedelta

# 色は「席にいたか」で系統を分ける。緑＝いた、灰＝いない、青＝観ていた。
KIND_STYLE = {
    "present": ("#4c9a5c", "在席"),
    "passive": ("#4a7fb5", "受動（再生中）"),
    "away:idle": ("#b9b2a6", "無入力"),
    "away:locked": ("#8d8b86", "ロック"),
    "away:sleep": ("#6f6d69", "スリープ"),
    "away:no_data": ("#d8d4cc", "記録なし"),
}
ACTIVE_COLOR = "#2f6b3d"


def kind_key(segment):
    """凡例と色を引くための鍵。離席は理由まで分ける（理由で意味が違うため）。"""
    if segment["kind"] == "away":
        return "away:" + (segment.get("reason") or "idle")
    return segment["kind"]


def _split_by_hour(segment):
    """スパンを時刻の境界で切る。1本が2時間にまたがっても行を跨げるように。"""
    cursor, end = segment["start"], segment["end"]
    while cursor < end:
        hour = cursor.replace(minute=0, second=0, microsecond=0)
        piece_end = min(end, hour + timedelta(hours=1))
        yield hour, cursor, piece_end
        cursor = piece_end


MAX_ROWS = 30


def hour_rows(segments, max_rows=MAX_ROWS):
    """1時間 = 1行のタイムライン。空の時間も行として残す。

    空行を詰めると、`09:00` の次が `14:00` という並びになり、**間が空いていた事実**が
    見た目から消える。1日の姿を見るのが目的なので、空白は空白のまま出す。

    ただし無条件には伸ばさない。数日〜数ヶ月 PC を止めた後の復帰は1本の長い
    `no_data` になるので、素直に埋めると数百行の表になる。`max_rows` を超えたら
    中身のある時間だけを残し、**飛ばした時間数を行として明示する**（黙って詰めると
    「空いていた事実」が消え、上の理由と矛盾する）。
    """
    pieces = [(hour, start, end, segment)
              for segment in segments for hour, start, end in _split_by_hour(segment)]
    if not pieces:
        return []

    filled = {}
    for hour, start, end, segment in pieces:
        sec = segment.get("sec") or (end - start).total_seconds()
        active = segment.get("active_sec", 0.0) if segment["kind"] == "present" else 0.0
        filled.setdefault(hour, []).append({
            "left": (start - hour).total_seconds() / 36.0,      # % （3600秒 = 100%）
            "width": max((end - start).total_seconds() / 36.0, 0.15),
            "key": kind_key(segment),
            # 割合が 1 を超えるのはデータの異常。描画で丸めて、形が壊れないようにする。
            "active_ratio": min(active / sec, 1.0) if sec > 0 else 0.0,
            "start": start, "end": end,
            "proc": segment.get("proc", ""), "title": segment.get("title", ""),
        })

    first, last = min(filled), max(filled)
    span_hours = int((last - first).total_seconds() // 3600) + 1
    if span_hours <= max_rows:
        hours = [first + timedelta(hours=n) for n in range(span_hours)]
    else:
        hours = sorted(filled)

    rows = []
    previous = None
    for hour in hours:
        skipped = 0 if previous is None else int((hour - previous).total_seconds() // 3600) - 1
        rows.append({"hour": hour, "skipped": max(0, skipped),
                     "pieces": sorted(filled.get(hour, []), key=lambda p: p["left"])})
        previous = hour
    return rows


def _e(text):
    return html_escape.escape(str(text or ""))


def _hours(seconds):
    return f"{seconds / 3600:.1f}h"


def _ratio(value):
    return "データ無し" if value is None else f"{value:.0%}"


CSS = """
:root { color-scheme: light dark; }
body { font-family: "Yu Gothic UI", "Meiryo", system-ui, sans-serif;
       margin: 0 auto; padding: 24px 20px 64px; max-width: 900px; line-height: 1.6;
       background: #fbfaf8; color: #23211e; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 15px; margin: 32px 0 10px; padding-bottom: 4px;
     border-bottom: 1px solid #ddd8cf; font-weight: 600; }
.sub { color: #6d675e; font-size: 12px; margin: 0 0 20px; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; }
.card { flex: 1 1 120px; background: #fff; border: 1px solid #e5e0d6; border-radius: 6px;
        padding: 10px 12px; }
.card .k { font-size: 11px; color: #6d675e; }
.card .v { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
.card .n { font-size: 11px; color: #8a8378; }
.row { display: flex; align-items: center; gap: 8px; margin: 2px 0; }
.row .h { width: 42px; font-size: 11px; color: #8a8378; text-align: right;
          font-variant-numeric: tabular-nums; }
.track { position: relative; flex: 1; height: 20px; background: #efece6;
         border-radius: 3px; overflow: hidden; }
.track .tick { position: absolute; top: 0; bottom: 0; width: 1px; background: #e2ded6; }
.piece { position: absolute; top: 0; bottom: 0; }
.piece .act { position: absolute; left: 0; right: 0; bottom: 0; background: %ACTIVE%; }
.legend { display: flex; flex-wrap: wrap; gap: 14px; font-size: 11px; color: #6d675e;
          margin-top: 8px; }
.legend i { display: inline-block; width: 11px; height: 11px; border-radius: 2px;
            margin-right: 4px; vertical-align: -1px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 4px 8px; border-bottom: 1px solid #eae6de;
         vertical-align: top; }
th { font-size: 11px; color: #6d675e; font-weight: 600; }
td.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td.t { word-break: break-all; }
.warn { color: #a4471f; }
.muted { color: #8a8378; }
.paths { font-size: 12px; color: #6d675e; }
.paths code { background: #f1eee8; padding: 1px 4px; border-radius: 3px; }
@media (prefers-color-scheme: dark) {
  body { background: #171614; color: #e6e2db; }
  .card, .track { background: #211f1c; border-color: #35322d; }
  .track { background: #26241f; }
  h2 { border-color: #35322d; }
  .paths code { background: #26241f; }
  th, td { border-color: #2b2825; }
}
""".replace("%ACTIVE%", ACTIVE_COLOR)


def _timeline(summary):
    rows = hour_rows(summary.get("segments") or [])
    if not rows:
        return "<p class='muted'>この日の記録が無い。</p>"

    ticks = "".join(f"<div class='tick' style='left:{q * 25}%'></div>" for q in (1, 2, 3))
    lines = []
    for row in rows:
        if row.get("skipped"):
            lines.append(f"<div class='row'><div class='h'></div>"
                         f"<div class='muted'>… {row['skipped']}時間 記録なし …</div></div>")
        pieces = []
        for piece in row["pieces"]:
            color = KIND_STYLE.get(piece["key"], ("#c8c2b8", piece["key"]))[0]
            tip = (f"{piece['start']:%H:%M}-{piece['end']:%H:%M} "
                   f"{KIND_STYLE.get(piece['key'], ('', piece['key']))[1]}"
                   + (f" / {piece['proc']} {piece['title']}" if piece["title"] else ""))
            inner = ""
            if piece["active_ratio"] > 0:
                inner = f"<div class='act' style='height:{piece['active_ratio'] * 100:.0f}%'></div>"
            pieces.append(
                f"<div class='piece' style='left:{piece['left']:.3f}%;"
                f"width:{piece['width']:.3f}%;background:{color}' title='{_e(tip)}'>{inner}</div>")
        lines.append(f"<div class='row'><div class='h'>{row['hour']:%H:%M}</div>"
                     f"<div class='track'>{ticks}{''.join(pieces)}</div></div>")

    legend = "".join(f"<span><i style='background:{color}'></i>{_e(label)}</span>"
                     for color, label in KIND_STYLE.values())
    legend += (f"<span><i style='background:{ACTIVE_COLOR}'></i>"
               "濃い部分の高さ = その帯で入力のあった割合（位置ではない）</span>")
    return "".join(lines) + f"<div class='legend'>{legend}</div>"


def _cards(summary):
    items = [
        ("在席", _hours(summary["wall_sec"]), "前景があって席にいた"),
        ("入力あり", _hours(summary["net_sec"]), "キーかマウスが動いた"),
        ("slack率", _ratio(summary["slack_ratio"]), "入力あり ÷ 在席"),
        ("受動", _hours(summary.get("passive_sec", 0.0)), "再生中（在席にも離席にも入れない）"),
        ("離席", _hours(summary["away_sec"]), "無入力・ロック・スリープ・記録なし"),
    ]
    return "".join(f"<div class='card'><div class='k'>{_e(k)}</div>"
                   f"<div class='v'>{_e(v)}</div><div class='n'>{_e(note)}</div></div>"
                   for k, v, note in items)


def _away_table(summary):
    blocks = summary.get("away_blocks") or []
    if not blocks:
        return "<p class='muted'>まとまった離席は無い。</p>"
    rows = []
    for block in blocks:
        label = block.get("label")
        # 未ラベルでも、両隣の前景からの推定は出す。ただし **推定と明記する**。
        # 実測の顔をさせると、そのまま所要時間DBへ入ってしまう（context.py の警告）。
        guess = block.get("guess") or {}
        hint = ""
        if not label and guess.get("subject"):
            hint = (f" <span class='muted'>推定 {_e(guess['subject'])} "
                    f"{_e(guess.get('kind') or '')}（{_e(guess.get('via') or '')}）</span>")
        cell = (_e(label) if label
                else f"<span class='warn'>未ラベル</span> "
                     f"<span class='muted'>({_e(block.get('reason_label') or block['reason'])})</span>"
                     + hint)
        detail = block.get("detail") or {}
        extra = detail.get("detail") if isinstance(detail, dict) else None
        rows.append(f"<tr><td class='n'>{block['start']:%H:%M}-{block['end']:%H:%M}</td>"
                    f"<td class='n'>{block['sec'] / 60:.0f}分</td>"
                    f"<td>{cell}{' / ' + _e(extra) if extra else ''}</td></tr>")
    unlabeled = sum(1 for b in blocks if not b.get("label"))
    # 「未ラベル 0本」は書かない。片付いている状態を、片付いていない状態と
    # 同じ字数で報告すると、残っている日に目が留まらなくなる。
    note = (f"<p class='sub'>{len(blocks)}本"
            + (f" / 未ラベル {unlabeled}本（<code>chronofit label</code> で付ける）"
               if unlabeled else "") + "</p>")
    return (note + "<table><tr><th>時刻</th><th>長さ</th><th>ラベル</th></tr>"
            + "".join(rows) + "</table>")


def _title_table(summary, limit=20):
    rows = []
    for row in (summary.get("titles") or [])[:limit]:
        passive = row.get("passive_sec", 0.0)
        rows.append(f"<tr><td class='n'>{row['net_sec'] / 60:.0f}分</td>"
                    f"<td class='n muted'>{f'{passive / 60:.0f}分' if passive else ''}</td>"
                    f"<td class='muted'>{_e(row['proc'])}</td>"
                    f"<td class='t'>{_e(row['title'])}</td></tr>")
    if not rows:
        return "<p class='muted'>前景の記録が無い。</p>"
    return ("<table><tr><th>入力あり</th><th>受動</th><th>アプリ</th><th>タイトル</th></tr>"
            + "".join(rows) + "</table>")


def _board_table(board_rows, board_summary, as_of=None):
    if not board_rows:
        return ("<p class='muted'>やることの一覧が空。"
                "<code>chronofit task add &lt;科目&gt; &lt;種別&gt; --count N</code> で足す。</p>")
    rows = []
    for row in board_rows:
        hours = "―" if row["remaining_hours"] is None else f"{row['remaining_hours']:.1f}h"
        due = row["due"] or ""
        if row["days_left"] is not None:
            due += f" ({row['days_left']:+d}日)"
        pace = f"{row['hours_per_day']:.1f}h/日" if row["hours_per_day"] else ""
        rows.append(
            f"<tr><td>{_e(row['subject'])}</td><td class='muted'>{_e(row['kind'])}</td>"
            f"<td class='t'>{_e(row.get('target') or '')}</td>"
            f"<td class='n'>{row['done']}/{row['goal']}</td>"
            f"<td class='n'>{_e(hours)}</td>"
            f"<td class='n{' warn' if row['overdue'] else ''}'>{_e(due)}</td>"
            f"<td class='n muted'>{_e(pace)}</td></tr>")
    head = ("<table><tr><th>科目</th><th>種別</th><th>対象</th><th>進捗</th>"
            "<th>残り</th><th>締切</th><th>ペース</th></tr>")
    note = f"<p class='sub'>{_e(as_of)}</p>" if as_of else ""
    if board_summary:
        note += (f"<p class='sub'>残り {board_summary['units_left']}本 "
                f"{board_summary['remaining_hours']:.0f}h(net) / 投入済み "
                f"{board_summary['spent_hours']:.0f}h"
                + (f" / <span class='warn'>締切超過 {board_summary['overdue']}件</span>"
                   if board_summary["overdue"] else "")
                + (f" / 見積もれない {board_summary['unestimated']}件"
                   if board_summary["unestimated"] else "") + "</p>")
    return note + head + "".join(rows) + "</table>"


def _paths_block(sources):
    if not sources:
        return ""
    items = "".join(f"<li><code>{_e(path)}</code> — {_e(note)}</li>"
                    for note, path in sources)
    return f"<h2>この画面の出どころ</h2><ul class='paths'>{items}</ul>"


def render(summary, date_label, board_rows=None, board_summary=None, sources=None,
           as_of=None):
    """1日ぶんの HTML を組み立てる。文字列を返すだけで、書き出しはしない。"""
    body = [
        f"<h1>{_e(date_label)}</h1>",
        "<p class='sub'>chronofit — 測った値だけを出す。推測した値は入っていない。</p>",
        f"<div class='cards'>{_cards(summary)}</div>",
        "<h2>1日の流れ</h2>", _timeline(summary),
        "<h2>離席</h2>", _away_table(summary),
        "<h2>何に時間を使ったか</h2>", _title_table(summary),
        "<h2>進捗</h2>", _board_table(board_rows, board_summary, as_of),
        _paths_block(sources),
    ]
    return ("<!doctype html>\n<html lang='ja'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>chronofit {_e(date_label)}</title><style>{CSS}</style></head>"
            f"<body>{''.join(body)}</body></html>\n")


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
