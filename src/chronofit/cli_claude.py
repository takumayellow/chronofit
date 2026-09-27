"""`chronofit claude`: Claude との会話に使った時間の配分を、日ごとに畳んで残す / 見る。"""
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import config, paths
from .model import claude_time, work
from .sources import claude_sessions, claude_turns

LOOKBACK_DAYS = 30   # 会話ログが消える前に畳み終える


def _dirs(settings, key):
    return [Path(value).expanduser() for value in settings.get(key) or []]


def _classifier(settings):
    project_rules = settings.get("project_rules") or []
    title_rules = settings.get("title_rules") or []
    kind_rules = settings.get("kind_rules") or []
    repos, cache = {}, {}

    def repo_of(cwd):
        if cwd not in repos:
            repos[cwd] = work.repo_name(cwd)
        return repos[cwd]

    def classify(cwd, title):
        if (cwd, title) not in cache:
            cache[(cwd, title)] = claude_time.classify(cwd, title, project_rules,
                                                       title_rules, repo_of, kind_rules)
        return cache[(cwd, title)]
    return classify


def compute(since_day, archives=None, settings=None):
    """`since_day` 以降の日ごとの配分（今日を含む）。"""
    settings = settings or config.load()
    since = datetime.combine(since_day, time()).astimezone()
    roots = _dirs(settings, "claude_dirs") or claude_sessions.default_roots()
    archives = archives if archives is not None else _dirs(settings, "claude_archive_dirs")
    files = claude_sessions.unique_sessions(
        claude_sessions.transcript_files(roots, since)
        + claude_sessions.archive_files(archives, since))
    sessions = (claude_turns.read_turns(path) for path in files)
    days = claude_time.summarize(sessions, _classifier(settings))
    return {day: value for day, value in days.items() if day >= since_day.isoformat()}


def _path(day):
    return paths.claude_time_dir() / f"{day}.json"


def load_day(day):
    try:
        return json.loads(_path(day).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save(days, today=None, replace=False):
    """終わった日だけ書く。会話ログが消えた後に測り直して減った日は上書きしない
    （`replace` なら上書きする。数え方を直したときに使う）。"""
    today = (today or date.today()).isoformat()
    written = []
    for day, value in sorted(days.items()):
        if day >= today:
            continue
        stored = load_day(day)
        if not replace and stored and stored.get("total_sec", 0) > value["total_sec"]:
            continue
        path = paths.ensure(paths.claude_time_dir()) / f"{day}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"date": day, **value}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        temporary.replace(path)
        written.append(day)
    return written


def record(since_day=None, archives=None, replace=False):
    since_day = since_day or date.today() - timedelta(days=LOOKBACK_DAYS)
    return save(compute(since_day, archives), replace=replace)


def _merge(days):
    total, projects = 0, {}
    for value in days:
        total += value.get("total_sec", 0)
        for name, entry in value.get("projects", {}).items():
            merged = projects.setdefault(name, {"sec": 0, "wait_sec": 0, "talk_sec": 0,
                                                "prompts": 0, "kinds": {}})
            for field in ("sec", "wait_sec", "talk_sec", "prompts"):
                merged[field] += entry.get(field, 0)
            for kind, sec in entry.get("kinds", {}).items():
                merged["kinds"][kind] = merged["kinds"].get(kind, 0) + sec
    return total, projects


def show(days_back=7, today=None):
    today = today or date.today()
    first = today - timedelta(days=days_back - 1)
    days = [(first + timedelta(days=i)).isoformat() for i in range(days_back)]
    stored = {day: load_day(day) for day in days[:-1]}
    # 残っていない日は、会話ログが残っている範囲で測り直す（今日は常に測る）
    missing = [day for day in days if not stored.get(day)]
    live = compute(date.fromisoformat(missing[0])) if missing else {}
    values = [stored.get(day) or live.get(day) for day in days]
    values = [value for value in values if value]
    total, projects = _merge(values)
    if not total:
        print("この期間の会話の記録が無い。")
        return 1
    print(f"{first} - {today}  Claude との会話 {total / 3600:.1f}h"
          f"（{len(values)}日ぶん・並行分は等分）")
    print("プロジェクト                 時間   割合   待ち   対話  入力  種別")
    for name, entry in sorted(projects.items(), key=lambda item: -item[1]["sec"]):
        if entry["sec"] < 60:
            continue
        kinds = " ".join(f"{kind}{sec / 3600:.1f}h" for kind, sec in
                         sorted(entry["kinds"].items(), key=lambda item: -item[1]))
        print(f"{name[:24]:24} {entry['sec'] / 3600:6.1f}h {entry['sec'] / total:5.0%} "
              f"{entry['wait_sec'] / 3600:5.1f}h {entry['talk_sec'] / 3600:5.1f}h "
              f"{entry['prompts']:5}  {kinds}")
    kinds = {}
    for entry in projects.values():
        for kind, sec in entry["kinds"].items():
            kinds[kind] = kinds.get(kind, 0) + sec
    if kinds:
        print("種別の合計: " + "  ".join(f"{kind} {sec / 3600:.1f}h（{sec / total:.0%}）" for kind, sec in
                                    sorted(kinds.items(), key=lambda item: -item[1])))
    return 0


def cmd_claude(args):
    if args.action == "record":
        since = date.fromisoformat(args.since) if args.since else None
        archives = [Path(value).expanduser() for value in args.archive] if args.archive else None
        written = record(since, archives, args.replace)
        print(f"{len(written)}日ぶんを書いた")
        return 0
    return show(args.days)


def register(sub):
    cmd = sub.add_parser("claude", help="Claude との会話に使った時間の配分")
    cmd.add_argument("action", nargs="?", default="show", choices=("show", "record"))
    cmd.add_argument("--days", type=int, default=7, help="直近何日を見るか（既定 7）")
    cmd.add_argument("--since", help=f"record: この日以降を畳む（既定は {LOOKBACK_DAYS} 日前）")
    cmd.add_argument("--archive", action="append",
                     help="record: 会話ログの退避先（<プロジェクト>/<会話>.jsonl）")
    cmd.add_argument("--replace", action="store_true",
                     help="record: 残っている日より少なくても上書きする（数え方を直したとき）")
    cmd.set_defaults(func=cmd_claude)
