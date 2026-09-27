import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chronofit.estimate import db, pr_actuals
from chronofit.sources import claude_sessions, github_prs

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def repo_of(cwd):
    return (cwd.split("/")[1], True) if cwd.startswith("/") else (cwd, False)


def pr(repo, number, created, merged):
    return {"repo": repo, "number": number, "created": at(created), "merged": at(merged)}


class EngagedTest(unittest.TestCase):
    def test_counts_short_gaps_only(self):
        marks = [(at(0), "/alpha"), (at(3), "/alpha"), (at(30), "/alpha"), (at(32), "/alpha")]
        spans = pr_actuals.engaged([marks], repo_of)
        self.assertEqual(spans["alpha"], [(at(0), at(3)), (at(30), at(32))])

    def test_gap_is_attributed_to_the_earlier_directory(self):
        marks = [(at(0), "/alpha"), (at(2), "/beta"), (at(4), "/beta")]
        spans = pr_actuals.engaged([marks], repo_of)
        self.assertEqual(spans["alpha"], [(at(0), at(2))])
        self.assertEqual(spans["beta"], [(at(2), at(4))])

    def test_outside_git_is_ignored(self):
        self.assertEqual(pr_actuals.engaged([[(at(0), "home"), (at(1), "home")]], repo_of), {})

    def test_overlapping_sessions_are_not_double_counted(self):
        first = [(at(0), "/alpha"), (at(4), "/alpha")]
        second = [(at(2), "/alpha"), (at(6), "/alpha")]
        self.assertEqual(pr_actuals.engaged([first, second], repo_of)["alpha"],
                         [(at(0), at(6))])


class MeasureTest(unittest.TestCase):
    spans = {"alpha": [(at(0), at(60)), (at(120), at(150))]}

    def test_window_starts_at_previous_merge(self):
        result = pr_actuals.measure([pr("alpha", 1, 0, 60), pr("alpha", 2, 100, 200)],
                                    self.spans, since=at(-600))
        self.assertEqual([r["hours"] for r in result], [1.0, 0.5])
        self.assertTrue(all(r["status"] == pr_actuals.MEASURED for r in result))

    def test_window_is_capped_before_creation(self):
        result = pr_actuals.measure([pr("alpha", 1, 130, 150)], self.spans, since=at(-600),
                                    max_lead=timedelta(minutes=10))
        self.assertEqual(result[0]["hours"], 0.5)

    def test_parallel_pr_is_not_measured(self):
        result = pr_actuals.measure([pr("alpha", 1, 0, 60), pr("alpha", 2, 30, 150)],
                                    self.spans, since=at(-600))
        self.assertEqual(result[1]["status"], pr_actuals.PARALLEL)

    def test_no_transcript_time_is_unmeasured(self):
        result = pr_actuals.measure([pr("beta", 1, 0, 60)], self.spans, since=at(-600))
        self.assertEqual(result[0]["status"], pr_actuals.UNMEASURED)


class NewRowsTest(unittest.TestCase):
    def test_skips_recorded_and_numbers_per_repo(self):
        existing = [db.make("alpha", "PR", "#1", 1, 1.0, source=pr_actuals.SOURCE)]
        measured = [
            {"repo": "alpha", "number": 1, "merged": at(0), "hours": 1.0, "status": "measured"},
            {"repo": "alpha", "number": 2, "merged": at(60), "hours": 0.5, "status": "measured"},
            {"repo": "alpha", "number": 3, "merged": at(90), "hours": 0.0, "status": "unmeasured"},
            {"repo": "beta", "number": 1, "merged": at(90), "hours": 0.2, "status": "measured"},
        ]
        rows = pr_actuals.new_rows(measured, existing, db.make)
        self.assertEqual([(r["subject"], r["target"], r["index"]) for r in rows],
                         [("alpha", "#2", 2), ("beta", "#1", 1)])
        self.assertEqual(rows[0]["mode"], "oneoff")
        self.assertEqual(rows[0]["source"], pr_actuals.SOURCE)


class BacktestTest(unittest.TestCase):
    def test_repo_median_beats_overall_when_repos_differ(self):
        rows = ([db.make("alpha", "PR", f"#{i}", i, 0.2) for i in range(1, 5)]
                + [db.make("beta", "PR", f"#{i}", i, 2.0) for i in range(1, 5)])
        result = pr_actuals.backtest(rows)
        self.assertEqual(result["n"], 8)
        self.assertEqual(result["repo"]["mae"], 0.0)
        self.assertEqual(result["repo"]["within_2x"], 1.0)
        self.assertGreater(result["overall"]["mae"], 0.5)

    def test_zero_hour_rows_are_kept(self):
        rows = [db.make("alpha", "PR", "#1", 1, 0.0), db.make("alpha", "PR", "#2", 2, 1.0)]
        self.assertEqual(pr_actuals.backtest(rows)["n"], 2)

    def test_too_few_rows(self):
        self.assertIsNone(pr_actuals.backtest([db.make("alpha", "PR", "#1", 1, 0.2)])["repo"])


class GithubPrsTest(unittest.TestCase):
    def test_parse_drops_broken_rows(self):
        found = [
            {"repository": {"name": "alpha"}, "number": 2,
             "createdAt": "2026-01-05T09:00:00Z", "closedAt": "2026-01-05T10:00:00Z"},
            {"repository": {}, "number": 3, "createdAt": "x", "closedAt": "y"},
            "junk",
        ]
        rows = github_prs.parse(found)
        self.assertEqual([(r["repo"], r["number"]) for r in rows], [("alpha", 2)])
        self.assertEqual(rows[0]["merged"] - rows[0]["created"], timedelta(hours=1))


class ArchiveTest(unittest.TestCase):
    def test_unique_sessions_keeps_larger_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            small = Path(tmp, "a", "s1.jsonl")
            large = Path(tmp, "b", "s1.jsonl")
            other = Path(tmp, "b", "s2.jsonl")
            for path, text in ((small, "x"), (large, "xxxx"), (other, "y")):
                path.parent.mkdir(exist_ok=True)
                path.write_text(text, encoding="utf-8")
            files = claude_sessions.archive_files([tmp], datetime.now(timezone.utc))
            self.assertEqual(len(files), 3)
            kept = claude_sessions.unique_sessions(files)
            self.assertEqual(sorted(kept), sorted([large, other]))

    def test_archive_skips_old_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "a", "s1.jsonl")
            path.parent.mkdir()
            path.write_text("x", encoding="utf-8")
            old = datetime.now().timestamp() - 5 * 86400
            os.utime(path, (old, old))
            since = datetime.now(timezone.utc) - timedelta(days=2)
            self.assertEqual(claude_sessions.archive_files([tmp], since), [])


if __name__ == "__main__":
    unittest.main()
