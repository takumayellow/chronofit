"""タスクの現在地。「何本のうち何本が終わって、残りに何時間要るか」だけを持つ。

`fit.py` が答えるのは「入るか / 入らないか」で、これは**予定を立てる瞬間**の問い。
続けて必要になるのは「**いま、どこまで来ているか**」という別の問いで、こちらは
予定を立て直すたびに要る。両方を fit に押し込むと、予定の割り付けと進捗の記録が
同じ構造体を奪い合って、どちらも読めなくなる。

設計の勘所:

- **進捗は宣言ではなく、所要時間DBの実績から数える。** 「7割終わった」は測れないが、
  「5本のうち2本が DB に入っている」は測れる。人が別途チェックを付ける仕組みにすると、
  付け忘れた瞬間に嘘の board になる（過去3回の失敗と同じ形）。
- **目標本数（goal）と残り本数（left）を分けて持つ。** `fit.expand` は
  `count` 本を**これから**やるものとして展開するので、恒久的なタスク一覧に
  「全部で5本」と書いたまま渡すと、2本終わった時点で 7本分の計画になる。
  board が `left` を計算して fit に渡すのはこのため。
- **残り時間は1本ずつ見積もる。** 学習曲線があるので、残り3本は「1本の見積もり×3」に
  ならない。3本目・4本目・5本目をそれぞれ見積もって足す。
- **締切があるものは1日あたりの必要量まで落とす。** 「残り12時間」は判断に使えないが、
  「締切まで6日、1日2時間」は使える。
"""
from datetime import date as date_type

from ..estimate import curve, kinds

NOT_STARTED = "未着手"
IN_PROGRESS = "進行中"
DONE = "完了"


def goal_count(task):
    """やるつもりの本数。指定が無ければ1本。

    `count or 1` と書かない。0 を 1 に化かすと、`--count 0`（＝もうやらない）が
    黙って1本の需要に戻る。負と 0 はそのまま 0 として扱う。
    """
    count = task.get("count")
    if count is None:
        return 1
    return max(0, int(count))


def done_count(instances, subject, kind, target=None):
    """所要時間DBに入っている本数。ここが唯一の「終わった」の定義。

    `target` を指定したタスクは、その target の行だけを数える。指定していなければ
    (科目, 種別) 全体で数える。
    """
    return sum(1 for row in instances
               if row.get("subject") == subject
               and row.get("kind") == kind
               and (target is None or row.get("target") == target))


def spent_hours(instances, subject, kind, target=None):
    """そのタスクに実際にかかった時間の合計（net）。"""
    return sum(row.get("net_hours") or 0.0 for row in instances
               if row.get("subject") == subject
               and row.get("kind") == kind
               and (target is None or row.get("target") == target))


def _remaining_estimate(instances, subject, kind, left, settings, assumed=None):
    """残り `left` 本ぶんの合計見積もり。1本ずつ見積もって足す。

    見積もれない本が混ざったら、合計を出さずに **None** を返す。足せるものだけ
    足して「残り4時間」と言うと、見積もれない本を 0 時間として計画に混ぜることになる。
    """
    if left <= 0:
        return 0.0, None
    mode = kinds.mode_for(kind, settings)
    # 通し番号は target で絞らない。学習曲線が効くのは「その種別を何本こなしたか」で、
    # どの年度をやったかではない。2024年度を3本こなした後の2023年度は4本目として速い。
    # 一方で done / left は target で絞る（「その対象が終わったか」は対象ごとの話）。
    start = curve.next_index(instances, subject, kind)
    total = 0.0
    basis = None
    for offset in range(left):
        if mode == kinds.ONEOFF:
            estimate = kinds.estimate_oneoff(instances, subject, kind, assumed)
        else:
            estimate = curve.estimate(instances, subject, kind, start + offset, assumed)
        if estimate.get("hours") is None:
            return None, estimate.get("basis")
        total += estimate["hours"]
        basis = basis or estimate.get("basis")
    return total, basis


def _days_left(due, today):
    """締切までの日数。読めない日付は締切なしとして扱う。

    ここで例外を上げると、毎晩自動で走る `daily` が一覧の1行のせいで丸ごと落ち、
    **その日以降ずっと進捗が残らなくなる**。締切1つの書き間違いが計測の停止に
    化けるのは割に合わない。入口（`task add`）で弾き、読む側では落とさない。
    """
    if not due:
        return None
    try:
        return (date_type.fromisoformat(due) - today).days
    except (ValueError, TypeError):
        return None


def progress(task, instances, settings=None, today=None):
    """1タスクの現在地。"""
    today = today or date_type.today()
    subject, kind = task.get("subject"), task.get("kind")
    target = task.get("target")
    goal = goal_count(task)
    done = done_count(instances, subject, kind, target)
    left = max(0, goal - done)
    hours, basis = _remaining_estimate(instances, subject, kind, left, settings,
                                       task.get("assumed_hours"))
    days = _days_left(task.get("due"), today)
    per_day = None
    if hours is not None and days is not None and days > 0 and left:
        per_day = hours / days
    return {
        "subject": subject, "kind": kind, "target": target,
        "priority": task.get("priority"), "due": task.get("due"),
        "goal": goal, "done": done, "left": left,
        "spent_hours": round(spent_hours(instances, subject, kind, target), 2),
        "remaining_hours": None if hours is None else round(hours, 2),
        "basis": basis,
        "days_left": days,
        "hours_per_day": None if per_day is None else round(per_day, 2),
        "state": DONE if not left else (NOT_STARTED if not done else IN_PROGRESS),
        "overdue": bool(left and days is not None and days < 0),
    }


def rows(tasks, instances, settings=None, today=None):
    """全タスクの現在地。習慣は進捗を持たないので外す。"""
    result = []
    for task in tasks:
        if not task.get("subject") or not task.get("kind"):
            continue
        if kinds.mode_for(task["kind"], settings) == kinds.HABIT:
            continue
        result.append(progress(task, instances, settings, today))
    return result


def order(board_rows):
    """遅れているもの → 締切の早いもの → 優先度、の順。上から読めば手が付く順になる。"""
    rank = {"S": 0, "A": 1, "B": 2, "C": 3}
    return sorted(board_rows, key=lambda row: (
        row["state"] == DONE,
        not row["overdue"],
        row["due"] or "9999-99-99",
        rank.get(row["priority"], 9),
    ))


def summarize(board_rows):
    """全体の残り。見積もれない本があることを数字から隠さない。"""
    left = [row for row in board_rows if row["left"]]
    unknown = [row for row in left if row["remaining_hours"] is None]
    return {
        "tasks": len(board_rows),
        "done": sum(1 for row in board_rows if row["state"] == DONE),
        "in_progress": sum(1 for row in board_rows if row["state"] == IN_PROGRESS),
        "not_started": sum(1 for row in board_rows if row["state"] == NOT_STARTED),
        "overdue": sum(1 for row in board_rows if row["overdue"]),
        "units_left": sum(row["left"] for row in board_rows),
        "remaining_hours": round(sum(row["remaining_hours"] or 0.0 for row in left), 1),
        "unestimated": len(unknown),
        "spent_hours": round(sum(row["spent_hours"] for row in board_rows), 1),
    }


def remaining_tasks(tasks, instances, settings=None):
    """残っているぶんだけの task 定義。`fit.make` へはこれを渡す。

    `count` を残り本数に置き換える。恒久的な一覧をそのまま fit へ渡すと、
    終わったぶんまで**もう一度**計画に載る。
    """
    result = []
    for task in tasks:
        if not task.get("subject") or not task.get("kind"):
            continue
        if kinds.mode_for(task["kind"], settings) == kinds.HABIT:
            continue
        left = max(0, goal_count(task)
                   - done_count(instances, task["subject"], task["kind"],
                                task.get("target")))
        if left:
            result.append({**task, "count": left})
    return result


def format_rows(board_rows):
    """人が読む形。数字だけでなく、根拠が無いことも1行で分かるようにする。"""
    lines = []
    for row in order(board_rows):
        mark = "!" if row["overdue"] else (" " if row["state"] != DONE else "x")
        hours = ("  --  " if row["remaining_hours"] is None
                 else f"{row['remaining_hours']:5.1f}h")
        due = row["due"] or "  ―  "
        days = "" if row["days_left"] is None else f"({row['days_left']:+d}日)"
        pace = "" if not row["hours_per_day"] else f"  {row['hours_per_day']:.1f}h/日"
        target = f" {row['target']}" if row.get("target") else ""
        lines.append(
            f" {mark} {row['subject']:14.14} {row['kind']:6.6}{target:10.10} "
            f"{row['done']}/{row['goal']}  残り{hours}  {due}{days}{pace}")
    return "\n".join(lines)


def format_summary(summary):
    parts = [f"{summary['tasks']}件  完了{summary['done']} / "
             f"進行中{summary['in_progress']} / 未着手{summary['not_started']}",
             f"残り {summary['units_left']}本 {summary['remaining_hours']:.0f}h(net)",
             f"投入済み {summary['spent_hours']:.0f}h"]
    if summary["overdue"]:
        parts.append(f"**締切超過 {summary['overdue']}件**")
    if summary["unestimated"]:
        parts.append(f"見積もれない {summary['unestimated']}件（実測が無い）")
    return "  /  ".join(parts)
