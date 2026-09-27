"""スマホの位置履歴を「どこにいたか」の滞在へ畳み、離席ブロックへ重ねる。

L0 は離席の**長さ**を秒で持っているが、その間どこにいたかは知らない。外出の予定が
崩れたとき、崩れたのが移動なのか待ち時間なのか食事なのかを後から辿れるよう、
位置履歴で離席ブロックを場所ごとに割る。**長さは L0、場所は位置履歴**と役割を分け、
位置は L0 を置き換えない（位置だけでは PC の前にいたかは分からない）。

位置履歴はウィンドウタイトルよりさらに機微なので、置き場所は git の外
（`paths.location_dir()`）に限る。その内側では予測に使えるよう削らずに持つ:

- 滞在は座標と、書き出しにあれば `placeId`・`semanticType` も残す。場所を後から
  登録し直しても、過去の滞在へ当て直せる（`rematch`）
- 移動区間は「移動」という区分と時刻、書き出しにあれば移動手段を残す
- 公開リポジトリへ出るのは場所の名前と秒だけ（ロールアップ）。座標は出さない
- 期限で座標を消したい場合は `location_retention_days` を設定する（既定は消さない）
"""
import json
import math
import os
from datetime import date as date_type, datetime, timedelta

EARTH_RADIUS_M = 6371000.0
DEFAULT_RADIUS_M = 100.0
COORD_DIGITS = 6            # 測位の精度より細かくは持たない
LOOKBACK_DAYS = 7           # これより前に始まった滞在は、当日の離席に重ねない
DOMINANT_SHARE = 0.8        # ブロックのこれ以上を1か所で過ごしたら、その場所のラベルを提案する
MOVING = "移動"
UNKNOWN = "未登録の場所"
PLACES_FILE = "places.json"
AUTO_PLACES_FILE = "auto_places.json"   # 繰り返し行った場所の自動登録（estimate.auto_places）


def _parse_time(text):
    """ISO 時刻を aware な datetime へ。Python 3.10 は末尾の Z を読めない。"""
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.astimezone()


def parse_latlng(value):
    """書き出しの座標表記を (緯度, 経度) へ。読めなければ None。

    Android の書き出しは `"35.7°, 139.8°"`、iOS は `"geo:35.7,139.8"` と表記が違う。
    """
    if isinstance(value, dict):
        value = value.get("latLng")
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("geo:"):
        text = text[4:]
    parts = [part.strip().rstrip("°").strip() for part in text.split(",")]
    if len(parts) != 2:
        return None
    try:
        lat, lng = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0):
        return None
    return lat, lng


def segments(data):
    """タイムライン書き出しから (開始, 終了, 座標 or None, 付帯情報) を取り出す。

    座標が None の区間は移動。軌跡だけの区間（`timelinePath`）は時刻が滞在・移動の
    区間と重なるので区間にはしない（生の書き出しは `raw/` に残る）。
    """
    items = data.get("semanticSegments") if isinstance(data, dict) else data
    result = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        start, end = _parse_time(item.get("startTime")), _parse_time(item.get("endTime"))
        if start is None or end is None or end <= start:
            continue
        if isinstance(item.get("visit"), dict):
            candidate = item["visit"].get("topCandidate") or {}
            coords = parse_latlng(candidate.get("placeLocation"))
            if coords is not None:
                meta = {key: candidate[key] for key in ("placeId", "semanticType")
                        if isinstance(candidate.get(key), str)}
                result.append((start, end, coords, meta))
        elif isinstance(item.get("activity"), dict):
            candidate = item["activity"].get("topCandidate") or {}
            mode = candidate.get("type")
            result.append((start, end, None, {"mode": mode} if isinstance(mode, str) else {}))
    return sorted(result, key=lambda segment: segment[0])


def distance_m(a, b):
    """2点間の距離（m）。数百m の判定には球面の近似で十分。"""
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def valid_places(places):
    """設定の場所のうち、名前と座標を持つものだけ。壊れた1件で全体を止めない。"""
    result = []
    for place in places or []:
        if not isinstance(place, dict) or not place.get("name"):
            continue
        try:
            lat, lng = float(place["lat"]), float(place["lng"])
            radius = float(place.get("radius_m") or DEFAULT_RADIUS_M)
        except (KeyError, TypeError, ValueError):
            continue
        result.append({**place, "lat": lat, "lng": lng, "radius_m": radius})
    return result


def _read_list(path):
    if not path.is_file():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [place for place in loaded if isinstance(place, dict)] if isinstance(loaded, list) else []


def load_places(root, configured=None, auto=True):
    """場所の登録。git の外の `places.json` と設定の `places`、自動登録を合わせる。

    座標は個人の生活圏そのものなので、設定ファイル（利用側のリポジトリから配置される
    ことがある）でなく位置履歴と同じ git の外に置くのを正とする。同じ名前があれば
    `places.json`、設定、自動登録（`auto_places.json`）の順に取る。
    """
    stored = _read_list(root / PLACES_FILE)
    names = {place.get("name") for place in stored}
    extra = [place for place in configured or []
             if isinstance(place, dict) and place.get("name") not in names]
    names |= {place.get("name") for place in extra}
    guessed = ([place for place in _read_list(root / AUTO_PLACES_FILE)
                if place.get("name") not in names] if auto else [])
    return valid_places(stored + extra + guessed)


def save_auto_places(root, places):
    """自動登録を `auto_places.json` へ丸ごと置き直す。変わらなければ書かない。"""
    root.mkdir(parents=True, exist_ok=True)
    path = root / AUTO_PLACES_FILE
    text = json.dumps(places, ensure_ascii=False, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == text:
        return path
    # レポートの定期更新と手のコマンドが同時に書いても壊れないよう、一時ファイルから置き換える
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)
    return path


def save_place(root, place):
    """場所を1件 `places.json` へ足す。同じ名前は置き換える。"""
    path = root / PLACES_FILE
    current = [p for p in load_places(root, auto=False) if p["name"] != place["name"]]
    root.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current + [place], ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def match(coords, places):
    """半径内で最も近い登録済みの場所の名前。どこにも入らなければ None。"""
    best = None
    for place in places:
        distance = distance_m(coords, (place["lat"], place["lng"]))
        if distance <= place["radius_m"] and (best is None or distance < best[0]):
            best = (distance, place["name"])
    return best[1] if best else None


def to_stays(raw_segments, places):
    """区間を保存用の滞在へ。区間は (開始, 終了, 座標 or None[, 付帯情報])。"""
    places = valid_places(places)
    stays = []
    for segment in raw_segments:
        start, end, coords = segment[:3]
        stay = {"start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds")}
        if coords is None:
            stay["place"] = MOVING
        else:
            stay["place"] = match(coords, places) or UNKNOWN
            stay["lat"] = round(coords[0], COORD_DIGITS)
            stay["lng"] = round(coords[1], COORD_DIGITS)
        if len(segment) > 3 and segment[3]:
            stay.update(segment[3])
        stays.append(stay)
    return stays


def rematch(stays, places):
    """座標を持つ滞在を、今の場所の登録へ当て直す。登録の追加・移動が過去にも効く。"""
    places = valid_places(places)
    result = []
    for stay in stays:
        if stay.get("place") != MOVING and "lat" in stay and "lng" in stay:
            stay = {**stay, "place": match((stay["lat"], stay["lng"]), places) or UNKNOWN}
        result.append(stay)
    return result


def day_of(stay):
    return _parse_time(stay["start"]).astimezone().date().isoformat()


def path_for(day, root):
    return root / f"{day}.json"


def load_day(day, root):
    """その日に始まった滞在の列。無い・壊れているなら空。"""
    path = path_for(day, root)
    if not path.is_file():
        return []
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return [stay for stay in stored if isinstance(stay, dict)
            and _parse_time(stay.get("start")) and _parse_time(stay.get("end"))]


def _save_day(day, root, stays):
    root.mkdir(parents=True, exist_ok=True)
    ordered = sorted(stays, key=lambda stay: stay["start"])
    path_for(day, root).write_text(
        json.dumps(ordered, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def store(stays, root):
    """滞在を開始日ごとのファイルへ足す。同じ開始時刻は新しい取り込みで上書きする。

    書き出しは毎回全期間を含むので、同じ区間を何度取り込んでも増えないようにする。
    返り値は書いた日付の列。
    """
    by_day = {}
    for stay in stays:
        by_day.setdefault(day_of(stay), []).append(stay)
    for day, incoming in by_day.items():
        merged = {stay["start"]: stay for stay in load_day(day, root)}
        merged.update({stay["start"]: stay for stay in incoming})
        _save_day(day, root, list(merged.values()))
    return sorted(by_day)


def purge(root, today, retention_days=None):
    """保持期間を過ぎた日の座標を消す。滞在の時刻と区分は残す。None なら何もしない。

    返り値は座標を消した滞在の数。
    """
    if retention_days is None or not root.is_dir():
        return 0
    cutoff = (today - timedelta(days=retention_days)).isoformat()
    removed = 0
    for path in sorted(root.glob("*.json")):
        try:
            date_type.fromisoformat(path.stem)
        except ValueError:
            continue            # 日付でない名前のファイルは chronofit のものではない
        if path.stem >= cutoff:
            continue
        stays = load_day(path.stem, root)
        stripped = [{key: value for key, value in stay.items() if key not in ("lat", "lng")}
                    for stay in stays]
        count = sum(1 for stay in stays if "lat" in stay or "lng" in stay)
        if count:
            _save_day(path.stem, root, stripped)
            removed += count
    return removed


def stays_around(day, root, places=None, lookback_days=LOOKBACK_DAYS):
    """その日の離席に重なりうる滞在。前の日から続く滞在（数日家にいた等）も拾う。"""
    current = date_type.fromisoformat(day)
    stays = []
    for back in range(lookback_days, -1, -1):
        earlier = (current - timedelta(days=back)).isoformat()
        stays += [stay for stay in load_day(earlier, root)
                  if back == 0 or _parse_time(stay["end"]).astimezone().date() >= current]
    return stays if places is None else rematch(stays, places)


def overlay(block, stays):
    """離席ブロックを場所ごとの秒に割る。位置履歴の無い時間はどこにも数えない。

    返り値は [{"place", "sec"}] を最初に現れた順に並べたもの。
    """
    totals = {}
    for stay in stays:
        start = max(block["start"], _parse_time(stay["start"]))
        end = min(block["end"], _parse_time(stay["end"]))
        seconds = (end - start).total_seconds()
        if seconds <= 0:
            continue
        place = stay.get("place") or UNKNOWN
        totals.setdefault(place, [start, 0.0])
        totals[place][1] += seconds
    ordered = sorted(totals.items(), key=lambda item: item[1][0])
    return [{"place": place, "sec": round(seconds, 1)} for place, (_, seconds) in ordered]


def annotate(summary, stays):
    """各離席ブロックに場所の内訳を付ける。位置履歴が無ければ何も付けない。"""
    for block in summary.get("away_blocks", []):
        parts = overlay(block, stays)
        if parts:
            block["places"] = parts
    return summary


def suggest(block, places):
    """ブロックの大半を1か所で過ごし、その場所に `away_label` があればそれを返す。"""
    parts = block.get("places") or []
    if not parts or block.get("sec", 0) <= 0:
        return None
    top = max(parts, key=lambda part: part["sec"])
    if top["sec"] < block["sec"] * DOMINANT_SHARE:
        return None
    for place in valid_places(places):
        if place["name"] == top["place"]:
            return place.get("away_label")
    return None


def stored_days(root, since=None):
    """取り込み済みの日付（YYYY-MM-DD）の列。"""
    days = []
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            date_type.fromisoformat(path.stem)
        except ValueError:
            continue
        if not since or path.stem >= since:
            days.append(path.stem)
    return days


def unknown_spots(stays):
    """未登録の滞在を約100m 単位で束ねて合計する。場所を登録するときの手掛かり。"""
    spots = {}
    for stay in stays:
        if stay.get("place") != UNKNOWN or "lat" not in stay or "lng" not in stay:
            continue
        seconds = (_parse_time(stay["end"]) - _parse_time(stay["start"])).total_seconds()
        day = day_of(stay)
        key = (round(stay["lat"], 3), round(stay["lng"], 3))
        spot = spots.setdefault(key, {"lat": key[0], "lng": key[1],
                                      "sec": 0.0, "visits": 0, "last": day})
        spot["sec"] += seconds
        spot["visits"] += 1
        spot["last"] = max(spot["last"], day)
    return sorted(spots.values(), key=lambda spot: -spot["sec"])


def format_parts(parts):
    """内訳を1行に。例: 「移動 12分 / 未登録の場所 40分」。"""
    return " / ".join(f"{part['place']} {part['sec'] / 60:.0f}分" for part in parts)


def merge_sources(primary, secondary):
    """2つの位置源の滞在を1列に。重なる時間は primary（タイムラインの書き出し）を取る。

    同じ外出を両方が持っていると、離席ブロックの内訳が二重に数えられる。secondary は
    primary と重なる部分だけ削り、覆われていない前後は残す。
    """
    spans = sorted((_parse_time(stay["start"]), _parse_time(stay["end"])) for stay in primary)
    extra = []
    for stay in secondary:
        pieces = [(_parse_time(stay["start"]), _parse_time(stay["end"]))]
        for cut_start, cut_end in spans:
            pieces = [part for start, end in pieces
                      for part in ((start, min(end, cut_start)), (max(start, cut_end), end))
                      if part[1] > part[0]]
        extra += [{**stay, "start": start.isoformat(timespec="seconds"),
                   "end": end.isoformat(timespec="seconds")} for start, end in pieces]
    return sorted(primary + extra, key=lambda stay: _parse_time(stay["start"]))
