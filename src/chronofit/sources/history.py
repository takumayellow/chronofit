"""ブラウザ履歴を「分類済みの過去の時間」へ変える。

`browser.py` は原本を保全するだけで、それ自体は予定の役に立たない。ここでやるのは
**溜まっているものを使える形にする**ことで、L0 が動き出す前の期間についてはこれが
唯一の痕跡になる（実測では Vivaldi に16日分しか残っていなかった）。

訪問時刻の列は「点」であって長さを持たない。長さは次の訪問との間隔から復元する:

- 次の訪問まで `GAP_SEC` 未満なら、その間ずっと前の頁を見ていたとみなす
- 空いていたら、その頁は `TAIL_SEC` だけ見て離れたとみなす（開いたまま離席した扱い）

これは L0 の実測ほど正確ではない。**過去の埋め合わせと分類にだけ使い、
所要時間DBの実測としては入れない**（`source` を分ける理由がここにある）。
"""
import re
from datetime import datetime
from urllib.parse import urlsplit

GAP_SEC = 900.0     # これ以上空いたら連続した閲覧とみなさない（15分）
TAIL_SEC = 180.0    # 途切れる直前の1頁に与える長さ（3分）


def domain(url):
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def classify(url, title, rules):
    """URL とタイトルを利用側の規則へ当てる。当たらなければ None。

    規則は `title_rules` と同じ形に揃える（`match` / `subject` / `kind`）。
    `match` は URL とタイトルの両方に当てる — 科目名はタイトル側にしか出ないことが多い。
    """
    haystack = f"{url}\n{title or ''}"
    for rule in rules:
        pattern = rule.get("match")
        if not pattern:
            continue
        try:
            if re.search(pattern, haystack, re.IGNORECASE):
                return {"subject": rule.get("subject"),
                        "kind": rule.get("kind"),
                        "category": rule.get("category")}
        except re.error:
            continue   # 設定の正規表現が壊れていても分類を止めない
    return None


def spans(visits, gap_sec=GAP_SEC, tail_sec=TAIL_SEC):
    """訪問の列を (開始, 秒, URL, タイトル) の列へ。長さを復元する。"""
    ordered = sorted(visits, key=lambda visit: visit[0])
    result = []
    for position, (moment, url, title) in enumerate(ordered):
        if position + 1 < len(ordered):
            delta = (ordered[position + 1][0] - moment).total_seconds()
            seconds = delta if 0 < delta < gap_sec else tail_sec
        else:
            seconds = tail_sec
        result.append((moment, seconds, url, title))
    return result


def dedupe(visits):
    """複数のスナップショットに同じ訪問が入っているので落とす。"""
    seen = set()
    unique = []
    for moment, url, title in visits:
        key = (moment, url)
        if key in seen:
            continue
        seen.add(key)
        unique.append((moment, url, title))
    return unique


def collect(histories, since=None):
    """(名前, プロファイル, パス) の列から訪問を全部読む。壊れた1本で止めない。"""
    from . import browser

    gathered = []
    for entry in histories:
        path = entry[-1]
        try:
            gathered.extend(browser.read_visits(path, since))
        except Exception:
            continue
    return dedupe(gathered)


def snapshots(snapshot_root):
    """退避済みの History を新しい順で列挙する。"""
    if not snapshot_root.is_dir():
        return []
    found = []
    for day in sorted(snapshot_root.iterdir(), reverse=True):
        if day.is_dir():
            found.extend(("snapshot", day.name, path)
                         for path in sorted(day.glob("*History.sqlite")))
    return found


def daily(visits, rules, gap_sec=GAP_SEC, tail_sec=TAIL_SEC):
    """日ごと・分類ごとの秒数。分類できなかったぶんはドメイン別に残す。"""
    days = {}
    for moment, seconds, url, title in spans(visits, gap_sec, tail_sec):
        date = moment.strftime("%Y-%m-%d")
        day = days.setdefault(date, {"date": date, "sec": 0.0,
                                     "by_label": {}, "unknown": {}})
        day["sec"] += seconds
        hit = classify(url, title, rules)
        if hit:
            label = hit.get("subject") or hit.get("category") or "分類あり"
            day["by_label"][label] = day["by_label"].get(label, 0.0) + seconds
        else:
            host = domain(url) or "(不明)"
            day["unknown"][host] = day["unknown"].get(host, 0.0) + seconds
    return [days[key] for key in sorted(days)]


def unclassified(days, limit=10):
    """分類規則がまだ無いドメインを重い順に。規則を育てるための材料。"""
    totals = {}
    for day in days:
        for host, seconds in day["unknown"].items():
            totals[host] = totals.get(host, 0.0) + seconds
    return sorted(totals.items(), key=lambda item: -item[1])[:limit]


def parse_since(value):
    return datetime.strptime(value, "%Y-%m-%d") if value else None
