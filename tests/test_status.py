"""`status` のテスト。

守りたいのは1つ:「同じ日について status と report が違うことを言わない」。
status が生スパンを独自に数えていた頃、記録された時間をまるごと在席と呼び、離席を
0.0h と表示していた（同じ日を report は在席 9.4h / 離席 2.6h と出す）。
数字が食い違うと、どちらが壊れているのか分からず「測れていない」ようにしか見えない。
"""
import argparse
import json
from datetime import datetime, timedelta, timezone

from chronofit import cli, paths
from chronofit.model import rollup

JST = timezone(timedelta(hours=9))


def _write(tmp_path, monkeypatch, records):
    monkeypatch.setenv(paths.ENV_ROOT, str(tmp_path))
    raw = paths.ensure(paths.raw_dir())
    (raw / "2026-08-09.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _at(hour, minute):
    return datetime(2026, 8, 9, hour, minute, tzinfo=JST).isoformat(timespec="seconds")


def _span(start, end, sec, active_sec, proc="Code.exe"):
    return {"t": "span", "start": start, "end": end, "sec": sec,
            "active_sec": active_sec, "media_sec": 0.0, "proc": proc, "title": "main.py"}


def test_無入力を在席に数えない(tmp_path, monkeypatch, capsys):
    """rollup と同じ畳み方をしていることを、両者の数字が一致することで確かめる。"""
    records = [_span(_at(9, 0), _at(10, 0), 3600, 3600),
               _span(_at(10, 0), _at(12, 0), 7200, 0.0)]   # 2時間の無入力＝離席
    _write(tmp_path, monkeypatch, records)

    cli.cmd_status(argparse.Namespace(days=7))
    line = [l for l in capsys.readouterr().out.splitlines() if "spans=" in l][0]

    summary = rollup.summarize_day(records)
    assert summary["away_sec"] == 7200          # 前提: rollup はこれを離席と呼ぶ
    assert "在席  1.0h" in line
    assert "離席  2.0h" in line
    assert "記録  3.0h" in line                 # 記録された総量は別の列として残す


def test_最終記録の時刻が出る(tmp_path, monkeypatch, capsys):
    """「今も取れてる？」に答えるのは常駐の生死ではなく最後の記録の時刻。"""
    now = datetime.now().astimezone()
    recent = now - timedelta(minutes=1)
    _write(tmp_path, monkeypatch, [
        _span((recent - timedelta(minutes=5)).isoformat(timespec="seconds"),
              recent.isoformat(timespec="seconds"), 300, 300)])

    cli.cmd_status(argparse.Namespace(days=7))
    out = capsys.readouterr().out
    assert f"最終記録: {recent:%H:%M:%S}" in out
    assert "遅れている" not in out


def test_長いスパンを畳んでいる最中を遅れと呼ばない(tmp_path, monkeypatch, capsys):
    """前景が変わらない間、スパンは MAX_SPAN_SEC まで書き出されない。

    閾値をそこに揃えると、PDF を読み続けているだけの健全な収集を「止まっている」と
    言ってしまう。それは今回直したかった誤解そのもの。
    """
    open_span = datetime.now().astimezone() - timedelta(minutes=6)
    _write(tmp_path, monkeypatch, [
        _span((open_span - timedelta(minutes=5)).isoformat(timespec="seconds"),
              open_span.isoformat(timespec="seconds"), 300, 300)])

    cli.cmd_status(argparse.Namespace(days=7))
    assert "遅れている" not in capsys.readouterr().out


def test_時刻を欠いた行があっても読める(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(paths.ENV_ROOT, str(tmp_path))
    raw = paths.ensure(paths.raw_dir())
    (raw / "2026-08-09.jsonl").write_text(
        json.dumps({"t": "span", "sec": 300}) + "\n"
        + json.dumps(_span(_at(9, 0), _at(10, 0), 3600, 3600)) + "\n", encoding="utf-8")

    cli.cmd_status(argparse.Namespace(days=7))
    assert "在席  1.0h" in capsys.readouterr().out


def test_記録が止まっていれば遅れを明示する(tmp_path, monkeypatch, capsys):
    stale = datetime.now().astimezone() - timedelta(hours=3)
    _write(tmp_path, monkeypatch, [
        _span((stale - timedelta(minutes=5)).isoformat(timespec="seconds"),
              stale.isoformat(timespec="seconds"), 300, 300)])

    cli.cmd_status(argparse.Namespace(days=7))
    assert "遅れている" in capsys.readouterr().out
