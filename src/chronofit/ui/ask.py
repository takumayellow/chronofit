"""離席ブロックにラベルを付ける対話。1日1回・数十秒で終わることだけを目標にする。

凝った UI にしない理由: 立ち上げるのに手間が要る仕組みは、それ自体が続かない理由になる。
端末で1打ずつ潰せれば十分で、既定値が育つほど Enter 連打に近づく。
"""
import sys


def _read_key():
    """1文字だけ受け取る。取れない環境では行入力へ落とす。"""
    try:
        import msvcrt
    except ImportError:
        line = sys.stdin.readline()
        return line.strip()[:1] if line.strip() else "\r"
    char = msvcrt.getwch()
    if char in ("\x00", "\xe0"):   # 機能キーは2バイト。2バイト目を捨てる
        msvcrt.getwch()
        return ""
    return char


def ask_blocks(blocks, categories, suggest):
    """未ラベルのブロックを順に聞く。{開始時刻ISO: ラベル} を返す。

    suggest(block) は既定ラベル（無ければ None）。Enter で採用できるので、
    履歴が溜まるほど打鍵が減る。
    """
    if not blocks:
        print("未ラベルの離席ブロックは無い。")
        return {}

    keys = {c["key"]: c["label"] for c in categories}
    menu = "  ".join(f"[{c['key']}] {c['label']}" for c in categories)
    print(f"離席ブロック {len(blocks)}本。{menu}   [Enter] 既定  [s] 後回し  [q] 中断")

    answers = {}
    for index, block in enumerate(blocks, 1):
        default = suggest(block)
        hint = f"既定={default}" if default else "既定なし"
        print(f"\n({index}/{len(blocks)}) {block['start']:%m/%d %H:%M}-{block['end']:%H:%M} "
              f"{block['sec'] / 60:.0f}分  {block['reason_label']}  {hint}")
        while True:
            sys.stdout.write("  > ")
            sys.stdout.flush()
            char = _read_key()
            print()
            if char in ("q", "Q", "\x03"):
                print("中断。ここまでの回答は保存する。")
                return answers
            if char in ("s", "S"):
                break
            if char in ("\r", "\n", ""):
                if default:
                    answers[block["start"].isoformat(timespec="seconds")] = default
                    print(f"  -> {default}")
                    break
                continue   # 既定が無いのに Enter は無効。聞き直す
            if char in keys:
                answers[block["start"].isoformat(timespec="seconds")] = keys[char]
                print(f"  -> {keys[char]}")
                break
    return answers
