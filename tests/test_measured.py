"""習慣の実測を生の記録から拾えるかのテスト。

習慣は完了しないので所要時間DBには入らない。ここが動かないと実測がゼロのままで、
容量から何も引けない（＝毎日2時間のピアノが無かったことになる）。
"""
import json

from chronofit.estimate import kinds, measured


def write_labels(root, date, entries):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{date}.json").write_text(json.dumps(entries, ensure_ascii=False),
                                       encoding="utf-8")


def write_spans(root, date, spans):
    root.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"t": "span", "start": f"{date}T{s}+09:00",
                         "end": f"{date}T{e}+09:00", "sec": sec, "active_sec": sec,
                         "proc": "vivaldi.exe", "title": title}, ensure_ascii=False)
             for s, e, sec, title in spans]
    (root / f"{date}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


PIANO = {"name": "ピアノ", "subject": "ピアノ"}
ATCODER = {"name": "AtCoder", "subject": "AtCoder", "match": r"atcoder"}


def test_ラベルから習慣の時間を拾う(tmp_path):
    write_labels(tmp_path, "2026-08-08", {
        "2026-08-08T20:00:00": {"label": "ピアノ", "sec": 7200,
                                "subject": "ピアノ", "kind": "練習"},
        "2026-08-08T12:00:00": {"label": "食事・休憩", "sec": 1800},
    })
    rows = measured.habit_rows([PIANO], tmp_path, None, "2026-08-01", "2026-08-08")
    assert rows == [{"date": "2026-08-08", "subject": "ピアノ", "kind": None,
                     "net_hours": 2.0}]


def test_PC上の習慣は前景タイトルから拾う(tmp_path):
    raw = tmp_path / "raw"
    write_spans(raw, "2026-08-08", [("21:00:00", "22:00:00", 3600,
                                     "AtCoder Beginner Contest - atcoder.jp")])
    rows = measured.habit_rows([ATCODER], None, raw, "2026-08-01", "2026-08-08")
    assert rows[0]["subject"] == "AtCoder"
    assert rows[0]["net_hours"] == 1.0


def test_同じ日の紙とPCを足す(tmp_path):
    labels, raw = tmp_path / "labels", tmp_path / "raw"
    entry = {"name": "AtCoder", "subject": "AtCoder", "match": r"atcoder"}
    write_labels(labels, "2026-08-08", {
        "2026-08-08T09:00:00": {"label": "オフPC作業", "sec": 1800,
                                "subject": "AtCoder", "kind": "紙で考える"},
    })
    write_spans(raw, "2026-08-08", [("21:00:00", "22:00:00", 3600, "atcoder.jp")])
    rows = measured.habit_rows([entry], labels, raw, "2026-08-01", "2026-08-08")
    assert len(rows) == 1
    assert rows[0]["net_hours"] == 1.5


def test_実測が容量から引く値になる(tmp_path):
    # 14日の窓で7時間なら1日あたり0.5時間。やらなかった日も分母に入る
    for date in ("2026-08-01", "2026-08-05"):
        write_labels(tmp_path, date, {
            f"{date}T20:00:00": {"label": "ピアノ", "sec": 12600,
                                 "subject": "ピアノ", "kind": "練習"},
        })
    rows = measured.habit_rows([PIANO], tmp_path, None, "2026-08-01", "2026-08-14")
    load = kinds.habit_load(rows, {"habits": [PIANO]},
                            since="2026-08-01", until="2026-08-14")
    assert load[0]["hours_per_day"] == 0.5
    assert load[0]["basis"] == "実測"


def test_窓の外のラベルは数えない(tmp_path):
    write_labels(tmp_path, "2026-07-20", {
        "2026-07-20T20:00:00": {"label": "ピアノ", "sec": 7200,
                                "subject": "ピアノ", "kind": "練習"},
    })
    assert measured.habit_rows([PIANO], tmp_path, None,
                               "2026-08-01", "2026-08-14") == []
