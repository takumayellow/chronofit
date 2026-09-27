"""Claude との会話に、どのプロジェクトでどれだけ時間を使ったか。

見たいのは開発の速さではなく、**持ち時間の配分**である。会話の1往復を2つに分ける:

- 待ち（`wait`）: 人が入力してから、その往復が終わるまで。Claude が動いている時間
- 対話（`talk`）: 往復が終わってから、次に人が入力するまで。読んで考えて打つ時間。
  `TALK_CAP` より空いたら席を離れたとみなし、その間は数えない

往復の終わりが記録されないまま次の入力が来た（途中で止めた）ときは、次の入力までを
待ちとし、`TALK_CAP` で打ち切る。往復の終わりに所要時間が記録されていれば、待ちは
そこから逆算した開始より前に伸ばさない（途中で止めた入力の後に何日も経ってから
再開した会話を、丸ごと待ちに数えないため）。1往復の待ちは `WAIT_CAP` までしか数えない
（何日も自走させた往復は、持ち時間を使っていない）。

複数の会話を並行させている時間は、動いていた会話の数で等分する。そうしないと
3つ並べた1時間が3時間に数えられ、持ち時間の配分として意味を失う。

振り分けは、利用側の規則（`project_rules`、次に `title_rules`）を会話の題、次に作業ディレクトリに
当て、当たらなければ作業ディレクトリのリポジトリ名、それも無ければ `UNSORTED`。
"""
import re
from collections import Counter
from datetime import datetime, time, timedelta

from ..sources.claude_turns import END, PROMPT

TALK_CAP = timedelta(minutes=10)
WAIT_CAP = timedelta(minutes=30)
WAIT, TALK = "wait", "talk"
UNSORTED = "未分類"


def _first_match(text, rules, field):
    if not text:
        return None
    for rule in rules:
        pattern = rule.get("match")
        if not pattern or not rule.get(field):
            continue
        try:
            if re.search(pattern, text, re.IGNORECASE):
                return rule
        except re.error:
            continue
    return None


def classify(cwd, title, project_rules, title_rules, repo_of, kind_rules=()):
    """`(プロジェクト, 種別 or None)`。題を先に、作業ディレクトリを後に見る。

    題は作業ディレクトリより確かなので、どちらの規則でも題に当たればそれを採る
    （共通の置き場で開いた会話は、作業ディレクトリでは何の作業か分からない）。
    `kind_rules` は題だけに当て、当たれば種別をそれで上書きする（科目は作業ディレクトリで
    決まり、レポートかどうかは題で決まる、という会話のため）。
    """
    kind_rule = _first_match(title, kind_rules, "kind")
    project, kind = _project(cwd, title, project_rules, title_rules, repo_of)
    return project, (kind_rule["kind"] if kind_rule else kind)


def _project(cwd, title, project_rules, title_rules, repo_of):
    for text in (title, cwd.replace("\\", "/") if cwd else None):
        for rules, field in ((project_rules, "project"), (title_rules, "subject")):
            rule = _first_match(text, rules, field)
            if rule:
                return rule[field], rule.get("kind")
    name, in_git = repo_of(cwd)
    return (name if in_git else UNSORTED), None


def pieces(events, talk_cap=TALK_CAP, wait_cap=WAIT_CAP):
    """1本の会話の出来事の列 → `[(開始, 終了, WAIT|TALK, 作業ディレクトリ, 題)]`。"""
    result, prompt, ended = [], None, None
    for moment, kind, cwd, title, duration in events:
        if kind == PROMPT:
            if prompt is not None and moment - prompt[0] <= talk_cap:
                result.append((prompt[0], moment, WAIT, prompt[1], prompt[2]))
            if ended is not None and moment - ended[0] <= talk_cap:
                result.append((ended[0], moment, TALK, ended[1], ended[2]))
            prompt, ended = (moment, cwd, title), None
        elif kind == END and prompt is not None:
            start = prompt[0] if duration is None else max(prompt[0], moment - duration)
            result.append((start, min(moment, start + wait_cap), WAIT, prompt[1], prompt[2]))
            prompt, ended = None, (moment, cwd, title)
    return [piece for piece in result if piece[1] > piece[0]]


def _split_days(start, end):
    """現地時刻の0時で区切る。"""
    while start < end:
        local = start.astimezone()
        midnight = datetime.combine(local.date() + timedelta(days=1), time()).astimezone()
        cut = min(end, midnight)
        yield local.date().isoformat(), start, cut
        start = cut


def allocate(labeled):
    """`[(開始, 終了, キー)]` → `{日: {キー: 秒}}` と `{日: 重なりを除いた合計秒}`。

    境目ごとに区切り、その区間で動いていた会話の数で等分する。
    """
    points = sorted(((start, 1, key) for start, _, key in labeled),
                    key=lambda point: point[0])
    points = sorted(points + [(end, -1, key) for _, end, key in labeled],
                    key=lambda point: (point[0], point[1]))
    shares, union, active, previous = {}, {}, Counter(), None
    for moment, delta, key in points:
        if previous is not None and moment > previous and active:
            count = sum(active.values())
            for day, a, b in _split_days(previous, moment):
                seconds = (b - a).total_seconds()
                union[day] = union.get(day, 0.0) + seconds
                bucket = shares.setdefault(day, {})
                for name, weight in active.items():
                    bucket[name] = bucket.get(name, 0.0) + seconds * weight / count
        active[key] += delta
        if active[key] <= 0:
            del active[key]
        previous = moment
    return shares, union


def summarize(sessions, classify_one, talk_cap=TALK_CAP):
    """会話ごとの出来事の列 → `{日: {"total_sec", "projects": {名前: 内訳}}}`。

    内訳は `{"sec", "wait_sec", "talk_sec", "prompts", "kinds": {種別: 秒}}`。
    `classify_one(作業ディレクトリ, 題)` は `(プロジェクト, 種別)` を返す。
    """
    labeled, prompts = [], {}
    for events in sessions:
        for start, end, part, cwd, title in pieces(events, talk_cap):
            labeled.append((start, end, (*classify_one(cwd, title), part)))
        for moment, kind, cwd, title, _ in events:
            if kind == PROMPT:
                day = moment.astimezone().date().isoformat()
                project = classify_one(cwd, title)[0]
                bucket = prompts.setdefault(day, {})
                bucket[project] = bucket.get(project, 0) + 1
    shares, union = allocate(labeled)
    days = {}
    for day in sorted(set(shares) | set(prompts)):
        projects = {}
        for (project, kind, part), seconds in shares.get(day, {}).items():
            entry = projects.setdefault(project, {"sec": 0.0, "wait_sec": 0.0, "talk_sec": 0.0,
                                                  "prompts": 0, "kinds": {}})
            entry["sec"] += seconds
            entry[f"{part}_sec"] += seconds
            if kind:
                entry["kinds"][kind] = entry["kinds"].get(kind, 0.0) + seconds
        for project, count in prompts.get(day, {}).items():
            projects.setdefault(project, {"sec": 0.0, "wait_sec": 0.0, "talk_sec": 0.0,
                                          "prompts": 0, "kinds": {}})["prompts"] = count
        days[day] = {"total_sec": round(union.get(day, 0.0)),
                     "projects": {name: _rounded(entry) for name, entry in projects.items()}}
    return days


def _rounded(entry):
    return {"sec": round(entry["sec"]), "wait_sec": round(entry["wait_sec"]),
            "talk_sec": round(entry["talk_sec"]), "prompts": entry["prompts"],
            "kinds": {kind: round(sec) for kind, sec in entry["kinds"].items()}}
