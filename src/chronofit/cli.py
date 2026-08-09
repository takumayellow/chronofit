"""chronofit のコマンドライン入口。

    python -m chronofit collect              端末イベントの収集（常駐）
    python -m chronofit snapshot             ブラウザ履歴を退避（刈られる前に）
    python -m chronofit history              退避した履歴を日ごと・分類ごとに見る
    python -m chronofit status               収集状況の確認
    python -m chronofit rollup               1日分を畳んで net/wall/離席に分ける
    python -m chronofit label                離席ブロックにラベルを付ける（1日1回）
    python -m chronofit done S K T           終わったタスクを実測込みでDBへ入れる
    python -m chronofit estimate S K         (科目, 種別, 何本目) の見積もり
    python -m chronofit slack                日タイプごとの slack 率
    python -m chronofit capacity             習慣を引いた、実際に割り当てられる時間
    python -m chronofit plan tasks.json      タスク一覧を週の容量へ割り付ける
    python -m chronofit coverage             所要時間DBに何が溜まっているか
    python -m chronofit backfill-clockify F  過去の Clockify CSV を取り込む
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import config, paths
from .collect import daemon
from .estimate import attribute, curve, kinds, measured, offpc, slack
from .plan import fit
from .estimate import db as estimate_db
from .model import context as context_model
from .model import labels as labels_model
from .model import rollup
from .sources import browser, clockify, history
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


def cmd_history(args):
    """溜まっているブラウザ履歴を、日ごと・分類ごとの時間として見る。

    L0 が動き出す前の期間について残っている唯一の痕跡なので、ここを読めるように
    しておかないと、過去は分類できないまま蒸発する。
    """
    settings = config.load()
    rules = (settings.get("url_rules") or []) + (settings.get("title_rules") or [])
    since = history.parse_since(args.since)

    sources = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        sources.extend(browser.find_histories(local_app_data))
    sources.extend(history.snapshots(paths.snapshot_dir()))

    visits = history.collect(sources, since)
    if not visits:
        print("履歴が読めない（スナップショットも実ファイルも空）。")
        return 1

    days = history.daily(visits, rules)
    print(f"{len(visits)}訪問  {days[0]['date']} - {days[-1]['date']}  "
          f"（{len(sources)}本の History から）")
    if not rules:
        print("  分類規則が空（config の url_rules / title_rules）。下の重い順を規則へ写す。")

    for day in days[-args.days:]:
        known = sum(day["by_label"].values())
        top = sorted(day["by_label"].items(), key=lambda item: -item[1])[:3]
        detail = "  ".join(f"{label} {seconds / 3600:.1f}h" for label, seconds in top)
        print(f"  {day['date']}  接触 {day['sec'] / 3600:5.1f}h  "
              f"分類済 {known / 3600:4.1f}h  {detail}")

    print("未分類のドメイン（重い順）:")
    for host, seconds in history.unclassified(days):
        print(f"  {seconds / 3600:5.1f}h  {host}")
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
    # 離席の中身は両隣の前景から当たる。人間に聞くのは、両隣にも手掛かりが
    # 無かったぶんだけになる。
    context_model.annotate(summary, settings.get("title_rules") or [])
    defaults = labels_model.build_defaults(paths.label_dir(), weekend)
    pending = labels_model.unlabeled(summary)
    presets = config.study_presets(settings)

    answers = ask.ask_blocks(
        pending,
        config.away_categories(settings),
        lambda block: labels_model.suggest(block, defaults, weekend),
        detail_for=lambda block: ask.ask_detail(presets, block.get("guess")))
    if not answers:
        return 0

    by_start = {block["start"].isoformat(timespec="seconds"): block for block in pending}
    stored = labels_model.load(date, paths.label_dir())
    for start, (label, detail) in answers.items():
        stored[start] = labels_model.make_entry(by_start[start], label, detail)
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


def _habit_load(settings, days=14, today=None):
    """習慣が1日あたり実際に持っていっている時間。実測が無ければ引かない。

    枠を宣言して引くのはやめる。宣言した枠は、やらなかった日も容量を食い、
    やり過ぎた日は食わない。どちらも実際とずれるので、直近の窓の実測から出す。

    実測は所要時間DBではなくラベルと生スパンから直に取る。習慣には「終わった」が
    無いので `done` が一度も走らず、DBには一生1行も入らないためである。
    """
    end = today or datetime.now().date()
    start = end - timedelta(days=days - 1)
    rows = measured.habit_rows(kinds.habits(settings), paths.label_dir(),
                               paths.raw_dir(), start.isoformat(), end.isoformat())
    return kinds.habit_load(rows, settings,
                            since=start.isoformat(), until=end.isoformat())


def cmd_capacity(args):
    """習慣ぶんを引いた、実際にタスクへ割り当てられる時間。

    毎日やるもの（ピアノ・精進）をタスクとして積むと需要が膨らんで見えるが、
    実際に起きているのは1日の使える時間が減ることだけなので、ここで引く。
    引く量は**直近の実測から出した1日平均**で、実測が無ければ引かない。
    """
    settings = config.load()
    load = _habit_load(settings, args.window)
    if not load:
        print("習慣が設定されていない（config の habits が空）。")
    for entry in load:
        basis = entry["basis"] or "実測なし"
        if entry["samples"]:
            detail = f"{entry['samples']}件 / 直近{entry['days']}日"
        elif entry["hours_per_day"]:
            detail = "実測がまだ無いので仮値で引いている"
        else:
            detail = "実測も仮値も無いので引かない"
        print(f"  -{entry['hours_per_day']:.1f}h/日  {entry['name']}"
              f"（{basis}・{detail}）")

    available = kinds.available_hours(args.hours, load)
    print(f"暦 {args.hours:.1f}h/日 - 習慣 {kinds.habit_hours_per_day(load):.1f}h "
          f"= 割り当て可能 {available:.1f}h/日")

    bucket = _slack_buckets(settings).get(args.day_type)
    if bucket and bucket["ratio"]:
        print(f"  うち実作業に落ちるのは {available * bucket['ratio']:.1f}h/日 "
              f"({args.day_type} の slack率 {bucket['ratio']:.0%}, {bucket['days']}日分の実測)")
    else:
        print(f"  {args.day_type} の slack 率がまだ無いので、実作業ぶんは出せない")

    if args.days:
        print(f"  {args.days}日ぶんで {available * args.days:.0f}h")
    return 0


def cmd_plan(args):
    """タスク一覧を、使える容量へ週単位で割り付ける。

    入りきらなかったものを**必ず名指しで出す**。容量を超えているという事実こそが、
    計画を立てて分かるべきことなので、黙って削ると計画の意味が無くなる。
    """
    settings = config.load()
    try:
        spec = json.loads(args.tasks.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"タスク定義を読めない: {error}")
        return 1
    tasks = spec.get("tasks") if isinstance(spec, dict) else spec
    if not tasks:
        print("タスクが空。{\"tasks\": [{\"subject\": ..., \"kind\": ...}]} の形で渡す。")
        return 1

    until = datetime.strptime(args.until, "%Y-%m-%d").date()
    hours_by_type = dict(settings.get("capacity_hours") or {})
    hours_by_type.setdefault("平日", args.hours)
    hours_by_type.setdefault("休日", args.hours)
    hours_by_type.setdefault("出社", args.workday_hours)

    day_types = settings.get("day_types", {})
    load = _habit_load(settings, args.window)
    result = fit.make(tasks, _instances(), hours_by_type, until, settings,
                      _slack_buckets(settings),
                      weekend_days=tuple(day_types.get("weekend", (5, 6))),
                      workdays=tuple(day_types.get("workdays", ())),
                      habit_load=load)

    for entry in load:
        if entry["hours_per_day"]:
            print(f"習慣 -{entry['hours_per_day']:.1f}h/日  {entry['name']}"
                  f"（{entry['basis']}）")
        else:
            print(f"習慣 {entry['name']} は実測がまだ無いので容量から引いていない")

    print(f"需要 {result['demand']:.0f}h(net) / 供給 {result['supply']:.0f}h(net)"
          f"  〜{args.until}")
    if result["unknown_days"]:
        print(f"  slack 率の無い日が {result['unknown_days']}日ある。"
              f"その日ぶんは供給に数えていない（過小に出ている）")

    for week in result["placed"]:
        if not week["items"]:
            continue
        print(f"\n[{week['week']} の週]  残り {week['left']:.1f}h")
        for item in week["items"]:
            target = f" {item['target']}" if item.get("target") else ""
            print(f"  {item['hours']:5.1f}h  {item['subject']} {item['kind']}"
                  f"{target}（{item['index']}本目・{item['basis']}）")

    if result["overflow"]:
        print("\n入りきらなかったもの:")
        for item in result["overflow"]:
            hours = f"{item['hours']:.1f}h" if item["hours"] is not None else "見積もり無し"
            print(f"  {hours}  {item['subject']} {item['kind']}"
                  f"（{item['index']}本目）— {item['reason']}")
        print("  → 減らすか、締切を動かすか、1日の持ち時間を増やすかを決める")
    return 0


def cmd_estimate(args):
    """1インスタンスの見積もり。根拠を必ず一緒に出す。

    種別によって見積もり方を変える。繰り返して速くなるものにしか学習曲線は
    当たらないし、習慣にはそもそも見積もりが要らない（`estimate/kinds.py`）。
    """
    settings = config.load()
    instances = _instances()
    mode = kinds.mode_for(args.kind, settings)

    if mode == kinds.HABIT:
        print(f"{args.kind} は習慣。見積もらず容量から引く -> chronofit capacity")
        return 0

    if mode == kinds.ONEOFF:
        index = None
        result = kinds.estimate_oneoff(instances, args.subject, args.kind, args.assumed)
        print(f"{args.subject} {args.kind}（一点物）")
    else:
        index = args.index or curve.next_index(instances, args.subject, args.kind)
        result = curve.estimate(instances, args.subject, args.kind, index, args.assumed)
        print(f"{args.subject} {args.kind} {index}本目")

    if result["hours"] is None:
        print(f"  見積もれない: {result['note']}")
        return 1
    print(f"  net {result['hours']:.1f}h  [{result['basis']}] {result['note']}")
    if result.get("median") is not None and result["median"] != result["hours"]:
        print(f"  （中央値 {result['median']:.1f}h。予定には p80、見通しには中央値を使う）")

    buckets = _slack_buckets(settings)
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
    知っているので、--match でタイトルを指定すれば実測から拾う。紙で進めた
    ぶんは --offpc でラベルから拾う（長さは離席ブロックが持っている）。
    """
    settings = config.load()
    instances = _instances()
    mode = kinds.mode_for(args.kind, settings)
    index = args.index or curve.next_index(instances, args.subject, args.kind)

    net = args.net
    wall = args.wall
    units = args.units
    sessions = 1
    source = "manual"
    if net is None:
        if args.offpc:
            days = offpc.collect(paths.label_dir(), args.subject, args.kind,
                                 args.target, args.since, args.until)
            summed = offpc.totals(days)
            if summed is None:
                print(f"{args.subject}/{args.kind}/{args.target} のオフPC記録が "
                      f"{args.since} 以降に無い。")
                return 1
            source = "offpc"
        elif args.match:
            days = attribute.collect(paths.raw_dir(), args.match, args.since, args.until)
            if not days:
                print(f"'{args.match}' に一致するスパンが {args.since} 以降に無い。")
                return 1
            summed = attribute.totals(days)
            source = "chronofit"
        else:
            print("--net / --match / --offpc のどれかが要る。時間の出どころを空にしない。",
                  file=sys.stderr)
            return 1

        net, wall, sessions = summed["net_hours"], summed["wall_hours"], summed["sessions"]
        units = units or summed.get("units")
        print(f"実測から集計: {summed['first']} - {summed['last']}  "
              f"{sessions}日  net {net:.1f}h / 在席 {wall:.1f}h")
        for title in (summed.get("titles") or [])[:5]:
            print(f"    {title[:70]}")

    row = estimate_db.make(args.subject, args.kind, args.target, index, net, wall,
                           sessions, args.date or datetime.now().strftime("%Y-%m-%d"),
                           source=source, mode=mode, units=units)
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

    hist = sub.add_parser("history", help="ブラウザ履歴を日ごと・分類ごとに見る")
    hist.add_argument("--days", type=int, default=14, help="直近何日を表示するか")
    hist.add_argument("--since", help="この日以降だけ読む YYYY-MM-DD")
    hist.set_defaults(func=cmd_history)

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
    done.add_argument("--offpc", action="store_true",
                      help="紙で進めたぶん。離席ブロックのラベルから時間を拾う")
    done.add_argument("--units", type=int, help="こなした量（問数・ページ数など）")
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

    capacity = sub.add_parser("capacity", help="習慣の実測ぶんを引いた割り当て可能時間")
    capacity.add_argument("--hours", type=float, default=16.0,
                          help="1日の暦時間（睡眠を除いた素の持ち時間）")
    capacity.add_argument("--days", type=int, help="この日数ぶんの合計も出す")
    capacity.add_argument("--day-type", default="平日", help="slack 率を引く日タイプ")
    capacity.add_argument("--window", type=int, default=14,
                          help="習慣の1日平均を出す窓の日数（既定 14）")
    capacity.set_defaults(func=cmd_capacity)

    plan_cmd = sub.add_parser("plan", help="タスク一覧を週の容量へ割り付ける")
    plan_cmd.add_argument("tasks", type=Path, help="タスク定義の JSON")
    plan_cmd.add_argument("--until", required=True, help="いつまでに YYYY-MM-DD")
    plan_cmd.add_argument("--hours", type=float, default=8.0,
                          help="平日・休日の1日の持ち時間（暦）")
    plan_cmd.add_argument("--workday-hours", type=float, default=3.0,
                          help="出社日の1日の持ち時間（暦）")
    plan_cmd.add_argument("--window", type=int, default=14,
                          help="習慣の1日平均を出す窓の日数（既定 14）")
    plan_cmd.set_defaults(func=cmd_plan)

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
