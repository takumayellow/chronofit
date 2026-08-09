"""前景ウィンドウとアイドルを定期サンプリングし、変化点でスパンに畳んで JSONL に書く。

設計の勘所:

- **人間の操作をゼロにする。** 起動はログオン時のタスクスケジューラ任せ。開始/停止の
  ポチポチが要る仕組みは過去3回とも死んでいる（docs/DESIGN.md §0）。
- **サンプルではなくスパンで持つ。** 15秒ごとの点を全部書くと1日5760行になるうえ、
  後段が結局畳み直すことになる。前景が変わった時だけ1行にする。
- **在席は「前景が何か」ではなく「入力があったか」で数える。** active_sec は
  アイドルがしきい値未満だったサンプル数から出す。PDF を開いたまま離席した分は
  sec には乗るが active_sec には乗らない。
- **ただし入力の無い在席が全部「離席」ではない。** 動画・講義映像を観ている間は手が
  動かない。前景プロセスが実際に音を鳴らしていたサンプルを media_sec として別に数え、
  「手は止まっているが観ていた時間」を離席から切り離す（判定は collect/audio.py）。
- **時計の飛びをスリープとして検出する。** サンプル間隔が想定を大きく超えたら、
  その間はスリープ/シャットダウンなので span ではなく gap として記録する。
  これがオフPC時間の主要な入口になる。
"""
import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

from .. import paths
from ..model.title import normalize
from . import audio, win32

INTERVAL_SEC = 15.0        # サンプリング間隔
ACTIVE_IDLE_SEC = 60.0     # これ未満のアイドルなら「入力していた」とみなす
MAX_SPAN_SEC = 300.0       # 同じ前景でもこの長さで一旦書き出す（クラッシュ時の損失上限）
GAP_FACTOR = 4.0           # 間隔がこの倍数を超えたらスリープ扱い

LOCKED_KEY = ("__locked__", "")
NO_WINDOW_KEY = ("__none__", "")

# タイトルを記録しないプロセス（機密が題名に出るもの）。時間だけ残す。
TITLE_DENYLIST = frozenset({
    "KeePass.exe", "KeePassXC.exe", "1Password.exe", "Bitwarden.exe",
})


def _now():
    return datetime.now(timezone.utc).astimezone()


def _iso(moment):
    return moment.isoformat(timespec="seconds")


def _log_path(moment):
    """JST 等ローカル日付でファイルを分ける（人間が日単位で見るため）。"""
    return paths.raw_dir() / f"{moment:%Y-%m-%d}.jsonl"


def _append(record, moment):
    path = _log_path(moment)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _media_playing(process):
    """前景プロセスがいま音を鳴らしているか。判定できなければ False。

    名前で突き合わせるのは、ブラウザの音声が別プロセスから出るため（audio.py）。
    """
    names = audio.playing(win32.process_name)
    return bool(names) and process in names


def _sample():
    """現在の (キー, タイトル, アイドル秒, 再生中か)。キーはスパンの同一性判定に使う。"""
    process, title = win32.foreground()
    idle = win32.idle_seconds()
    if process is None:
        return NO_WINDOW_KEY, "", idle, False
    if win32.is_locked(process):
        return LOCKED_KEY, "", idle, False
    title = "" if process in TITLE_DENYLIST else normalize(title)
    return (process, title), title, idle, _media_playing(process)


class _Span:
    """畳み中のスパン。書き出す直前まで JSON にしない。"""

    def __init__(self, key, title, started, interval):
        self.key = key
        self.title = title
        self.started = started
        self.ended = started
        self.interval = interval
        self.samples = 0
        self.active_samples = 0
        self.media_samples = 0
        self.last_idle = None

    def add(self, idle, moment, media=False):
        self.ended = moment
        self.samples += 1
        self.last_idle = idle
        if idle is not None and idle < ACTIVE_IDLE_SEC:
            self.active_samples += 1
        if media:
            self.media_samples += 1

    def seconds(self):
        """1サンプル = interval 秒ぶんを代表させる。

        end - start だとサンプル1個のスパンが 0 秒になり、合計が実時間に一致しない。
        active_sec と同じ数え方に揃えることで、両者の比（= slack 率）が意味を持つ。
        """
        return self.samples * self.interval

    def to_record(self):
        return {
            "t": "span",
            "start": _iso(self.started),
            "end": _iso(self.ended),
            "sec": round(self.seconds(), 1),
            # active は「入力があったサンプル数 × 間隔」。前景時間との差が離席・放置。
            "active_sec": round(self.active_samples * self.interval, 1),
            # 前景が音を鳴らしていた時間。入力が無くても観ていた時間の証拠になる。
            "media_sec": round(self.media_samples * self.interval, 1),
            "proc": self.key[0],
            "title": self.title,
            "idle_end": None if self.last_idle is None else round(self.last_idle, 1),
        }


def _acquire_lock():
    """多重起動を防ぐ。既存 PID が生きていれば False。"""
    lock = paths.data_root() / "collector.pid"
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            pid = None
        if pid and _pid_alive(pid):
            return None
    lock.write_text(str(os.getpid()), encoding="utf-8")
    return lock


def _pid_alive(pid):
    """Windows で PID が生きているかを標準ライブラリだけで判定する。"""
    import ctypes
    SYNCHRONIZE = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _install_signal_handlers():
    """SIGTERM 等でも finally を通す。

    ログオフ・シャットダウン・タスクスケジューラの停止はいずれも SIGTERM 相当で来る。
    素通しさせると畳み中のスパン（最大 MAX_SPAN_SEC 分）が毎回失われる。
    """
    def raise_interrupt(_signum, _frame):
        raise KeyboardInterrupt

    for name in ("SIGTERM", "SIGBREAK", "SIGINT"):
        handler = getattr(signal, name, None)
        if handler is not None:
            try:
                signal.signal(handler, raise_interrupt)
            except (ValueError, OSError):
                pass  # メインスレッド以外では設定できない。致命的ではない。


def run(interval=INTERVAL_SEC, verbose=False, duration=None):
    """サンプリングループ本体。終了コードを返す。

    duration を指定すると その秒数で自然終了する（スモークテスト用）。
    """
    paths.ensure(paths.raw_dir())
    _install_signal_handlers()
    audio.initialize()   # 音声セッションを読むための COM 初期化。失敗しても収集は続ける
    lock = _acquire_lock()
    if lock is None:
        print("collector は既に起動している。二重起動しない。", file=sys.stderr)
        return 1

    started = _now()
    _append({"t": "start", "at": _iso(started), "interval": interval}, started)
    if verbose:
        print(f"chronofit collector 起動 interval={interval}s -> {paths.raw_dir()}")

    span = None
    previous = time.monotonic()
    deadline = None if duration is None else time.monotonic() + duration
    try:
        while deadline is None or time.monotonic() < deadline:
            time.sleep(interval)
            moment = _now()
            elapsed = time.monotonic() - previous
            previous = time.monotonic()

            # 時計が飛んだ = スリープ/休止。span を閉じて gap を残す。
            if elapsed > interval * GAP_FACTOR:
                if span is not None:
                    _append(span.to_record(), span.started)
                    span = None
                gap_start = moment - timedelta(seconds=elapsed)
                _append({"t": "gap", "start": _iso(gap_start), "end": _iso(moment),
                         "sec": round(elapsed, 1), "reason": "clock_jump"}, gap_start)
                if verbose:
                    print(f"  gap {elapsed / 60:.0f}min (sleep/resume)")
                continue

            try:
                key, title, idle, media = _sample()
            except OSError as error:  # 一過性の API 失敗でループを死なせない
                if verbose:
                    print(f"  sample 失敗（継続）: {error}")
                continue

            rotated = span is not None and span.started.date() != moment.date()
            too_long = span is not None and span.seconds() >= MAX_SPAN_SEC
            if span is not None and (key != span.key or rotated or too_long):
                _append(span.to_record(), span.started)
                span = None

            if span is None:
                span = _Span(key, title, moment, interval)
            span.add(idle, moment, media)

            if verbose:
                mark = "*" if idle is not None and idle < ACTIVE_IDLE_SEC else (
                    "~" if media else " ")
                print(f"  [{moment:%H:%M:%S}]{mark} {key[0]:24.24} {title[:56]}")
    except KeyboardInterrupt:
        pass
    finally:
        if span is not None:
            _append(span.to_record(), span.started)
        stopped = _now()
        _append({"t": "stop", "at": _iso(stopped)}, stopped)
        try:
            lock.unlink()
        except OSError:
            pass
    return 0


def add_arguments(parser):
    parser.add_argument("--interval", type=float, default=INTERVAL_SEC,
                        help=f"サンプリング間隔 秒（既定 {INTERVAL_SEC:.0f}）")
    parser.add_argument("--verbose", action="store_true", help="サンプルを標準出力に流す")
    parser.add_argument("--duration", type=float, default=None,
                        help="指定秒で終了する（動作確認用。既定は無期限）")


def main(argv=None):
    parser = argparse.ArgumentParser(description="chronofit 端末イベント収集デーモン")
    add_arguments(parser)
    args = parser.parse_args(argv)
    return run(interval=args.interval, verbose=args.verbose, duration=args.duration)


if __name__ == "__main__":
    sys.exit(main())
