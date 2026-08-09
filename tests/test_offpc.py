"""紙で進めたぶんの実測を、ラベルから拾えるかのテスト。

長さは離席ブロックが既に持っているので、ここで確かめるのは
「中身で正しく絞れるか」と「日をまたいでも1インスタンスに畳めるか」。
"""
import json

from chronofit.estimate import offpc


def write(root, date, entries):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{date}.json").write_text(json.dumps(entries, ensure_ascii=False),
                                       encoding="utf-8")


def entry(label, sec, subject=None, kind=None, target=None, units=None):
    record = {"label": label, "sec": sec}
    for key, value in (("subject", subject), ("kind", kind),
                       ("target", target), ("units", units)):
        if value:
            record[key] = value
    return record


def test_科目と種別で絞って拾う(tmp_path):
    write(tmp_path, "2026-08-09", {
        "2026-08-09T13:00:00": entry("オフPC作業", 5400, "応用数学B", "過去問", "2024"),
        "2026-08-09T19:00:00": entry("食事・休憩", 2400),
    })
    days = offpc.collect(tmp_path, "応用数学B", "過去問", "2024")
    assert offpc.totals(days)["net_hours"] == 1.5


def test_日をまたいだ続きを1件に畳む(tmp_path):
    write(tmp_path, "2026-08-09",
          {"2026-08-09T13:00:00": entry("オフPC作業", 3600, "情報理論", "参考書", "3章")})
    write(tmp_path, "2026-08-10",
          {"2026-08-10T10:00:00": entry("オフPC作業", 1800, "情報理論", "参考書", "3章")})
    summed = offpc.totals(offpc.collect(tmp_path, "情報理論", "参考書", "3章"))
    assert summed["net_hours"] == 1.5
    assert summed["sessions"] == 2      # 2日かけた、が消えない
    assert (summed["first"], summed["last"]) == ("2026-08-09", "2026-08-10")


def test_対象が違えば混ぜない(tmp_path):
    # 章や年度をまたいで合算すると「1本あたり」が壊れる
    write(tmp_path, "2026-08-09", {
        "2026-08-09T13:00:00": entry("オフPC作業", 3600, "情報理論", "参考書", "3章"),
        "2026-08-09T16:00:00": entry("オフPC作業", 3600, "情報理論", "参考書", "4章"),
    })
    assert offpc.totals(offpc.collect(tmp_path, "情報理論", "参考書", "3章"))["net_hours"] == 1.0


def test_対象を省けば種別ぜんぶを拾う(tmp_path):
    write(tmp_path, "2026-08-09", {
        "2026-08-09T13:00:00": entry("オフPC作業", 3600, "情報理論", "参考書", "3章"),
        "2026-08-09T16:00:00": entry("オフPC作業", 3600, "情報理論", "参考書", "4章"),
    })
    assert offpc.totals(offpc.collect(tmp_path, "情報理論", "参考書"))["net_hours"] == 2.0


def test_オフPCではnetと在席を分けない(tmp_path):
    write(tmp_path, "2026-08-09",
          {"2026-08-09T13:00:00": entry("オフPC作業", 3600, "情報理論", "参考書")})
    summed = offpc.totals(offpc.collect(tmp_path, "情報理論", "参考書"))
    assert summed["net_hours"] == summed["wall_hours"]


def test_こなした量を合計する(tmp_path):
    write(tmp_path, "2026-08-09", {
        "2026-08-09T13:00:00": entry("オフPC作業", 3600, "情報理論", "参考書", "3章", units=12),
        "2026-08-09T16:00:00": entry("オフPC作業", 1800, "情報理論", "参考書", "3章", units=8),
    })
    assert offpc.totals(offpc.collect(tmp_path, "情報理論", "参考書", "3章"))["units"] == 20


def test_期間で絞れる(tmp_path):
    write(tmp_path, "2026-08-01",
          {"2026-08-01T13:00:00": entry("オフPC作業", 3600, "情報理論", "参考書")})
    write(tmp_path, "2026-08-09",
          {"2026-08-09T13:00:00": entry("オフPC作業", 1800, "情報理論", "参考書")})
    days = offpc.collect(tmp_path, "情報理論", "参考書", since="2026-08-05")
    assert offpc.totals(days)["net_hours"] == 0.5


def test_記録が無ければ何も返さない(tmp_path):
    assert offpc.totals(offpc.collect(tmp_path, "情報理論", "参考書")) is None


def test_古い文字列だけのラベルを読み飛ばす(tmp_path):
    # 中身を持たない初期の記録が混ざっていても壊れない
    write(tmp_path, "2026-08-09", {"2026-08-09T13:00:00": "食事・休憩"})
    assert offpc.collect(tmp_path, "情報理論", "参考書") == []
