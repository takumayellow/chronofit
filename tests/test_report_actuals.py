"""連作のレポート1本ごとの作業時間のテスト。科目名・リポジトリ名・回の書き方はすべて架空。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from chronofit import cli_reports
from chronofit.estimate import db as estimate_db
from chronofit.estimate import report_actuals as ra

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
SERIES = {"subject": "実験", "kind": "レポート", "repo": "lab",
          "unit_patterns": [r"unit(?P<theme>\d)/(?P<n>\d+)", r"課題(?P<theme>\d)-(?P<n>\d+)"],
          "target": "課題{theme}-{n}", "done_pattern": "提出を記録",
          "title_patterns": ["実験ノート"], "span_patterns": [r"^calc\.exe "]}


def at(hours):
    return T0 + timedelta(hours=hours)


def pr(number, merged, title, repo="lab"):
    return {"repo": repo, "number": number, "title": title,
            "created": at(merged - 1), "merged": at(merged)}


@pytest.fixture
def series():
    return ra.compile_series(SERIES)


class Test設定:
    def test_回の名前は名前付きグループから作り先頭の0を落とす(self, series):
        assert ra.unit_of("docs(unit1/02): 図を足す", series) == "課題1-2"
        assert ra.unit_of("課題3-10 の下書き", series) == "課題3-10"
        assert ra.unit_of("README を直す", series) is None

    def test_足りない項目は理由つきで弾く(self):
        with pytest.raises(ra.SeriesError, match="done_pattern"):
            ra.compile_series({**SERIES, "done_pattern": ""})

    def test_targetの名前がグループに無ければ弾く(self):
        with pytest.raises(ra.SeriesError, match="theme"):
            ra.compile_series({**SERIES, "unit_patterns": [r"第(?P<n>\d+)回"]})

    def test_壊れた正規表現は弾く(self):
        with pytest.raises(ra.SeriesError, match="正規表現"):
            ra.compile_series({**SERIES, "unit_patterns": ["(unclosed"]})

    def test_書き間違えた項目だけ飛ばす(self, capsys):
        loaded = cli_reports.load_series({"report_series": [SERIES, {"subject": "x"}]})
        assert [entry["repo"] for entry in loaded] == ["lab"]
        assert "飛ばした" in capsys.readouterr().err


class Test窓:
    def test_PRで区切り_完了の後は提出後_最後は作業中の回(self, series):
        prs = [pr(1, 1, "unit1/01: 下書き"), pr(2, 2, "unit1/01 の提出を記録"),
               pr(3, 3, "fix(unit1/01): 誤字"), pr(4, 4, "unit1/02: 下書き"),
               pr(9, 3.5, "別のリポジトリ", repo="other")]
        window_list, done = ra.windows(prs, series, at(0), at(6))
        assert [(w["number"], w["unit"], w["phase"]) for w in window_list] == [
            (1, "課題1-1", ra.WORK), (2, "課題1-1", ra.WORK), (3, "課題1-1", ra.AFTER),
            (4, "課題1-2", ra.WORK), (None, "課題1-2", ra.WORK)]
        assert done == {"課題1-1": at(2)}
        assert (window_list[0]["start"], window_list[-1]["end"]) == (at(0), at(6))

    def test_同じ回に挟まれた印なしの窓はその回_挟まれなければ共通(self, series):
        prs = [pr(1, 1, "雛形を置く"), pr(2, 2, "unit1/01: 下書き"), pr(3, 3, "typo"),
               pr(4, 4, "unit1/01: 図"), pr(5, 5, "サイトを直す"), pr(6, 6, "unit2/01: 下書き")]
        window_list, _ = ra.windows(prs, series, at(0), at(6))
        assert [w["unit"] for w in window_list] == [
            None, "課題1-1", "課題1-1", "課題1-1", None, "課題2-1"]

    def test_全部完了していれば最後の窓は共通(self, series):
        prs = [pr(1, 1, "unit1/01: 下書き"), pr(2, 2, "unit1/01 の提出を記録")]
        window_list, _ = ra.windows(prs, series, at(0), at(5))
        assert window_list[-1]["unit"] is ra.COMMON

    def test_計測の始まりより前の窓は作らない(self, series):
        prs = [pr(1, 1, "unit1/01: 下書き"), pr(2, 3, "unit1/01: 図")]
        window_list, _ = ra.windows(prs, series, at(2), at(3))
        assert [(w["number"], w["start"]) for w in window_list] == [(2, at(2))]


class Test数えるスパン:
    def test_割り付け先かタイトルか回の印で数える(self, series):
        assert ra.counts({"proc": "code.exe", "title": "x"}, "lab", series)
        assert ra.counts({"proc": "browser.exe", "title": "実験ノート - 第1回"}, None, series)
        assert ra.counts({"proc": "calc.exe", "title": "計算"}, None, series)
        assert ra.counts({"proc": "viewer.exe", "title": "課題1-2.pdf"}, None, series)
        assert not ra.counts({"proc": "browser.exe", "title": "動画"}, "other", series)


class Test集計:
    def test_PCとClaudeの和集合で数え_回の印は窓より優先する(self, series):
        prs = [pr(1, 2, "unit1/01: 下書き"), pr(2, 3, "unit1/01 の提出を記録"),
               pr(3, 5, "unit1/02: 下書き")]
        window_list = ra.windows(prs, series, at(0), at(6))
        spans = [(at(1), 1800, "エディタ"),                 # 課題1-1 の窓、Claude と重なる
                 (at(4), 1800, "課題1-1.pdf の見直し"),      # 課題1-2 の窓だが印は 1-1（提出後）
                 (at(5.5), 900, "エディタ")]                  # 最後の窓 = 課題1-2
        engaged = [(at(1), at(2)), (at(4.5), at(5))]
        tallied = ra.tally(window_list, spans, engaged, series)
        first = tallied["課題1-1"]
        assert first["work"] == {"pc": 0.5, "claude": 1.0, "net": 1.0}
        assert first["after"] == {"pc": 0.5, "claude": 0.0, "net": 0.5}
        assert first["done"] == at(3)
        assert first["prs"] == [1, 2]
        second = tallied["課題1-2"]
        assert second["work"] == {"pc": 0.25, "claude": 0.5, "net": 0.75}
        assert ra.open_units(tallied) == {"課題1-2": 0.75}

    def test_ごく短い回は作業中に出さない(self):
        tallied = {"課題1-3": {"work": {"net": 0.01}, "done": None}}
        assert ra.open_units(tallied) == {}


class TestDB:
    def test_完了した回だけを完了順に_まだ無いものだけ足す(self, series):
        tallied = {
            "課題1-2": {"work": {"net": 2.0}, "done": at(30), "days": {1, 2}},
            "課題1-1": {"work": {"net": 3.0}, "done": at(10), "days": {1}},
            "課題2-1": {"work": {"net": 1.0}, "done": None, "days": set()},
            None: {"work": {"net": 5.0}, "done": None, "days": set()},
        }
        existing = [estimate_db.make("実験", "レポート", "課題0-1", 4, 1.0)]
        rows = ra.new_rows(tallied, series, existing, estimate_db.make)
        assert [(r["target"], r["index"], r["net_hours"], r["sessions"]) for r in rows] == [
            ("課題1-1", 5, 3.0, 1), ("課題1-2", 6, 2.0, 2)]
        assert {r["source"] for r in rows} == {ra.SOURCE}
        assert ra.new_rows(tallied, series, existing + rows, estimate_db.make) == []

    def test_残りは見積もりから使ったぶんを引き_使い切っても0にしない(self):
        assert ra.remaining_hours(5.0, 2.0) == 3.0
        assert ra.remaining_hours(5.0, 7.0) == ra.MIN_REMAINING_HOURS


class Test作業中の記録:
    def test_書いて読める_壊れていれば空(self, tmp_path, monkeypatch, series):
        monkeypatch.setattr(cli_reports, "progress_path", lambda: tmp_path / "p.json")
        tallied = {"課題1-2": {"work": {"net": 1.5}, "done": None}}
        cli_reports.save_progress([(series, tallied)], T0)
        assert cli_reports.load_progress() == {("実験", "レポート", "課題1-2"): 1.5}
        (tmp_path / "p.json").write_text("{壊れた", encoding="utf-8")
        assert cli_reports.load_progress() == {}
        (tmp_path / "p.json").write_text(json.dumps({"units": [{"x": 1}]}), encoding="utf-8")
        assert cli_reports.load_progress() == {}

    def test_見積もりは作業中の回を除いた実績から(self, series):
        instances = [estimate_db.make("実験", "レポート", "課題1-1", 1, 4.0),
                     estimate_db.make("実験", "レポート", "課題1-2", 2, 99.0)]
        estimate, left = cli_reports.forecast(instances, series, "課題1-2", 1.0)
        assert (estimate["hours"], left) == (4.0, 3.0)
        estimate, left = cli_reports.forecast([], series, "課題1-2", 1.0)
        assert left is None
