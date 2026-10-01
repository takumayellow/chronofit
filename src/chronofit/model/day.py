"""生活の1日（既定は朝5時〜翌朝5時）で区切る。

暦の0時で切ると、夜中まで続けた作業や寝る前のスマホが翌日の頭に入り、
「その日の終わり」と「次の日の始まり」が1枚に混ざる。人が1日として思い出すのは
起きてから寝るまでなので、区切りを夜明け前に置く。

- 区切りの時刻は利用側の設定（`day_start`）。生活の形をロジックに焼き込まない
- 生ログは暦の日ごとのファイルのまま。読むときに前後の日を足して切り直す
- 区切りをまたぐスパンは、長さに比例して秒を割る（片側に全部入れると合計が合わない）
"""
from datetime import date as date_type, datetime, time, timedelta

DEFAULT_START = "05:00"


def start_clock(settings=None):
    """設定の `day_start`（"HH:MM"）。読めなければ既定の 5:00。"""
    text = (settings or {}).get("day_start") or DEFAULT_START
    try:
        return time.fromisoformat(str(text))
    except ValueError:
        return time.fromisoformat(DEFAULT_START)


def bounds(day, clock):
    """`day`（YYYY-MM-DD か date）の生活の1日 [開始, 終了)。現地時刻。"""
    if isinstance(day, str):
        day = date_type.fromisoformat(day)
    start = datetime.combine(day, clock).astimezone()
    return start, datetime.combine(day + timedelta(days=1), clock).astimezone()


def logical_date(moment, clock):
    """その時刻が属する生活の1日。区切りより前なら前の日。"""
    moment = moment.astimezone()
    day = moment.date()
    if moment.time().replace(tzinfo=None) < clock:
        day -= timedelta(days=1)
    return day.isoformat()


def calendar_days(day):
    """生活の1日 `day` が触れる暦の日（その日と翌日）。"""
    current = date_type.fromisoformat(day)
    return [current.isoformat(), (current + timedelta(days=1)).isoformat()]


def _parse(text):
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.astimezone()


def clip_records(records, start, end):
    """生のレコード列を [start, end) に切り詰める。

    秒（sec・active_sec・media_sec）は残った長さの割合で割る。スパンの中で
    いつ手が動いたかは測っていないので、均等にあったとみなすしかない。
    """
    clipped = []
    for record in records:
        a, b = _parse(record["start"]), _parse(record["end"])
        lo, hi = max(a, start), min(b, end)
        whole = (b - a).total_seconds()
        if whole <= 0:
            if start <= a < end:
                clipped.append(dict(record))
            continue
        if hi <= lo:
            continue
        ratio = (hi - lo).total_seconds() / whole
        item = dict(record, start=lo.isoformat(timespec="seconds"),
                    end=hi.isoformat(timespec="seconds"))
        for key in ("sec", "active_sec", "media_sec"):
            if isinstance(record.get(key), (int, float)):
                item[key] = record[key] * ratio
        clipped.append(item)
    return sorted(clipped, key=lambda record: record["start"])
