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
from datetime import date as date_type

from . import report_phone, report_projects, report_work, style, timeline
from .style import e as _e
from .timeline import KIND_STYLE, MAX_ROWS, hour_rows, kind_key  # noqa: F401  （既存の呼び出し口）

WEEKDAYS = "月火水木金土日"


def _hours(seconds):
    return f"{seconds / 3600:.1f}h"


def _ratio(value):
    return "データ無し" if value is None else f"{value:.0%}"


def _table(inner):
    return f"<div class='table'>{inner}</div>"


def day_title(label):
    """`2026-10-01` → (`10月1日`, `木`)。日付でなければそのまま。"""
    try:
        day = date_type.fromisoformat(str(label))
    except ValueError:
        return str(label), ""
    return f"{day.month}月{day.day}日", WEEKDAYS[day.weekday()]


def _cards(summary):
    items = [
        ("在席", _hours(summary["wall_sec"]), "前景があって席にいた"),
        ("入力あり", _hours(summary["net_sec"]), "キーかマウスが動いた"),
        ("slack率", _ratio(summary["slack_ratio"]), "入力あり ÷ 在席"),
        ("受動", _hours(summary.get("passive_sec", 0.0)), "再生中（在席にも離席にも入れない）"),
        ("離席", _hours(summary["away_sec"]), "無入力・ロック・スリープ・記録なし"),
    ] + report_phone.cards(summary)
    return "".join(f"<div class='stat'><div class='k'>{_e(k)}</div>"
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
        where = " / ".join(f"{_e(part['place'])} {part['sec'] / 60:.0f}分"
                           for part in block.get("places") or [])
        rows.append(f"<tr><td class='n'>{block['start']:%H:%M}-{block['end']:%H:%M}</td>"
                    f"<td class='n'>{block['sec'] / 60:.0f}分</td>"
                    f"<td>{cell}{' / ' + _e(extra) if extra else ''}</td>"
                    + report_phone.away_cells(summary, block) +
                    f"<td class='muted'>{where}</td></tr>")
    unlabeled = sum(1 for b in blocks if not b.get("label"))
    # 「未ラベル 0本」は書かない。片付いている状態を、片付いていない状態と
    # 同じ字数で報告すると、残っている日に目が留まらなくなる。
    note = (f"<p class='sub'>{len(blocks)}本"
            + (f" / 未ラベル {unlabeled}本（<code>chronofit label</code> で付ける）"
               if unlabeled else "") + "</p>")
    return note + _table("<table><tr><th>時刻</th><th>長さ</th><th>ラベル</th>"
                         + report_phone.away_head(summary) + "<th>場所</th></tr>"
                         + "".join(rows) + "</table>")


def _title_table(summary, limit=15):
    rows = []
    for row in summary.get("titles") or []:
        passive = row.get("passive_sec", 0.0)
        rows.append(f"<tr><td class='n'>{row['net_sec'] / 60:.0f}分</td>"
                    f"<td class='n muted'>{f'{passive / 60:.0f}分' if passive else ''}</td>"
                    f"<td class='muted'>{_e(row['proc'])}</td>"
                    f"<td class='t'>{_e(row['title'])}</td></tr>")
    if not rows:
        return "<p class='muted'>前景の記録が無い。</p>"
    head = "<table><tr><th>入力あり</th><th>受動</th><th>アプリ</th><th>タイトル</th></tr>"
    shown = _table(head + "".join(rows[:limit]) + "</table>")
    if len(rows) <= limit:
        return shown
    return (shown + f"<details class='more'><summary>残り {len(rows) - limit} 件</summary>"
            + _table(head + "".join(rows[limit:]) + "</table>") + "</details>")


def _target(row):
    """対象の欄。GitHub の Issue から来たタスクは、その Issue へのリンクにする。"""
    text = row.get("target") or ""
    url = style.issue_url(row.get("issue"))
    return style.link(url, text) if url and text else _e(text)


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
            f"<td class='t'>{_target(row)}</td>"
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
    return note + _table(head + "".join(rows) + "</table>")


def _paths_block(sources):
    if not sources:
        return ""
    items = "".join(f"<li><code>{_e(path)}</code> — {_e(note)}</li>"
                    for note, path in sources)
    return f"<section><h2>この画面の出どころ</h2><ul class='paths'>{items}</ul></section>"


def _section(title, inner, total=None):
    extra = f"<span class='total'>{_e(total)}</span>" if total else ""
    return f"<section><h2>{_e(title)}{extra}</h2>{inner}</section>"


def _nav(day):
    if not day:
        return ""
    return style.topbar([("← 前日", day.get("prev")), ("一覧", "index.html"),
                         ("翌日 →", day.get("next"))])


def _header(date_label, day):
    title, weekday = day_title(date_label)
    span = ""
    if day and day.get("start") and day.get("end"):
        span = (f"<div class='range'>{day['start']:%m/%d %H:%M} → "
                f"{day['end']:%m/%d %H:%M}（{day['start']:%H:%M} で1日を区切る）</div>")
    small = f"<small>{_e(weekday)}曜</small>" if weekday else ""
    return f"<header class='day'><h1>{_e(title)}{small}</h1>{span}</header>"


def render(summary, date_label, board_rows=None, board_summary=None, sources=None,
           as_of=None, extras=None, day=None):
    """1日ぶんの HTML を組み立てる。文字列を返すだけで、書き出しはしない。

    `extras` は `{"done", "work", "outings", "agenda", "project_groups", "default_group"}`。
    上から「何をしたか → 予定どおりだったか → 何が残っているか」の順に読めるようにし、
    時間の使い方の細部（流れ・離席・アプリ別）はその下に置く。
    `day` は生活の1日の `{"start", "end", "prev", "next"}`（前後のページへのリンク）。
    """
    extras = extras or {}
    day = day or {}
    projects = report_projects.section(
        extras.get("done"), extras.get("work"), summary.get("net_sec") or 0.0,
        extras.get("project_groups"), extras.get("default_group") or "開発",
        outings=extras.get("outings"), phone=summary.get("phone_categories"))
    body = [
        _header(date_label, day),
        f"<div class='stats'>{_cards(summary)}</div>",
        "<p class='sub'>測った値だけを出す。推定した値は推定と書く。</p>",
        _section("やったこと", projects),
        _section("予定と実際", _table_or(report_work.agenda_section(extras.get("agenda")))),
        "<section><h2>残っているもの</h2>" + _board_table(board_rows, board_summary, as_of)
        + "</section>",
        _section("1日の流れ", timeline.render(summary, day.get("start"), day.get("end"))),
        _section("離席", _away_table(summary)),
        _section("スマホの使い道", _table_or(report_phone.section(summary))),
        _section("アプリ・タイトル別", _title_table(summary)),
        _paths_block(sources),
    ]
    title, _ = day_title(date_label)
    return style.page(f"{title} — chronofit", "".join(body), _nav(day))


def _table_or(html):
    """表を含む断片なら枠に入れる（文だけのときはそのまま）。"""
    if "<table>" not in html:
        return html
    head, _, rest = html.partition("<table>")
    return head + _table("<table>" + rest)


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path
