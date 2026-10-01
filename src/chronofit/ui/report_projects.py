"""やったこと: 大きな括り（開発・大学・仕事 …）→ プロジェクトごとに畳んだ一覧。

「開発」だけでは何をしたか分からず、PR や作業を平たく並べると何のための作業かが
読めない。そこで括り → プロジェクトの2段にし、プロジェクトの行には時間と
成果の数だけを出して、中身（PR・Issue・作業の内訳）は開いたときだけ見せる。

括りはリポジトリ名のパターンで決める（設定の `project_groups`）。当たらないものは
`default_project_group` に入れる。

プロジェクトに割り付かなかった PC の時間（連絡・視聴 …）は、規則が決めた括りの中に
同じ形で並べ、どれにも当たらなかった時間は「未分類」としてアプリ・タイトルのまま出す。
外出は先頭に、スマホは PC の後ろに置く（どちらも PC の割合の分母には入れない）。
"""
from datetime import datetime
from fnmatch import fnmatch

from .style import e, link

MIN_SHOWN_SEC = 60   # これ未満の作業は内訳に出さない（合計には入れる）
UNKNOWN = "(不明)"
OUTING_GROUP = "外出"
PHONE_GROUP = "スマホ"
UNCLASSIFIED_GROUP = "未分類"


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


def _parts(work):
    """`work` を (プロジェクト, 活動, 未分類) に分ける。古い形（プロジェクトの列）も読む。"""
    if isinstance(work, list):
        return work, [], []
    if isinstance(work, dict) and not work.get("error"):
        return (work.get("projects") or [], work.get("activities") or [],
                work.get("unclassified") or [])
    return [], [], []


def _item(name, sec, tasks=(), note=None):
    return {"repo": name, "sec": sec, "prs": [], "issues": [], "commits": 0,
            "tasks": list(tasks), "note": note}


def collect(done, work, project_groups=None, default="開発"):
    """成果と作業時間をプロジェクトごとに寄せ、括りごとに並べる。

    返り値は [{"name", "sec", "projects": [{"repo", "sec", "prs", "issues", "commits",
    "tasks"}]}]。時間の多い順。時間が無くても成果があるプロジェクトは残す。
    `work` が `activity.attribute` の形なら、プロジェクトでない活動も規則の括りへ入れる
    （未分類は入れない。`section` が別に出す）。
    """
    done = done if isinstance(done, dict) and not done.get("error") else {}
    work, activities, _ = _parts(work)
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

    placed = [(group_of(item["repo"], project_groups, default), item)
              for item in projects.values()]
    placed += [(row.get("group") or default,
                _item(row.get("label") or UNKNOWN, row.get("active_sec") or 0.0,
                      row.get("tasks") or []))
               for row in activities]
    groups = {}
    for name, item in placed:
        if item["sec"] < MIN_SHOWN_SEC and not (item["prs"] or item["issues"] or item["commits"]):
            continue
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
                    f"<span class='what'>{link(row.get('url'), row.get('title'))}</span>"
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
    if item.get("note"):
        body = f"<p class='sub'>{e(item['note'])}</p>" + body
    if not body:
        body = ("<p class='muted'>1分以上の作業も、閉じた PR・Issue も無い"
                "（コミットか細かい作業の積み重ね）。</p>")
    time = minutes(item["sec"]) if item["sec"] >= MIN_SHOWN_SEC else "―"
    return (f"<details class='proj'><summary><span class='name'>{e(item['repo'])}</span>"
            f"<span class='meter'><i style='width:{width:.1f}%'></i></span>"
            f"<span class='time'>{e(time)}</span>{_chips(item)}</summary>"
            f"<div class='body'>{body}</div></details>")


def _outing_note(row):
    span = f"{row['start']:%H:%M}–{row['end']:%H:%M}"
    what = row.get("activity") or "・".join(dict.fromkeys(row.get("events") or []))
    parts = [span, f"用事: {what}" if what else "用事の手がかり無し（予定・決済に無い）"]
    if row.get("phone_sec"):
        parts.append(f"そのあいだスマホ {minutes(row['phone_sec'])}")
    return "・".join(parts)


def _outing_group(outings):
    if not isinstance(outings, list) or not outings:
        return None
    items = []
    for row in outings:
        what = row.get("activity") or "・".join(dict.fromkeys(row.get("events") or []))
        name = f"{row['place']}（{what}）" if what else row["place"]
        items.append(_item(name, row["sec"], note=_outing_note(row)))
    return {"name": OUTING_GROUP, "sec": sum(item["sec"] for item in items), "projects": items}


def _phone_group(phone):
    if not isinstance(phone, dict) or not phone:
        return None
    items = [_item(name, sec, note="スマホを手で触っていた時間（自動プレイは除く）")
             for name, sec in sorted(phone.items(), key=lambda kv: -kv[1])
             if sec >= MIN_SHOWN_SEC]
    if not items:
        return None
    return {"name": PHONE_GROUP, "sec": sum(phone.values()), "projects": items}


def _unclassified_group(rows):
    items = [_item(row.get("label") or UNKNOWN, row.get("active_sec") or 0.0,
                   row.get("tasks") or [],
                   note="どの規則にも当たらなかった。activity_rules に足すと上の括りへ入る")
             for row in rows if (row.get("active_sec") or 0) >= MIN_SHOWN_SEC]
    if not items:
        return None
    return {"name": UNCLASSIFIED_GROUP,
            "sec": sum(row.get("active_sec") or 0 for row in rows), "projects": items}


def _share(work, groups, present_net_sec):
    """PC の入力時間のうち、プロジェクト・ほかの用途・未分類がそれぞれ何割か。"""
    projects, activities, unclassified = _parts(work)
    if isinstance(work, dict) and work.get("sec"):
        total = work["sec"]
        project = sum(row.get("active_sec") or 0 for row in projects)
        other = sum(row.get("active_sec") or 0 for row in activities)
        rest = sum(row.get("active_sec") or 0 for row in unclassified)
        return (f"PC で入力のあった {minutes(total)} のうち、プロジェクト {project / total:.0%}"
                f"・ほかの用途 {other / total:.0%}・未分類 {rest / total:.0%}。"
                "外出とスマホはこの割合の外に並べた。")
    total = sum(group["sec"] for group in groups)
    if not present_net_sec:
        return ""
    return (f"入力のあった時間のうち {total / present_net_sec:.0%}（{minutes(total)}）を"
            "プロジェクトに割り付けた。残りはアプリ・タイトル別を見る。")


def _top(groups):
    return max((item["sec"] for group in groups for item in group["projects"]), default=0)


def _group_html(group, top):
    return (f"<div class='group'><h3>{e(group['name'])}"
            f"<span class='t'>{e(minutes(group['sec']))}</span></h3>"
            + "".join(_project(item, top) for item in group["projects"]) + "</div>")


def section(done, work, present_net_sec=0.0, project_groups=None, default="開発",
            outings=None, phone=None):
    """やったことの節（見出しの下）。読めなかった源は理由を1行出す。

    `outings` は `activity.outings` の列、`phone` はスマホのカテゴリ → 秒。
    """
    notes = []
    for label, part in (("GitHub", done), ("作業の割り付け", work)):
        if part is None:
            notes.append(f"{label}: 読んでいない")
        elif isinstance(part, dict) and part.get("error"):
            notes.append(f"{label}: 読めなかった（{part['error']}）")
    if isinstance(outings, dict) and outings.get("error"):
        notes.append(f"外出: 読めなかった（{outings['error']}）")
    if isinstance(done, dict):
        notes += [f"GitHub の検索: {error}" for error in done.get("errors") or []]
    groups = collect(done, work, project_groups, default)
    unclassified = _unclassified_group(_parts(work)[2])
    pc = groups + ([unclassified] if unclassified else [])
    others = [group for group in (_outing_group(outings), _phone_group(phone)) if group]
    warn = "".join(f"<p class='sub warn'>{e(note)}</p>" for note in notes)
    if not (pc or others):
        return warn + "<p class='muted'>プロジェクトに割り付いた作業も、GitHub に残った成果も無い。</p>"
    # 外出とスマホは PC と物差しが違うので、それぞれの括りの中で一番長いものを幅いっぱいにする
    scaled = [(group, _top(pc)) for group in pc] + [(group, _top([group])) for group in others]
    order = {OUTING_GROUP: 0, PHONE_GROUP: 2, UNCLASSIFIED_GROUP: 3}
    scaled.sort(key=lambda pair: order.get(pair[0]["name"], 1))
    share = _share(work, groups, present_net_sec)
    parts = [f"<p class='sub'>{e(share)}</p>" if share else ""]
    parts += [_group_html(group, top) for group, top in scaled]
    return warn + f"<div class='panel'>{''.join(parts)}</div>"
