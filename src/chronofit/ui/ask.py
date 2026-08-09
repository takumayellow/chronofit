"""離席ブロックにラベルを付ける対話。1日1回・数十秒で終わることだけを目標にする。

凝った UI にしない理由: 立ち上げるのに手間が要る仕組みは、それ自体が続かない理由になる。
端末で1打ずつ潰せれば十分で、既定値が育つほど Enter 連打に近づく。

2段目（科目・対象）を聞くのは **オフPC作業を選んだブロックだけ** にする。紙で解いた90分を
所要時間DBへ入れるには中身が要るが、昼食や移動には要らない。全ブロックに中身を聞く形に
すると1日1回のインタビューへ戻り、過去に続かなかったやり方と同じになる。
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


def _read_line(prompt):
    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        return sys.stdin.readline().strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def ask_detail(presets, guess=None):
    """オフPC作業の中身（科目・種別・対象）を聞く。

    `guess` は両隣の前景タイトルからの推定（`model/context.py`）。当たっていれば
    Enter だけで済むので、実際に打鍵が要るのは「PC を一度も触らずに紙だけで進めた」
    ブロックに限られる。
    """
    if guess and guess.get("subject"):
        kind = guess.get("kind") or "種別不明"
        print(f"    推定 {guess['subject']} / {kind}"
              f"（{guess.get('via', '?')}: {(guess.get('title') or '')[:40]}）")
        sys.stdout.write("    [Enter] 採用  [他キー] 選び直す > ")
        sys.stdout.flush()
        if _read_key() in ("\r", "\n", ""):
            print()
            return {"subject": guess["subject"], "kind": guess.get("kind"),
                    "target": _read_line("    対象（章・年度など。空可）> ") or None}
        print()

    if presets:
        print("    " + "  ".join(f"[{p['key']}] {p['subject']}/{p.get('kind') or '?'}"
                                 for p in presets) + "   [0] 手入力")
        sys.stdout.write("    > ")
        sys.stdout.flush()
        char = _read_key()
        print()
        chosen = next((p for p in presets if p["key"] == char), None)
        if chosen:
            return {"subject": chosen["subject"], "kind": chosen.get("kind"),
                    "target": _read_line("    対象（章・年度など。空可）> ") or None}

    subject = _read_line("    科目（空ならラベルだけ残す）> ")
    if not subject:
        return None
    return {"subject": subject,
            "kind": _read_line("    種別 > ") or None,
            "target": _read_line("    対象 > ") or None}


def ask_blocks(blocks, categories, suggest, detail_for=None):
    """未ラベルのブロックを順に聞く。{開始時刻ISO: (ラベル, 中身)} を返す。

    suggest(block) は既定ラベル（無ければ None）。Enter で採用できるので、
    履歴が溜まるほど打鍵が減る。detail_for(block) は `study` 付きの選択肢を
    選んだときだけ呼ばれ、科目と対象を返す。
    """
    if not blocks:
        print("未ラベルの離席ブロックは無い。")
        return {}

    by_key = {c["key"]: c for c in categories}
    by_label = {c["label"]: c for c in categories}
    menu = "  ".join(f"[{c['key']}] {c['label']}" for c in categories)
    print(f"離席ブロック {len(blocks)}本。{menu}   [Enter] 既定  [s] 後回し  [q] 中断")

    answers = {}

    def record(block, category):
        if category.get("subject"):
            # 中身が最初から決まっている選択肢（ピアノ等）は追加で聞かない。
            # 1打で科目まで確定するので、習慣も実測が貯まる。
            detail = {"subject": category["subject"],
                      "kind": category.get("kind") or category["label"],
                      "target": None, "units": None}
        else:
            detail = detail_for(block) if category.get("study") and detail_for else None
        answers[block["start"].isoformat(timespec="seconds")] = (category["label"], detail)
        suffix = f"  {detail['subject']}" if detail and detail.get("subject") else ""
        print(f"  -> {category['label']}{suffix}")

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
                    record(block, by_label.get(default, {"label": default}))
                    break
                continue   # 既定が無いのに Enter は無効。聞き直す
            if char in by_key:
                record(block, by_key[char])
                break
    return answers
