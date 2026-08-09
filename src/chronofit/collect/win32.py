"""Windows のフォアグラウンド状態を ctypes だけで読む（依存パッケージなし）。

ここに閉じ込めるのは「OS に問い合わせる」ことだけ。サンプリング間隔・スパン化・
書き出しは daemon.py の責務。移植するときはこのファイルだけ差し替えれば済む。
"""
import ctypes
import ctypes.wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MAX_PATH_LONG = 32768

# 画面ロック中はこれらが前景に来る。作業ではなく在席境界として扱う。
LOCK_PROCESSES = frozenset({"LockApp.exe", "logonui.exe"})

user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)
]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetTickCount64.restype = ctypes.c_ulonglong


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


def idle_seconds():
    """最後のキーボード/マウス入力からの経過秒。取得できなければ None。

    「前景は PDF のままだが本人は離席していた」を作業時間から除くための唯一の手掛かり。
    """
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    # dwTime は 32bit の GetTickCount 基準。49.7日で一周するので下位32bitで引く。
    elapsed = (kernel32.GetTickCount64() & 0xFFFFFFFF) - info.dwTime
    return (elapsed & 0xFFFFFFFF) / 1000.0


def _process_name(hwnd):
    """ウィンドウを所有するプロセスの実行ファイル名。取れなければ None。"""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        size = wt.DWORD(MAX_PATH_LONG)
        path = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            return None
        return path.value.rsplit("\\", 1)[-1]
    finally:
        kernel32.CloseHandle(handle)


def foreground():
    """(プロセス名, ウィンドウタイトル) を返す。前景が無ければ (None, None)。

    タイトルは作業の *対象* を持つ（`応用数学A_2024_期末.pdf - SumatraPDF` 等）。
    科目・種別・何本目を後から復元できるかはここの情報量で決まる。
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return _process_name(hwnd), buf.value


def is_locked(process_name):
    """画面ロック中か（前景プロセス名から判定）。"""
    return process_name in LOCK_PROCESSES
