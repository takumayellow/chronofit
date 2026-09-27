"""日次レポートの上半分: 何を片付け、どのリポジトリで何をして、予定どおりだったか。

アプリ別・タイトル別の表だけでは「今日何が進んだか」が読めない。ここでは
GitHub に残った成果、Claude の会話から割り付けた作業、カレンダーの予定と実際の場所を並べる。
どれも取れなかったときは空欄にせず、取れなかった理由を1行出す。
"""
import html as html_escape
from datetime import datetime, timedelta

MIN_SHOWN_SEC = 60   # これ未満の作業は行に出さない（合計には入れる）


def _e(text):
    return html_escape.escape(str(text or ""))


def _minutes(seconds):
    minutes = round(seconds / 60)
    return f"{minutes // 60}時間{minutes % 60:02d}分" if minutes >= 60 else f"{minutes}分"


def _clock(moment):
    return datetime.fromisoformat(str(moment).replace("Z", "+00:00")).strftime("%H:%M")


def _failed(part):
    if part is None:
        return "<p class='muted'>読んでいない。</p>"
    if isinstance(part, dict) and part.get("error"):
        return f"<p class='muted'>読めなかった: {_e(part['error'])}</p>"
    return None


def done_section(done):
    """マージした PR・閉じた Issue・リポジトリ別のコミット数。"""
    failed = _failed(done)
    if failed:
        return failed
    items = [f"<li><span class='tag'>PR</span> {_e(row['repo'])}#{_e(row['number'])} "
             f"{_e(row['title'])}</li>" for row in done["prs"]]
    items += [f"<li><span class='tag'>Issue</span> {_e(row['repo'])}#{_e(row['number'])} "
              f"{_e(row['title'])}</li>" for row in done["issues"]]
    items += [f"<li class='muted'>読めなかった検索: {_e(error)}</li>"
              for error in done.get("errors") or []]
    commits = done.get("commits") or {}
    if commits:
        listed = "、".join(f"{_e(repo)} {count}" for repo, count in commits.items())
        items.append(f"<li><span class='tag'>コミット</span> {sum(commits.values())}件"
                     f"（{listed}）</li>")
    if not items:
        return "<p class='muted'>GitHub に残った成果は無い（マージ・クローズ・コミットなし）。</p>"
    return f"<ul class='done'>{''.join(items)}</ul>"


def work_section(groups, present_net_sec):
    """リポジトリ × 作業の入力あり時間。割り付けられた割合も出す。"""
    failed = _failed(groups)
    if failed:
        return failed
    total = sum(group["active_sec"] for group in groups)
    shown = [group for group in groups if group["active_sec"] >= MIN_SHOWN_SEC]
    if not shown:
        return ("<p class='muted'>ターミナルの Claude 作業にも GitHub の閲覧にも"
                "当たる時間が無い。</p>")
    rows = []
    for group in shown:
        tasks = "".join(
            f"<li>{_e(task['task'])} <span class='muted'>{_minutes(task['active_sec'])}"
            f"（{_clock(task['first'])}–{_clock(task['last'])}）</span></li>"
            for task in group["tasks"] if task["active_sec"] >= MIN_SHOWN_SEC)
        cell = (f"<ul class='tasks'>{tasks}</ul>" if tasks
                else "<span class='muted'>1分未満の作業の積み重ね</span>")
        rows.append(f"<tr><td class='t'>{_e(group['repo'])}</td>"
                    f"<td class='n'>{_minutes(group['active_sec'])}</td><td>{cell}</td></tr>")
    share = f"{total / present_net_sec:.0%}" if present_net_sec else "―"
    note = (f"<p class='sub'>入力ありの時間のうち {share}（{_minutes(total)}）を"
            "リポジトリに割り付けた。残りはアプリ別の表を見る。</p>")
    return (note + "<table><tr><th>リポジトリ</th><th>入力あり</th><th>作業</th></tr>"
            + "".join(rows) + "</table>")


def _ahead(event, guess):
    """これからの予定に、同じ場所の過去の予定から見た「出る・終わる」の目安を付ける。"""
    if not guess:
        return "これから"
    start = datetime.fromisoformat(str(event["start"]).replace("Z", "+00:00"))
    parts = [f"これから {guess['place']}"]
    if guess.get("leave") is not None:
        parts.append(f"{start + timedelta(minutes=guess['leave']):%H:%M} ごろ出る")
    if guess.get("done") is not None:
        parts.append(f"{start + timedelta(minutes=guess['done']):%H:%M} ごろ終わる")
    return "・".join(parts) + f"（過去{guess['n']}回）"


def agenda_section(agenda):
    """その日の予定と、実際にいた場所・時刻。"""
    failed = _failed(agenda)
    if failed:
        return failed
    checked, upcoming = agenda["checked"], agenda["upcoming"]
    if not checked and not upcoming:
        return "<p class='muted'>この日の時刻付きの予定は無い。</p>"
    rows = []
    for row in checked:
        event = row["event"]
        actual = row["status"]
        if row.get("actual_start"):
            actual = (f"{row['actual_start']:%H:%M}–{row['actual_end']:%H:%M} "
                      f"{row.get('place') or ''}（{row['status']}）")
        rows.append(f"<tr><td class='n'>{_clock(event['start'])}–{_clock(event['end'])}</td>"
                    f"<td class='t'>{_e(event['title'])}</td><td>{_e(actual)}</td></tr>")
    for item in upcoming:
        event = item["event"]
        rows.append(f"<tr><td class='n'>{_clock(event['start'])}–{_clock(event['end'])}</td>"
                    f"<td class='t'>{_e(event['title'])}</td>"
                    f"<td class='muted'>{_e(_ahead(event, item.get('forecast')))}</td></tr>")
    return ("<table><tr><th>予定の時刻</th><th>予定</th><th>実際</th></tr>"
            + "".join(rows) + "</table>")
