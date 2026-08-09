"""生データの置き場所。

生イベントはウィンドウタイトルを含む＝「今まで開いた全ウィンドウの題名」なので、
private リポジトリにも置かない。OS のアプリデータ領域（git 外）に閉じる。
GitHub へ出るのは集計後のカテゴリ別時間とタスクインスタンスだけ。
"""
import os
from pathlib import Path

ENV_ROOT = "CHRONOFIT_HOME"


def data_root():
    """生データのルート。CHRONOFIT_HOME で上書きできる（テスト・別マシン用）。"""
    override = os.environ.get(ENV_ROOT)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    if base:
        return Path(base) / "chronofit"
    return Path.home() / ".local" / "share" / "chronofit"


def raw_dir():
    """L0 端末イベントの JSONL 置き場。"""
    return data_root() / "raw"


def snapshot_dir():
    """ブラウザ履歴など、放置すると消える外部ソースの日次スナップショット。"""
    return data_root() / "snapshots"


def rollup_dir():
    """スパンを畳んだ日次ロールアップ（ここから先は共有してよい粒度）。"""
    return data_root() / "rollup"


def ensure(path):
    """ディレクトリを作って返す。"""
    path.mkdir(parents=True, exist_ok=True)
    return path
