"""Claude Code の会話ログから、人が入力した時刻と、1往復が終わった時刻を読む。

`claude_sessions` と同じく、発言の中身は読まない。読むのは記録の種類・時刻・作業ディレクトリ・
会話の題（`ai-title` / `custom-title`）だけ。人の入力かどうかは `origin.kind` で見分ける
（`task-notification` などの自動で差し込まれた入力は数えない）。古い会話ログには `origin`
が無いので、その場合は `promptSource` と `entrypoint` で見分ける。ただし `origin` の付いた
記録が1つでもある会話では、`origin` の無い記録（中断の印・シェル実行・要約の差し込み）を
人の入力に数えない。
"""
import json
from datetime import timedelta

from .claude_sessions import TITLE_TYPES, _time

PROMPT, END = "prompt", "end"


def is_human_prompt(record, legacy=True):
    """`legacy` が偽なら、`origin` の無い記録は人の入力とみなさない。"""
    if record.get("type") != "user" or record.get("isSidechain") or record.get("isMeta"):
        return False
    if record.get("isCompactSummary") or record.get("toolUseResult") is not None:
        return False
    content = (record.get("message") or {}).get("content")
    if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result"
                                         for block in content):
        return False
    origin = record.get("origin")
    if isinstance(origin, dict):
        return origin.get("kind") == "human"
    return legacy and (record.get("promptSource") in (None, "typed", "queued")
            and record.get("entrypoint") != "sdk-cli")


def _is_turn_end(record):
    return (record.get("type") == "system" and record.get("subtype") == "turn_duration"
            and not record.get("isSidechain"))


def _duration(record):
    value = record.get("durationMs")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return timedelta(milliseconds=value)
    return None


def read_turns(path):
    """1本の会話ログから `[(時刻, PROMPT|END, 作業ディレクトリ, 題, 所要), ...]`（時刻順）。

    題はその記録より前に付いた最新のもの。題が付く前の記録には、最初に付いた題を当てる。
    所要は往復の終わり（END）にだけ付く、その往復に掛かった時間（無ければ None）。
    """
    events, title, untitled, has_origin = [], None, [], False
    try:
        lines = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return events
    with lines:
        for line in lines:
            if '"cwd"' not in line and 'Title"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            key = TITLE_TYPES.get(record.get("type"))
            if key and isinstance(record.get(key), str) and record[key].strip():
                title = record[key].strip()
                continue
            if isinstance(record.get("origin"), dict):
                has_origin = True
            kind = PROMPT if is_human_prompt(record) else END if _is_turn_end(record) else None
            moment = _time(record.get("timestamp")) if kind else None
            if moment and isinstance(record.get("cwd"), str):
                duration = _duration(record) if kind == END else None
                legacy = kind == PROMPT and not isinstance(record.get("origin"), dict)
                events.append([moment, kind, record["cwd"], title, duration, legacy])
                if title is None:
                    untitled.append(events[-1])
    if has_origin:
        events = [event for event in events if not event[5]]
    first_title = next((event[3] for event in events if event[3]), None)
    for event in untitled:
        event[3] = first_title
    events.sort(key=lambda event: event[0])
    return [tuple(event[:5]) for event in events]
