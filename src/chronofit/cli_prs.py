"""`chronofit prs`: マージした PR の作業時間を所要時間DBへ自動で入れる / 当たり具合を測る。"""
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import config, paths
from .estimate import db as estimate_db
from .estimate import pr_actuals
from .model import repo_context
from .sources import claude_sessions, github_prs

LOOKBACK_DAYS = 35   # 会話ログは既定で 30 日で消えるので、それより少し長く見る


def _dirs(settings, key):
    return [Path(value).expanduser() for value in settings.get(key) or []]


def _session_files(settings, since, archives):
    roots = _dirs(settings, "claude_dirs") or claude_sessions.default_roots()
    files = claude_sessions.transcript_files(roots, since)
    files += claude_sessions.archive_files(archives, since)
    return claude_sessions.unique_sessions(files)


def engaged_by_repo(settings, since, archives):
    """リポジトリ名 → Claude が動いていた区間。見つけたリポジトリの置き場所は残しておく。"""
    sessions = [claude_sessions.read_session(path)
                for path in _session_files(settings, since, archives)]
    resolver = repo_context.Resolver(repo_context.load_cache(paths.data_root()))
    resolver.learn(sessions)
    repo_context.save_cache(paths.data_root(), resolver.roots)
    return pr_actuals.engaged([resolver.marks(session) for session in sessions],
                              lambda repo: (repo, repo is not None))


def record(since_day=None, archives=None, quiet=False):
    """`since_day` 以降にマージした PR のうち、まだ DB に無いものを測って入れる。"""
    settings = config.load()
    since_day = since_day or date.today() - timedelta(days=LOOKBACK_DAYS)
    since = datetime.combine(since_day, time()).astimezone()
    archives = archives if archives is not None else _dirs(settings, "claude_archive_dirs")
    prs = github_prs.fetch_merged(since_day)
    measured = pr_actuals.measure(prs, engaged_by_repo(settings, since, archives), since)
    db_path = estimate_db.default_path(paths.data_root())
    rows = pr_actuals.new_rows(measured, estimate_db.load(db_path), estimate_db.make)
    for row in rows:
        estimate_db.append(db_path, row)
    if not quiet or rows:
        counts = {status: sum(1 for item in measured if item["status"] == status)
                  for status in (pr_actuals.MEASURED, pr_actuals.PARALLEL,
                                 pr_actuals.UNMEASURED)}
        print(f"PR {len(prs)}件  測れた {counts['measured']}  "
              f"並行して測れない {counts['parallel']}  会話ログ外 {counts['unmeasured']}  "
              f"-> 新しく記録 {len(rows)}件")
    return rows


def show_backtest():
    rows = [row for row in estimate_db.load(estimate_db.default_path(paths.data_root()))
            if row.get("source") == pr_actuals.SOURCE]
    result = pr_actuals.backtest(rows)
    if not result["repo"]:
        print("PR の実績が足りない（2件未満）。先に `chronofit prs record`。")
        return 1
    print(f"PR {result['n']}件を1件ずつ抜いて、残りの中央値で当てた")
    print("予測の元              平均誤差  誤差の中央値  2倍以内")
    for label, key in (("同じリポジトリ", "repo"), ("全体", "overall")):
        score = result[key]
        print(f"{label:16}  {score['mae']:6.2f}h  {score['median_ae']:8.2f}h  "
              f"{score['within_2x']:6.0%}")
    return 0


def cmd_prs(args):
    if args.action == "backtest":
        return show_backtest()
    since = date.fromisoformat(args.since) if args.since else None
    archives = [Path(value).expanduser() for value in args.archive] if args.archive else None
    record(since, archives)
    return 0


def register(sub):
    prs = sub.add_parser("prs", help="マージした PR の作業時間を所要時間DBへ入れる")
    prs.add_argument("action", nargs="?", default="record", choices=("record", "backtest"))
    prs.add_argument("--since", help=f"この日以降にマージした PR（既定は {LOOKBACK_DAYS} 日前）")
    prs.add_argument("--archive", action="append",
                     help="会話ログの退避先（<プロジェクト>/<会話>.jsonl）。何度でも指定できる")
    prs.set_defaults(func=cmd_prs)
