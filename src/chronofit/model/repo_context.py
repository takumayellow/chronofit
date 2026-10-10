"""会話の記録の各時点で、どのリポジトリを触っていたかを決める。

作業ディレクトリだけで決めると、2つの場面で時間が消える。

- git の外（ホームや Downloads）で開いた会話が、`cd` や絶対パスの編集で別のリポジトリを触る
- worktree で作業してマージ後に消した。今のディスクに `.git` が無いので判定できない

そこで会話ログの「触ったパス」（編集したファイルと Bash の先頭の `cd` 先。
`claude_sessions.read_session` の `touches`）も使い、パス1つのリポジトリを次の順で決める。

1. 今 git の中ならそのリポジトリ（worktree は元のリポジトリ名）
2. 前に見たリポジトリの置き場所（`repo_dirs.json` に残す）の下ならそのリポジトリ
3. 消えたディレクトリなら、その下で編集したファイルの相対パス（2階層以上）が
   今あるどのリポジトリにあるかの多数決
4. 消えたディレクトリの名前が `<リポジトリ名>-…` ならそのリポジトリ

各時点のリポジトリは「`ttl` 以内に触ったパスのリポジトリ」を優先し、無ければ作業ディレクトリの
リポジトリにする。会話を開いた場所と違うリポジトリへ移って作業する使い方が多いため。
"""
import bisect
import json
import os
from collections import Counter
from datetime import timedelta
from pathlib import Path

from . import work

TOUCH_TTL = timedelta(minutes=15)
MIN_REL_PARTS = 2   # README.md 1つのような、どのリポジトリにもある相対パスでは決めない
CACHE_NAME = "repo_dirs.json"


def norm(path):
    return os.path.normcase(os.path.normpath(str(path)))


def _ancestors(path):
    """`path` 自身から根へ向かって（正規化済みの文字列）。"""
    current = norm(path)
    while True:
        yield current
        parent = os.path.dirname(current)
        if parent == current:
            return
        current = parent


class Resolver:
    """パス → リポジトリ名。結果は覚えておく（同じパスを何千回も聞かれるため）。"""

    def __init__(self, known_roots=None):
        self.roots = {norm(root): name for root, name in (known_roots or {}).items()}
        self._live, self._exists, self._repo = {}, {}, {}
        self._dead_votes = {}

    # ---- 1. 今のディスク
    def _exists_dir(self, path):
        if path not in self._exists:
            self._exists[path] = os.path.isdir(path)
        return self._exists[path]

    def live(self, path):
        """今 git の中なら名前、外なら None。見つけた置き場所は `roots` に覚える。"""
        key = norm(path)
        if key not in self._live:
            found = work.repo_root(key) if self._exists_dir(key) or self._exists_dir(
                os.path.dirname(key)) else None
            if found:
                self.roots[norm(found[1])] = found[0]
            self._live[key] = found[0] if found else None
        return self._live[key]

    # ---- 2. 前に見た置き場所
    def cached(self, path):
        for ancestor in _ancestors(path):
            if ancestor in self.roots:
                return self.roots[ancestor]
        return None

    # ---- 3. 消えたディレクトリの中身の相対パス
    def learn(self, sessions):
        """会話の全パスを一度見て、今ある置き場所と、消えたディレクトリの多数決を作る。"""
        paths = {cwd for session in sessions for _, cwd in session["marks"]}
        paths |= {touched for session in sessions for _, _, touched in session["touches"]}
        outside = [path for path in paths if not self.live(path)]   # 先に置き場所を全部覚える
        dead = [path for path in outside if not self.cached(path)]
        roots = self._existing_roots()
        votes = {}
        for path in {norm(path) for path in dead}:
            for ancestor, rel in self._dead_splits(path):
                for root, name in roots:
                    candidate = Path(root, *rel)
                    if candidate.is_file() or (len(rel) > MIN_REL_PARTS
                                               and candidate.parent.is_dir()):
                        votes.setdefault(ancestor, Counter())[name] += 1
        self._dead_votes = votes
        self._repo.clear()

    def _existing_roots(self):
        """名前ごとに1つ（元のリポジトリを優先）、今ディスクにある置き場所。"""
        chosen = {}
        for root, name in sorted(self.roots.items()):
            if not os.path.isdir(root):
                continue
            main = os.path.isdir(os.path.join(root, ".git"))
            if name not in chosen or (main and not chosen[name][1]):
                chosen[name] = (root, main)
        return [(root, name) for name, (root, _) in chosen.items()]

    def _dead_splits(self, path):
        """消えた部分の中で切った `(切った位置, 残りの相対パスの部品)`。"""
        parts = []
        for ancestor in _ancestors(path):
            if self._exists_dir(ancestor):
                return
            if len(parts) >= MIN_REL_PARTS:
                yield ancestor, tuple(reversed(parts))
            parts.append(os.path.basename(ancestor))

    def _by_votes(self, path):
        """消えた部分の祖先のうち、票のいちばん多い位置の勝者。

        消えた worktree の根には、その下の全ファイルが長い相対パスで票を入れるので、
        浅い位置ほど票が多く当てになる。深い位置の票は短い相対パスの偶然の一致を含む。
        """
        best = None
        for ancestor in _ancestors(path):
            if self._exists_dir(ancestor):
                break
            votes = self._dead_votes.get(ancestor)
            if votes and (best is None or sum(votes.values()) >= sum(best.values())):
                best = votes
        return best.most_common(1)[0][0] if best else None

    # ---- 4. 名前
    def _by_name(self, path):
        names = sorted(set(self.roots.values()), key=len, reverse=True)
        for ancestor in _ancestors(path):
            if self._exists_dir(ancestor):
                return None
            base = os.path.basename(ancestor)
            for name in names:
                if base.startswith(norm(name) + "-"):
                    return name
        return None

    def repo(self, path):
        """パス1つのリポジトリ名。決まらなければ None。"""
        key = norm(path)
        if key not in self._repo:
            self._repo[key] = (self.live(key) or self.cached(key) or self._by_votes(key)
                               or self._by_name(key))
        return self._repo[key]

    def marks(self, session, ttl=TOUCH_TTL):
        """会話1本の `[(時刻, リポジトリ名 または None)]`。`pr_actuals.engaged` に渡す形。"""
        touches = [(moment, repo) for moment, _, touched in session["touches"]
                   for repo in (self.repo(touched),) if repo]
        times = [moment for moment, _ in touches]
        result = []
        for moment, cwd in session["marks"]:
            index = bisect.bisect_right(times, moment) - 1
            if index >= 0 and moment - touches[index][0] <= ttl:
                result.append((moment, touches[index][1]))
            else:
                result.append((moment, self.repo(cwd)))
        return result


def load_cache(data_root):
    path = Path(data_root) / CACHE_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {root: name for root, name in data.items()
            if isinstance(root, str) and isinstance(name, str)} if isinstance(data, dict) else {}


def save_cache(data_root, roots):
    """見つけた置き場所を足して残す。消えた worktree を後で引けるように、消えたものも残す。"""
    path = Path(data_root) / CACHE_NAME
    merged = {**load_cache(data_root), **roots}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=1, sort_keys=True),
                    encoding="utf-8")
    return path
