"""離席ブロックが「何の続きだったか」を、前後の前景タイトルから推定する。

紙で過去問を解いているあいだ PC は無入力のままだが、人は必ずその前後で PC を触る。
問題 PDF を開く → 90分の離席 → 同じ科目の解答 PDF を開く、という並びになる。
つまり**離席の中身は、離席そのものではなく両隣を見れば当たる**。

ここが「オフPCの時間をどう測るか」の実質的な答えになる。長さは L0 が既に秒で
持っているので、足りないのはラベルだけで、そのラベルの大半は両隣から自動で埋まる。
人間に聞くのは、両隣にも手掛かりが無かったブロックだけになる。

出すのはあくまで**既定値**である。推定を実測として所要時間DBへ入れてはいけない。
"""
import re

MAX_GAP_SEC = 1800.0   # これ以上離れた前景は「その離席の文脈」とみなさない（30分）


def _match_rules(title, rules):
    """タイトル -> (科目, 種別)。規則は利用側の設定から来る。"""
    if not title:
        return None
    for rule in rules:
        pattern = rule.get("match")
        if not pattern:
            continue
        try:
            if re.search(pattern, title, re.IGNORECASE):
                return {"subject": rule.get("subject"), "kind": rule.get("kind")}
        except re.error:
            continue   # 設定側の正規表現が壊れていても推定を止めない
    return None


def neighbors(segments, block):
    """ブロックの直前・直後で、タイトルを持つ在席セグメント。"""
    before = after = None
    for segment in segments:
        if segment.get("kind") != "present" or not segment.get("title"):
            continue
        if segment["end"] <= block["start"]:
            before = segment
        elif segment["start"] >= block["end"] and after is None:
            after = segment
    return before, after


def guess(block, segments, rules, max_gap_sec=MAX_GAP_SEC):
    """離席ブロックの中身の推定。当たらなければ None。

    直前を先に見るのは、席を立つ直前に開いていたものが、その離席で続けていた作業
    である場合がほとんどのため。直後（戻ってきて最初に開いたもの）はその次に強い。
    """
    before, after = neighbors(segments, block)
    candidates = (
        (before, "直前", lambda s: (block["start"] - s["end"]).total_seconds()),
        (after, "直後", lambda s: (s["start"] - block["end"]).total_seconds()),
    )
    for segment, side, distance_of in candidates:
        if segment is None or distance_of(segment) > max_gap_sec:
            continue
        hit = _match_rules(segment["title"], rules)
        if hit:
            return {**hit, "via": side, "title": segment["title"]}
    return None


def annotate(summary, rules, max_gap_sec=MAX_GAP_SEC):
    """離席ブロックそれぞれに推定を付ける。ラベル UI の既定値として使う。"""
    segments = summary.get("segments", [])
    for block in summary.get("away_blocks", []):
        block["guess"] = guess(block, segments, rules, max_gap_sec)
    return summary
