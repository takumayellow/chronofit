"""離席ブロックのラベル。人間が触るのはここだけ。

過去3回の計測が死んだのは、記録の中に人間の操作が埋まっていたから（docs/DESIGN.md §0）。
ここでは操作をこの形にまで削る:

- 聞くのは **15分以上の離席ブロックだけ**。作業側のラベルは L0 から導出するので聞かない
- 聞くのは **1日1回まとめて**。開始時にも終了時にも何も要求しない
- 2週間も溜まれば **履歴の最頻値を既定値にする**。以後は例外だけ直せばよい

長さは L0 が既に秒単位で持っている。足りないのはラベルだけなので、常時録音や
カメラのような「もう一つの忘れうる仕組み」は要らない。
"""
import json
from datetime import datetime

SLOT_MINUTES = 30      # 既定値を引くときの時間帯の粒度
MIN_SAMPLES = 2        # これ以上同じ答えが出た時間帯だけ既定値にする


def label_of(entry):
    """記録からラベル文字列だけ取り出す。

    初期は `{開始時刻: "ラベル"}` の文字列だけだった。オフPC作業に科目と対象を
    持たせる必要が出たので辞書に広げてある。読む側は両方受ける。
    """
    if isinstance(entry, dict):
        return entry.get("label")
    return entry


def detail_of(entry):
    """オフPC作業の中身（科目・種別・対象）。持っていなければ None。"""
    if isinstance(entry, dict) and entry.get("subject"):
        return {k: entry.get(k) for k in ("subject", "kind", "target", "units")}
    return None


def make_entry(block, label, detail=None):
    """1ブロック分の記録を組み立てる。

    長さをここに焼くのは、後からオフPC作業の実測を引くときに、ロールアップを
    作り直さなくても済むようにするため。ラベルは作り直せないが長さは何度でも
    作り直せる — にもかかわらず一緒に持つのは、この2つが常に一緒に使われるから。
    """
    entry = {
        "label": label,
        "end": block["end"].isoformat(timespec="seconds"),
        "sec": round(block["sec"], 1),
    }
    for key, value in (detail or {}).items():
        if value:
            entry[key] = value
    return entry


def path_for(date, root):
    return root / f"{date}.json"


def load(date, root):
    """その日のラベル（開始時刻 ISO -> ラベル）。無ければ空。"""
    path = path_for(date, root)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save(date, root, labels):
    root.mkdir(parents=True, exist_ok=True)
    path_for(date, root).write_text(
        json.dumps(labels, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _slot(moment, weekend_days):
    """(平日か週末か, 30分刻みの時間帯) — 既定値を引くためのキー。

    平日と週末を混ぜない。平日 12:00 は昼食でも、週末 12:00 はそうとは限らない。
    """
    is_weekend = moment.weekday() in weekend_days
    return is_weekend, (moment.hour * 60 + moment.minute) // SLOT_MINUTES


def build_defaults(root, weekend_days=(5, 6), min_samples=MIN_SAMPLES):
    """過去のラベル全部から、時間帯ごとの最頻ラベルを作る。

    これが「タップ数が日に日に減る」仕組み。1〜2件しか実績が無い時間帯は既定値に
    しない — 一度きりの出来事を毎日提案されるほうが、聞かれるより煩わしい。
    """
    counts = {}
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for start, entry in entries.items():
            label = label_of(entry)
            if not label:
                continue
            try:
                moment = datetime.fromisoformat(start)
            except ValueError:
                continue
            slot = _slot(moment, weekend_days)
            counts.setdefault(slot, {})
            counts[slot][label] = counts[slot].get(label, 0) + 1

    defaults = {}
    for slot, tally in counts.items():
        label, count = max(tally.items(), key=lambda item: item[1])
        if count >= min_samples:
            defaults[slot] = label
    return defaults


def suggest(block, defaults, weekend_days=(5, 6)):
    """このブロックの既定ラベル。無ければ None。"""
    return defaults.get(_slot(block["start"], weekend_days))


def unlabeled(summary):
    return [b for b in summary["away_blocks"] if not b.get("label")]
