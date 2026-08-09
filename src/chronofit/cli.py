"""chronofit のコマンドライン入口。

    python -m chronofit collect              端末イベントの収集（常駐）
    python -m chronofit snapshot             ブラウザ履歴を退避（刈られる前に）
    python -m chronofit status               収集状況の確認
    python -m chronofit rollup               1日分を畳んで net/wall/離席に分ける
    python -m chronofit label                離席ブロックにラベルを付ける（1日1回）
    python -m chronofit done S K T           終わったタスクを実測込みでDBへ入れる
    python -m chronofit estimate S K         (科目, 種別, 何本目) の見積もり
    python -m chronofit slack                日タイプごとの slack 率
    python -m chronofit coverage             所要時間DBに何が溜まっているか
    python -m chronofit backfill-clockify F  過去の Clockify CSV を取り込む
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from . import config, paths
from .collect import daemon
from .estimate import attribute, curve, slack
from .estimate import db as estimate_db
from .model import labels as labels_model
from .model import rollup
from .sources import browser, clockify
from .ui import ask


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


def _resolve_date(value):
    """日付引数を解決する。既定は今日。"""
    return value or datetime.now().strftime("%Y-%m-%d")


def _load_summary(date):
    """その日のロールアップをラベル込みで作る。生ログが無ければ None。"""
    path = paths.raw_dir() / f"{date}.jsonl"
    if not path.is_file():
        return None
    summary = rollup.summarize_day(rollup.read_day(path))
    return rollup.merge_labels(summary, labels_model.load(date, paths.label_dir()))


def cmd_rollup(args):
    """1日分を畳んで表示し、共有できる粒度の JSON に落とす。"""
    date = _resolve_date(args.date)
    summary = _load_summary(date)
    if summary is None:
        print(f"{date} の生ログが無い。")
        return 1

    print(rollup.format_summary(summary, date))

    # segments は生タイトルを含むので書き出さない。出すのは集計値だけ。
    shareable = {
        "date": date,
        "wall_sec": round(summary["wall_sec"], 1),
        "net_sec": round(summary["net_sec"], 1),
        "away_sec": round(summary["away_sec"], 1),
        "slack_ratio": summary["slack_ratio"],
        "away_blocks": [{"start": b["start"].isoformat(timespec="seconds"),
                         "end": b["end"].isoformat(timespec="seconds"),
                         "sec": round(b["sec"], 1), "reason": b["reason"],
                         "label": b.get("label")} for b in summary["away_blocks"]],
    }
    destination = paths.ensure(paths.rollup_dir()) / f"{date}.json"
    destination.write_text(json.dumps(shareable, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"-> {destination}")
    return 0


def cmd_label(args):
    """未ラベルの離席ブロックをまとめて聞く。1日1回・数十秒で終わる分量にする。"""
    date = _resolve_date(args.date)
    summary = _load_summary(date)
    if summary is None:
        print(f"{date} の生ログが無い。")
        return 1

    settings = config.load()
    weekend = tuple(settings.get("day_types", {}).get("weekend", (5, 6)))
    defaults = labels_model.build_defaults(paths.label_dir(), weekend)
    pending = labels_model.unlabeled(summary)

    answers = ask.ask_blocks(
        pending,
        config.away_categories(settings),
        lambda block: labels_model.suggest(block, defaults, weekend))
    if not answers:
        return 0

    stored = labels_model.load(date, paths.label_dir())
    stored.update(answers)
    labels_model.save(date, paths.label_dir(), stored)
    print(f"\n{len(answers)}本を記録 -> {labels_model.path_for(date, paths.label_dir())}")
    return 0


def _instances():
    return estimate_db.load(estimate_db.default_path(paths.data_root()))


def _slack_buckets(settings):
    weekend = tuple(settings.get("day_types", {}).get("weekend", (5, 6)))
    workdays = tuple(settings.get("day_types", {}).get("workdays", ()))
    return slack.ratios(slack.load_rollups(paths.rollup_dir()), weekend, workdays)


def cmd_slack(args):
    """日タイプごとの slack 率。見積もりを暦時間へ直すときの分母になる。"""
    print(slack.format_ratios(_slack_buckets(config.load())))
    return 0


def cmd_coverage(args):
    """所要時間DBに何が溜まっているか。見積もりの信頼度そのもの。"""
    rows = estimate_db.coverage(_instances())
    if not rows:
        print("所要時間DBが空。まだ1件も確定していない。")
        return 1
    print("科目            種別      件数  最大本数  net合計")
    for row in rows:
        print(f"{row['subject']:14.14} {row['kind']:8.8} {row['count']:5}  "
              f"{row['max_index']:6}  {row['net_hours']:6.1f}h")
    return 0


def cmd_estimate(args):
    """1インスタンスの見積もり。根拠を必ず一緒に出す。"""
    instances = _instances()
    index = args.index or curve.next_index(instances, args.subject, args.kind)
    result = curve.estimate(instances, args.subject, args.kind, index, args.assumed)

    print(f"{args.subject} {args.kind} {index}本目")
    if result["hours"] is None:
        print(f"  見積もれない: {result['note']}")
        return 1
    print(f"  net {result['hours']:.1f}h  [{result['basis']}] {result['note']}")

    buckets = _slack_buckets(config.load())
    bucket = buckets.get(args.day_type)
    if bucket and bucket["ratio"]:
        hours = slack.calendar_hours(result["hours"], bucket["ratio"])
        print(f"  暦時間 {hours:.1f}h  ({args.day_type} の slack率 {bucket['ratio']:.0%}, "
              f"{bucket['days']}日分の実測)")
    else:
        print(f"  暦時間は出せない: {args.day_type} の slack 率がまだ無い")
    return 0


def cmd_done(args):
    """終わったタスクを1インスタンスとしてDBへ入れる。

    人間が言うのは「これが終わった」だけでよい。かかった時間は L0 が既に
    知っているので、--match でタイトルを指定すれば実測から拾う。
    """
    instances = _instances()
    index = args.index or curve.next_index(instances, args.subject, args.kind)

    net = args.net
    wall = args.wall
    sessions = 1
    if net is None:
        if not args.match:
            print("--net か --match のどちらかが要る。時間の出どころを空にしない。",
                  file=sys.stderr)
            return 1
        days = attribute.collect(paths.raw_dir(), args.match, args.since, args.until)
        if not days:
            print(f"'{args.match}' に一致するスパンが {args.since} 以降に無い。")
            return 1
        summed = attribute.totals(days)
        net, wall, sessions = summed["net_hours"], summed["wall_hours"], summed["sessions"]
        print(f"実測から集計: {summed['first']} - {summed['last']}  "
              f"{sessions}日  net {net:.1f}h / 在席 {wall:.1f}h")
        for title in summed["titles"][:5]:
            print(f"    {title[:70]}")

    row = estimate_db.make(args.subject, args.kind, args.target, index, net, wall,
                           sessions, args.date or datetime.now().strftime("%Y-%m-%d"),
                           source="chronofit" if args.match else "manual")
    estimate_db.append(estimate_db.default_path(paths.data_root()), row)
    print(f"{args.subject} {args.kind} {args.target} = {index}本目  "
          f"net {row['net_hours']}h を記録")
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

    rollup_cmd = sub.add_parser("rollup", help="1日分を畳んで net/wall/離席に分ける")
    rollup_cmd.add_argument("--date", help="YYYY-MM-DD（既定は今日）")
    rollup_cmd.set_defaults(func=cmd_rollup)

    label = sub.add_parser("label", help="離席ブロックにラベルを付ける")
    label.add_argument("--date", help="YYYY-MM-DD（既定は今日）")
    label.set_defaults(func=cmd_label)

    est = sub.add_parser("estimate", help="(科目, 種別, 何本目) の見積もり")
    est.add_argument("subject", help="科目")
    est.add_argument("kind", help="種別（過去問 / 参考書 / レポート ...）")
    est.add_argument("--index", type=int, help="何本目か（既定は実績の次）")
    est.add_argument("--assumed", type=float, help="実績も流用元も無い場合に置く仮値 時間")
    est.add_argument("--day-type", default="平日", help="暦時間へ直すときの日タイプ")
    est.set_defaults(func=cmd_estimate)

    done = sub.add_parser("done", help="終わったタスクを所要時間DBへ入れる")
    done.add_argument("subject", help="科目")
    done.add_argument("kind", help="種別")
    done.add_argument("target", help="対象（2024年度期末 など）")
    done.add_argument("--match", help="タイトルの正規表現。実測から時間を拾う")
    done.add_argument("--since", default=datetime.now().strftime("%Y-%m-%d"),
                      help="実測を拾い始める日 YYYY-MM-DD（既定は今日）")
    done.add_argument("--until", help="実測を拾い終える日 YYYY-MM-DD（既定は今日）")
    done.add_argument("--net", type=float, help="net 時間を直接指定する（実測が無い場合）")
    done.add_argument("--wall", type=float, help="在席時間を直接指定する")
    done.add_argument("--index", type=int, help="何本目か（既定は実績の次）")
    done.add_argument("--date", help="完了日 YYYY-MM-DD")
    done.set_defaults(func=cmd_done)

    slack_cmd = sub.add_parser("slack", help="日タイプごとの slack 率")
    slack_cmd.set_defaults(func=cmd_slack)

    coverage = sub.add_parser("coverage", help="所要時間DBの中身")
    coverage.set_defaults(func=cmd_coverage)

    backfill = sub.add_parser("backfill-clockify", help="Clockify CSV を取り込む")
    backfill.add_argument("csv", type=Path, help="task_entries_normalized.csv")
    backfill.set_defaults(func=cmd_backfill_clockify)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
