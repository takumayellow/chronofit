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


def report_dir():
    """1日の姿を見るための HTML。

    生タイトルをそのまま載せるので、ここも git の外に置く。共有できる粒度は
    `rollup_dir()` のほうで、こちらは**自分が見るための面**だと割り切る。
    """
    return data_root() / "report"


def location_dir():
    """スマホの位置履歴を畳んだ滞在。

    ウィンドウタイトルよりさらに機微なので、ここも git の外に置く。中身は
    日ごとの滞在（`*.json`）、スマホから受け取った点（`points/`）、
    タイムライン書き出しの原本（`exports/`）、受け口の認証（`receiver.json`）。
    """
    return data_root() / "location"


def payments_dir():
    """決済の記録（時刻・店名・金額）と、店名 → 用事の対応、メールを読むトークン。

    買い物の履歴は位置履歴と同じくらい機微なので git の外に置く。メール本文は残さない。
    """
    return data_root() / "payments"


def calendar_dir():
    """Google カレンダーの予定（時刻・件名・場所欄）と、読み取り専用のトークン。

    予定の件名は私生活そのものなので git の外に置く。
    """
    return data_root() / "calendar"


def claude_time_dir():
    """Claude との会話に使った時間を、日ごと・プロジェクトごとに畳んだもの。

    会話ログは既定で 30 日で消えるので、消える前に畳んで残す。作業名（会話の題）から
    振り分けた結果を持つので、ここも git の外に置く。
    """
    return data_root() / "claude"


def phone_dir():
    """スマホの使用状況（画面の点灯・解除・アプリの前面化の時刻とパッケージ名）。

    どのアプリをいつ開いたかは生活そのものなので git の外に置く。中身は
    日ごとのイベント（`events/`）、取得できた時刻（`pulls.jsonl`）、取得のログ。
    """
    return data_root() / "phone"


def ensure(path):
    """ディレクトリを作って返す。"""
    path.mkdir(parents=True, exist_ok=True)
    return path
