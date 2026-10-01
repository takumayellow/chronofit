"""スマホの使用状況（`phone`）のサブコマンドと、1日の要約への睡眠・スマホの書き足し。

    python -m chronofit phone pull     usagestats を読んで git の外へ足す（定期実行用）
    python -m chronofit phone apps     アプリごとの前面時間とカテゴリ（対応表を書くため）
    python -m chronofit phone watch    再生中のタイトルを1分ごとに読み続ける（常駐用）
"""
import time as clock
from datetime import datetime, time, timedelta
from pathlib import Path

from . import config, paths
from .model import phone as phone_model
from .model import rollup
from .sources import media, phone


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
    media.purge(root, now.date(), options.get("retention_days"))
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


def annotate_summary(summary, date, settings=None, bounds=None):
    """1日の要約に、睡眠・スマホの使用・カテゴリ別の時間・起床と就寝を足す。

    スマホのイベントが1つも無い日は何も足さない。`bounds` は生活の1日の区間で、
    無ければ暦の日（0時〜翌0時）。生活の1日では、睡眠はその朝に終わった1晩を数える
    （区切りの5時で1晩を2日に割ると、どちらの日の睡眠も実際より短く見える）。

    自動で動かしているアプリ（設定の `automated`）の前面時間は人の操作に数えず、
    `phone_auto_sec` として別に持つ。
    """
    options = config.phone(settings)
    root = paths.phone_dir()
    events = phone.events_around(date, root, after=2 if bounds else 1)
    if not events:
        return summary
    day, start, end = _day_bounds(date)
    if bounds:
        start, end = bounds
    now = datetime.now().astimezone()
    automated = options.get("automated") or []
    usage = phone_model.human_usage(events, automated, until=now)
    auto = phone_model.automated_intervals(events, automated, until=now)
    activity = _pc_activity(day) + usage
    covered = phone.covered_spans(root)
    sleep_settings = options.get("sleep") or {}
    tonight = phone_model.detect_sleep(day + timedelta(days=1), activity, covered, sleep_settings)
    last_night = phone_model.detect_sleep(day, activity, covered, sleep_settings)
    sleeps = [s for s in (last_night, tonight) if s]
    apps = phone_model.app_seconds(events, start, end, until=now)
    for package in automated:
        apps.pop(package, None)
    categories = phone_model.by_category(apps, options.get("categories") or {})
    played = phone_model.media_seconds(events, media.load(day.isoformat(), root),
                                       start, end, until=now)
    day_usage = phone_model.clip(usage, start, end)
    phone_model.annotate(summary, sleeps, day_usage, categories, bounds=(start, end))
    if categories:
        # アプリ名・タイトルはページにだけ出す。rollup（共有できる形）には入れない
        summary["phone_apps"] = phone_model.app_breakdown(
            apps, options.get("categories") or {}, options.get("labels") or {}, played)
    if "sleep_sec" in summary:
        if bounds:
            summary["sleep_sec"] = phone_model.night_sec(last_night, usage)
        summary.update(phone_model.wake_and_bed(last_night, tonight))
        summary["sleep_spans"] = [(s["start"], s["end"]) for s in sleeps]
        summary["phone_spans"] = day_usage
        summary["auto_spans"] = phone_model.clip(auto, start, end)
        summary["phone_auto_sec"] = sum((b - a).total_seconds()
                                        for a, b in summary["auto_spans"])
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


RELOG_EVERY = 50   # 失敗が続くとき、何回ごとにログへ書くか（最大間隔で約8時間）


def watch(settings=None, interval=60, rounds=None, sleep=clock.sleep):
    """再生中のセッションを `interval` 秒ごとに読んで足し続ける。

    端末が見えない間は間隔を広げる（最大10分）。ログには失敗に変わった時と、失敗が
    続く間は `RELOG_EVERY` 回ごとに書く。どの失敗でも常駐は止めない。
    `rounds` は読む回数（テスト用。None なら止まらない）。
    """
    options = config.phone(settings)
    root = paths.phone_dir()
    failures, done = 0, 0
    while rounds is None or done < rounds:
        done += 1
        try:
            text = media.fetch(options.get("adb_serials"), adb=options.get("adb") or "adb")
            media.append(media.parse_sessions(text), datetime.now().astimezone(), root)
        except Exception as error:  # noqa: BLE001 常駐なので、何が起きても次の回へ進む
            failures += 1
            if failures % RELOG_EVERY == 1:
                reason = error if isinstance(error, RuntimeError) else type(error).__name__
                _log(root, f"media failed x{failures} {reason}")
        else:
            if failures:
                _log(root, "media ok")
            failures = 0
        if rounds is None or done < rounds:
            sleep(min(interval * 2 ** min(failures, 4), 600) if failures else interval)
    return 0


def cmd_phone(args):
    if args.action == "watch":
        return watch(interval=max(args.interval, 10))
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
    cmd.add_argument("action", nargs="?", default="apps", choices=("pull", "apps", "watch"))
    cmd.add_argument("--days", type=int, default=7, help="apps: 直近何日を見るか（既定 7）")
    cmd.add_argument("--interval", type=int, default=60,
                     help="watch: 再生中のタイトルを読む間隔（秒、既定 60）")
    cmd.set_defaults(func=cmd_phone)
