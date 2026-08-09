"""chronofit のコマンドライン入口。

    python -m chronofit collect              端末イベントの収集（常駐）
    python -m chronofit snapshot             ブラウザ履歴を退避（刈られる前に）
    python -m chronofit status               収集状況の確認
    python -m chronofit backfill-clockify F  過去の Clockify CSV を取り込む
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import paths
from .collect import daemon
from .sources import browser, clockify


def cmd_collect(args):
    return daemon.run(interval=args.interval, verbose=args.verbose, duration=args.duration)


def cmd_snapshot(args):
    """ブラウザ履歴を日付つきで退避する。既に今日の分があれば上書きする。"""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        print("LOCALAPPDATA が無い（Windows 以外？）", file=sys.stderr)
        return 1

    today = datetime.now().strftime("%Y-%m-%d")
    destination_root = paths.snapshot_dir() / today
    histories = browser.find_histories(local_app_data)
    if not histories:
        print("ブラウザ履歴が見つからない。")
        return 1

    for name, profile, path in histories:
        safe_profile = profile.replace(" ", "_")
        destination = destination_root / f"{name}-{safe_profile}-History.sqlite"
        browser.snapshot(path, destination)
        visits = browser.read_visits(path)
        span = (f"{visits[0][0]:%Y-%m-%d} - {visits[-1][0]:%Y-%m-%d}" if visits else "空")
        print(f"  {name}/{profile:10} {len(visits):6} visits  {span}")
    print(f"-> {destination_root}")
    return 0


def cmd_status(args):
    """収集がちゃんと動いているかを一目で見る。"""
    raw = paths.raw_dir()
    print(f"データルート: {paths.data_root()}")
    if not raw.is_dir():
        print("  raw ディレクトリなし。まだ一度も収集していない。")
        return 1

    files = sorted(raw.glob("*.jsonl"))
    if not files:
        print("  収集ログなし。")
        return 1

    print(f"  収集日数: {len(files)}日  ({files[0].stem} - {files[-1].stem})")
    for path in files[-args.days:]:
        spans = active = total = 0
        gap = 0.0
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("t") == "span":
                spans += 1
                total += record.get("sec", 0)
                active += record.get("active_sec", 0)
            elif record.get("t") == "gap":
                gap += record.get("sec", 0)
        share = (active / total) if total else 0.0
        print(f"  {path.stem}  spans={spans:5}  在席{total / 3600:5.1f}h  "
              f"入力あり{active / 3600:5.1f}h ({share:4.0%})  離席/休止{gap / 3600:5.1f}h")

    lock = paths.data_root() / "collector.pid"
    print(f"  常駐: {'稼働中 pid=' + lock.read_text(encoding='utf-8').strip() if lock.exists() else '停止'}")
    return 0


def cmd_backfill_clockify(args):
    entries = clockify.read_normalized(args.csv)
    report = clockify.summarize(entries)
    print(f"取り込み: {report['entries']}件  "
          f"{report['first']:%Y-%m-%d} - {report['last']:%Y-%m-%d}")
    print(f"  合計          {report['total_hours']:7.1f}h")
    print(f"  健全          {report['clean_hours']:7.1f}h ({report['clean_entries']}件)")
    print(f"  汚染(止め忘れ) {report['contaminated_hours']:7.1f}h "
          f"({report['contaminated_entries']}件, 全体の{report['contaminated_share']:.0%})")

    destination = paths.ensure(paths.rollup_dir()) / "backfill-clockify.jsonl"
    with destination.open("w", encoding="utf-8") as handle:
        for entry in entries:
            record = dict(entry)
            record["start"] = entry["start"].isoformat(timespec="seconds")
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"-> {destination}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="chronofit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="端末イベントの収集（常駐）")
    daemon.add_arguments(collect)
    collect.set_defaults(func=cmd_collect)

    snapshot = sub.add_parser("snapshot", help="ブラウザ履歴を退避")
    snapshot.set_defaults(func=cmd_snapshot)

    status = sub.add_parser("status", help="収集状況の確認")
    status.add_argument("--days", type=int, default=7, help="直近何日を表示するか")
    status.set_defaults(func=cmd_status)

    backfill = sub.add_parser("backfill-clockify", help="Clockify CSV を取り込む")
    backfill.add_argument("csv", type=Path, help="task_entries_normalized.csv")
    backfill.set_defaults(func=cmd_backfill_clockify)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
