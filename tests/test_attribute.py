"""実測からの時間切り出しのテスト。

ここが甘いと、無関係な時間がタスクの所要時間として学習曲線へ入る。
"""
import json
from datetime import timedelta, timezone

import pytest

from chronofit.estimate import attribute

JST = timezone(timedelta(hours=9))


def write_day(raw_dir, date, spans):
    raw_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"t": "span", "start": f"{date}T{s}+09:00",
                         "end": f"{date}T{e}+09:00", "sec": sec, "active_sec": active,
                         "proc": "SumatraPDF.exe", "title": title}, ensure_ascii=False)
             for s, e, sec, active, title in spans]
    (raw_dir / f"{date}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_一致したタイトルだけ足す(tmp_path):
    write_day(tmp_path, "2026-08-01", [
        ("09:00:00", "10:00:00", 3600, 3000, "応用数学B_2024_期末.pdf"),
        ("10:00:00", "11:00:00", 3600, 3000, "YouTube - 関係ない動画"),
    ])
    days = attribute.collect(tmp_path, "応用数学B", "2026-08-01", "2026-08-01")
    assert len(days) == 1
    assert days[0]["net_hours"] == pytest.approx(3000 / 3600)


def test_入力の無い時間はnetに入れない(tmp_path):
    # 開いたまま放置した1時間を「やった1時間」にしない
    write_day(tmp_path, "2026-08-01", [
        ("09:00:00", "10:00:00", 3600, 0, "応用数学B_2024_期末.pdf"),
    ])
    assert attribute.collect(tmp_path, "応用数学B", "2026-08-01", "2026-08-01") == []


def test_複数日にまたがるとsessionsが増える(tmp_path):
    for date in ("2026-08-01", "2026-08-02", "2026-08-03"):
        write_day(tmp_path, date, [("09:00:00", "10:00:00", 3600, 1800,
                                    "応用数学B_2024_期末.pdf")])
    totals = attribute.totals(
        attribute.collect(tmp_path, "応用数学B", "2026-08-01", "2026-08-03"))
    assert totals["sessions"] == 3
    assert totals["net_hours"] == pytest.approx(1.5)
    assert totals["first"] == "2026-08-01"
    assert totals["last"] == "2026-08-03"


def test_記録の無い日は飛ばす(tmp_path):
    write_day(tmp_path, "2026-08-03", [("09:00:00", "10:00:00", 3600, 3600, "応用数学B")])
    days = attribute.collect(tmp_path, "応用数学B", "2026-08-01", "2026-08-03")
    assert [d["date"] for d in days] == ["2026-08-03"]


def test_日付を逆に渡しても動く(tmp_path):
    write_day(tmp_path, "2026-08-02", [("09:00:00", "10:00:00", 3600, 3600, "応用数学B")])
    assert len(attribute.collect(tmp_path, "応用数学B", "2026-08-03", "2026-08-01")) == 1


def test_大文字小文字を区別しない(tmp_path):
    write_day(tmp_path, "2026-08-01", [("09:00:00", "10:00:00", 3600, 3600, "Main.PY")])
    assert len(attribute.collect(tmp_path, "main.py", "2026-08-01", "2026-08-01")) == 1


def write_media_day(raw_dir, date, spans):
    """再生していたスパン（無入力・音あり）を書く。"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"t": "span", "start": f"{date}T{s}+09:00",
                         "end": f"{date}T{e}+09:00", "sec": sec, "active_sec": 0.0,
                         "media_sec": sec, "proc": "vivaldi.exe", "title": title},
                        ensure_ascii=False)
             for s, e, sec, title in spans]
    (raw_dir / f"{date}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_観ていた時間もタスクの所要時間に入れる(tmp_path):
    # 講義映像は手が動かない。入力だけで測るとその科目が実際より軽く見える
    write_media_day(tmp_path, "2026-08-01",
                    [("20:00:00", "21:00:00", 3600, "情報理論 第3回 - YouTube")])
    days = attribute.collect(tmp_path, "情報理論", "2026-08-01", "2026-08-01")
    assert days[0]["net_hours"] == pytest.approx(1.0)
    assert days[0]["input_hours"] == 0.0
    assert days[0]["passive_hours"] == pytest.approx(1.0)
