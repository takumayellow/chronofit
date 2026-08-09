"""オフPC作業（紙の本・紙の過去問・手書き）の実測を、ラベルから拾う。

「パソコンを触っていない時間はどう測るのか」への答えがここに閉じている。
常時録音でもカメラでもなく、**長さと中身を別々の経路で取る**:

- **長さ** は L0 が既に秒単位で持っている（入力が無い＝離席ブロック）。
- **中身** は両隣の前景タイトルからほぼ自動で当たる（`model/context.py`）。
  当たらなかったぶんだけ、1日1回のラベル付けで科目と対象を足す。

開始を宣言する操作は最後まで一度も要らない。人間が言うのは「終わった」だけで、
それも `done` の1回に集約される。

オフPC時間に net と wall の区別は置かない。本を読んでいるあいだ手は止まらないので、
在席時間がそのまま net である。逆に「読みながら寝ていた」ぶんは測れないが、それは
カメラを付けても解決しない種類の誤差なので、測らないと決めている。
"""
import json


def _entries(label_root, since=None, until=None):
    """ラベルファイルを日付順に走査して (日付, 開始時刻, 記録) を返す。"""
    if not label_root.is_dir():
        return
    for path in sorted(label_root.glob("*.json")):
        date = path.stem
        if since and date < since:
            continue
        if until and date > until:
            continue
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for start, entry in sorted(stored.items()):
            if isinstance(entry, dict):
                yield date, start, entry


def collect(label_root, subject, kind=None, target=None, since=None, until=None):
    """条件に一致するオフPCブロックを日ごとに合計する。

    `target` を省くとその (科目, 種別) の全ブロックを拾う。章や年度をまたいで
    合算してしまわないよう、所要時間DBへ入れるときは target を指定する。
    `kind` も省ける。習慣のように種別を分ける意味が無いものは科目だけで拾う。
    """
    days = {}
    for date, start, entry in _entries(label_root, since, until):
        if entry.get("subject") != subject:
            continue
        if kind is not None and entry.get("kind") != kind:
            continue
        if target is not None and entry.get("target") != target:
            continue
        day = days.setdefault(date, {"date": date, "sec": 0.0, "blocks": 0,
                                     "units": 0, "labels": []})
        day["sec"] += entry.get("sec", 0.0)
        day["blocks"] += 1
        day["units"] += entry.get("units") or 0
        day["labels"].append(f"{start[11:16]} {entry.get('label', '')}")
    return [days[key] for key in sorted(days)]


def totals(days):
    """日ごとの集計を1インスタンス分へ畳む。"""
    if not days:
        return None
    seconds = sum(day["sec"] for day in days)
    units = sum(day["units"] for day in days)
    return {
        "net_hours": round(seconds / 3600.0, 2),
        "wall_hours": round(seconds / 3600.0, 2),
        "sessions": len(days),
        "units": units or None,
        "first": days[0]["date"],
        "last": days[-1]["date"],
        "blocks": sum(day["blocks"] for day in days),
    }
