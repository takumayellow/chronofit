"""生スパンから「このタスクに何時間かかったか」を切り出す。

所要時間DBへ入れる数字を**人間に思い出させない**ための層。人間が言うのは
「応用数学Bの過去問2024、終わった」だけで、何時間かかったかは L0 が既に知っている。

タイトルの正規表現で拾うのは、タイトルが作業の *対象* を持っているから
（`応用数学A_2024_期末.pdf - SumatraPDF`）。ブラウザ履歴では PDF もエディタも見えない。
"""
import re
from datetime import date as date_type
from datetime import timedelta

from ..model import rollup


def _dates(since, until):
    start = date_type.fromisoformat(since)
    end = date_type.fromisoformat(until) if until else date_type.today()
    if end < start:
        start, end = end, start
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def collect(raw_dir, pattern, since, until=None):
    """タイトルが pattern に一致したスパンの net / passive / wall を日別に合算する。

    net は在席かつ入力ありの時間。passive は手が止まっていたが前景が再生していた時間で、
    講義映像を観た時間はここに入る。**観ていた時間も作業時間**なので、DBへ入れる値は
    net + passive にする。片方だけにすると、動画で学ぶ科目が実際より軽く見える。
    """
    regex = re.compile(pattern, re.IGNORECASE)
    days = []
    for day in _dates(since, until):
        path = raw_dir / f"{day.isoformat()}.jsonl"
        if not path.is_file():
            continue
        segments = rollup.to_segments(rollup.read_day(path))
        net = wall = passive = 0.0
        matched = set()
        for segment in segments:
            if not regex.search(segment["title"]):
                continue
            if segment["kind"] == "present":
                net += segment["active_sec"]
                wall += segment["sec"]
            elif segment["kind"] == "passive":
                passive += segment["sec"]
                wall += segment["sec"]
            else:
                continue
            matched.add(segment["title"])
        if net + passive > 0:
            days.append({"date": day.isoformat(),
                         "net_hours": (net + passive) / 3600.0,
                         "input_hours": net / 3600.0,
                         "passive_hours": passive / 3600.0,
                         "wall_hours": wall / 3600.0, "titles": sorted(matched)})
    return days


def totals(days):
    """日別を1インスタンス分へ畳む。sessions は「何日に分けたか」。"""
    return {
        "net_hours": sum(d["net_hours"] for d in days),
        "input_hours": sum(d.get("input_hours", d["net_hours"]) for d in days),
        "passive_hours": sum(d.get("passive_hours", 0.0) for d in days),
        "wall_hours": sum(d["wall_hours"] for d in days),
        "sessions": len(days),
        "first": days[0]["date"] if days else None,
        "last": days[-1]["date"] if days else None,
        "titles": sorted({t for d in days for t in d["titles"]}),
    }
