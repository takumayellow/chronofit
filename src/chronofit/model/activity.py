"""入力のあった時間を、プロジェクトに割り付かなかった分まで全部どこかへ寄せる。

`work.attribute` が決めるのは、会話の作業名と GitHub のページから分かるリポジトリだけ。
それ以外（Slack・動画・作った物の確認・名前の無いターミナル …）は、次の順で決める。

1. 会話の作業名・GitHub のページ（`work.span_key`）
2. ブラウザはページのタイトルで閲覧履歴を引き、URL を得る。GitHub の URL はそのリポジトリへ
3. 利用側の規則 `activity_rules`（`{match, project | group, label, follow_claude}`）。
   `match` は「プロセス名・URL・タイトル」をつないだ文字列に当てる
4. 名前の無いターミナルの窓（会話が題を付ける前）は、同じ tmux の窓に前後で付いた作業名。
   それも無ければ、その時刻に動いていた会話の作業ディレクトリの多数決
5. 閲覧の分類規則（`title_rules` / `url_rules`）
6. どれにも当たらなければ未分類（プロセスとタイトルのまま残し、規則を育てる材料にする）

`follow_claude` の規則（作った物のプレビューなど）は、直前 `FOLLOW_GAP_SEC` 以内に見ていた
ターミナルの作業のリポジトリへ寄せる。無ければ会話の多数決、それも無ければ `group` へ入れる。
確認の画面は、直前に Claude と話していた作業の結果であることがほとんどだから。

外出（家と移動を除く滞在）も、この日にしたこととして並べる。
"""
import re
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from . import work
from ..sources import claude_sessions, history

CLAUDE_WINDOW_SEC = 900        # 多数決は、この前後に記録のある会話から決める
CLAUDE_FALLBACK_SEC = 1800
FOLLOW_GAP_SEC = 900           # 確認の画面は、この時間以内に見ていたターミナルの作業へ寄せる
SAME_WINDOW_SEC = 3 * 3600     # 名前の無い窓は、この時間以内に同じ窓へ付いた作業名を使う
URL_MAX_GAP = timedelta(hours=6)   # これより離れた同じタイトルの訪問は別の閲覧とみなす
MIN_OUTING_SEC = 300
UNNAMED_CLAUDE = "Claude Code（名前の無い窓）"
STUDY_GROUP = "学習"

_BROWSER_SUFFIX = re.compile(
    r"\s+[-—]\s+(Vivaldi|Google Chrome|Microsoft​? Edge|Mozilla Firefox|Chromium)$")
_GITHUB_URL = re.compile(r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9._-]+)")
_TMUX_WINDOW = re.compile(r"^(\S+ \d+):")
_GITHUB_RESERVED = {"settings", "notifications", "orgs", "users", "search", "marketplace",
                    "pulls", "issues", "explore", "topics", "sponsors", "login", "new"}


def page_title(title):
    """ブラウザの窓のタイトルから、末尾のブラウザ名を外す。"""
    return _BROWSER_SUFFIX.sub("", title or "").strip()


def url_index(visits):
    """閲覧履歴（naive UTC の時刻, URL, タイトル）を、タイトル → 時刻順の (時刻, URL) へ。"""
    index = {}
    for moment, url, title in visits:
        if not title or not url:
            continue
        aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        index.setdefault(title.strip(), []).append((aware, url))
    for rows in index.values():
        rows.sort(key=lambda row: row[0])
    return index


def url_for(span, index, max_gap=URL_MAX_GAP):
    """ブラウザのスパンが見ていたページの URL。同じタイトルの訪問のうち時刻が一番近いもの。"""
    rows = index.get(page_title(span.get("title")))
    if not rows:
        return None
    moment = datetime.fromisoformat(span["start"])
    position = bisect_left(rows, moment, key=lambda row: row[0])
    near = [rows[i] for i in (position - 1, position) if 0 <= i < len(rows)]
    best = min(near, key=lambda row: abs(row[0] - moment))
    return best[1] if abs(best[0] - moment) <= max_gap else None


def github_url_repo(url):
    match = _GITHUB_URL.match(url or "")
    if not match or match.group(1).lower() in _GITHUB_RESERVED:
        return None
    return match.group(2).removesuffix(".git")


def match_rule(rules, proc, url, title):
    haystack = f"{proc or ''}\n{url or ''}\n{title or ''}"
    for rule in rules or []:
        pattern = rule.get("match") if isinstance(rule, dict) else None
        if not pattern:
            continue
        try:
            if re.search(pattern, haystack, re.IGNORECASE):
                return rule
        except re.error:
            continue    # 設定の正規表現が壊れていても割り付けを止めない
    return None


def _host(url):
    try:
        host = urlsplit(url or "").hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _app(proc):
    return (proc or "").removesuffix(".exe") or "(不明なアプリ)"


def _seconds_between(a, b):
    return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds())


class _Resolver:
    """スパンを時刻順に1本ずつ決める。直前に見ていたターミナルの作業を覚えておく。"""

    def __init__(self, spans, sessions, cwds_near, visits, rules, browse_rules, repo_of):
        self.sessions, self.cwds_near, self.repo_of = sessions or {}, cwds_near, repo_of
        self.repo_cache, self.index = {}, url_index(visits)
        self.rules, self.browse_rules = rules or [], browse_rules or []
        seen, self.all_sessions = set(), []
        for group in self.sessions.values():
            for session in group:
                if id(session) not in seen:
                    seen.add(id(session))
                    self.all_sessions.append(session)
        self.all_marks = claude_sessions.MarkIndex(self.all_sessions)
        self.keys = [self._project_key(span) for span in spans]
        self.windows = {}         # tmux の窓 → [(開始, (リポジトリ, 作業))]
        for span, key in zip(spans, self.keys):
            window = _window(span)
            if key and window:
                self.windows.setdefault(window, []).append((span["start"], key))
        self.last_terminal = None  # (終わり, (リポジトリ, 作業))

    def _project_key(self, span):
        return work.span_key(span, self.sessions, self.cwds_near, self.repo_of, self.repo_cache)

    def _vote(self, span):
        if not self.all_sessions:
            return None
        cwds = self.all_marks.near(datetime.fromisoformat(span["start"]),
                                   window_sec=CLAUDE_WINDOW_SEC,
                                   fallback_max_sec=CLAUDE_FALLBACK_SEC)
        return work.vote(cwds, self.repo_of, self.repo_cache)

    def _same_window(self, span):
        named = self.windows.get(_window(span)) or []
        near = [(_seconds_between(start, span["start"]), key) for start, key in named]
        near = [row for row in near if row[0] <= SAME_WINDOW_SEC]
        return min(near, key=lambda row: row[0])[1] if near else None

    def _followed(self, span):
        if self.last_terminal and _seconds_between(self.last_terminal[0],
                                                   span["start"]) <= FOLLOW_GAP_SEC:
            return self.last_terminal[1][0]
        return self._vote(span)

    def decide(self, position, span):
        """スパン1本の行き先 (プロジェクト, 括り, 名前, 内訳)。プロジェクトと括りはどちらか一方。"""
        proc, title = span.get("proc"), span.get("title") or ""
        key = self.keys[position]
        if key and proc in work.TERMINALS:
            self.last_terminal = (span["end"], key)
        if key:
            return key[0], None, key[1], None
        browser = proc in work.BROWSERS
        url = url_for(span, self.index) if browser else None
        shown = page_title(title) if browser else title
        repo = github_url_repo(url)
        if repo:
            return repo, None, work.GITHUB_VIEW, shown
        rule = match_rule(self.rules, proc, url, title)
        if rule:
            label = rule.get("label") or _host(url) or shown or _app(proc)
            repo = self._followed(span) if rule.get("follow_claude") else None
            if repo or rule.get("project"):
                return repo or rule["project"], None, label, shown
            return None, rule.get("group") or "その他", label, shown
        if proc in work.TERMINALS:
            named = self._same_window(span)
            if named:
                self.last_terminal = (span["end"], named)
                return named[0], None, named[1], None
            repo = self._vote(span)
            if repo:
                return repo, None, UNNAMED_CLAUDE, None
        hit = history.classify(url or "", title, self.browse_rules) if (url or title) else None
        if hit and hit.get("subject"):
            return None, STUDY_GROUP, hit["subject"], shown
        if hit and hit.get("category"):
            return None, hit["category"], _host(url) or shown or _app(proc), shown
        return None, None, _app(proc), shown or "(タイトル無し)"


def _window(span):
    if span.get("proc") not in work.TERMINALS:
        return None
    match = _TMUX_WINDOW.match(span.get("title") or "")
    return match.group(1) if match else None


def decisions(spans, sessions, cwds_near, visits=(), rules=(), browse_rules=(),
              repo_of=work.repo_name):
    """入力のあったスパンを時刻順に、`(スパン, (プロジェクト, 括り, 名前, 内訳))` で返す。

    `attribute` の集計の元。スパン単位で数え直したいとき（レポート1本の時間など）に使う。
    """
    spans = sorted((span for span in spans if float(span.get("active_sec") or 0) > 0),
                   key=lambda span: span["start"])
    resolver = _Resolver(spans, sessions, cwds_near, visits, rules, browse_rules, repo_of)
    return [(span, resolver.decide(position, span)) for position, span in enumerate(spans)]


def attribute(spans, sessions, cwds_near, visits=(), rules=(), browse_rules=(),
              repo_of=work.repo_name):
    """入力のあったスパンを全部、行き先ごとの行へまとめる。

    返り値は {"projects", "activities", "unclassified", "sec"}:

    - projects: `work.by_repo` と同じ形（リポジトリ → 作業）
    - activities: [{"group", "label", "active_sec", "tasks"}]（tasks はページや窓のタイトル）
    - unclassified: [{"label"（アプリ）, "active_sec", "tasks"}]
    - sec: 入力のあった時間の合計
    """
    project_rows, activity_rows, total = {}, {}, 0.0
    for span, (project, group, name, detail) in decisions(
            spans, sessions, cwds_near, visits, rules, browse_rules, repo_of):
        seconds = float(span["active_sec"])
        total += seconds
        if project:
            _add(project_rows, (project, name), {"repo": project, "task": name}, span, seconds)
        else:
            row = _add(activity_rows, (group, name), {"group": group, "label": name,
                                                      "details": {}}, span, seconds)
            if detail:
                _add(row["details"], detail, {"task": detail}, span, seconds)
    projects = work.by_repo(sorted(project_rows.values(), key=lambda row: -row["active_sec"]))
    activities, unclassified = [], []
    for row in sorted(activity_rows.values(), key=lambda row: -row["active_sec"]):
        tasks = sorted(row.pop("details").values(), key=lambda part: -part["active_sec"])
        item = {**row, "tasks": tasks}
        (activities if row["group"] else unclassified).append(item)
    return {"projects": projects, "activities": activities, "unclassified": unclassified,
            "sec": total}


def _add(table, key, base, span, seconds):
    row = table.setdefault(key, {**base, "active_sec": 0.0,
                                 "first": span["start"], "last": span["end"]})
    row["active_sec"] += seconds
    row["first"] = min(row["first"], span["start"])
    row["last"] = max(row["last"], span["end"])
    return row


def _overlap(a_start, a_end, b_start, b_end):
    return max(0.0, (min(a_end, b_end) - max(a_start, b_start)).total_seconds())


def _event_time(value):
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.astimezone()


def outings(visits, events, bounds, skip_places, phone_spans=()):
    """その日の外出（家と移動を除く滞在）を、用事とスマホの時間つきで時刻順に。

    用事は決済や繰り返しの予定で付いたもの（`activity`）を先に使い、無ければ滞在の半分以上、
    または予定の半分以上が重なったその日の予定の件名を使う。
    """
    lo, hi = bounds
    result = []
    for visit in visits:
        if visit["place"] in skip_places:
            continue
        start, end = max(visit["start"], lo), min(visit["end"], hi)
        seconds = (end - start).total_seconds()
        if seconds < MIN_OUTING_SEC:
            continue
        titles = []
        for event in events or []:
            if not event.get("title"):
                continue
            e_start, e_end = _event_time(event["start"]), _event_time(event["end"])
            shared = _overlap(start, end, e_start, e_end)
            length = (e_end - e_start).total_seconds()
            if shared and (shared >= seconds / 2 or (length and shared >= length / 2)):
                titles.append(event["title"])
        phone = sum(_overlap(start, end, a, b) for a, b in phone_spans or [])
        result.append({"place": visit["place"], "start": start, "end": end, "sec": seconds,
                       "activity": visit.get("activity"), "events": titles,
                       "phone_sec": phone})
    return sorted(result, key=lambda row: row["start"])
