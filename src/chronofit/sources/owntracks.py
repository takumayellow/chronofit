"""スマホから届く位置を受け取り、滞在と移動の区間へ畳む（OwnTracks の HTTP モード）。

Google のタイムラインは端末内にしか無く、PC から読む口が無い。毎回スマホで書き出す
運用は人間を測定ループに戻すので採らない。代わりにスマホの常駐アプリ（OwnTracks）に
位置を押し出させ、Tailscale の内側だけで待ち受けるこの受け口で受け取る。

- PC が寝ていて届かなかった位置はアプリ側が溜めて再送するので、受け口は常駐で良い
- 受け取った JSON はそのまま日ごとの JSONL に足す（git の外。予測に使うため全部残す）
- 滞在への畳み込みは読むときに毎回やる。閾値を変えても過去分を作り直せる
"""
import base64
import hmac
import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import location

MAX_BODY_BYTES = 256 * 1024
STAY_RADIUS_M = 100.0       # この半径に収まる点の並びを1つの滞在とみなす
MIN_STAY_SEC = 5 * 60       # これより短い滞在は信号待ち等として移動に含める
MAX_ACCURACY_M = 200.0      # 精度がこれより悪い点は捨てる（屋内の Wi-Fi 測位の飛び）
MAX_TST = 4102444800        # 2100-01-01。これより先の時刻は壊れた値として捨てる
MAX_GAP_SEC = 60 * 60       # 点の空白がこれを超えたら移動が続いているとみなさない
MAX_STILL_GAP_SEC = 3 * 3600  # 同じ場所の点どうしなら、空白がこれ以内の間は居続けたとみなす
DEPARTURE_RADIUS_M = 500.0  # 空白明けの点がこの距離なら、出発の直前まで前の場所にいたとみなす
WALK_M_PER_SEC = 1.2        # 空白明けの点までの距離を歩いたぶん、出発を早める
REQUEST_TIMEOUT_SEC = 15    # 読み込みが止まった接続を切る。溜まるとスレッドが増え続ける


def points_dir(root):
    return root / "points"


def parse_payload(body):
    """受信した本文から位置の点を取り出す。位置以外（遷移・設定など）は捨てずに返す。

    返り値は (位置の点の列, その他のメッセージの列)。OwnTracks は1件ずつ送るが、
    溜めた分を配列で送る実装もあるので両方読む。
    """
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return [], []
    items = data if isinstance(data, list) else [data]
    points, others = [], []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("_type") == "location" and _valid_point(item):
            points.append(item)
        else:
            others.append(item)
    return points, others


def _valid_point(item):
    try:
        lat, lon, tst = float(item["lat"]), float(item["lon"]), int(item["tst"])
    except (KeyError, TypeError, ValueError):
        return False
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0 and 0 < tst < MAX_TST


def _moment(point):
    return datetime.fromtimestamp(int(point["tst"]), tz=timezone.utc).astimezone()


def append(points, root):
    """点を測位日ごとの JSONL に足す。返り値は書いた日付の列。"""
    directory = points_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    by_day = {}
    for point in points:
        by_day.setdefault(_moment(point).date().isoformat(), []).append(point)
    for day, items in by_day.items():
        with (directory / f"{day}.jsonl").open("a", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return sorted(by_day)


def load_points(day, root):
    """その日に測った点。同じ時刻の重複（再送）は1つにする。壊れた行は飛ばす。"""
    path = points_dir(root) / f"{day}.jsonl"
    if not path.is_file():
        return []
    unique = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and _valid_point(item):
            unique[int(item["tst"])] = item
    return [unique[key] for key in sorted(unique)]


def _longest_gap(times, start, end):
    """start〜end の間で、点が1つも無かった最長の秒数。"""
    inside = [start] + [t for t in times if start < t < end] + [end]
    return max((b - a).total_seconds() for a, b in zip(inside, inside[1:]))


def to_segments(points, radius_m=STAY_RADIUS_M, min_stay_sec=MIN_STAY_SEC,
                max_gap_sec=MAX_GAP_SEC, max_still_gap_sec=MAX_STILL_GAP_SEC):
    """点の列を (開始, 終了, 座標 or None) の区間へ。`location.to_stays` にそのまま渡せる。

    最初の点から半径内に収まる点が続く間を1つの滞在とし、その間を移動とする。
    スマホは止まっている間ほとんど送らない（実測で店に84分いた間は1点も来なかった）ので、
    同じ場所の2点の間の空白は `max_still_gap_sec` まで滞在に数える。空白明けの最初の点が
    少し離れた所（歩き出した直後）なら、その点を歩いて移動したぶんだけ手前まで滞在を延ばす。
    それより長い空白は電源断かもしれず、そこで区切ってどこにも数えない
    （朝の家→不明→夜の家 を丸ごと家にしない）。移動の間の空白は `max_gap_sec` で区切る。
    """
    usable = sorted((p for p in points if float(p.get("acc") or 0) <= MAX_ACCURACY_M),
                    key=lambda p: int(p["tst"]))
    clusters = []
    for point in usable:
        coords, moment = (float(point["lat"]), float(point["lon"])), _moment(point)
        current = clusters[-1] if clusters else None
        # 平均からの距離で比べると、ゆっくり歩いた経路全体が1つの滞在に化ける
        if (current and location.distance_m(coords, current["anchor"]) <= radius_m
                and (moment - current["end"]).total_seconds() <= max_still_gap_sec):
            current["members"].append(coords)
            current["end"] = moment
        else:
            if current:
                _extend_to_departure(current, coords, moment, max_still_gap_sec)
            clusters.append({"start": moment, "end": moment, "anchor": coords,
                             "members": [coords]})

    times = [_moment(point) for point in usable]
    stays = [c for c in clusters if (c["end"] - c["start"]).total_seconds() >= min_stay_sec]
    result = []
    for index, stay in enumerate(stays):
        if index:
            previous = stays[index - 1]
            if _longest_gap(times, previous["end"], stay["start"]) <= max_gap_sec:
                result.append((previous["end"], stay["start"], None))
        count = len(stay["members"])
        center = (sum(c[0] for c in stay["members"]) / count,
                  sum(c[1] for c in stay["members"]) / count)
        result.append((stay["start"], stay["end"], center))
    return [segment for segment in result if segment[1] > segment[0]]


def _extend_to_departure(cluster, coords, moment, max_still_gap_sec):
    """空白明けの点が近くなら、そこまで歩いた時間を引いた時刻まで滞在を延ばす。"""
    gap = (moment - cluster["end"]).total_seconds()
    distance = location.distance_m(coords, cluster["anchor"])
    if gap <= max_still_gap_sec and distance <= DEPARTURE_RADIUS_M:
        departed = moment - timedelta(seconds=distance / WALK_M_PER_SEC)
        cluster["end"] = max(cluster["end"], departed)


def all_points(root):
    """受け取った全期間の点。"""
    points = []
    for path in sorted(points_dir(root).glob("*.jsonl")):
        points += load_points(path.stem, root)
    return points


def stays_for(day, root, places=None):
    """その日に重なる滞在。日をまたぐ滞在を切らないよう前後1日の点も読む。"""
    current = datetime.fromisoformat(day).date()
    points = []
    for offset in (-1, 0, 1):
        points += load_points((current + timedelta(days=offset)).isoformat(), root)
    stays = location.to_stays(to_segments(points), places)
    return [stay for stay in stays
            if location._parse_time(stay["start"]).astimezone().date() <= current
            <= location._parse_time(stay["end"]).astimezone().date()]


def authorized(header, user, token):
    """Basic 認証の照合。時間差で推測されないよう定数時間で比べる。"""
    if not header or not header.startswith("Basic ") or not token:
        return False
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    given_user, _, given_token = decoded.partition(":")
    return (hmac.compare_digest(given_user.encode(), str(user).encode())
            & hmac.compare_digest(given_token.encode(), str(token).encode()))


def make_handler(root, user, token, log=None):
    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT_SEC

        def _reply(self, status, body=b"[]"):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not authorized(self.headers.get("Authorization"), user, token):
                self._reply(401, b"")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY_BYTES:
                self._reply(413, b"")
                return
            points, _ = parse_payload(self.rfile.read(length))
            if points:
                append(points, root)
            # OwnTracks は応答本文を「相手からのメッセージの配列」として読む。空配列で返す。
            self._reply(200)

        def do_GET(self):
            self._reply(405, b"")

        def log_message(self, fmt, *args):
            # 既定は stderr へ出すが、pythonw では stderr が無い。座標は書かない。
            if log:
                log("".join(c if c.isprintable() else "?" for c in fmt % args))

    return Handler


def serve(host, port, root, user, token, log=None):
    """受け口を立てて待ち受け続ける。全インターフェース（0.0.0.0）には出さない。"""
    if host in ("", "0.0.0.0", "::"):
        raise ValueError("待ち受けは Tailscale の IP など特定のアドレスに限る")
    server = ThreadingHTTPServer((host, port), make_handler(root, user, token, log))
    try:
        server.serve_forever()
    finally:
        server.server_close()
