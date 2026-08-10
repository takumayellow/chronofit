"""生スパンを1日分の在席ブロックへ畳み、net / wall / 離席を分離する。

ここが「バッファの中にだらけ時間が混ざっている」問題を解く層になる。出すのは3つ:

- **wall（在席）** 前景に何かが出ていて、ロックもスリープもしていなかった時間
- **net（入力あり）** そのうち実際に手が動いていた時間
- **受動（passive）** 手は止まっているが、前景が音を鳴らしていた時間。動画・講義映像。
  net にも離席にも入れず、第3の状態として分けて持つ
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
MEDIA_SHARE = 0.5           # 無入力スパンのうち音が出ていた割合がこれ以上なら「観ていた」
MIN_PASSIVE_SEC = 300.0     # これ未満の再生は通知音等の可能性があるので数えない（5分）
LOCKED_PROC = "__locked__"
NO_WINDOW_PROC = "__none__"

# 離席ブロックの理由。ラベル UI の既定値を決めるのに使う。
REASON_LABELS = {
    "locked": "ロック",
    "sleep": "スリープ/休止",
    "no_data": "PC停止/未収集",
    "idle": "無入力（画面は出たまま）",
    "media": "再生中（手は止まっている）",
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
        # 時刻を欠いた行も捨てる。1行の欠損で1日分が読めなくなるほうが損。
        if record.get("t") in ("span", "gap") and record.get("start") and record.get("end"):
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

        if record["t"] == "span":
            # 記録された `start` は最初のサンプルを撮った時刻で、そのサンプルが代表するのは
            # **直前の1間隔**（デーモンは interval 秒眠ってから撮る）。したがって実際に
            # 覆っている範囲は `[end - sec, end]` になる。
            #
            # 補正しないと1スパンにつき interval 秒（15秒 × 1日 200本 ≒ 50分）が
            # どこにも属さない隙間として落ちる。合計値は `sec` から出しているので狂わないが、
            # 時刻で描くタイムラインだけが櫛の歯になり、「測れていない」ように見える。
            length = record.get("sec", 0.0) or 0.0
            if length > (end - start).total_seconds():
                start = end - timedelta(seconds=length)

        if previous_end is not None:
            # 前のセグメントへ食い込ませない。重ねて描くと、同じ時刻に2つの状態が
            # 立っている絵になり、どちらが本当か読めなくなる。
            start = max(start, min(previous_end, end))
            hole = (start - previous_end).total_seconds()
            if hole > hole_tolerance:
                segments.append({
                    "start": previous_end, "end": start, "sec": hole,
                    "kind": "away", "reason": "no_data", "active_sec": 0.0, "media_sec": 0.0,
                    "proc": "", "title": "",
                })

        if record["t"] == "gap":
            segments.append({
                "start": start, "end": end, "sec": record.get("sec", 0.0),
                "kind": "away", "reason": "sleep", "active_sec": 0.0, "media_sec": 0.0,
                "proc": "", "title": "",
            })
        else:
            proc = record.get("proc", "")
            active = record.get("active_sec", 0.0)
            media = record.get("media_sec", 0.0) or 0.0
            length = record.get("sec", 0.0)
            if proc == LOCKED_PROC:
                kind, reason = "away", "locked"
            elif active > 0.0:
                kind, reason = "present", None
            elif media > 0.0 and media >= length * MEDIA_SHARE:
                # 手は動いていないが前景が音を鳴らしていた。動画・講義映像を観ていた
                # 時間で、離席ではない。net（入力あり）でもないので別に持つ。
                kind, reason = "passive", "media"
            else:
                # 前景はあるが手が動いていない。席にいるのか離れたのかは
                # ここでは決められないので、ラベル対象へ回す。
                kind, reason = "away", "idle"
            segments.append({
                "start": start, "end": end, "sec": length,
                "kind": kind, "reason": reason, "active_sec": active,
                "media_sec": media,
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


def passive_blocks(segments, min_sec=MIN_PASSIVE_SEC):
    """連続する受動セグメント（再生中）を束ねる。

    離席ブロックと違ってラベルを聞かない。前景のタイトルが残っているので、
    何を観ていたかは既に分かっているため。人に聞くのは分からないことだけにする。
    """
    blocks = []
    run = []

    def flush():
        if run:
            total = sum(s["sec"] for s in run)
            if total >= min_sec:
                longest = max(run, key=lambda s: s["sec"])
                blocks.append({"start": run[0]["start"], "end": run[-1]["end"],
                               "sec": total, "proc": longest["proc"],
                               "title": longest["title"]})
        run.clear()

    for segment in segments:
        if segment["kind"] == "passive":
            run.append(segment)
        else:
            flush()
    flush()
    return blocks


def by_title(segments):
    """(プロセス, タイトル) ごとに net / passive / wall を集計する。

    受動を別列で持つのは、足し合わせると「入力ありの時間」の意味が壊れるから。
    講義映像は passive_sec のほうが作業時間に近く、娯楽の動画は net でも作業でもない。
    どちらなのかはタイトル規則（`title_rules`）が決める。
    """
    table = {}
    for segment in segments:
        if segment["kind"] not in ("present", "passive"):
            continue
        key = (segment["proc"], segment["title"])
        row = table.setdefault(key, {"proc": segment["proc"], "title": segment["title"],
                                     "wall_sec": 0.0, "net_sec": 0.0,
                                     "passive_sec": 0.0, "spans": 0})
        if segment["kind"] == "present":
            row["wall_sec"] += segment["sec"]
            row["net_sec"] += segment["active_sec"]
        else:
            row["passive_sec"] += segment["sec"]
        row["spans"] += 1
    return sorted(table.values(), key=lambda r: -(r["net_sec"] + r["passive_sec"]))


def summarize_day(records, min_away_sec=MIN_AWAY_SEC):
    """1日分の要約。これが所要時間DBと slack 率の入力になる。"""
    segments = to_segments(records)
    present = [s for s in segments if s["kind"] == "present"]
    wall = sum(s["sec"] for s in present)
    net = sum(s["active_sec"] for s in present)
    away = sum(s["sec"] for s in segments if s["kind"] == "away")
    passive = sum(s["sec"] for s in segments if s["kind"] == "passive")

    return {
        "wall_sec": wall,
        "net_sec": net,
        "away_sec": away,
        # 受動は wall にも away にも足さない。足すと slack 率（net/wall）が
        # 「動画を観ていた日はだらけていた」という別の意味に化ける。
        "passive_sec": passive,
        "passive_blocks": passive_blocks(segments),
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
        entry = labels.get(block["start"].isoformat(timespec="seconds"))
        block["label"] = entry.get("label") if isinstance(entry, dict) else entry
        block["detail"] = entry if isinstance(entry, dict) else None
    return summary


def format_summary(summary, date_label=""):
    """人が読む1日サマリ。数字より「未ラベルが何本あるか」が主役。"""
    lines = []
    wall, net = summary["wall_sec"], summary["net_sec"]
    ratio = summary["slack_ratio"]
    ratio_text = f"{ratio:.0%}" if ratio is not None else "データ無し"
    lines.append(f"{date_label}  在席 {wall / 3600:.1f}h  入力あり {net / 3600:.1f}h "
                 f"(slack率 {ratio_text})  受動 {summary.get('passive_sec', 0.0) / 3600:.1f}h"
                 f"  離席 {summary['away_sec'] / 3600:.1f}h")

    for block in summary.get("passive_blocks", []):
        lines.append(f"    {block['start']:%H:%M}-{block['end']:%H:%M} "
                     f"{block['sec'] / 60:5.0f}分  再生 {block['title'][:44]}")

    unlabeled = [b for b in summary["away_blocks"] if not b.get("label")]
    if summary["away_blocks"]:
        lines.append(f"  離席ブロック {len(summary['away_blocks'])}本"
                     f"（未ラベル {len(unlabeled)}本）")
        for block in summary["away_blocks"]:
            mark = block.get("label") or f"? {block['reason_label']}"
            lines.append(f"    {block['start']:%H:%M}-{block['end']:%H:%M} "
                         f"{block['sec'] / 60:5.0f}分  {mark}")

    for row in summary["titles"][:10]:
        passive = row.get("passive_sec", 0.0)
        mark = f"(+受動 {passive / 60:.0f}分)" if passive else ""
        lines.append(f"    {row['net_sec'] / 60:5.0f}分  {row['proc']:20.20} "
                     f"{row['title'][:52]}{mark}")
    return "\n".join(lines)
