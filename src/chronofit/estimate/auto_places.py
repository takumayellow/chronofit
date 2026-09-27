"""2回以上行った未登録の場所を、重なった予定の件名で仮の名前を付けて登録する。

場所を手で登録するのを待つと、行った先の大半は「未登録の場所」のままになり、予定との
突き合わせも見積もりも効かない。同じ場所（約100m 以内）に別の日に2回以上、10分以上
いたら、その時間に重なった予定の件名のうち一番多い日数のものを仮の名前にする。

- 件名が別の日に2回以上重なっていない場所は登録しない。1回だけの件名は、その日に
  そこでたまたま済ませた用事（「洗濯物」など）であることが多く、場所の名前にならない
- 手で登録した場所と同じ名前になったら、手の登録を優先して番号を付ける
- 結果は git の外の `auto_places.json` に置き直す（手の登録 `places.json` とは別。
  読み書きは `location.load_places` / `location.save_auto_places`）
"""
from collections import Counter
from datetime import timedelta

from ..sources import location
from . import agenda

CLUSTER_RADIUS_M = 100.0
MIN_VISIT_DAYS = 2                    # 別の日にこれ以上行った場所だけ
MIN_VISIT = timedelta(minutes=10)     # バスで通り過ぎただけの滞在を数えない
MAX_EVENT = timedelta(hours=6)        # 終日・長時間の予定は場所の名前にしない
MIN_EVENT_SHARE = 0.5                 # 予定の時間のこれ以上をその場所で過ごした予定だけ
MIN_TITLE_DAYS = 2                    # 名前にする件名は、別の日にこれ以上重なったものだけ


def _t(value):
    return location._parse_time(value)


def _clusters(stays, radius_m):
    """未登録の滞在を、重心から `radius_m` 以内で貪欲に束ねる。"""
    clusters = []
    for stay in sorted(stays, key=lambda s: _t(s["start"])):
        coords = (stay["lat"], stay["lng"])
        near = [c for c in clusters if location.distance_m(coords, c["center"]) <= radius_m]
        if near:
            cluster = min(near, key=lambda c: location.distance_m(coords, c["center"]))
            members = cluster["stays"] + [stay]
            cluster["stays"] = members
            cluster["center"] = (sum(s["lat"] for s in members) / len(members),
                                 sum(s["lng"] for s in members) / len(members))
        else:
            clusters.append({"center": coords, "stays": [stay]})
    return clusters


def _titles(cluster_stays, index):
    """その場所にいた日ごとに、場所で過ごした予定の件名を数える（同じ日の同じ件名は1回）。"""
    by_day = {}
    for stay in cluster_stays:
        start, end = _t(stay["start"]), _t(stay["end"])
        for event in index.overlapping(start, end):
            e_start, e_end = _t(event["start"]), _t(event["end"])
            span = (e_end - e_start).total_seconds()
            if not event["title"] or not 0 < span <= MAX_EVENT.total_seconds():
                continue
            if agenda._overlap(start, end, e_start, e_end) >= span * MIN_EVENT_SHARE:
                by_day.setdefault(location.day_of(stay), set()).add(event["title"])
    counts = Counter()
    for titles in by_day.values():
        counts.update(titles)
    return counts


def _unique(name, taken):
    candidate, n = name, 2
    while candidate in taken:
        candidate, n = f"{name} {n}", n + 1
    return candidate


def discover(stays, events, known=(), radius_m=CLUSTER_RADIUS_M,
             min_days=MIN_VISIT_DAYS, min_visit=MIN_VISIT, min_title_days=MIN_TITLE_DAYS):
    """未登録の滞在から、繰り返し行った場所を名前付きで返す。元の列は変えない。"""
    candidates = [s for s in stays
                  if s.get("place") == location.UNKNOWN and "lat" in s and "lng" in s
                  and _t(s["end"]) - _t(s["start"]) >= min_visit]
    index = agenda._Index(events)
    found = []
    for cluster in _clusters(candidates, radius_m):
        days = {location.day_of(s) for s in cluster["stays"]}
        if len(days) < min_days:
            continue
        titles = _titles(cluster["stays"], index)
        if not titles or titles.most_common(1)[0][1] < min_title_days:
            continue
        found.append({"days": len(days), "center": cluster["center"],
                      "titles": [title for title, _ in titles.most_common()]})
    taken = {place["name"] for place in known}
    result = []
    for spot in sorted(found, key=lambda s: -s["days"]):
        name = _unique(spot["titles"][0], taken)
        taken.add(name)
        result.append({"name": name,
                       "lat": round(spot["center"][0], location.COORD_DIGITS),
                       "lng": round(spot["center"][1], location.COORD_DIGITS),
                       "radius_m": radius_m, "auto": True, "visit_days": spot["days"],
                       "titles": spot["titles"]})
    return result

