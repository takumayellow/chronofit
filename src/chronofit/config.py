"""利用側から注入する設定。

このリポジトリには個人固有の定数（科目名・カテゴリ・容量）を焼き込まない。
焼き込まないことが、そのまま「設定で動く汎用ツール」であることを強制する。

読み込み順:

1. 環境変数 `CHRONOFIT_CONFIG` が指すファイル
2. `<データルート>/config.json`
3. 何も無ければ下の既定値

JSON にしているのは依存パッケージを増やさないため。利用側が YAML で持っているなら、
JSON へ書き出してからここへ渡す。
"""
import json
import os
from pathlib import Path

from . import paths

# 離席ブロックに付けるラベルの選択肢。1日の終わりにキー1打で潰せるよう5つに絞る。
# 選択肢を増やすほど1本あたりの逡巡が増え、続かなくなる。
# `study: true` の選択肢を選んだときだけ、科目と対象を追加で聞く。オフPC作業の
# 中身が要るのは所要時間DBへ入れるときだけなので、他の離席では一切聞かない。
# 中身が毎回同じ選択肢（ピアノ等）は `subject` / `kind` を持たせる。1打で科目まで
# 決まるので、追加の質問なしに習慣の実測が貯まる。
# 例: {"key": "4", "label": "ピアノ", "subject": "ピアノ", "kind": "練習"}
DEFAULT_AWAY_CATEGORIES = [
    {"key": "1", "label": "移動・身支度"},
    {"key": "2", "label": "食事・休憩"},
    {"key": "3", "label": "オフPC作業", "study": True},
    {"key": "4", "label": "睡眠"},
    {"key": "5", "label": "その他"},
]

DEFAULTS = {
    "away_categories": DEFAULT_AWAY_CATEGORIES,
    # ウィンドウタイトル -> (科目, 種別) の対応。利用側が入れる。
    # 例: {"match": "応用数学A.*\\.pdf", "subject": "応用数学A", "kind": "過去問"}
    "title_rules": [],
    # URL/タイトル -> (科目, 種別) または (カテゴリ)。ブラウザ履歴の分類に使う。
    # 例: {"match": "atcoder\\.jp", "subject": "AtCoder", "kind": "精進"}
    "url_rules": [],
    # 日タイプの判定。slack 率は日タイプごとに違うので分けて集計する。
    "day_types": {"weekend": [5, 6]},
    # 種別 -> モード（series / oneoff / habit）。どの種別がどれかは科目構成で
    # 変わるので、ここには表を持たない。既定は series。
    "kind_modes": {},
    # 習慣として扱う対象。時間は宣言せず、実測の1日平均を容量から引く。
    # 例: {"name": "ピアノ", "subject": "ピアノ", "assumed_hours_per_day": 2.0}
    # `assumed_hours_per_day` は実測が貯まるまでの仮値で、無ければ何も引かない。
    "habits": [],
    # オフPC作業のラベル2段目で1打で選べる組み合わせ。
    # 例: {"key": "1", "subject": "情報理論", "kind": "参考書"}
    "study_presets": [],
}


def config_path():
    override = os.environ.get("CHRONOFIT_CONFIG")
    if override:
        return Path(override)
    return paths.data_root() / "config.json"


def load():
    """設定を読む。壊れていても既定値で動き続ける（収集を止めない方が大事）。"""
    path = config_path()
    merged = dict(DEFAULTS)
    if path.is_file():
        try:
            merged.update(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return merged


def away_categories(config=None):
    categories = (config or load()).get("away_categories") or DEFAULT_AWAY_CATEGORIES
    return [c for c in categories if c.get("key") and c.get("label")]


def study_presets(config=None):
    """オフPC作業のラベル2段目の選択肢。"""
    presets = (config or load()).get("study_presets") or []
    return [p for p in presets if p.get("key") and p.get("subject")]
