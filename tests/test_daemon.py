"""収集デーモンのスパンの切り方のテスト。"""
from datetime import datetime, timedelta, timezone

from chronofit.collect import daemon

JST = timezone(timedelta(hours=9))
KEY = ("Code.exe", "main.py")


def _span(idle):
    start = datetime(2026, 8, 9, 9, 0, tzinfo=JST)
    span = daemon._Span(KEY, "main.py", start, 15.0)
    span.add(idle, start)
    return span, start + timedelta(seconds=15)


def test_入力の有無が切り替わったらスパンを切る():
    span, moment = _span(idle=1.0)
    assert daemon._should_close(span, KEY, moment, idle=90.0)
    span, moment = _span(idle=90.0)
    assert daemon._should_close(span, KEY, moment, idle=1.0)


def test_入力の有無が同じならスパンを続ける():
    span, moment = _span(idle=1.0)
    assert not daemon._should_close(span, KEY, moment, idle=5.0)
    span, moment = _span(idle=90.0)
    assert not daemon._should_close(span, KEY, moment, idle=105.0)


def test_アイドルを読めなかったサンプルでは切らない():
    span, moment = _span(idle=1.0)
    assert not daemon._should_close(span, KEY, moment, idle=None)


def test_前景が変わったらスパンを切る():
    span, moment = _span(idle=1.0)
    assert daemon._should_close(span, ("vivaldi.exe", "x"), moment, idle=1.0)


def test_スパンは入力の有無が揃っている():
    span, moment = _span(idle=90.0)
    span.add(100.0, moment)
    record = span.to_record()
    assert record["active_sec"] == 0.0 and record["sec"] == 30.0
