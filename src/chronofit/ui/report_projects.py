"""やったこと: 大きな括り（開発・大学・仕事 …）→ プロジェクトごとに畳んだ一覧。

「開発」だけでは何をしたか分からず、PR や作業を平たく並べると何のための作業かが
読めない。そこで括り → プロジェクトの2段にし、プロジェクトの行には時間と
成果の数だけを出して、中身（PR・Issue・作業の内訳）は開いたときだけ見せる。

括りはリポジトリ名のパターンで決める（設定の `project_groups`）。当たらないものは
`default_project_group` に入れる。
"""
from datetime import datetime
from fnmatch import fnmatch

from .style import e

MIN_SHOWN_SEC = 60   # これ未満の作業は内訳に出さない（合計には入れる）
UNKNOWN = "(不明)"


def minutes(seconds):
    total = round((seconds or 0) / 60)
    return f"{total // 60}時間{total % 60:02d}分" if total >= 60 else f"{total}分"


def _clock(moment):
    if not moment:
        return ""
    try:
        return datetime.fromisoformat(str(moment).replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    except ValueError:
        return ""


def group_of(repo, project_groups, default):
    for name, patterns in (project_groups or {}).items():
        if any(fnmatch(repo.lower(), str(pattern).lower()) for pattern in patterns or []):
            return name
    return default


def collect(done, work, project_groups=None, default="開発"):
    """成果と作業時間をプロジェクトごとに寄せ、括りごとに並べる。

    返り値は [{"name", "sec", "projects": [{"repo", "sec", "prs", "issues", "commits",
    "tasks"}]}]。時間の多い順。時間が無くても成果があるプロジェクトは残す。
    """
    done = done if isinstance(done, dict) and not done.get("error") else {}
    work = work if isinstance(work, list) else []
    projects = {}

    def project(repo):
        repo = repo or UNKNOWN
        return projects.setdefault(repo, {"repo": repo, "sec": 0.0, "prs": [], "issues": [],
                                          "commits": 0, "tasks": []})

    for group in work:
        item = project(group.get("repo"))
        item["sec"] += group.get("active_sec") or 0.0
        item["tasks"] += group.get("tasks") or []
    for row in done.get("prs") or []:
        project(row.get("repo"))["prs"].append(row)
    for row in done.get("issues") or []:
        project(row.get("repo"))["issues"].append(row)
    for repo, count in (done.get("commits") or {}).items():
        project(repo)["commits"] += count

    groups = {}
    for item in projects.values():
        if item["sec"] < MIN_SHOWN_SEC and not (item["prs"] or item["issues"] or item["commits"]):
            continue
        name = group_of(item["repo"], project_groups, default)
        bucket = groups.setdefault(name, {"name": name, "sec": 0.0, "projects": []})
        bucket["sec"] += item["sec"]
        bucket["projects"].append(item)

    def weight(item):
        return (-item["sec"], -(len(item["prs"]) + len(item["issues"])), -item["commits"])

    for bucket in groups.values():
        bucket["projects"].sort(key=weight)
    return sorted(groups.values(), key=lambda bucket: (-bucket["sec"], bucket["name"]))


def _chips(item):
    chips = []
    if item["prs"]:
        chips.append(f"<span class='chip pr'>PR {len(item['prs'])}</span>")
    if item["issues"]:
        chips.append(f"<span class='chip'>Issue {len(item['issues'])}</span>")
    if item["commits"]:
        chips.append(f"<span class='chip'>コミット {item['commits']}</span>")
    return f"<span class='chips'>{''.join(chips)}</span>"


def _done_list(title, rows):
    if not rows:
        return ""
    items = "".join(f"<li><span class='no'>#{e(row.get('number'))}</span>"
                    f"<span class='what'>{e(row.get('title'))}</span>"
                    f"<span class='when'>{e(_clock(row.get('closed')))}</span></li>"
                    for row in rows)
    return f"<div><h4>{title}</h4><ul>{items}</ul></div>"


def _task_list(tasks):
    shown = sorted((task for task in tasks if (task.get("active_sec") or 0) >= MIN_SHOWN_SEC),
                   key=lambda task: -task["active_sec"])
    if not shown:
        return ""
    items = "".join(f"<li><span class='what'>{e(task.get('task'))}</span>"
                    f"<span class='when'>{e(minutes(task['active_sec']))}"
                    f"（{e(_clock(task.get('first')))}–{e(_clock(task.get('last')))}）</span></li>"
                    for task in shown)
    return f"<div><h4>作業（入力のあった時間）</h4><ul>{items}</ul></div>"


def _project(item, top_sec):
    width = 0 if not top_sec else max(item["sec"] / top_sec * 100, 1 if item["sec"] else 0)
    body = (_done_list("マージした PR", item["prs"]) + _done_list("閉じた Issue", item["issues"])
            + _task_list(item["tasks"]))
    if not body:
        body = ("<p class='muted'>1分以上の作業も、閉じた PR・Issue も無い"
                "（コミットか細かい作業の積み重ね）。</p>")
    time = minutes(item["sec"]) if item["sec"] >= MIN_SHOWN_SEC else "―"
    return (f"<details class='proj'><summary><span class='name'>{e(item['repo'])}</span>"
            f"<span class='meter'><i style='width:{width:.1f}%'></i></span>"
            f"<span class='time'>{e(time)}</span>{_chips(item)}</summary>"
            f"<div class='body'>{body}</div></details>")


def section(done, work, present_net_sec=0.0, project_groups=None, default="開発"):
    """やったことの節（見出しの下）。読めなかった源は理由を1行出す。"""
    notes = []
    for label, part in (("GitHub", done), ("作業の割り付け", work)):
        if part is None:
            notes.append(f"{label}: 読んでいない")
        elif isinstance(part, dict) and part.get("error"):
            notes.append(f"{label}: 読めなかった（{part['error']}）")
    if isinstance(done, dict):
        notes += [f"GitHub の検索: {error}" for error in done.get("errors") or []]
    groups = collect(done, work, project_groups, default)
    warn = "".join(f"<p class='sub warn'>{e(note)}</p>" for note in notes)
    if not groups:
        return warn + "<p class='muted'>プロジェクトに割り付いた作業も、GitHub に残った成果も無い。</p>"
    total = sum(group["sec"] for group in groups)
    top = max((item["sec"] for group in groups for item in group["projects"]), default=0)
    share = (f"入力のあった時間のうち {total / present_net_sec:.0%}（{minutes(total)}）を"
             "プロジェクトに割り付けた。残りはアプリ・タイトル別を見る。"
             if present_net_sec else "")
    parts = [f"<p class='sub'>{e(share)}</p>" if share else ""]
    for group in groups:
        parts.append(f"<div class='group'><h3>{e(group['name'])}"
                     f"<span class='t'>{e(minutes(group['sec']))}</span></h3>"
                     + "".join(_project(item, top) for item in group["projects"]) + "</div>")
    return warn + f"<div class='panel'>{''.join(parts)}</div>"
