"""カレンダーの予定と位置の記録の突き合わせ。

予定どおりに動いたとは限らないので、予定は「その時間にどこにいたか」で確かめる:

- 過去の予定: 予定の時間に一番長くいた場所と、実際にそこにいた時刻（`check`）
- 件名 → 場所: 同じ件名の予定を同じ場所で2回以上こなしていれば、その件名の場所とみなす
- 滞在の用事: 繰り返しの予定（同じ件名が3回以上）が滞在の半分以上を覆えば、その件名を用事にする
- 未登録の場所: そこにいた時間に重なった予定の件名を、場所を登録するときの手掛かりにする
- 先の予定: 同じ場所の過去の予定から、何分前に出て何分後に終わるかを見積もる（`forecast`）
"""
import re
from bisect import bisect_left
from collections import Counter
from datetime import datetime, timedelta

from ..sources import location

MIN_COVERAGE = 0.5          # 予定の時間のこれ以上が記録で埋まっていなければ「記録なし」
MIN_REPEAT = 3              # 用事として使う件名の最低回数（粒度の決まり: 3回以上繰り返すもの）
MIN_PLACE_MATCH = 2         # 件名 → 場所とみなす最低回数
JOIN_GAP = timedelta(minutes=10)
MAX_EVENT = timedelta(days=1)
MAX_LEAD = timedelta(hours=2)    # これより前に出ていたら、寄り道してから行ったとみなす
MAX_BOOKING_GAP = timedelta(minutes=30)   # 着いた時刻と予定の開始がこれ以内の予定を「予約」とみなす


def _t(value):
    return value if isinstance(value, datetime) else location._parse_time(value)


def _overlap(a_start, a_end, b_start, b_end):
    return max(0.0, (min(a_end, b_end) - max(a_start, b_start)).total_seconds())


class _Index:
    """開始時刻順に並べて、ある時間帯に重なるものを速く探す。"""

    def __init__(self, items, longest=MAX_EVENT):
        self.items = sorted(items, key=lambda item: _t(item["start"]))
        self.starts = [_t(item["start"]) for item in self.items]
        self.longest = longest

    def overlapping(self, start, end):
        i = bisect_left(self.starts, start - self.longest)
        while i < len(self.items) and self.starts[i] < end:
            item = self.items[i]
            if _t(item["end"]) > start:
                yield item
            i += 1


def _actual(stays, place, start, end):
    """予定の時間に重なる `place` の滞在を、短い切れ目をつないで1続きにした実際の時刻。"""
    spans = sorted((_t(s["start"]), _t(s["end"])) for s in stays
                   if s.get("place") == place and _overlap(start, end, _t(s["start"]), _t(s["end"])))
    if not spans:
        return None, None
    # 予定の時間に一番長く重なる塊を選ぶ
    blocks, current = [], list(spans[0])
    for s_start, s_end in spans[1:]:
        if s_start - current[1] <= JOIN_GAP:
            current[1] = max(current[1], s_end)
        else:
            blocks.append(current)
            current = [s_start, s_end]
    blocks.append(current)
    best = max(blocks, key=lambda b: _overlap(start, end, b[0], b[1]))
    return best[0], best[1]


def check(events, stays, homes, now=None):
    """終わった予定ごとに、その時間に一番長くいた場所と実際の時刻を返す。"""
    index = _Index(stays, longest=timedelta(days=7))
    rows = []
    for event in events:
        start, end = _t(event["start"]), _t(event["end"])
        if now and end > now:
            continue
        span = (end - start).total_seconds()
        recorded, by_place = 0.0, Counter()
        for stay in index.overlapping(start, end):
            seconds = _overlap(start, end, _t(stay["start"]), _t(stay["end"]))
            recorded += seconds
            if stay.get("place") not in (None, location.MOVING):
                by_place[stay["place"]] += seconds
        row = {"event": event, "coverage": recorded / span if span else 0.0}
        if row["coverage"] < MIN_COVERAGE:
            row["status"] = "記録なし"
        elif not by_place:
            row["status"] = "移動中"
        else:
            place = by_place.most_common(1)[0][0]
            row["place"] = place
            row["status"] = ("家" if place in homes
                             else "未登録の場所" if place == location.UNKNOWN else "外出")
            row["actual_start"], row["actual_end"] = _actual(stays, place, start, end)
        rows.append(row)
    return rows


def title_places(check_rows, min_match=MIN_PLACE_MATCH):
    """件名 → 場所。同じ件名で同じ登録済みの場所に `min_match` 回以上いたものだけ。"""
    seen = {}
    for row in check_rows:
        if row["status"] == "外出" and row["event"]["title"]:
            seen.setdefault(row["event"]["title"], Counter())[row["place"]] += 1
    result = {}
    for title, counts in seen.items():
        place, n = counts.most_common(1)[0]
        if n >= min_match and n > sum(counts.values()) / 2:
            result[title] = place
    return result


def label_visits(visit_list, events, min_repeat=MIN_REPEAT):
    """用事の無い滞在に、滞在の半分以上を覆う繰り返しの予定の件名を用事として付ける。

    決済で付いた用事は上書きしない。元の列は変えない。
    """
    counts = Counter(event["title"] for event in events if event["title"])
    index = _Index([event for event in events if counts[event["title"]] >= min_repeat])
    result = []
    for visit in visit_list:
        if visit.get("activity"):
            result.append(visit)
            continue
        start, end = _t(visit["start"]), _t(visit["end"])
        span = (end - start).total_seconds()
        best, title = 0.0, None
        for event in index.overlapping(start, end):
            seconds = _overlap(start, end, _t(event["start"]), _t(event["end"]))
            if seconds > best:
                best, title = seconds, event["title"]
        result.append({**visit, "activity": title} if span and best >= span / 2 else visit)
    return result


def spot_titles(stays, events):
    """未登録の滞在（約100m 単位）ごとに、重なった予定の件名と回数。"""
    index = _Index(events)
    result = {}
    for stay in stays:
        if stay.get("place") != location.UNKNOWN or "lat" not in stay or "lng" not in stay:
            continue
        start, end = _t(stay["start"]), _t(stay["end"])
        titles = {event["title"] for event in index.overlapping(start, end) if event["title"]}
        if titles:
            key = (round(stay["lat"], 3), round(stay["lng"], 3))
            result.setdefault(key, Counter()).update(titles)
    return result


def _words(title):
    return {word for word in re.split(r"[\s、,・/]+", title or "") if len(word) >= 2}


def place_for(title, check_rows):
    """件名から、その件名（か同じ語を含む件名）の予定をこなした場所を選ぶ。

    件名は毎回同じとは限らない（「美容室」と「髪切る 〇〇」）。完全一致を先に見て、
    無ければ語の重なる件名でこなした回数が一番多い場所を取る。
    """
    exact, loose = Counter(), Counter()
    for row in check_rows:
        if row["status"] != "外出" or not row["event"]["title"]:
            continue
        past = row["event"]["title"]
        if past == title:
            exact[row["place"]] += 1
        elif _words(past) & _words(title):
            loose[row["place"]] += 1
    counts = exact or loose
    return counts.most_common(1)[0][0] if counts else None


def _median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2] if ordered else None


def _left_before(stays, place, arrived):
    """`arrived` の直前に、`place` 以外の場所を出た時刻。"""
    before = [_t(s["end"]) for s in stays
              if s.get("place") not in (None, location.MOVING, location.UNKNOWN, place)
              and _t(s["end"]) <= arrived]
    return max(before) if before else None


def forecast(event, check_rows, stays, min_n=MIN_PLACE_MATCH):
    """先の予定の場所と、過去にその場所へ行ったときの「出る・着く・終わる」の中央値（分）。

    分は予定の開始からの差（負 = 前）。使うのは、着いた時刻の前後 30 分に始まった予定
    （予約の時刻に合わせて行った予定）だけ。滞在の途中に重なっただけのリマインダ等は
    「何分後に終わるか」を狂わせる。そういう過去が `min_n` 回未満なら None。
    """
    place = place_for(event["title"], check_rows)
    if not place:
        return None
    past = [row for row in check_rows
            if row["status"] == "外出" and row["place"] == place and row.get("actual_start")
            and abs(row["actual_start"] - _t(row["event"]["start"])) <= MAX_BOOKING_GAP]
    if len(past) < min_n:
        return None
    arrive, done, leave = [], [], []
    for row in past:
        start = _t(row["event"]["start"])
        arrive.append((row["actual_start"] - start).total_seconds() / 60)
        done.append((row["actual_end"] - start).total_seconds() / 60)
        left = _left_before(stays, place, row["actual_start"])
        if left and row["actual_start"] - left <= MAX_LEAD:
            leave.append((left - start).total_seconds() / 60)
    return {"place": place, "n": len(past), "arrive": _median(arrive),
            "done": _median(done), "leave": _median(leave)}
