"""ウィンドウタイトルの正規化。

素のタイトルは1秒単位で変わる要素を含む（スピナー・経過秒・未読件数）。そのまま
スパンの同一性キーにすると、同じ作業が数秒ごとに切れて数千行に膨らむ。実測でも
`claude-work  2:⠐ ...` → `2:⠂ ...` の一文字違いだけで別スパンになった。

ここで落とすのは「同じ作業を見分けるのに要らない揺れ」だけ。作業の対象を示す部分
（ファイル名・科目名・年度）は絶対に落とさない。
"""
import re

# ブライユ点字ブロック = CLI スピナーの実体（⠋⠙⠹…）
_SPINNER = re.compile(r"[⠀-⣿]+")
# 経過時間表示: (12s · ...) / (1m 30s)
_ELAPSED = re.compile(r"\(\s*\d+\s*[smh](?:\s*[·\-–]\s*[^)]*)?\)")
# 先頭の未読/通知件数: "(3) 受信トレイ"
_UNREAD = re.compile(r"^\(\d+\)\s*")
# メディア再生位置: 12:34 / 1:02:33
_TIMECODE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
# 保存前を示す印。同じファイルなので別作業にしない。
_DIRTY = re.compile(r"^[*●•]\s*")

_WHITESPACE = re.compile(r"\s+")


def normalize(title):
    """スパン同一性の判定に使う形へ整える。"""
    if not title:
        return ""
    text = _SPINNER.sub("", title)
    text = _ELAPSED.sub("", text)
    text = _UNREAD.sub("", text)
    text = _TIMECODE.sub("", text)
    text = _DIRTY.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip(" -–—·|")
    return text
