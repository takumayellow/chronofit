"""日次ページと一覧ページを書き出す（`chronofit report`）。

ページは生活の1日（`day_start`、既定 5:00 から翌 5:00）で1枚。夜中の作業や寝る前の
スマホは前の日のページに入る。書き出すたびに、前の日のページと一覧（index.html）も
書き直す（区切りの直後に前の日の最後の数時間が落ちないように）。

置き場所は `paths.report_dir()` だけ。生タイトルを含むので git には入れない。

`chronofit site` はこのフォルダだけを 127.0.0.1 で配る。外へは出さず、スマホ等からは
`tailscale serve --bg http://127.0.0.1:<port>` で tailnet の中にだけ通す（funnel は使わない）。
"""
import functools
import time as clock_time
from datetime import date as date_type, datetime, timedelta
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import config, paths, report_extras
from .model import context as context_model
from .model import day as day_model
from .estimate import db as estimate_db
from .ui import report, site_index

INDEX_DAYS = 14
SITE_HOST = "127.0.0.1"
SITE_PORT = 47615      # 開発用サーバーがよく使う 8000 番台を避ける


def logical_today(settings):
    return day_model.logical_date(datetime.now(), day_model.start_clock(settings))


def resolve(value, settings):
    """`--date` を生活の1日に直す。既定と `today` は「いま属している1日」。"""
    today = logical_today(settings)
    if not value or value == "today":
        return today
    if value == "yesterday":
        return (date_type.fromisoformat(today) - timedelta(days=1)).isoformat()
    from . import cli
    return cli._resolve_date(value)


def _shift(day, n):
    return (date_type.fromisoformat(day) + timedelta(days=n)).isoformat()


def _summary(day, settings, cache=None):
    """生活の1日のサマリ。`cache` を渡すと同じ実行の中で同じ日を2度作らない。"""
    from . import cli
    if cache is not None and day in cache:
        return cache[day]
    bounds = day_model.bounds(day, day_model.start_clock(settings))
    result = cli._load_summary(day, bounds=bounds, settings=settings), bounds
    if cache is not None:
        cache[day] = result
    return result


def write_day(day, settings, tasks=None, today=None, cache=None):
    """1日ぶんのページを書く。生ログが無ければ None。"""
    from . import cli
    summary, bounds = _summary(day, settings, cache)
    if summary is None:
        return None
    context_model.annotate(summary, settings.get("title_rules") or [])
    today = today or logical_today(settings)
    if not tasks and day == today:
        cli.sync_issues(quiet=True)          # 今日のページは、いま開いている Issue で数える
    rows, board_summary, as_of = cli._board_for(day, settings, tasks)
    sources = [
        ("この日の生スパン（暦の日ごと・1行1スパン・タイトル込み）",
         paths.raw_dir() / f"{day}.jsonl"),
        ("畳んだ集計（暦の日ごと・共有できる粒度・タイトルは入らない）",
         paths.rollup_dir() / f"{day}.json"),
        ("離席に付けたラベル", paths.label_dir() / f"{day}.json"),
        ("やることの一覧", paths.tasks_path()),
        ("その日に見えていた残量", paths.board_dir() / f"{day}.json"),
        ("所要時間DB（終わったタスクの実測）", estimate_db.default_path(paths.data_root())),
    ]
    records = cli._read_records(day, bounds)
    extras = report_extras.gather(day, settings, bounds=bounds, records=records, today=today)
    extras["project_groups"] = settings.get("project_groups") or {}
    extras["default_group"] = settings.get("default_project_group") or "開発"
    nav = {"start": bounds[0], "end": bounds[1], "prev": f"{_shift(day, -1)}.html",
           "next": f"{_shift(day, 1)}.html" if day < today else None}
    content = report.render(summary, day, rows, board_summary, sources, as_of, extras, nav)
    return report.write(paths.ensure(paths.report_dir()) / f"{day}.html", content)


def write_index(settings, today=None, count=INDEX_DAYS, cache=None):
    today = today or logical_today(settings)
    days = []
    for n in range(count):
        day = _shift(today, -n)
        summary, bounds = _summary(day, settings, cache)
        days.append((day, bounds[0], summary))
    while days and days[-1][2] is None:      # 記録を始める前の日は並べない
        days.pop()
    content = site_index.render(days, today)
    return report.write(paths.ensure(paths.report_dir()) / "index.html", content)


def run(args, open_file):
    settings = config.load()
    today = logical_today(settings)
    started = clock_time.monotonic()
    cache = {}
    if args.rebuild:
        written = [write_day(_shift(today, -n), settings, args.tasks, today, cache)
                   for n in range(args.days)]
        print(f"-> {sum(1 for path in written if path)} 日ぶん書き直した")
        destination = write_index(settings, today, max(args.days, INDEX_DAYS), cache)
    else:
        day = resolve(args.date, settings)
        destination = write_day(day, settings, args.tasks, today, cache)
        if destination is None:
            print(f"{day} の生ログが無い。")
            return 1
        if day == today:
            write_day(_shift(day, -1), settings, args.tasks, today, cache)
        write_index(settings, today, cache=cache)
    print(f"-> {destination}（{clock_time.monotonic() - started:.1f}秒）")
    if not args.no_open:
        open_file(destination)
    return 0


class _PageHandler(SimpleHTTPRequestHandler):
    """書き出したページだけを返す。フォルダの一覧は出さず、アクセスの記録も残さない。"""

    def list_directory(self, path):
        self.send_error(HTTPStatus.NOT_FOUND)
        return None

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Robots-Tag", "noindex")
        super().end_headers()

    def log_message(self, format, *args):    # pythonw では stderr が無い
        pass


class _ExclusiveServer(ThreadingHTTPServer):
    # 既定の SO_REUSEADDR は Windows では使用中のポートにも重ねて bind できてしまい、
    # 別のサーバーと同じポートで黙って取り合う。使用中なら起動に失敗させる。
    allow_reuse_address = False


def make_server(port=SITE_PORT, host=SITE_HOST):
    handler = functools.partial(_PageHandler, directory=str(paths.ensure(paths.report_dir())))
    return _ExclusiveServer((host, port), handler)


def serve(args):
    server = make_server(args.port)
    print(f"-> http://{SITE_HOST}:{args.port}/ で配る（Ctrl+C で止める）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
