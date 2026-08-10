"""chronofit のコマンドライン入口。

    python -m chronofit collect              端末イベントの収集（常駐）
    python -m chronofit snapshot             ブラウザ履歴を退避（刈られる前に）
    python -m chronofit history              退避した履歴を日ごと・分類ごとに見る
    python -m chronofit status               収集状況の確認
    python -m chronofit rollup               1日分を畳んで net/wall/離席に分ける
    python -m chronofit report               1日の測定結果を HTML にして開く
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
from .plan import board, fit
from .plan import tasks as tasks_store
from .estimate import db as estimate_db
from .model import context as context_model
from .model import labels as labels_model
from .model import rollup
from .sources import browser, clockify, history
from .ui import ask, report


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
    # 畳み方は rollup と同じものを使う。ここだけ独自に数えると、同じ日について
    # status と report が違う「在席」を言う。実際それで「測れていない」ように見えた。
    latest = None
    for path in files[-args.days:]:
        records = rollup.read_day(path)
        if not records:
            print(f"  {path.stem}  記録なし")
            continue
        summary = rollup.summarize_day(records)
        latest = max(latest or "", records[-1]["end"])
        ratio = summary["slack_ratio"]
        print(f"  {path.stem}  spans={len(records):5}  "
              f"記録{sum(r.get('sec', 0) for r in records) / 3600:5.1f}h  "
              f"在席{summary['wall_sec'] / 3600:5.1f}h  "
              f"入力あり{summary['net_sec'] / 3600:5.1f}h "
              f"({'  --' if ratio is None else f'{ratio:4.0%}'})  "
              f"受動{summary['passive_sec'] / 3600:4.1f}h  "
              f"離席{summary['away_sec'] / 3600:5.1f}h")

    lock = paths.data_root() / "collector.pid"
    print(f"  常駐: {'稼働中 pid=' + lock.read_text(encoding='utf-8').strip() if lock.exists() else '停止'}")
    if latest:
        # 「いま取れているか」はプロセスの生死ではなく**最後の記録がいつか**で決まる。
        # 常駐しているのに書けていない（権限・例外）状態を、生きている扱いにしない。
        #
        # ただし前景が変わらない間（PDF・動画）スパンは MAX_SPAN_SEC まで書き出されない。
        # 閾値をそこに揃えると、健全な収集を「遅れている」と言ってしまう。倍を取る。
        stale_after = daemon.MAX_SPAN_SEC * 2
        last = datetime.fromisoformat(latest)
        if last.tzinfo is None:      # 手で書いた・古い形式のログ。ローカル時刻とみなす
            last = last.astimezone()
        behind = (datetime.now().astimezone() - last).total_seconds()
        print(f"  最終記録: {latest[11:19]}（{behind / 60:.0f}分前）"
              + ("  ** 遅れている **" if behind > stale_after else ""))
    return 0


def _resolve_date(value):
    """日付引数を解決する。既定は今日。

    `today` / `yesterday` を受けるのは、タスクスケジューラが日付を計算できないため。
    「前日ぶんを毎晩畳む」を人手ゼロで回すには、日付側が相対語を解せる必要がある。

    形を厳しく見るのは、この戻り値が**そのままファイル名になる**ため。`2026-8-9` の
    ような書き方を通すと、同じ日のロールアップが別ファイルに散る。
    """
    relative = {"today": 0, "yesterday": 1}
    if value in relative:
        return (datetime.now() - timedelta(days=relative[value])).strftime("%Y-%m-%d")
    if not value:
        return datetime.now().strftime("%Y-%m-%d")
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d")
    except ValueError:
        raise SystemExit(f"日付は YYYY-MM-DD / today / yesterday で書く: {value}")


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


def _capacity_hours(settings, args):
    """日タイプごとの1日の持ち時間。設定が持っていればそちらが勝つ。"""
    hours_by_type = dict(settings.get("capacity_hours") or {})
    hours_by_type.setdefault("平日", getattr(args, "hours", None) or 8.0)
    hours_by_type.setdefault("休日", getattr(args, "hours", None) or 8.0)
    hours_by_type.setdefault("出社", getattr(args, "workday_hours", None) or 3.0)
    return hours_by_type


def cmd_plan(args):
    """タスク一覧を、使える容量へ週単位で割り付ける。

    入りきらなかったものを**必ず名指しで出す**。容量を超えているという事実こそが、
    計画を立てて分かるべきことなので、黙って削ると計画の意味が無くなる。
    """
    settings = config.load()
    try:
        stored = _tasks(args.tasks)
    except (OSError, json.JSONDecodeError) as error:
        print(f"タスク定義を読めない: {error}")
        return 1
    if not stored:
        print("タスクが空。chronofit task add <科目> <種別> --count N で足す。")
        return 1

    # 終わったぶんを落としてから割り付ける。一覧をそのまま渡すと、2本終わった
    # 時点で「残り3本」ではなく「これから5本」の計画になる。
    instances = _instances()
    tasks = board.remaining_tasks(stored, instances, settings)
    if not tasks:
        print("残っているものが無い。全部終わっている。")
        return 0

    until = datetime.strptime(args.until, "%Y-%m-%d").date()
    hours_by_type = _capacity_hours(settings, args)

    day_types = settings.get("day_types", {})
    load = _habit_load(settings, args.window)
    result = fit.make(tasks, instances, hours_by_type, until, settings,
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


def _tasks(path=None):
    return tasks_store.load(path or paths.tasks_path())


def cmd_task(args):
    """やることの一覧を足す / 消す / 見る。進捗は持たせない（DBから数える）。"""
    path = paths.tasks_path()
    current = _tasks(path)

    if args.action in ("add", "rm") and not args.subject:
        print(f"科目が要る: chronofit task {args.action} <科目> <種別>", file=sys.stderr)
        return 1
    if args.action == "add" and not args.kind:
        print("種別が要る: chronofit task add <科目> <種別>", file=sys.stderr)
        return 1

    if args.action == "add":
        # 締切はここで弾く。読む側（board / daily）で落とすと、書き間違えた1行のせいで
        # 毎晩の自動実行が止まり、そのぶんの進捗が永久に残らなくなる。
        if args.due:
            try:
                datetime.strptime(args.due, "%Y-%m-%d")
            except ValueError:
                print(f"締切は YYYY-MM-DD で書く: --due {args.due}", file=sys.stderr)
                return 1
        if args.count < 1:
            print("--count は1以上。やらないなら task rm で消す。", file=sys.stderr)
            return 1
        entry = {"subject": args.subject, "kind": args.kind,
                 "count": args.count, "priority": args.priority}
        for name in ("target", "due", "assumed_hours"):
            if getattr(args, name, None) is not None:
                entry[name] = getattr(args, name)
        tasks_store.save(path, tasks_store.upsert(current, entry))
        print(f"{args.subject} {args.kind} {args.count}本 -> {path}")
        return 0

    if args.action == "rm":
        removed, kept = tasks_store.remove(current, args.subject, args.kind, args.target)
        if not removed:
            print(f"{args.subject} {args.kind or ''} は一覧に無い。")
            return 1
        tasks_store.save(path, kept)
        print(f"{removed}件 消した。残り {len(kept)}件")
        return 0

    if not current:
        print(f"一覧が空。chronofit task add <科目> <種別> --count N で足す。\n  {path}")
        return 0
    for entry in current:
        target = f" {entry['target']}" if entry.get("target") else ""
        due = f"  〜{entry['due']}" if entry.get("due") else ""
        print(f"  [{entry.get('priority') or '-'}] {entry['subject']} "
              f"{entry['kind']}{target}  {entry.get('count', 1)}本{due}")
    return 0


def cmd_board(args):
    """いまどこまで来ているか。予定を立て直す前に必ず見る面。

    残量は宣言ではなく所要時間DBから数える。数えられないもの（実測が無くて
    見積もれない本）は 0 として合計に混ぜず、件数として別に出す。
    """
    settings = config.load()
    task_list = _tasks(args.tasks)
    if not task_list:
        print("一覧が空。chronofit task add <科目> <種別> --count N で足す。")
        # 一覧がまだ無いのは異常ではない。毎晩の自動実行から呼ばれるので、ここで
        # 失敗を返すとスケジューラが毎日「失敗」を記録し、本物の故障が埋もれる。
        return 0 if getattr(args, "quiet_when_empty", False) else 1

    instances = _instances()
    rows = board.rows(task_list, instances, settings)
    print(board.format_rows(rows))
    summary = board.summarize(rows)
    print("\n" + board.format_summary(summary))

    if args.until:
        remaining = board.remaining_tasks(task_list, instances, settings)
        load = _habit_load(settings, args.window)
        result = fit.make(remaining, instances, _capacity_hours(settings, args),
                          datetime.strptime(args.until, "%Y-%m-%d").date(), settings,
                          _slack_buckets(settings),
                          weekend_days=tuple(settings.get("day_types", {})
                                             .get("weekend", (5, 6))),
                          workdays=tuple(settings.get("day_types", {})
                                         .get("workdays", ())),
                          habit_load=load)
        print(f"\n残りの需要 {result['demand']:.0f}h(net) / "
              f"供給 {result['supply']:.0f}h(net)  〜{args.until}")
        if result["overflow"]:
            print(f"  入りきらない {len(result['overflow'])}本 — "
                  f"詳しくは chronofit plan --until {args.until}")

    if args.save:
        date = _resolve_date(args.date)
        destination = paths.ensure(paths.board_dir()) / f"{date}.json"
        destination.write_text(json.dumps(
            {"date": date, "summary": summary, "rows": rows},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"-> {destination}")
    return 0


def cmd_daily(args):
    """人手ゼロで回す1日の締め。前日を畳んで、進捗を写して残す。

    畳むのと進捗を見るのを別コマンドのままにすると、**忙しい日ほど飛ぶ**。
    忙しい日こそ予定が崩れているので、そこが欠けると記録として使えない。
    """
    date = _resolve_date(args.date or "yesterday")
    failed = cmd_rollup(argparse.Namespace(date=date))
    print()
    board_args = argparse.Namespace(tasks=None, save=True, date=date,
                                    until=None, window=14,
                                    hours=8.0, workday_hours=3.0,
                                    quiet_when_empty=True)
    return cmd_board(board_args) or failed


def _open_file(path):
    """既定のアプリで開く。開けなくても、書き出し自体は成功として扱う。

    `os.startfile` を使うのは、Git Bash から `cmd //c start` を呼ぶ経路が
    引数変換で壊れやすいため（同じ理由で `explorer` の戻り値も当てにしない）。
    """
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(path))            # noqa: S606 - Windows 既定の関連付け
        else:
            import webbrowser
            webbrowser.open(path.as_uri())
        return True
    except OSError as error:
        print(f"開けなかった（ファイルはある）: {error}", file=sys.stderr)
        return False


def _board_for(date, settings, tasks_path=None):
    """レポートに載せる進捗と、それが「いつ時点の値か」。

    過去の日のページに今日の残量を載せると、タイムライン（その日）と進捗（今）が
    同じ紙面で食い違う。`board/<date>.json` はまさに「その日に見えていた残量」なので、
    在るならそれを使う。無ければ数え直すしかないが、その旨を画面に書く（DBは
    「いつ終わったか」を持たないので、過去の done 本数は再現できない）。
    """
    snapshot = paths.board_dir() / f"{date}.json"
    if date != datetime.now().strftime("%Y-%m-%d") and snapshot.is_file():
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        return data.get("rows") or [], data.get("summary"), f"{date} 時点の記録"

    task_list = _tasks(tasks_path)
    if not task_list:
        return [], None, None
    rows = board.rows(task_list, _instances(), settings,
                      datetime.strptime(date, "%Y-%m-%d").date())
    as_of = None if date == datetime.now().strftime("%Y-%m-%d") else "いま数え直した値"
    return board.order(rows), board.summarize(rows), as_of


def cmd_report(args):
    """1日の測定結果を1枚の HTML にして開く。

    標準出力は流れて消えるので、「今日はどうだったか」を見返す面をファイルとして
    残す。中身は生タイトルを含むため、置き場所は data_root() の下に固定する。
    """
    date = _resolve_date(args.date)
    summary = _load_summary(date)
    if summary is None:
        print(f"{date} の生ログが無い。")
        return 1

    settings = config.load()
    # 離席の中身は両隣の前景から当たる。ラベルを付けていない日でも、
    # 「何の前後で離れたか」だけは見えるようにする。
    context_model.annotate(summary, settings.get("title_rules") or [])

    rows, board_summary, as_of = _board_for(date, settings, args.tasks)
    sources = [
        ("この日の生スパン（1行1スパン・タイトル込み）", paths.raw_dir() / f"{date}.jsonl"),
        ("畳んだ集計（共有できる粒度・タイトルは入らない）", paths.rollup_dir() / f"{date}.json"),
        ("離席に付けたラベル", paths.label_dir() / f"{date}.json"),
        ("やることの一覧", paths.tasks_path()),
        ("その日に見えていた残量", paths.board_dir() / f"{date}.json"),
        ("所要時間DB（終わったタスクの実測）", estimate_db.default_path(paths.data_root())),
    ]
    content = report.render(summary, date, rows, board_summary, sources, as_of)
    destination = report.write(paths.ensure(paths.report_dir()) / f"{date}.html", content)
    print(f"-> {destination}")
    if not args.no_open:
        _open_file(destination)
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
    rollup_cmd.add_argument("--date", help="YYYY-MM-DD / today / yesterday（既定は今日）")
    rollup_cmd.set_defaults(func=cmd_rollup)

    label = sub.add_parser("label", help="離席ブロックにラベルを付ける")
    label.add_argument("--date", help="YYYY-MM-DD / today / yesterday（既定は今日）")
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

    task_cmd = sub.add_parser("task", help="やることの一覧を足す / 消す / 見る")
    task_cmd.add_argument("action", nargs="?", default="list",
                          choices=("list", "add", "rm"))
    task_cmd.add_argument("subject", nargs="?", help="科目")
    task_cmd.add_argument("kind", nargs="?", help="種別（過去問 / 参考書 ...）")
    task_cmd.add_argument("--count", type=int, default=1, help="全部で何本やるか")
    task_cmd.add_argument("--due", help="締切 YYYY-MM-DD")
    task_cmd.add_argument("--priority", default="B", help="S / A / B / C")
    task_cmd.add_argument("--target", help="対象（2024年度期末 など）")
    task_cmd.add_argument("--assumed-hours", type=float,
                          help="実測も流用元も無い場合に置く仮値 時間")
    task_cmd.set_defaults(func=cmd_task)

    board_cmd = sub.add_parser("board", help="いまどこまで来ているか（進捗）")
    board_cmd.add_argument("--tasks", type=Path, help="別のタスク定義を使う")
    board_cmd.add_argument("--until", help="この日までの需要と供給も出す YYYY-MM-DD")
    board_cmd.add_argument("--save", action="store_true", help="その日の残量を残す")
    board_cmd.add_argument("--date", help="保存する日付 YYYY-MM-DD / today / yesterday")
    board_cmd.add_argument("--hours", type=float, default=8.0)
    board_cmd.add_argument("--workday-hours", type=float, default=3.0)
    board_cmd.add_argument("--window", type=int, default=14)
    board_cmd.set_defaults(func=cmd_board)

    report_cmd = sub.add_parser("report", help="1日の測定結果を HTML にして開く")
    report_cmd.add_argument("--date", help="YYYY-MM-DD / today / yesterday（既定は今日）")
    report_cmd.add_argument("--tasks", type=Path, help="別のタスク定義を使う")
    report_cmd.add_argument("--no-open", action="store_true", help="書き出すだけで開かない")
    report_cmd.set_defaults(func=cmd_report)

    daily = sub.add_parser("daily", help="前日を畳んで進捗を残す（毎晩の自動実行用）")
    daily.add_argument("--date", help="YYYY-MM-DD / today / yesterday（既定は前日）")
    daily.set_defaults(func=cmd_daily)

    plan_cmd = sub.add_parser("plan", help="タスク一覧を週の容量へ割り付ける")
    plan_cmd.add_argument("--tasks", type=Path, help="別のタスク定義を使う")
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
