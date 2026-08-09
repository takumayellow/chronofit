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


def label_dir():
    """離席ブロックに人が付けたラベル。

    ロールアップは何度でも作り直せるが、人が付けたラベルは作り直せない。
    再生成物と同じ場所に置くと、作り直しのたびに消す事故が起きる。
    """
    return data_root() / "labels"


def tasks_path():
    """やることの一覧。**唯一の正本**にする。

    計画のたびに JSON を書き起こす形だと、書き起こした瞬間の写しが増えていくだけで、
    「今どこまで来たか」を持てる場所がどこにも無い。一覧を1つに固定して、進捗は
    所要時間DBと突き合わせて毎回数え直す。
    """
    return data_root() / "tasks.json"


def board_dir():
    """進捗の日次スナップショット。

    現在地は毎回計算できるが、**過去の現在地**は計算できない（DBは「いつ終わったか」は
    持っても「あの日どれだけ残っていると思っていたか」は持たない）。予定が崩れた理由を
    後から辿るには、その日に見えていた残量を残しておく必要がある。
    """
    return data_root() / "board"


def ensure(path):
    """ディレクトリを作って返す。"""
    path.mkdir(parents=True, exist_ok=True)
    return path
