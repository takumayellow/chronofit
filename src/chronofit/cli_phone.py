"""スマホの使用状況（`phone`）のサブコマンドと、1日の要約への睡眠・スマホの書き足し。

    python -m chronofit phone pull     usagestats を読んで git の外へ足す（定期実行用）
    python -m chronofit phone apps     アプリごとの前面時間とカテゴリ（対応表を書くため）
"""
from datetime import datetime, time, timedelta
from pathlib import Path

from . import config, paths
from .model import phone as phone_model
from .model import rollup
from .sources import phone


def _log(root, message):
    paths.ensure(root)
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    with (root / "pull.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def pull(settings=None):
    """端末から直近24時間のイベントを読んで足す。返り値は足した件数。

    ログには件数だけを書く。端末の宛先もパッケージ名も書かない。
    """
    options = config.phone(settings)
    root = paths.phone_dir()
    now = datetime.now().astimezone()
    try:
        _, text = phone.fetch(options.get("adb_serials"), adb=options.get("adb") or "adb")
    except RuntimeError as error:
        _log(root, f"failed {error}")
        raise
    events = phone.parse_usagestats(text)
    if not events:
        _log(root, "failed 読めたがイベントが0件")
        raise RuntimeError("usagestats にイベントが無い（端末の使用状況の記録が止まっていないか）")
    added = phone.append(events, root)
    phone.record_pull(root, now)
    removed = phone.purge(root, now.date(), options.get("retention_days"))
    _log(root, f"ok events={len(events)} added={added} purged_days={removed}")
    return added


def _day_bounds(date):
    day = datetime.strptime(date, "%Y-%m-%d").date()
    start = datetime.combine(day, time()).astimezone()
    return day, start, start + timedelta(days=1)


def _pc_activity(day):
    """前後の日を含む、PC で手が動いていた区間。睡眠の判定で「起きていた」証拠に使う。"""
    spans = []
    for offset in (-1, 0, 1):
        path = paths.raw_dir() / f"{day + timedelta(days=offset)}.jsonl"
        if not path.is_file():
            continue
        spans += [(s["start"], s["end"]) for s in rollup.to_segments(rollup.read_day(path))
                  if s["kind"] == "present" and s.get("active_sec", 0.0) > 0]
    return spans


def annotate_summary(summary, date, settings=None):
    """1日の要約に、睡眠・スマホの使用・カテゴリ別の時間・起床と就寝を足す。

    スマホのイベントが1つも無い日は何も足さない。
    """
    options = config.phone(settings)
    root = paths.phone_dir()
    events = phone.events_around(date, root)
    if not events:
        return summary
    day, start, end = _day_bounds(date)
    now = datetime.now().astimezone()
    usage = phone_model.usage_intervals(events, until=now)
    activity = _pc_activity(day) + usage
    covered = phone.covered_spans(root)
    sleep_settings = options.get("sleep") or {}
    tonight = phone_model.detect_sleep(day + timedelta(days=1), activity, covered, sleep_settings)
    last_night = phone_model.detect_sleep(day, activity, covered, sleep_settings)
    sleeps = [s for s in (last_night, tonight) if s]
    apps = phone_model.app_seconds(events, start, end, until=now)
    categories = phone_model.by_category(apps, options.get("categories") or {})
    phone_model.annotate(summary, sleeps,
                         phone_model.clip(usage, start, end), categories,
                         bounds=(start, end))
    if "sleep_sec" in summary:
        summary.update(phone_model.wake_and_bed(last_night, tonight))
    return summary


def show_apps(days=7, settings=None, today=None):
    """直近の日ごとに、アプリ（パッケージ）ごとの前面時間を長い順に出す。

    パッケージ名を画面にだけ出す。カテゴリの対応表を書くときの材料で、ファイルには残さない。
    """
    options = config.phone(settings)
    mapping = options.get("categories") or {}
    root = paths.phone_dir()
    last = today or datetime.now().date()
    totals = {}
    for offset in range(days):
        date = (last - timedelta(days=offset)).isoformat()
        _, start, end = _day_bounds(date)
        events = phone.events_around(date, root)
        for package, seconds in phone_model.app_seconds(events, start, end).items():
            totals[package] = totals.get(package, 0.0) + seconds
    if not totals:
        print("スマホのイベントが無い（`chronofit phone pull` が動いているか確かめる）")
        return 1
    print(f"直近 {days} 日のアプリ別の前面時間（1日平均）")
    for package, seconds in sorted(totals.items(), key=lambda kv: -kv[1]):
        category = mapping.get(package) or f"({phone_model.UNCATEGORIZED})"
        print(f"  {seconds / days / 60:6.1f}分  {category:10}  {package}")
    return 0


def cmd_phone(args):
    if args.action == "pull":
        try:
            added = pull()
        except RuntimeError as error:
            print(error)
            return 1
        print(f"足したイベント {added} 件 -> {Path(paths.phone_dir())}")
        return 0
    return show_apps(days=args.days)


def register(sub):
    cmd = sub.add_parser("phone", help="スマホの使用状況（睡眠・アプリ別の時間）")
    cmd.add_argument("action", nargs="?", default="apps", choices=("pull", "apps"))
    cmd.add_argument("--days", type=int, default=7, help="apps: 直近何日を見るか（既定 7）")
    cmd.set_defaults(func=cmd_phone)
