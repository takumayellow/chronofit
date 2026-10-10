"""`chronofit reports`: 連作のレポート1本ごとの作業時間を出し、完了した回を所要時間DBへ入れる。

連作は利用側の設定 `report_series` に書く（科目名や回の書き方は人によるので、リポジトリには置かない）:

    {"subject": "実験", "kind": "レポート", "repo": "lab-2026",
     "unit_patterns": ["theme(?P<theme>\\\\d)/(?P<n>\\\\d+)"], "target": "テーマ{theme} 第{n}回",
     "done_pattern": "提出を記録", "title_patterns": ["実験"], "span_patterns": ["^MATLAB"]}

`record` は完了した回を DB へ足し、作業中の回に使った時間を `report_progress.json` に残す。
`board` / `plan` はそれを読み、作業中の回の見積もりから使ったぶんを引く。
"""
import json
import sys
import unicodedata
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import cli_prs, config, paths, report_extras
from .estimate import db as estimate_db
from .estimate import kinds, report_actuals
from .model import activity, rollup
from .sources import claude_sessions, github_prs

LOOKBACK_DAYS = cli_prs.LOOKBACK_DAYS
PROGRESS_NAME = "report_progress.json"


def load_series(settings):
    """設定の連作。書き間違いは理由を出して、その項目だけ飛ばす。"""
    result = []
    for entry in settings.get("report_series") or []:
        try:
            result.append(report_actuals.compile_series(entry))
        except report_actuals.SeriesError as error:
            print(f"report_series を飛ばした: {error}", file=sys.stderr)
    return result


def _spans(since_day, until_day):
    spans, day = [], since_day
    while day <= until_day:
        path = paths.raw_dir() / f"{day.isoformat()}.jsonl"
        if path.exists():
            spans += [record for record in rollup.read_day(path) if record["t"] == "span"]
        day += timedelta(days=1)
    return spans


def measure(series_list, settings, since_day=None, archives=None, now=None):
    """連作ごとに `(連作, report_actuals.tally の結果)`。会話ログとスパンは一度だけ読む。"""
    if not series_list:
        return []
    now = now or datetime.now().astimezone()
    since_day = since_day or now.date() - timedelta(days=LOOKBACK_DAYS)
    since = datetime.combine(since_day, time()).astimezone()
    archives = archives if archives is not None else cli_prs._dirs(settings,
                                                                    "claude_archive_dirs")
    sessions = cli_prs.read_sessions(settings, since, archives)
    engaged = cli_prs.engaged_by_repo(settings, since, archives, sessions)
    browse_rules = (settings.get("title_rules") or []) + (settings.get("url_rules") or [])
    decided = activity.decisions(_spans(since_day, now.date()), claude_sessions.index(sessions),
                                 claude_sessions.cwds_near,
                                 visits=report_extras._visits(since),
                                 rules=settings.get("activity_rules") or [],
                                 browse_rules=browse_rules)
    prs = github_prs.fetch_merged(since_day)
    result = []
    for series in series_list:
        counted = [(datetime.fromisoformat(span["start"]).astimezone(),
                    float(span["active_sec"]), span.get("title") or "")
                   for span, (project, *_rest) in decided
                   if report_actuals.counts(span, project, series)]
        tallied = report_actuals.tally(report_actuals.windows(prs, series, since, now),
                                       counted, engaged.get(series["repo"], []), series)
        result.append((series, tallied))
    return result


def progress_path():
    return paths.data_root() / PROGRESS_NAME


def save_progress(measured, now):
    units = [{"subject": series["subject"], "kind": series["kind"], "target": unit,
              "hours": hours}
             for series, tallied in measured
             for unit, hours in sorted(report_actuals.open_units(tallied).items())]
    path = progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"updated": now.isoformat(timespec="seconds"), "units": units},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def load_progress(path=None):
    """`{(科目, 種別, 回): 使った時間}`。無い・壊れているときは空（計画は止めない）。"""
    try:
        data = json.loads((path or progress_path()).read_text(encoding="utf-8"))
        return {(row["subject"], row["kind"], row["target"]): float(row["hours"])
                for row in data.get("units") or []}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def record(since_day=None, archives=None, quiet=False, settings=None):
    """完了した回を DB へ足し、作業中の回の時間を残す。足した行を返す。"""
    settings = settings if settings is not None else config.load()
    series_list = load_series(settings)
    if not series_list:
        if not quiet:
            print("report_series が設定に無い。")
        return []
    now = datetime.now().astimezone()
    measured = measure(series_list, settings, since_day, archives, now)
    db_path = estimate_db.default_path(paths.data_root())
    existing = estimate_db.load(db_path)
    added = []
    for series, tallied in measured:
        rows = report_actuals.new_rows(tallied, series, existing + added, estimate_db.make)
        for row in rows:
            estimate_db.append(db_path, row)
        added += rows
    save_progress(measured, now)
    if not quiet or added:
        for row in added:
            print(f"記録: {row['subject']} {row['kind']} {row['target']}  {row['net_hours']}h")
        if not added and not quiet:
            print("新しく完了した回は無い。")
    return added


def _fmt(value, unit=""):
    return "    -" + " " * len(unit) if value is None else f"{value:5.1f}{unit}"


def _pad(text, width):
    """全角を2桁として `width` 桁に揃える。"""
    used = sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)
    return text + " " * max(0, width - used)


def _row(label, entry, state=None):
    work, after = entry["work"] or {}, entry["after"]
    if state is None:
        state = f"完了 {entry['done'].astimezone():%m/%d}" if entry["done"] else "作業中"
    return (f"{_pad(label, 20)}{_fmt(work.get('net'), 'h')}  PC{_fmt(work.get('pc'))}  "
            f"Claude{_fmt(work.get('claude'))}  {_fmt(after and after['net'], 'h')}  {state}")


def forecast(instances, series, unit, spent):
    """作業中の回の残り。同じ種別の完了した回（この回を除く）から見積もる。"""
    others = [row for row in instances if not (row.get("subject") == series["subject"]
                                               and row.get("kind") == series["kind"]
                                               and row.get("target") == unit)]
    estimate = kinds.estimate_oneoff(others, series["subject"], series["kind"])
    if estimate.get("hours") is None:
        return estimate, None
    return estimate, report_actuals.remaining_hours(estimate["hours"], spent)


def show(since_day=None, archives=None):
    settings = config.load()
    series_list = load_series(settings)
    if not series_list:
        print("report_series が設定に無い（README の「レポート1本の所要時間」）。")
        return 1
    instances = estimate_db.load(estimate_db.default_path(paths.data_root()))
    for series, tallied in measure(series_list, settings, since_day, archives):
        print(f"{series['subject']} {series['kind']}（{series['repo']}）")
        print(f"{_pad('回', 20)} 所要   内訳（PC / Claude）       提出後  状態")
        shown = set(report_actuals.open_units(tallied)) | {
            unit for unit, entry in tallied.items() if entry["done"]}
        units = sorted((unit for unit in tallied if unit in shown),
                       key=lambda unit: tallied[unit]["first"] or datetime.max.astimezone())
        for unit in units:
            print(_row(unit, tallied[unit]))
        if report_actuals.COMMON in tallied:
            print(_row("共通（回の印なし）", tallied[report_actuals.COMMON], state=""))
        for unit, spent in report_actuals.open_units(tallied).items():
            estimate, left = forecast(instances, series, unit, spent)
            if left is None:
                print(f"  {unit}: 使った {spent:.1f}h。{estimate['note']}")
            else:
                print(f"  {unit}: 使った {spent:.1f}h / 見積もり {estimate['hours']:.1f}h"
                      f"（中央値 {estimate['median']:.1f}h, {estimate['note']}）"
                      f" -> 残り {left:.1f}h")
        print()
    return 0


def cmd_reports(args):
    since = date.fromisoformat(args.since) if args.since else None
    archives = [Path(value).expanduser() for value in args.archive] if args.archive else None
    if args.action == "record":
        record(since, archives)
        return 0
    return show(since, archives)


def register(sub):
    reports = sub.add_parser("reports", help="連作のレポート1本ごとの所要時間を出す / DB へ入れる")
    reports.add_argument("action", nargs="?", default="show", choices=("show", "record"))
    reports.add_argument("--since", help=f"この日から数える（既定は {LOOKBACK_DAYS} 日前）")
    reports.add_argument("--archive", action="append",
                         help="会話ログの退避先（<プロジェクト>/<会話>.jsonl）。何度でも指定できる")
    reports.set_defaults(func=cmd_reports)
