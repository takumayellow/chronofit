"""会話の記録をリポジトリへ寄せる部品のテスト。パスとリポジトリ名はすべて架空。"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chronofit.estimate import pr_actuals
from chronofit.model import repo_context, work
from chronofit.sources import claude_sessions

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


def make_repo(root, origin=None):
    (root / ".git").mkdir(parents=True)
    if origin:
        (root / ".git" / "config").write_text(
            f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {origin}\n', encoding="utf-8")
    return root


def write(path, text="x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def session(marks=(), touches=()):
    return {"titles": set(), "marks": list(marks), "touches": list(touches)}


class Testリポジトリ名:
    def test_originのURLの末尾を名前にする(self, tmp_path):
        make_repo(tmp_path / "local-folder", "https://github.com/someone/notes.git")
        assert work.repo_root(tmp_path / "local-folder" / "sub")[0] == "notes"

    def test_originが無ければフォルダ名(self, tmp_path):
        make_repo(tmp_path / "plain")
        assert work.repo_name(str(tmp_path / "plain"))[0] == "plain"

    def test_worktreeは元の設定を読む(self, tmp_path):
        main = make_repo(tmp_path / "main", "git@github.com:someone/notes.git")
        gitdir = main / ".git" / "worktrees" / "wt"
        write(gitdir / "commondir", "../..")
        write(tmp_path / "wt" / ".git", f"gitdir: {gitdir}")
        assert work.repo_root(tmp_path / "wt")[0] == "notes"

    def test_originの無いworktreeは元のフォルダ名(self, tmp_path):
        main = make_repo(tmp_path / "main")
        gitdir = main / ".git" / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        write(tmp_path / "wt" / ".git", f"gitdir: {gitdir}")
        assert work.repo_root(tmp_path / "wt")[0] == "main"

    def test_submoduleは相対のgitdirを辿る(self, tmp_path):
        parent = make_repo(tmp_path / "parent")
        module = parent / ".git" / "modules" / "sub"
        write(module / "config", '[remote "origin"]\n\turl = https://github.com/someone/kit\n')
        write(parent / "sub" / ".git", "gitdir: ../.git/modules/sub")
        assert work.repo_root(parent / "sub" / "x")[0] == "kit"


class Test触ったパス:
    def test_編集したファイルとcdの先だけを拾う(self, tmp_path):
        lines = [
            {"type": "assistant", "timestamp": "2026-01-05T09:00:00Z", "cwd": "/home/u",
             "message": {"content": [
                 {"type": "text", "text": "cd /nope"},
                 {"type": "tool_use", "name": "Edit", "input": {"file_path": "/w/a/x.py"}},
                 {"type": "tool_use", "name": "Bash", "input": {"command": "cd /work/b && ls"}},
                 {"type": "tool_use", "name": "Bash", "input": {"command": "ls /w/c"}},
                 {"type": "tool_use", "name": "Read", "input": {"file_path": "/w/d/y.py"}},
             ]}},
        ]
        path = write(tmp_path / "s.jsonl", "\n".join(json.dumps(line) for line in lines))
        read = claude_sessions.read_session(path)
        assert [touched for _, _, touched in read["touches"]] == [
            "/w/a/x.py", str(Path("/work/b"))]
        assert len(read["marks"]) == 1

    def test_cdの行き先の書き方(self):
        home = os.path.expanduser("~")
        assert claude_sessions.cd_target('cd "/work/a b" && ls', "/x") == str(Path("/work/a b"))
        assert claude_sessions.cd_target("cd ~/dev/notes; ls", "/x") == str(
            Path(home, "dev/notes"))
        assert claude_sessions.cd_target("cd sub", "/x") == str(Path("/x") / "sub")
        assert claude_sessions.cd_target('cd "$DIR"', "/x") is None
        assert claude_sessions.cd_target("git status", "/x") is None


class Test割り付け:
    def test_gitの外の会話はcdや編集の先のリポジトリに寄せる(self, tmp_path):
        repo = make_repo(tmp_path / "notes")
        home = tmp_path / "home"
        home.mkdir()
        resolver = repo_context.Resolver()
        one = session(marks=[(at(0), str(home)), (at(2), str(home)), (at(40), str(home))],
                      touches=[(at(0), str(home), str(repo / "a.py"))])
        resolver.learn([one])
        assert resolver.marks(one) == [(at(0), "notes"), (at(2), "notes"), (at(40), None)]

    def test_触った先が無ければ作業ディレクトリのリポジトリ(self, tmp_path):
        make_repo(tmp_path / "notes")
        resolver = repo_context.Resolver()
        one = session(marks=[(at(0), str(tmp_path / "notes" / "src"))])
        resolver.learn([one])
        assert resolver.marks(one) == [(at(0), "notes")]

    def test_消えたworktreeは中で編集したファイルの相対パスで決める(self, tmp_path):
        write(make_repo(tmp_path / "notes") / "docs" / "theme" / "01.md")
        write(make_repo(tmp_path / "other") / "README.md")
        gone = tmp_path / ".wt" / "review-1"
        (tmp_path / ".wt").mkdir()
        one = session(marks=[(at(0), str(gone / "sub")), (at(3), str(gone))],
                      touches=[(at(3), str(gone), str(gone / "docs" / "theme" / "01.md")),
                               (at(3), str(gone), str(gone / "README.md"))])
        # 置き場所は別の会話で見ている
        seen = session(marks=[(at(0), str(tmp_path / "notes")), (at(0), str(tmp_path / "other"))])
        resolver = repo_context.Resolver()
        resolver.learn([seen, one])
        assert resolver.repo(str(gone / "sub")) == "notes"

    def test_消えたディレクトリの名前がリポジトリ名で始まればそのリポジトリ(self, tmp_path):
        make_repo(tmp_path / "notes")
        gone = tmp_path / "notes-wt-feature"
        resolver = repo_context.Resolver()
        resolver.learn([session(marks=[(at(0), str(tmp_path / "notes")), (at(1), str(gone))])])
        assert resolver.repo(str(gone)) == "notes"

    def test_前に見た置き場所は消えても引ける(self, tmp_path):
        gone = tmp_path / "wt-x"
        resolver = repo_context.Resolver({str(gone): "notes"})
        resolver.learn([session(marks=[(at(0), str(gone / "src"))])])
        assert resolver.repo(str(gone / "src")) == "notes"

    def test_置き場所の記録は足して残す(self, tmp_path):
        repo_context.save_cache(tmp_path, {"a": "one"})
        repo_context.save_cache(tmp_path, {"b": "two"})
        assert repo_context.load_cache(tmp_path) == {"a": "one", "b": "two"}

    def test_engagedに渡せる(self, tmp_path):
        repo = make_repo(tmp_path / "notes")
        resolver = repo_context.Resolver()
        one = session(marks=[(at(0), str(tmp_path)), (at(3), str(tmp_path))],
                      touches=[(at(0), str(tmp_path), str(repo / "a.py"))])
        resolver.learn([one])
        spans = pr_actuals.engaged([resolver.marks(one)], lambda r: (r, r is not None))
        assert spans == {"notes": [(at(0), at(3))]}


def test_git_bashのドライブ表記はWindowsのパスに直す():
    if os.name != "nt":
        return
    assert claude_sessions.cd_target("cd /c/Users/u/dev && ls", "/x") == str(Path("C:/Users/u/dev"))
