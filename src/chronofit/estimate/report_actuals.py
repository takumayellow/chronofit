"""連作のレポート（実験の各回など）1本ごとの作業時間を、PR とスパンから出す。

手で `done` を打たなくても「レポート1本に何時間かかったか」が溜まるようにする部品。
1つの連作（設定 `report_series` の1項目）は1つのリポジトリで書いている前提で、考え方:

- 連作のリポジトリの PR を、マージ順に時間の区切りにする。PR の窓は「1つ前にマージした時刻」
  から「この PR のマージ時刻」まで。最後のマージから今までは、まだ終わっていない回の窓
- 回は PR のタイトルから `unit_patterns` で決める（`theme1/02`・`テーマ1 第2回` など）。
  回の印が無い PR の窓は「共通」（解説サイトや雛形など、どの回とも言えない作業）
- `done_pattern` に当たる PR（「提出を記録」など）で、その回を完了とする。
  完了の後にその回の印が付いた PR は「提出後」の手直しで、1本を仕上げる時間には入れない
- 数える時間は2つ。PC の入力（スパン）と、そのリポジトリで Claude が動いていた区間
  （`pr_actuals.engaged`）。所要時間は両方の和集合（同時に動いていたぶんは1回だけ数える）
- Claude の区間は、本人が PC にいた間（どのアプリでも入力があったスパンに `PRESENCE_PAD`
  を足した区間）だけを数える。席を外している間に Claude が1人で進めたぶんは「Claude単独」
  として別に出し、所要時間には入れない（手を動かした時間で見積もるため）
- スパンは、割り付け（`activity.decisions`）がそのリポジトリにしたもの、または
  タイトルが `title_patterns`・回の印に当たるもの（提出 PDF・LETUS の科目ページなど）を数える。
  タイトルに回の印があれば、窓よりそちらを優先する
"""
import re
from datetime import timedelta

SOURCE = "report-series"
MODE = "oneoff"
WORK, AFTER = "work", "after"
COMMON = None   # 回の印が無い窓
# 最後の入力からこの秒数までは席にいたとみなす。収集側が既に 60 秒未満のアイドルを
# 入力中に含めているので、その先の、Claude の出力を読んでいる間のぶん
PRESENCE_PAD = timedelta(seconds=120)
REQUIRED = ("subject", "kind", "repo", "unit_patterns", "target", "done_pattern")


class SeriesError(ValueError):
    pass


def compile_series(entry):
    """設定の1項目を検査し、正規表現をコンパイルした写しを返す。"""
    if not isinstance(entry, dict):
        raise SeriesError("report_series の項目は辞書で書く")
    missing = [name for name in REQUIRED if not entry.get(name)]
    if missing:
        raise SeriesError(f"report_series に {', '.join(missing)} が無い")
    try:
        units = [re.compile(pattern) for pattern in entry["unit_patterns"]]
        titles = [re.compile(pattern, re.I) for pattern in
                  (entry.get("title_patterns") or []) + (entry.get("span_patterns") or [])]
        done = re.compile(entry["done_pattern"])
    except (re.error, TypeError) as error:
        raise SeriesError(f"report_series の正規表現が読めない: {error}") from error
    for pattern in units:
        try:
            entry["target"].format(**{name: "0" for name in pattern.groupindex})
        except (KeyError, IndexError) as error:
            raise SeriesError(f"target {entry['target']!r} の {error} が "
                              f"{pattern.pattern!r} の名前付きグループに無い") from error
    return {**entry, "_units": units, "_titles": titles, "_done": done}


def unit_of(text, series):
    """文字列から回（`target` の形）を決める。印が無ければ None。数字の先頭の 0 は落とす。"""
    for pattern in series["_units"]:
        found = pattern.search(text or "")
        if found:
            values = {name: (value.lstrip("0") or "0") if value and value.isdigit() else value
                      for name, value in found.groupdict().items()}
            return series["target"].format(**values)
    return None


def windows(prs, series, since, now):
    """連作の PR で時間を区切る。

    返り値は `(窓の列, 完了時刻)`。窓は `{start, end, unit, phase, number}`
    （最後の窓の number は None）、完了時刻は `{回: 完了 PR のマージ時刻}`。
    """
    own = sorted((pr for pr in prs if pr["repo"] == series["repo"]),
                 key=lambda pr: pr["merged"])
    tagged = [(pr, unit_of(pr.get("title"), series)) for pr in own]
    done = {}
    for pr, unit in tagged:
        if unit and unit not in done and series["_done"].search(pr.get("title") or ""):
            done[unit] = pr["merged"]
    result, start = [], since
    for pr, unit in tagged:
        if pr["merged"] > start:
            phase = AFTER if unit in done and pr["merged"] > done[unit] else WORK
            result.append({"start": start, "end": pr["merged"], "unit": unit,
                           "phase": phase, "number": pr["number"]})
        start = max(start, pr["merged"])
    if now > start:
        open_unit = next((unit for _, unit in reversed(tagged)
                          if unit and unit not in done), COMMON)
        result.append({"start": start, "end": now, "unit": open_unit, "phase": WORK,
                       "number": None})
    return _fill_between(result), done


def _fill_between(window_list):
    """印の無い窓が、同じ回・同じ段階の窓に前後を挟まれていれば、その回の作業とみなす。

    1本のレポートの途中で、印を付け忘れた小さな修正 PR を挟むことが多いため。
    挟まれていない印なしの窓（雛形・解説サイトなど）は共通のまま残す。
    """
    tagged = [index for index, window in enumerate(window_list) if window["unit"] is not COMMON]
    result = list(window_list)
    for before, after in zip(tagged, tagged[1:]):
        left, right = window_list[before], window_list[after]
        if after - before > 1 and (left["unit"], left["phase"]) == (right["unit"],
                                                                    right["phase"]):
            for index in range(before + 1, after):
                result[index] = {**window_list[index], "unit": left["unit"],
                                 "phase": left["phase"]}
    return result


def counts(span, project, series):
    """このスパンを連作の作業として数えるか。"""
    if project == series["repo"]:
        return True
    text = f"{span.get('proc') or ''} {span.get('title') or ''}"
    return any(pattern.search(text) for pattern in series["_titles"]) or \
        unit_of(span.get("title"), series) is not None


def _window_at(moment, spans_of_windows):
    for window in spans_of_windows:
        if window["start"] < moment <= window["end"]:
            return window
    return None


def _merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _hours(intervals):
    return sum((end - start).total_seconds() for start, end in intervals) / 3600


def _clip(intervals, start, end):
    return [(max(a, start), min(b, end)) for a, b in intervals if a < end and b > start]


def presence(inputs, pad=PRESENCE_PAD):
    """本人が PC にいた区間。`inputs` は `[(開始, 入力秒)]`（どのアプリでも）。"""
    return _merge([(start, start + timedelta(seconds=seconds) + pad)
                   for start, seconds in inputs if seconds > 0])


def _intersect(intervals, present):
    """どちらも畳んである区間の列どうしの共通部分。"""
    result, index = [], 0
    for start, end in intervals:
        while index < len(present) and present[index][1] <= start:
            index += 1
        probe = index
        while probe < len(present) and present[probe][0] < end:
            result.append((max(start, present[probe][0]), min(end, present[probe][1])))
            probe += 1
    return result


def tally(windows_and_done, spans, engaged, series, present=None):
    """回ごとの時間。

    `spans` は `[(開始, 入力秒, タイトル)]`（数えると決めたスパンだけ。時刻は aware）。
    `engaged` はそのリポジトリで Claude が動いていた区間の列。
    `present` は本人が PC にいた区間（`presence`）。None なら Claude の区間を全部数える。
    返り値は `{回: {"work": 時間, "after": 時間, "done", "first", "last", "days", "prs"}}`。
    時間は `{"pc", "claude", "alone", "net"}`（h）。claude は本人がいた間の Claude、
    alone は本人がいない間に Claude だけが動いていたぶん（net に入れない）。
    回の印の無いぶんは鍵 None（共通）。
    """
    window_list, done = windows_and_done
    pieces = {}   # (回, phase) → {"pc": 秒, "claude": [区間], "spans": [区間]}

    def bucket(unit, phase):
        return pieces.setdefault((unit, phase), {"pc": 0.0, "claude": [], "spans": []})

    for start, seconds, title in spans:
        unit = unit_of(title, series)
        if unit is not None:
            phase = AFTER if unit in done and start > done[unit] else WORK
        else:
            window = _window_at(start, window_list)
            if window is None:
                continue
            unit, phase = window["unit"], window["phase"]
        piece = bucket(unit, phase)
        piece["pc"] += seconds
        piece["spans"].append((start, start + timedelta(seconds=seconds)))
    for window in window_list:
        clipped = _clip(engaged, window["start"], window["end"])
        if clipped:
            bucket(window["unit"], window["phase"])["claude"].extend(clipped)

    result = {}
    for (unit, phase), piece in pieces.items():
        engaged_here = _merge(piece["claude"])
        claude = engaged_here if present is None else _intersect(engaged_here, present)
        both = _merge(claude + piece["spans"])
        entry = result.setdefault(unit, {"work": None, "after": None, "done": done.get(unit),
                                         "first": None, "last": None, "days": set(),
                                         "prs": []})
        entry[phase] = {"pc": round(piece["pc"] / 3600, 2), "claude": round(_hours(claude), 2),
                        "alone": round(_hours(engaged_here) - _hours(claude), 2),
                        "net": round(_hours(both), 2)}
        if both:
            entry["first"] = min(filter(None, (entry["first"], both[0][0])))
            entry["last"] = max(filter(None, (entry["last"], both[-1][1])))
            if phase == WORK:
                entry["days"] |= {start.astimezone().date() for start, _ in both}
    for window in window_list:
        if window["number"] is not None and window["unit"] in result:
            result[window["unit"]]["prs"].append(window["number"])
    return result


def new_rows(tallied, series, existing, make):
    """完了した回のうち、まだ DB に無いものの行。`make` は `estimate.db.make`。"""
    subject, kind = series["subject"], series["kind"]
    recorded = {row.get("target") for row in existing
                if row.get("subject") == subject and row.get("kind") == kind}
    index = max([row.get("index") or 0 for row in existing
                 if row.get("subject") == subject and row.get("kind") == kind] or [0])
    rows = []
    finished = [(unit, entry) for unit, entry in tallied.items()
                if unit is not COMMON and entry["done"] and entry["work"]]
    for unit, entry in sorted(finished, key=lambda item: item[1]["done"]):
        if unit in recorded:
            continue
        index += 1
        rows.append(make(subject, kind, unit, index, entry["work"]["net"],
                         sessions=max(1, len(entry["days"])),
                         date=entry["done"].astimezone().date().isoformat(),
                         source=SOURCE, mode=MODE))
    return rows


MIN_OPEN_HOURS = 0.05   # これ未満の回は、タイトルにたまたま印が出ただけとみなす


def open_units(tallied, min_hours=MIN_OPEN_HOURS):
    """まだ完了していない回と、そこまでに使った時間（h）。"""
    return {unit: entry["work"]["net"] for unit, entry in tallied.items()
            if unit is not COMMON and not entry["done"] and entry["work"]
            and entry["work"]["net"] >= min_hours}


MIN_REMAINING_HOURS = 0.5   # 見積もりを使い切っても、終わったと言えるまでは最低これだけ置く


def remaining_hours(estimate_hours, spent_hours):
    """作業中の1本の残り。見積もりから使ったぶんを引く（見積もり超えでも 0 にはしない）。"""
    return round(max(estimate_hours - spent_hours, MIN_REMAINING_HOURS), 2)
