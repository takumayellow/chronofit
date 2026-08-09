"""生スパンを1日分の在席ブロックへ畳み、net / wall / 離席を分離する。

ここが「バッファの中にだらけ時間が混ざっている」問題を解く層になる。出すのは3つ:

- **wall（在席）** 前景に何かが出ていて、ロックもスリープもしていなかった時間
- **net（入力あり）** そのうち実際に手が動いていた時間
- **離席ブロック** ロック・スリープ・無入力・データ欠落を1つに束ねたもの。
  L2 のラベル付け対象はここだけで、長さは既に秒単位で分かっている

`slack_ratio = net / wall` は日タイプごとの実測定数になる。見積もりに勘でバッファを
積む代わりに、`必要な暦時間 = est_net ÷ slack_ratio` で暦時間へ直す。

離席の判定に「無入力」を含めるのが要点。画面に PDF を出したまま席を離れた時間は
wall には乗るが net には乗らず、かつラベル付け対象として表に出てくる。
"""
import json
from datetime import datetime, timedelta

MIN_AWAY_SEC = 900.0        # これ以上の離席だけラベルを聞く（15分）
HOLE_TOLERANCE_SEC = 120.0  # これ以下の記録の隙間はサンプリングの揺らぎとみなす
LOCKED_PROC = "__locked__"
NO_WINDOW_PROC = "__none__"

# 離席ブロックの理由。ラベル UI の既定値を決めるのに使う。
REASON_LABELS = {
    "locked": "ロック",
    "sleep": "スリープ/休止",
    "no_data": "PC停止/未収集",
    "idle": "無入力（画面は出たまま）",
}


def _parse(moment):
    return datetime.fromisoformat(moment)


def read_day(path):
    """1日分の JSONL を読んで、時刻順のレコード列にする。壊れた行は黙って捨てる。"""
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("t") in ("span", "gap"):
            records.append(record)
    records.sort(key=lambda r: r["start"])
    return records


def to_segments(records, hole_tolerance=HOLE_TOLERANCE_SEC):
    """レコード列を、隙間を埋めた連続したセグメント列にする。

    記録の隙間（デーモン停止・シャットダウン）は黙って詰めず、`no_data` として
    明示的なセグメントにする。詰めてしまうと「PCが落ちていた時間」が消え、
    1日の合計が実時間と合わなくなる。
    """
    segments = []
    previous_end = None

    for record in records:
        start, end = _parse(record["start"]), _parse(record["end"])

        if previous_end is not None:
            hole = (start - previous_end).total_seconds()
            if hole > hole_tolerance:
                segments.append({
                    "start": previous_end, "end": start, "sec": hole,
                    "kind": "away", "reason": "no_data", "active_sec": 0.0,
                    "proc": "", "title": "",
                })

        if record["t"] == "gap":
            segments.append({
                "start": start, "end": end, "sec": record.get("sec", 0.0),
                "kind": "away", "reason": "sleep", "active_sec": 0.0,
                "proc": "", "title": "",
            })
        else:
            proc = record.get("proc", "")
            active = record.get("active_sec", 0.0)
            if proc == LOCKED_PROC:
                kind, reason = "away", "locked"
            elif active <= 0.0:
                # 前景はあるが手が動いていない。席にいるのか離れたのかは
                # ここでは決められないので、ラベル対象へ回す。
                kind, reason = "away", "idle"
            else:
                kind, reason = "present", None
            segments.append({
                "start": start, "end": end, "sec": record.get("sec", 0.0),
                "kind": kind, "reason": reason, "active_sec": active,
                "proc": proc, "title": record.get("title", ""),
            })

        previous_end = max(end, previous_end) if previous_end else end

    return segments


def away_blocks(segments, min_sec=MIN_AWAY_SEC):
    """連続する離席セグメントを束ね、長いものだけラベル対象として返す。

    束ねるのは、15分の離席が「ロック → スリープ → 復帰直後の無入力」のように
    複数セグメントへ割れることがあり、1つずつ聞くと質問数が跳ね上がるため。
    """
    blocks = []
    run = []

    def flush():
        if not run:
            return
        total = sum(s["sec"] for s in run)
        if total >= min_sec:
            # 理由は「一番長く占めたもの」を代表にする
            weights = {}
            for segment in run:
                weights[segment["reason"]] = weights.get(segment["reason"], 0.0) + segment["sec"]
            reason = max(weights, key=weights.get)
            blocks.append({
                "start": run[0]["start"], "end": run[-1]["end"],
                "sec": total, "reason": reason,
                "reason_label": REASON_LABELS.get(reason, reason),
            })
        run.clear()

    for segment in segments:
        if segment["kind"] == "away":
            run.append(segment)
        else:
            flush()
    flush()
    return blocks


def by_title(segments):
    """(プロセス, タイトル) ごとに net / wall を集計する。学習曲線のキーの元になる。"""
    table = {}
    for segment in segments:
        if segment["kind"] != "present":
            continue
        key = (segment["proc"], segment["title"])
        row = table.setdefault(key, {"proc": segment["proc"], "title": segment["title"],
                                     "wall_sec": 0.0, "net_sec": 0.0, "spans": 0})
        row["wall_sec"] += segment["sec"]
        row["net_sec"] += segment["active_sec"]
        row["spans"] += 1
    return sorted(table.values(), key=lambda r: -r["net_sec"])


def summarize_day(records, min_away_sec=MIN_AWAY_SEC):
    """1日分の要約。これが所要時間DBと slack 率の入力になる。"""
    segments = to_segments(records)
    present = [s for s in segments if s["kind"] == "present"]
    wall = sum(s["sec"] for s in present)
    net = sum(s["active_sec"] for s in present)
    away = sum(s["sec"] for s in segments if s["kind"] == "away")

    return {
        "wall_sec": wall,
        "net_sec": net,
        "away_sec": away,
        # 在席していたのに手が動いていなかった割合が slack。0除算は「データ無し」を意味する
        # ので 0.0 ではなく None にする（0.0 だと「一切集中していない」と読めてしまう）。
        "slack_ratio": (net / wall) if wall else None,
        "away_blocks": away_blocks(segments, min_away_sec),
        "titles": by_title(segments),
        "segments": segments,
    }


def merge_labels(summary, labels):
    """既存のラベル（開始時刻 -> ラベル）を離席ブロックへ差し込む。

    ラベルは別ファイルに持つ。ロールアップは何度でも作り直せるが、人が付けた
    ラベルは作り直せないため、同じファイルに混ぜない。
    """
    for block in summary["away_blocks"]:
        block["label"] = labels.get(block["start"].isoformat(timespec="seconds"))
    return summary


def format_summary(summary, date_label=""):
    """人が読む1日サマリ。数字より「未ラベルが何本あるか」が主役。"""
    lines = []
    wall, net = summary["wall_sec"], summary["net_sec"]
    ratio = summary["slack_ratio"]
    ratio_text = f"{ratio:.0%}" if ratio is not None else "データ無し"
    lines.append(f"{date_label}  在席 {wall / 3600:.1f}h  入力あり {net / 3600:.1f}h "
                 f"(slack率 {ratio_text})  離席 {summary['away_sec'] / 3600:.1f}h")

    unlabeled = [b for b in summary["away_blocks"] if not b.get("label")]
    if summary["away_blocks"]:
        lines.append(f"  離席ブロック {len(summary['away_blocks'])}本"
                     f"（未ラベル {len(unlabeled)}本）")
        for block in summary["away_blocks"]:
            mark = block.get("label") or f"? {block['reason_label']}"
            lines.append(f"    {block['start']:%H:%M}-{block['end']:%H:%M} "
                         f"{block['sec'] / 60:5.0f}分  {mark}")

    for row in summary["titles"][:10]:
        lines.append(f"    {row['net_sec'] / 60:5.0f}分  {row['proc']:20.20} {row['title'][:52]}")
    return "\n".join(lines)
