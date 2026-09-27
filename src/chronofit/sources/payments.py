"""決済の通知メールから「いつ・どこで・いくら」だけを取り出し、滞在の用事を当てる。

同じ場所（商業施設など）でも、食事か買い物かで滞在の長さが違う。位置履歴だけでは
その区別が付かないので、滞在中に決済があればその店名から用事を決める。

- 残すのは時刻・店名・金額と、重複を避けるためのメッセージ ID だけ。本文は残さない
- 置き場所は git の外（`paths.payments_dir()`）
- 店名 → 用事の対応は git の外の `activities.json` に持つ。新しい店は1回だけ聞く
- どの送信元のメールを読むか（`sources.json`）も git の外に持つ。どのカードや店を
  使っているかは公開リポジトリに書かない。コードが知っているのは本文の形式だけ
"""
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

JST = timezone(timedelta(hours=9))
LEDGER = "payments.jsonl"
RULES = "activities.json"
SOURCES = "sources.json"
MATCH_SLACK = timedelta(minutes=5)      # 決済の時刻と滞在の端のずれ（測位の遅れ）を許す
IGNORE = "-"                            # 場所と結び付かない決済（通販・チャージ等）の用事

_DIGITS = str.maketrans("０１２３４５６７８９，．／：", "0123456789,./:")


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script"):
            self._skip += 1
        elif tag in ("br", "p", "tr", "div", "li", "table"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script") and self._skip:
            self._skip -= 1
        elif tag in ("td", "th"):
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html):
    parser = _TextOnly()
    parser.feed(html)
    return "".join(parser.parts)


def _normalize(text):
    return text.translate(_DIGITS).replace("　", " ").replace("|", " ")


def _amount(text):
    """「5,000円」「¥850」「110.28 USD」を (金額, 通貨) へ。"""
    text = text.strip()
    found = re.search(r"([\d,]+(?:\.\d+)?)\s*([A-Z]{3})\b", text)
    if found:
        return float(found.group(1).replace(",", "")), found.group(2)
    found = re.search(r"[¥￥]\s*([\d,]+)|([\d,]+)\s*円", text)
    if found:
        return float((found.group(1) or found.group(2)).replace(",", "")), "JPY"
    return None, None


def _time(text):
    found = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if not found:
        return None
    year, month, day, hour, minute, second = (int(v or 0) for v in found.groups())
    return datetime(year, month, day, hour, minute, second, tzinfo=JST)


def _field(text, label):
    found = re.search(label + r"\s*[:：]?\s*([^\n]+)", text)
    return found.group(1).strip() if found else None


def parse_card_notice(text, prefix=None):
    """カード利用の通知。`◇利用日：` `◇利用先：` `◇利用金額：` の形式を読む。"""
    text = _normalize(text)
    when, shop, amount = (_field(text, "◇利用日"), _field(text, "◇利用先"),
                          _field(text, "◇利用金額"))
    if not (when and shop and amount):
        return None
    moment = _time(when)
    value, currency = _amount(amount)
    if moment is None or value is None:
        return None
    return {"time": moment.isoformat(), "merchant": _merchant(prefix, shop), "amount": value,
            "currency": currency}


def _merchant(prefix, shop):
    return f"{prefix} {shop}" if prefix else shop


def parse_mobile_order(text, prefix=None):
    """モバイルオーダーの受付メール。`ご注文日時` `店舗名` `ご請求金額` を読む。

    店舗名はチェーン名を含まないことが多いので、`prefix`（チェーン名）を前に付ける。
    """
    text = _normalize(text)
    when, shop, amount = (_field(text, "ご注文日時"), _field(text, "店舗名"),
                          _field(text, "ご請求金額"))
    if not (when and shop and amount):
        return None
    moment = _time(when)
    value, currency = _amount(amount)
    if moment is None or value is None:
        return None
    return {"time": moment.isoformat(), "merchant": _merchant(prefix, shop.strip(" :：")),
            "amount": value, "currency": currency}


FORMATS = {"card_notice": parse_card_notice, "mobile_order": parse_mobile_order}
_ADDRESS = re.compile(r"[\w.+-]+@[\w.-]+")


def _valid_source(item):
    """送信元はアドレス1つ、件名は引用符を含まない（検索クエリを壊さない）、形式は既知のもの。"""
    subject = item.get("subject")
    return bool(_ADDRESS.fullmatch(str(item.get("from", ""))) and isinstance(subject, str)
                and subject and '"' not in subject and item.get("format") in FORMATS)


def load_sources(root):
    """読む通知: [{"from": 送信元, "subject": 件名に含まれる語, "format": 形式, "prefix"?}]

    件名でも絞るのは、同じ送信元が請求額の確定など決済の時刻を持たない通知も送るため。
    """
    try:
        items = json.loads((root / SOURCES).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict) and _valid_source(item)]


def save_source(root, sender, subject, fmt, prefix=None):
    source = {"from": sender.lower(), "subject": subject, "format": fmt}
    if prefix:
        source["prefix"] = prefix
    if not _valid_source(source):
        raise ValueError("送信元はメールアドレス、件名は \" を含めない、形式は "
                         + " / ".join(FORMATS))
    items = [item for item in load_sources(root)
             if (item["from"], item["subject"]) != (source["from"], subject)]
    items.append(source)
    root.mkdir(parents=True, exist_ok=True)
    (root / SOURCES).write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")


def gmail_queries(sources):
    return [f'from:{item["from"]} subject:"{item["subject"]}"' for item in sources]


def parse_message(sender, subject, text, sources):
    """1通のメールを決済1件へ。対象外・読めなければ None。"""
    found = _ADDRESS.search(sender or "")
    address = found.group(0).lower() if found else ""
    for item in sources:
        if item["from"] == address and item["subject"] in (subject or ""):
            return FORMATS[item["format"]](text, item.get("prefix"))
    return None


def load(root):
    path = root / LEDGER
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("time") and row.get("merchant"):
            rows.append(row)
    return rows


def append(rows, root):
    """まだ無いメッセージ ID の決済だけ足す。返り値は足した件数。"""
    seen = {row.get("id") for row in load(root)}
    fresh = [row for row in rows if row.get("id") not in seen]
    if fresh:
        root.mkdir(parents=True, exist_ok=True)
        with (root / LEDGER).open("a", encoding="utf-8") as handle:
            for row in fresh:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(fresh)


def load_rules(root):
    """店名に含まれる語 → 用事。[{"match": "マクドナルド", "activity": "食事"}]"""
    path = root / RULES
    try:
        rules = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [rule for rule in rules if isinstance(rule, dict)
            and rule.get("match") and rule.get("activity")] if isinstance(rules, list) else []


def save_rule(root, match, activity):
    rules = [rule for rule in load_rules(root) if rule["match"] != match]
    rules.append({"match": match, "activity": activity})
    root.mkdir(parents=True, exist_ok=True)
    (root / RULES).write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")


def activity_of(merchant, rules):
    """店名から用事。当てはまる規則が無ければ None（まだ聞いていない店）。"""
    for rule in rules:
        if rule["match"] in merchant:
            return rule["activity"]
    return None


def unmapped(rows, rules):
    """用事の決まっていない店名を、回数の多い順に。1回だけ聞くための一覧。"""
    counts = {}
    for row in rows:
        if activity_of(row["merchant"], rules) is None:
            counts[row["merchant"]] = counts.get(row["merchant"], 0) + 1
    return sorted(counts.items(), key=lambda item: -item[1])


def label_visits(visit_list, rows, rules):
    """滞在中に決済があれば、その店の用事を滞在の `activity` にする。

    滞在中に用事の違う決済が複数あれば、用事を決めない（食事と買い物を両方した滞在を
    どちらかの実測にすると、両方の分布が歪む）。元の列は変えず新しい列を返す。
    """
    moments = []
    for row in rows:
        activity = activity_of(row["merchant"], rules)
        if activity and activity != IGNORE:
            moments.append((datetime.fromisoformat(row["time"]), activity))
    result = []
    for visit in visit_list:
        found = {activity for moment, activity in moments
                 if visit["start"] - MATCH_SLACK <= moment <= visit["end"] + MATCH_SLACK}
        result.append({**visit, "activity": found.pop()} if len(found) == 1 else dict(visit))
    return result
