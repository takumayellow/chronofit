"""外出の見積もり（`trip`）と決済の記録（`payments`）のサブコマンド。

cli.py が大きくなりすぎないよう、外出まわりはここにまとめる。
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import config, paths
from .estimate import agenda, auto_places, outing
from .sources import gcal, location, owntracks, payments


def all_stays(root, places):
    """取り込み済みの書き出しと受信した点の両方から、全期間の滞在を集める。"""
    imported = []
    for day in location.stored_days(root):
        imported += location.load_day(day, root)
    # 点は全期間まとめて畳む。日ごとに畳むと、数日続いた滞在が日の境目で切れる。
    received = location.to_stays(owntracks.to_segments(owntracks.all_points(root)), None)
    return location.rematch(location.merge_sources(imported, received), places)


def _stays_and_places():
    """全期間の滞在と場所の登録。繰り返し行った未登録の場所は、ここで自動登録し直す。"""
    root = paths.location_dir()
    manual = location.load_places(root, config.load().get("places"), auto=False)
    stays = all_stays(root, manual)
    events = gcal.load(paths.calendar_dir())
    if events:
        guessed = location.valid_places(auto_places.discover(stays, events, manual))
        try:
            location.save_auto_places(root, guessed)
        except OSError:               # 書けなくても今回の結果には使う
            pass
    else:                             # 予定が読めないときは作り直さず、前回の登録を使う
        names = {place["name"] for place in manual}
        guessed = [place for place in location.load_places(root, auto=True)
                   if place.get("auto") and place["name"] not in names]
    places = manual + guessed
    return location.rematch(stays, places), places


def _label(visit_list):
    """滞在に用事を付ける。決済を先に使い、残りを繰り返しの予定で埋める。"""
    root = paths.payments_dir()
    paid = payments.label_visits(visit_list, payments.load(root), payments.load_rules(root))
    return agenda.label_visits(paid, gcal.load(paths.calendar_dir()))


def spot_titles(stays):
    """未登録の場所ごとに、そこにいた時間に重なった予定の件名（予定が無ければ空）。"""
    return agenda.spot_titles(stays, gcal.load(paths.calendar_dir()))


def _labeled_visits(stays):
    return _label(outing.visits(stays))


def _homes(places):
    return {place["name"] for place in places if place.get("home")}


def _fmt(minutes):
    return "   ?" if minutes is None else f"{minutes:4.0f}"


def _trip_legs(args):
    stays, _ = _stays_and_places()
    table = outing.leg_table(outing.legs(stays), since=args.since)
    rows = sorted(table.items(), key=lambda item: -item[1]["n"])
    print("区間                         件数  中央値  p80   最短  最長（分）")
    for (origin, destination), s in rows:
        if s["n"] < args.min_n:
            continue
        print(f"  {origin + ' → ' + destination:24.24} {s['n']:4}  {_fmt(s['median'])}  "
              f"{_fmt(s['p80'])}  {_fmt(s['min'])}  {_fmt(s['max'])}")
    return 0


def _trip_dwell(args):
    stays, _ = _stays_and_places()
    table = outing.dwell_table(_labeled_visits(stays), since=args.since)
    rows = sorted(table.items(), key=lambda item: -item[1]["n"])
    print("場所（用事）                 件数  中央値  p80（分）")
    for (place, activity), s in rows:
        if s["n"] < args.min_n:
            continue
        label = place + {outing.ANY: "（全体）", None: "（用事不明）"}.get(
            activity, f"（{activity}）")
        print(f"  {label:24.24} {s['n']:4}  {_fmt(s['median'])}  {_fmt(s['p80'])}")
    return 0


def _trip_plan(args):
    stays, places = _stays_and_places()
    try:
        steps = outing.parse_steps(args.steps)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if len(steps) < 2:
        print("出発地と到着地を含めて2つ以上の場所を渡す", file=sys.stderr)
        return 1
    depart = None
    if args.depart:
        try:
            clock = datetime.strptime(args.depart, "%H:%M")
        except ValueError:
            print("--depart は HH:MM", file=sys.stderr)
            return 1
        depart = datetime.now().replace(hour=clock.hour, minute=clock.minute,
                                        second=0, microsecond=0)
    result = outing.plan(steps, outing.leg_table(outing.legs(stays), since=args.since),
                         outing.dwell_table(_labeled_visits(stays), since=args.since),
                         places, depart)
    for row in result["rows"]:
        clock = (f"{row['start']:%H:%M}–{row['end']:%H:%M}  " if "start" in row else "")
        print(f"  {clock}{row['label']:22.22} {_fmt(row['median'])}分 "
              f"(p80 {_fmt(row['p80'])})  {row['basis']}")
    print(f"合計 中央値 {result['median']:.0f}分 / 余裕を見て {result['p80']:.0f}分")
    if result["unknown"]:
        print(f"見積もれない: {', '.join(result['unknown'])}（場所の登録か時間の指定が要る）")
    return 0


def _trip_backtest(args):
    stays, places = _stays_and_places()
    homes = _homes(places)
    if not homes:
        print("家を places.json で \"home\": true にする", file=sys.stderr)
        return 1
    results = outing.backtest(stays, homes, places, since=args.since, label=_label)
    print(f"外出 {len(results)}回（その日を除いた実測だけで見積もり直して比較）")
    for key, title in (("legs_only", "区間だけ予測（滞在は実績）"),
                       ("full", "滞在も予測")):
        for clean in (True, False):
            s = outing.score(results, key, clean_only=clean)
            scope = "寄り道なし" if clean else "全部"
            if s:
                print(f"  {title} / {scope:5}: {s['n']:3}回  平均誤差 {s['mae']:.0f}分  "
                      f"偏り {s['bias']:+.0f}分  p80 以内 {s['within_p80']:.0%}")
    detours = [r["detour"] for r in results if not r["clean"]]
    if detours:
        detours.sort()
        print(f"  寄り道のあった外出 {len(detours)}回: 登録外で過ごした時間の中央値 "
              f"{detours[len(detours) // 2]:.0f}分（`location spots` で場所を登録すると減る）")
    return 0


def cmd_trip(args):
    """外出の区間・滞在の実測と、予定した外出の見積もり。"""
    handlers = {"legs": _trip_legs, "dwell": _trip_dwell, "plan": _trip_plan,
                "backtest": _trip_backtest}
    return handlers[args.action](args)


def _valid_date(text):
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _payments_fetch(args):
    from .sources import gmail
    if args.since and not _valid_date(args.since):
        print("--since は YYYY-MM-DD", file=sys.stderr)
        return 1
    root = paths.ensure(paths.payments_dir())
    known = {row.get("id") for row in payments.load(root)}
    try:
        rows, skipped = gmail.fetch(root, since=args.since, known_ids=known)
    except RuntimeError as error:       # 未同意・未登録。こちらが書いた文面だけ
        print(f"取得できなかった: {error}", file=sys.stderr)
        return 1
    except Exception as error:        # noqa: BLE001 - 日次の自動実行を止めない
        # 認証まわりの例外の文面は外部の応答を含み得るので、種類だけ出す
        print(f"取得できなかった: {type(error).__name__}"
              "（同意が切れていれば `chronofit payments auth` をやり直す）", file=sys.stderr)
        return 1
    added = payments.append(rows, root)
    print(f"決済 {added}件を足した（読めなかった通知 {skipped}件）-> {root / payments.LEDGER}")
    return 0


def fetch_if_authorized():
    """日次の締めから呼ぶ。同意済みのものだけ取り直し、失敗しても締めは止めない。"""
    from .sources import gmail
    root = paths.payments_dir()
    if (root / gmail.TOKEN_FILE).is_file():
        print()
        _payments_fetch(argparse.Namespace(since=_fetch_since(payments.load(root))))
    if (paths.calendar_dir() / gcal.TOKEN_FILE).is_file():
        since = (datetime.now() - timedelta(days=CALENDAR_REFETCH_DAYS)).date().isoformat()
        _calendar_fetch(argparse.Namespace(since=since))


def _fetch_since(rows, overlap_days=3):
    """持っている最新の決済の数日前。通知の遅れを拾うため少し重ねる。無ければ全期間。"""
    if not rows:
        return None
    latest = max(datetime.fromisoformat(row["time"]) for row in rows)
    return (latest - timedelta(days=overlap_days)).date().isoformat()


def cmd_payments(args):
    """決済の通知メールを読んで、滞在の用事を当てる材料にする。"""
    root = paths.payments_dir()
    if args.action == "auth":
        from .sources import gmail
        secret = Path(args.client_secret)
        if not secret.is_file():
            print(f"OAuth クライアントの JSON が無い: {secret}", file=sys.stderr)
            return 1
        gmail.authorize(paths.ensure(root), secret)
        print(f"同意を受け取った。トークンは暗号化して保存 -> {root}")
        return 0
    if args.action == "fetch":
        return _payments_fetch(args)
    if args.action == "source":
        try:
            payments.save_source(root, args.sender, args.subject, args.format, args.prefix)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        print(f"読む通知: {args.sender} の件名「{args.subject}」（{args.format}）")
        return 0
    if args.action == "rule":
        payments.save_rule(root, args.match, args.activity)
        print(f"「{args.match}」を含む店 → {args.activity}")
        return 0
    rows = payments.load(root)
    rules = payments.load_rules(root)
    if args.action == "unmapped":
        pending = payments.unmapped(rows, rules)
        if not pending:
            print("用事の決まっていない店は無い。")
        for merchant, count in pending[:args.limit]:
            print(f"  {count:3}回  {merchant}")
        return 0
    for row in sorted(rows, key=lambda r: r["time"])[-args.limit:]:
        activity = payments.activity_of(row["merchant"], rules) or "?"
        print(f"  {row['time'][:16]}  {row['amount']:>9,.0f} {row['currency']}  "
              f"{row['merchant']}  [{activity}]")
    return 0


CALENDAR_REFETCH_DAYS = 14     # 日次はこの日数ぶん遡って取り直す（後から直した予定を拾う）
CALENDAR_DEFAULT_DAYS = 365


def _calendar_fetch(args):
    since_day = args.since or (datetime.now()
                               - timedelta(days=CALENDAR_DEFAULT_DAYS)).date().isoformat()
    if not _valid_date(since_day):
        print("--since は YYYY-MM-DD", file=sys.stderr)
        return 1
    root = paths.ensure(paths.calendar_dir())
    since, until = gcal.window(since_day)
    try:
        rows = gcal.fetch(root, since, until)
    except RuntimeError as error:
        print(f"予定を取得できなかった: {error}", file=sys.stderr)
        return 1
    except Exception as error:        # noqa: BLE001 - 日次の自動実行を止めない
        status = getattr(error, "status_code", None)
        hint = ("Google Cloud のプロジェクトで Calendar API が有効か確かめる" if status == 403
                else "同意が切れていれば `chronofit calendar auth` をやり直す")
        print(f"予定を取得できなかった: {type(error).__name__}"
              f"{f' {status}' if status else ''}（{hint}）", file=sys.stderr)
        return 1
    gcal.save(root, gcal.replace_window(gcal.load(root), rows, since, until))
    print(f"予定 {len(rows)}件（{since_day} 以降）-> {root / gcal.EVENTS}")
    return 0


def _fmt_clock(moment, day):
    """時刻。予定の日と違う日なら日付も付ける（日をまたぐ滞在を読み違えない）。"""
    if not moment:
        return "  ?  "
    moment = moment.astimezone(day.tzinfo)
    return f"{moment:%H:%M}" if moment.date() == day.date() else f"{moment:%m/%d %H:%M}"


def _calendar_check(args, events, stays, places):
    now = datetime.now().astimezone()
    rows = [row for row in agenda.check(events, stays, _homes(places), now=now)
            if not args.since or row["event"]["start"][:10] >= args.since]
    print("予定                              予定の時刻      実際（場所）")
    for row in rows[-args.limit:]:
        event = row["event"]
        start, end = datetime.fromisoformat(event["start"]), datetime.fromisoformat(event["end"])
        where = row["status"]
        if row["status"] in ("外出", "未登録の場所", "家"):
            where = (f"{_fmt_clock(row['actual_start'], start)}–"
                     f"{_fmt_clock(row['actual_end'], start)} "
                     f"{row['place']}")
        print(f"  {start:%m/%d} {event['title'][:24]:24}  {start:%H:%M}–{end:%H:%M}  {where}")
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("  内訳: " + " / ".join(f"{k} {v}件" for k, v in sorted(counts.items(),
                                                                key=lambda kv: -kv[1])))
    return 0


def _current_home(visit_list, homes):
    for visit in reversed(visit_list):
        if visit["place"] in homes:
            return visit["place"]
    return next(iter(sorted(homes)), None)


def _calendar_next(args, events, stays, places):
    """先の予定のうち、場所が分かるものに「家を出る時刻」を付ける。"""
    now = datetime.now().astimezone()
    homes = _homes(places)
    checked = agenda.check(events, stays, homes, now=now)
    where = agenda.title_places(checked)
    origin = _current_home(outing.visits(stays), homes)
    if not origin:
        print("家が登録されていない。`chronofit location place 家 緯度 経度 --home`",
              file=sys.stderr)
        return 1
    if not 1 <= args.days <= gcal.AHEAD_DAYS:
        print(f"--days は 1〜{gcal.AHEAD_DAYS}（取得しているのはその先まで）", file=sys.stderr)
        return 1
    legs_by_pair = outing.leg_table(outing.legs(stays))
    until = now + timedelta(days=args.days)
    shown = 0
    for event in events:
        start = datetime.fromisoformat(event["start"])
        if not now <= start < until:
            continue
        place = where.get(event["title"]) or agenda.place_for(event["title"], checked)
        line = f"  {start:%m/%d %H:%M} {event['title'][:24]:24}"
        if not place:
            print(f"{line}  場所は未確定")
            shown += 1
            continue
        found, basis = outing.leg_estimate(origin, place, legs_by_pair, places)
        if place == origin or not found:
            print(f"{line}  {place}（{origin}からの所要は{basis}）")
        else:
            leave = start - timedelta(minutes=found["median"])
            safe = start - timedelta(minutes=found["p80"])
            print(f"{line}  {place}  {origin}を {leave:%H:%M} に出る"
                  f"（余裕を見て {safe:%H:%M}・{basis}）")
        shown += 1
    if not shown:
        print("先の予定は無い。")
    return 0


def cmd_calendar(args):
    """Google カレンダーの予定を位置の記録と突き合わせる（読み取り専用）。"""
    if args.action == "auth":
        from .sources import gmail
        secret = Path(args.client_secret)
        if not secret.is_file():
            print(f"OAuth クライアントの JSON が無い: {secret}", file=sys.stderr)
            return 1
        root = paths.ensure(paths.calendar_dir())
        gmail.authorize(root, secret, scopes=gcal.SCOPES, name=gcal.TOKEN_FILE)
        print(f"同意を受け取った。トークンは暗号化して保存 -> {root}")
        return 0
    if args.action == "fetch":
        return _calendar_fetch(args)
    events = gcal.load(paths.calendar_dir())
    if not events:
        print("予定が無い。先に `chronofit calendar fetch`", file=sys.stderr)
        return 1
    stays, places = _stays_and_places()
    handler = _calendar_check if args.action == "check" else _calendar_next
    return handler(args, events, stays, places)


def cmd_place(args):
    """場所を git の外の places.json へ登録する。"""
    place = {"name": args.name, "lat": args.lat, "lng": args.lng, "radius_m": args.radius}
    if args.label:
        place["away_label"] = args.label
    if args.home:
        place["home"] = True
    path = location.save_place(paths.location_dir(), place)
    print(f"登録した: {args.name} -> {path}")
    return 0


def register(sub):
    trip = sub.add_parser("trip", help="外出の区間・滞在の実測と、予定した外出の見積もり")
    trip_sub = trip.add_subparsers(dest="action", required=True)
    for name, text in (("legs", "場所から場所への所要時間"), ("dwell", "場所（用事）ごとの滞在時間")):
        cmd = trip_sub.add_parser(name, help=text)
        cmd.add_argument("--since", help="この日付（YYYY-MM-DD）以降だけ使う")
        cmd.add_argument("--min-n", type=int, default=3, help="これ未満の件数は出さない")
    trip_plan = trip_sub.add_parser("plan", help="場所の列を見積もる。例: 家 駅前@食事 家")
    trip_plan.add_argument("steps", nargs="+",
                           help="場所[@用事][:分]。最初と最後は出発地と帰着地")
    trip_plan.add_argument("--depart", help="出発時刻 HH:MM")
    trip_plan.add_argument("--since", help="この日付以降の実測だけ使う（引っ越し後など）")
    trip_back = trip_sub.add_parser("backtest", help="過去の外出で見積もりの誤差を測る")
    trip_back.add_argument("--since")
    trip.set_defaults(func=cmd_trip)

    pay = sub.add_parser("payments", help="決済の通知メールから用事を当てる")
    pay_sub = pay.add_subparsers(dest="action", required=True)
    auth = pay_sub.add_parser("auth", help="Gmail の読み取り専用の権限に1回だけ同意する")
    auth.add_argument("--client-secret", required=True, help="OAuth クライアント（デスクトップ）の JSON")
    fetch = pay_sub.add_parser("fetch", help="新しい通知メールを読んで足す")
    fetch.add_argument("--since", help="この日付（YYYY-MM-DD）以降のメールだけ見る")
    source = pay_sub.add_parser("source", help="読む通知メールを登録する（git の外）")
    source.add_argument("sender", help="送信元のメールアドレス")
    source.add_argument("subject", help="件名に含まれる語")
    source.add_argument("format", choices=sorted(payments.FORMATS), help="本文の形式")
    source.add_argument("--prefix", help="店名の前に付ける語（チェーン名など）")
    rule = pay_sub.add_parser("rule", help="店名に含まれる語 → 用事 を登録する（- で場所と無関係）")
    rule.add_argument("match")
    rule.add_argument("activity")
    for name, text in (("list", "決済の一覧"), ("unmapped", "用事の決まっていない店")):
        cmd = pay_sub.add_parser(name, help=text)
        cmd.add_argument("--limit", type=int, default=30)
    pay.set_defaults(func=cmd_payments)

    cal = sub.add_parser("calendar", help="カレンダーの予定を位置の記録と突き合わせる")
    cal_sub = cal.add_subparsers(dest="action", required=True)
    cal_auth = cal_sub.add_parser("auth", help="カレンダーの読み取り専用の権限に1回だけ同意する")
    cal_auth.add_argument("--client-secret", required=True,
                          help="OAuth クライアント（デスクトップ）の JSON")
    cal_fetch = cal_sub.add_parser("fetch", help="予定を取り直す（git の外に保存）")
    cal_fetch.add_argument("--since", help="この日付（YYYY-MM-DD）以降。既定は1年前")
    cal_check = cal_sub.add_parser("check", help="過去の予定の時間に実際どこにいたか")
    cal_check.add_argument("--since", help="この日付（YYYY-MM-DD）以降の予定だけ")
    cal_check.add_argument("--limit", type=int, default=40)
    cal_next = cal_sub.add_parser("next", help="先の予定の場所と、家を出る時刻の目安")
    cal_next.add_argument("--days", type=int, default=7)
    cal.set_defaults(func=cmd_calendar)


def register_place(loc_sub):
    place = loc_sub.add_parser("place", help="場所を登録する（git の外の places.json）")
    place.add_argument("name")
    place.add_argument("lat", type=float)
    place.add_argument("lng", type=float)
    place.add_argument("--radius", type=float, default=location.DEFAULT_RADIUS_M)
    place.add_argument("--label", help="この場所にいた離席に提案するラベル")
    place.add_argument("--home", action="store_true", help="家として扱う（外出の起点）")


def agenda_for_day(day):
    """その日の予定を (終わった予定と実際にいた場所, まだ終わっていない予定と見積もり) に分ける。"""
    events = [event for event in gcal.load(paths.calendar_dir())
              if event["start"][:10] == day]
    if not events:
        return [], []
    now = datetime.now().astimezone()
    upcoming = [event for event in events
                if datetime.fromisoformat(event["end"].replace("Z", "+00:00")) > now]
    stays, places = _stays_and_places()
    homes = _homes(places)
    past = agenda.check(gcal.load(paths.calendar_dir()), stays, homes, now=now)
    ahead = [{"event": event, "forecast": agenda.forecast(event, past, stays)}
             for event in upcoming]
    return agenda.check(events, stays, homes, now=now), ahead


def refresh_calendar(day):
    """`day` から先の予定を取り直して写しを入れ替える。取れなければ写しのまま（False）。

    日次の取り直しは翌朝なので、それだけだと今日消した予定が今日のレポートに残る。
    """
    root = paths.ensure(paths.calendar_dir())
    try:
        since, until = gcal.window(day)
        rows = gcal.fetch(root, since, until)
    except Exception:                 # noqa: BLE001 - 取れなければ手元の写しで出す
        return False
    gcal.save(root, gcal.replace_window(gcal.load(root), rows, since, until))
    return True
