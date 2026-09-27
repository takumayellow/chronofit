import json
from datetime import date, datetime, timedelta, timezone

from chronofit import cli_claude
from chronofit.model import claude_time
from chronofit.sources import claude_turns
from chronofit.sources.claude_turns import END, PROMPT

JST = timezone(timedelta(hours=9))
T0 = datetime(2026, 1, 5, 9, 0, tzinfo=JST)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def prompt(minutes, cwd="/alpha", title="題A"):
    return (at(minutes), PROMPT, cwd, title, None)


def end(minutes, took=None, cwd="/alpha", title="題A"):
    return (at(minutes), END, cwd, title, None if took is None else timedelta(minutes=took))


def stamp(minutes):
    return at(minutes).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def repo_of(cwd):
    return (cwd.strip("/"), True) if cwd.startswith("/") else (cwd, False)


# --- 人の入力の見分け --------------------------------------------------------

def test_human_prompt_by_origin():
    assert claude_turns.is_human_prompt({"type": "user", "origin": {"kind": "human"}})
    assert not claude_turns.is_human_prompt({"type": "user", "origin": {"kind": "task-notification"}})


def test_tool_results_and_meta_are_not_prompts():
    tool = {"type": "user", "message": {"content": [{"type": "tool_result"}]}}
    assert not claude_turns.is_human_prompt(tool)
    assert not claude_turns.is_human_prompt({"type": "user", "toolUseResult": {}})
    assert not claude_turns.is_human_prompt({"type": "user", "isMeta": True})
    assert not claude_turns.is_human_prompt({"type": "user", "isSidechain": True})
    assert not claude_turns.is_human_prompt({"type": "user", "isCompactSummary": True})


def test_old_logs_without_origin():
    assert claude_turns.is_human_prompt({"type": "user", "promptSource": "typed"})
    assert not claude_turns.is_human_prompt({"type": "user", "entrypoint": "sdk-cli"})


def test_read_turns_titles_and_durations(tmp_path):
    lines = [
        {"type": "user", "origin": {"kind": "human"}, "timestamp": stamp(0), "cwd": "/alpha"},
        {"type": "ai-title", "aiTitle": "最初の題"},
        {"type": "system", "subtype": "turn_duration", "durationMs": 120000,
         "timestamp": stamp(3), "cwd": "/alpha"},
        {"type": "custom-title", "customTitle": "付け直した題"},
        {"type": "user", "origin": {"kind": "human"}, "timestamp": stamp(5), "cwd": "/alpha"},
        {"type": "assistant", "timestamp": stamp(6), "cwd": "/alpha"},
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n", encoding="utf-8")
    events = claude_turns.read_turns(path)
    assert [(event[1], event[3]) for event in events] == [
        (PROMPT, "最初の題"), (END, "最初の題"), (PROMPT, "付け直した題")]
    assert events[1][4] == timedelta(minutes=2)
    assert events[0][4] is None


def test_read_turns_missing_file(tmp_path):
    assert claude_turns.read_turns(tmp_path / "none.jsonl") == []


# --- 振り分け ----------------------------------------------------------------

def test_classify_prefers_title_over_cwd():
    project_rules = [{"match": "home", "project": "置き場"}, {"match": "報告", "project": "仕事"}]
    assert claude_time.classify("/home", "月次の報告", project_rules, [], repo_of) == ("仕事", None)
    assert claude_time.classify("/home", "雑談", project_rules, [], repo_of) == ("置き場", None)


def test_classify_title_rules_give_subject_and_kind():
    title_rules = [{"match": "線形代数", "subject": "線形代数", "kind": "過去問"}]
    assert claude_time.classify("/alpha", "線形代数の復習", [], title_rules,
                                repo_of) == ("線形代数", "過去問")


def test_classify_falls_back_to_repo_then_unsorted():
    assert claude_time.classify("/alpha", "何か", [], [], repo_of) == ("alpha", None)
    assert claude_time.classify("scratch", None, [], [], repo_of) == (claude_time.UNSORTED, None)


def test_kind_rules_override_kind_from_title():
    kind_rules = [{"match": "レポート", "kind": "レポート"}]
    title_rules = [{"match": "物理", "subject": "物理", "kind": "講義資料"}]
    assert claude_time.classify("/phys", "物理 第3回レポート", [], title_rules, repo_of,
                                kind_rules) == ("物理", "レポート")
    assert claude_time.classify("/alpha", "第3回レポート", [], [], repo_of,
                                kind_rules) == ("alpha", "レポート")


def test_broken_regex_is_skipped():
    rules = [{"match": "(", "project": "壊れ"}, {"match": "報告", "project": "仕事"}]
    assert claude_time.classify("/a", "報告", rules, [], repo_of) == ("仕事", None)


# --- 待ちと対話 --------------------------------------------------------------

def test_pieces_wait_then_talk():
    parts = claude_time.pieces([prompt(0), end(4), prompt(7), end(9)])
    assert [(p[0], p[1], p[2]) for p in parts] == [
        (at(0), at(4), claude_time.WAIT), (at(4), at(7), claude_time.TALK),
        (at(7), at(9), claude_time.WAIT)]


def test_long_silence_is_not_talk():
    parts = claude_time.pieces([prompt(0), end(4), prompt(60)])
    assert [p[2] for p in parts] == [claude_time.WAIT]


def test_interrupted_prompt_waits_until_next_prompt_within_cap():
    parts = claude_time.pieces([prompt(0), prompt(5), end(8)])
    assert [(p[0], p[1]) for p in parts] == [(at(0), at(5)), (at(5), at(8))]
    parts = claude_time.pieces([prompt(0), prompt(50), end(52)])
    assert [(p[0], p[1]) for p in parts] == [(at(50), at(52))]


def test_wait_is_clipped_by_recorded_duration():
    # 入力のあと何日も放置して再開した往復は、所要時間ぶんだけ待ちに数える
    parts = claude_time.pieces([prompt(0), end(3000, took=5)])
    assert [(p[0], p[1]) for p in parts] == [(at(2995), at(3000))]


def test_end_without_prompt_is_ignored():
    assert claude_time.pieces([end(3), end(5)]) == []


# --- 並行と日付 --------------------------------------------------------------

def test_parallel_sessions_are_split_evenly():
    shares, union = claude_time.allocate([(at(0), at(60), "a"), (at(30), at(60), "b")])
    day = T0.date().isoformat()
    assert union[day] == 3600
    assert shares[day]["a"] == 1800 + 900
    assert shares[day]["b"] == 900


def test_split_at_local_midnight():
    late = datetime(2026, 1, 5, 23, 30).astimezone()
    _, union = claude_time.allocate([(late, late + timedelta(hours=1), "a")])
    assert union == {"2026-01-05": 1800, "2026-01-06": 1800}


def test_summarize_counts_prompts_and_parts():
    sessions = [[prompt(0), end(4), prompt(7), end(9)],
                [prompt(0, cwd="/beta", title="題B"), end(4, cwd="/beta", title="題B")]]
    days = claude_time.summarize(sessions, lambda cwd, title: (repo_of(cwd)[0], None))
    day = days[T0.astimezone().date().isoformat()]
    assert day["total_sec"] == 9 * 60
    alpha, beta = day["projects"]["alpha"], day["projects"]["beta"]
    assert alpha["prompts"] == 2 and beta["prompts"] == 1
    assert alpha["wait_sec"] == 2 * 60 + 2 * 60 and alpha["talk_sec"] == 3 * 60
    assert beta["sec"] == 2 * 60
    assert alpha["sec"] + beta["sec"] == day["total_sec"]


# --- 残し方 ------------------------------------------------------------------

def _day(total):
    return {"total_sec": total, "projects": {}}


def test_save_skips_today_and_keeps_larger_stored(tmp_path, monkeypatch):
    monkeypatch.setenv("CHRONOFIT_HOME", str(tmp_path))
    today = date(2026, 1, 10)
    written = cli_claude.save({"2026-01-08": _day(100), "2026-01-10": _day(50)}, today)
    assert written == ["2026-01-08"]
    # 会話ログが消えて測り直しが減っても、残っているほうを上書きしない
    assert cli_claude.save({"2026-01-08": _day(40)}, today) == []
    assert cli_claude.load_day("2026-01-08")["total_sec"] == 100
    assert cli_claude.save({"2026-01-08": _day(200)}, today) == ["2026-01-08"]
    assert cli_claude.load_day("2026-01-08")["total_sec"] == 200


def test_merge_sums_days():
    days = [{"total_sec": 60, "projects": {"a": {"sec": 60, "wait_sec": 60, "talk_sec": 0,
                                                 "prompts": 1, "kinds": {"レポート": 60}}}},
            {"total_sec": 30, "projects": {"a": {"sec": 30, "wait_sec": 0, "talk_sec": 30,
                                                 "prompts": 2, "kinds": {}}}}]
    total, projects = cli_claude._merge(days)
    assert total == 90
    assert projects["a"]["sec"] == 90 and projects["a"]["prompts"] == 3
    assert projects["a"]["kinds"] == {"レポート": 60}


def test_records_without_origin_are_not_prompts_in_modern_logs(tmp_path):
    lines = [
        {"type": "user", "origin": {"kind": "human"}, "timestamp": stamp(0), "cwd": "/alpha"},
        {"type": "user", "entrypoint": "cli", "timestamp": stamp(1), "cwd": "/alpha"},
        {"type": "system", "subtype": "turn_duration", "durationMs": 180000,
         "timestamp": stamp(3), "cwd": "/alpha"},
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    assert [event[1] for event in claude_turns.read_turns(path)] == [PROMPT, END]


def test_talk_keeps_the_title_of_the_turn_it_follows():
    parts = claude_time.pieces([prompt(0), end(4), prompt(7, title="題B"), end(9, title="題B")])
    talk = [p for p in parts if p[2] == claude_time.TALK][0]
    assert talk[4] == "題A"


def test_long_autonomous_turn_counts_only_wait_cap():
    parts = claude_time.pieces([prompt(0), end(600, took=600)])
    assert [(p[0], p[1]) for p in parts] == [(at(0), at(30))]
