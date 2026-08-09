"""学習曲線。`(科目, 種別, 何本目)` から所要時間を出す。

なぜ「何本目か」を軸にするのか: 過去問は1本目に6時間かかっても5本目は1.5時間で終わる。
軸を持たずに平均を取ると、初回の重さと後半の軽さが混ざって、どちらの予定にも使えない
数字になる。

モデルは古典的な学習曲線 `t(n) = a * n^-b` の対数線形回帰。パラメータを2つに絞るのは、
実績が3〜5点しか無い段階で3パラメータを推定すると、外挿が暴れるため。

ただし素の冪則は n を増やすと 0 に漸近してしまい、「何本やっても最低これだけはかかる」
という現実に反する。実測の最小値を下限として噛ませる。
"""
import math

MIN_B, MAX_B = 0.0, 0.8   # 逓減の強さ。負（やるほど遅くなる）と極端な減衰を弾く
DEFAULT_B = 0.35          # 実績1点しか無いときに使う標準的な逓減
FLOOR_RATIO = 0.8         # 下限は実測最小値のこの倍まで

BASIS_MEASURED = "実測"
BASIS_BORROWED = "流用"
BASIS_ASSUMED = "仮"


def fit(points):
    """[(何本目, net時間), ...] から曲線を当てる。

    点が1つなら逓減は既定値を使う（傾きは1点からは決まらない）。
    """
    usable = [(n, hours) for n, hours in points if n >= 1 and hours > 0]
    if not usable:
        return None

    if len(usable) == 1:
        n, hours = usable[0]
        return {"a": hours * (n ** DEFAULT_B), "b": DEFAULT_B,
                "floor": hours * FLOOR_RATIO, "samples": 1}

    xs = [math.log(n) for n, _ in usable]
    ys = [math.log(hours) for _, hours in usable]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance == 0:
        # 全部同じ本数（例: 1本目が複数科目分）。逓減は測れないので平均だけ使う。
        average = sum(h for _, h in usable) / len(usable)
        return {"a": average, "b": 0.0, "floor": average * FLOOR_RATIO,
                "samples": len(usable)}

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance
    b = min(MAX_B, max(MIN_B, -slope))
    a = math.exp(mean_y + b * mean_x)
    return {"a": a, "b": b, "floor": min(h for _, h in usable) * FLOOR_RATIO,
            "samples": len(usable)}


def predict(model, n):
    """n 本目の net 時間。下限で頭打ちにする。"""
    if model is None or n < 1:
        return None
    return max(model["floor"], model["a"] * (n ** -model["b"]))


def _points(instances, subject=None, kind=None):
    """曲線に使えるのは series の行だけ。

    一点物や習慣の行を混ぜると、通し番号が単なる連番でしかないのに逓減として
    読まれてしまう。モードは行に焼いてあるので、ここで弾く。
    """
    return [(row["index"], row["net_hours"]) for row in instances
            if row.get("net_hours", 0) > 0
            and row.get("mode", "series") == "series"
            and (subject is None or row.get("subject") == subject)
            and (kind is None or row.get("kind") == kind)]


def estimate(instances, subject, kind, n, assumed_hours=None):
    """3段のフォールバックで見積もる。どの段で出したかを必ず添える。

    数字だけ返すと、実測から出たのか勘で置いたのかが後から区別できなくなる。
    区別できないものは検証もできないので、根拠を値と同じ重さで持ち回る。
    """
    model = fit(_points(instances, subject, kind))
    if model:
        return {"hours": predict(model, n), "basis": BASIS_MEASURED,
                "samples": model["samples"], "b": round(model["b"], 2),
                "note": f"{subject} {kind} の実測 {model['samples']}件から"}

    # 同じ種別を他科目から借りる。科目が違っても「過去問の逓減の形」は似る。
    model = fit(_points(instances, None, kind))
    if model:
        return {"hours": predict(model, n), "basis": BASIS_BORROWED,
                "samples": model["samples"], "b": round(model["b"], 2),
                "note": f"{kind} の他科目 {model['samples']}件から流用（{subject} の実測なし）"}

    if assumed_hours is None:
        return {"hours": None, "basis": BASIS_ASSUMED, "samples": 0, "b": None,
                "note": f"{subject} {kind} は実績も流用元も無い。実測が要る"}
    return {"hours": assumed_hours, "basis": BASIS_ASSUMED, "samples": 0, "b": None,
            "note": f"仮値。{subject} {kind} の実測なし。最優先で測る対象"}


def next_index(instances, subject, kind):
    """その (科目, 種別) で次は何本目か。"""
    done = [row["index"] for row in instances
            if row.get("subject") == subject and row.get("kind") == kind]
    return max(done) + 1 if done else 1
