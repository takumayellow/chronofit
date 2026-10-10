"""マージした PR ごとの作業時間を、会話ログの記録から自動で出す。

手で `done` を打たなくても実績が溜まるようにするための部品。考え方:

- 会話ログの記録（時刻・作業ディレクトリ）を、作業ディレクトリのリポジトリごとに並べる。
  隣り合う記録の間が `IDLE_CAP` 以内なら、その間は作業していたとみなす。
  それより空いた間は席を離れていたかもしれないので数えない
- PR の窓は「同じリポジトリで1つ前にマージした時刻」から「この PR のマージ時刻」まで。
  ただし作成より `MAX_LEAD` 以上前には遡らない
- 同じリポジトリで前の PR がマージされる前に作られた PR は、時間が混ざるので測らない
- 窓の中の作業が `MIN_HOURS` に満たないものは、会話ログの外で作った PR とみなして測らない

ブランチ名は会話ログではほとんど HEAD か main なので使わない。

測っているのは「そのリポジトリで Claude が動いていた時間」で、人の時間そのものではない。
2つのリポジトリで同時に動かしていれば両方に数える。各時点のリポジトリは
`model.repo_context` が作業ディレクトリと触ったパスから決める（消した worktree も含む）。
"""
import statistics
from datetime import timedelta

IDLE_CAP = timedelta(minutes=5)
MAX_LEAD = timedelta(days=14)
MIN_HOURS = 0.05
KIND = "PR"
SOURCE = "github-pr"
MODE = "oneoff"

MEASURED, PARALLEL, UNMEASURED = "measured", "parallel", "unmeasured"


def _merge(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def engaged(marks_per_session, repo_of, idle_cap=IDLE_CAP):
    """リポジトリ名 → 作業していた区間の列（重なりは畳む）。

    `marks_per_session` は会話ごとの `[(時刻, 作業ディレクトリ), ...]`（時刻順）。
    `repo_of(作業ディレクトリ)` は `(名前, git の中か)` を返す。git の外は数えない。
    """
    by_repo = {}
    for marks in marks_per_session:
        for (start, cwd), (end, _) in zip(marks, marks[1:]):
            if not timedelta(0) < end - start <= idle_cap:
                continue
            name, in_git = repo_of(cwd)
            if in_git:
                by_repo.setdefault(name, []).append((start, end))
    return {name: _merge(spans) for name, spans in by_repo.items()}


def _overlap_hours(intervals, start, end):
    seconds = sum(max(0.0, (min(b, end) - max(a, start)).total_seconds())
                  for a, b in intervals)
    return seconds / 3600


def measure(prs, engaged_by_repo, since, max_lead=MAX_LEAD, min_hours=MIN_HOURS):
    """PR ごとに `{repo, number, merged, hours, status}` を返す。

    `since` より前は会話ログが無いので、窓はそこから先だけを見る。
    """
    results, previous = [], {}
    for pr in sorted(prs, key=lambda row: row["merged"]):
        repo = pr["repo"]
        before = previous.get(repo)
        previous[repo] = pr["merged"]
        start = max(since, pr["created"] - max_lead, before or since)
        hours = _overlap_hours(engaged_by_repo.get(repo, ()), start, pr["merged"])
        if before is not None and pr["created"] < before:
            status = PARALLEL
        elif hours < min_hours:
            status = UNMEASURED
        else:
            status = MEASURED
        results.append({"repo": repo, "number": pr["number"], "merged": pr["merged"],
                        "hours": round(hours, 2), "status": status})
    return results


def _recorded(rows):
    return {(row["subject"], row.get("target")) for row in rows
            if row.get("kind") == KIND}


def new_rows(measured, existing, make):
    """まだ DB に無い、測れた PR の行。`make` は `estimate.db.make`。

    本数（index）はリポジトリごとの通し番号で、既存の行の続きから振る。
    """
    done = _recorded(existing)
    counts = {}
    for row in existing:
        if row.get("kind") == KIND:
            counts[row["subject"]] = max(counts.get(row["subject"], 0), row.get("index") or 0)
    rows = []
    for item in measured:
        target = f"#{item['number']}"
        if item["status"] != MEASURED or (item["repo"], target) in done:
            continue
        counts[item["repo"]] = counts.get(item["repo"], 0) + 1
        rows.append(make(item["repo"], KIND, target, counts[item["repo"]], item["hours"],
                         date=item["merged"].astimezone().date().isoformat(),
                         source=SOURCE, mode=MODE))
    return rows


def backtest(rows, min_group=3):
    """1件ずつ抜いて、残りの中央値で当てる（leave-one-out）。

    予測は同じリポジトリの残りが `min_group` 件以上あればその中央値、無ければ全体の中央値。
    比べるために、常に全体の中央値で当てた場合も出す。
    返り値は `{"n", "repo": 指標, "overall": 指標}`。指標は
    `{"mae": 平均絶対誤差 h, "median_ae": 絶対誤差の中央値 h, "within_2x": 2倍以内の割合}`。
    """
    samples = [(row["subject"], row["net_hours"]) for row in rows
               if row.get("kind") == KIND and row.get("net_hours") is not None]
    if len(samples) < 2:
        return {"n": len(samples), "repo": None, "overall": None}
    repo_guesses, overall_guesses = [], []
    for i, (subject, _) in enumerate(samples):
        rest = samples[:i] + samples[i + 1:]
        overall = statistics.median(hours for _, hours in rest)
        same = [hours for other, hours in rest if other == subject]
        repo_guesses.append(statistics.median(same) if len(same) >= min_group else overall)
        overall_guesses.append(overall)
    actual = [hours for _, hours in samples]
    return {"n": len(samples), "repo": _score(repo_guesses, actual),
            "overall": _score(overall_guesses, actual)}


def _score(guesses, actual):
    errors = [abs(g - a) for g, a in zip(guesses, actual)]
    within = sum(1 for g, a in zip(guesses, actual) if a / 2 <= g <= a * 2)
    return {"mae": round(statistics.fmean(errors), 2),
            "median_ae": round(statistics.median(errors), 2),
            "within_2x": round(within / len(actual), 2)}
