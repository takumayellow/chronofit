"""Chromium 系ブラウザの履歴を読み、消える前にスナップショットする。

なぜスナップショットが要るか: Chromium は履歴を自動で刈る。実測（2026-08-09）で
Vivaldi の History に残っていたのは **16日分** だけだった。日次で写しておかないと、
過去の接点ログは取り返しがつかない形で蒸発し続ける。

読み取りはロック中でも一時コピー経由で行うので、ブラウザを閉じる必要はない。
"""
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

# Chromium の visit_time は 1601-01-01 UTC からのマイクロ秒
CHROME_EPOCH = datetime(1601, 1, 1)

# ブラウザ名 -> User Data ディレクトリの相対位置（LOCALAPPDATA 起点）
CHROMIUM_BROWSERS = {
    "vivaldi": Path("Vivaldi") / "User Data",
    "chrome": Path("Google") / "Chrome" / "User Data",
    "edge": Path("Microsoft") / "Edge" / "User Data",
    "brave": Path("BraveSoftware") / "Brave-Browser" / "User Data",
}


def to_datetime(chrome_time):
    """Chromium のタイムスタンプを naive な datetime へ。"""
    return CHROME_EPOCH + timedelta(microseconds=chrome_time)


def find_histories(local_app_data):
    """(ブラウザ名, プロファイル名, History パス) を列挙する。"""
    root = Path(local_app_data)
    found = []
    for browser, relative in CHROMIUM_BROWSERS.items():
        user_data = root / relative
        if not user_data.is_dir():
            continue
        for profile in sorted(user_data.iterdir()):
            history = profile / "History"
            if history.is_file():
                found.append((browser, profile.name, history))
    return found


def _open_copy(history_path):
    """ロック中でも読めるよう一時コピーを開く。呼び出し側が close する。"""
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    shutil.copy2(history_path, handle.name)
    return sqlite3.connect(handle.name), Path(handle.name)


def read_visits(history_path, since=None):
    """[(訪問時刻, URL, タイトル), ...] を時刻順で返す。"""
    connection, temp_path = _open_copy(history_path)
    try:
        query = (
            "SELECT v.visit_time, u.url, u.title "
            "FROM visits v JOIN urls u ON u.id = v.url "
        )
        params = ()
        if since is not None:
            boundary = int((since - CHROME_EPOCH).total_seconds() * 1_000_000)
            query += "WHERE v.visit_time >= ? "
            params = (boundary,)
        query += "ORDER BY v.visit_time"
        return [(to_datetime(row[0]), row[1], row[2] or "")
                for row in connection.execute(query, params)]
    finally:
        connection.close()
        temp_path.unlink(missing_ok=True)


def snapshot(history_path, destination):
    """History をそのままコピーして保全する。書き込んだパスを返す。

    パースせず原本を残すのは、後からスキーマ解釈を変えたくなっても
    やり直せるようにするため。
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(history_path, destination)
    return destination
