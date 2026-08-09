"""いま音を出しているプロセスを WASAPI から読む（ctypes だけ・依存パッケージなし）。

**なぜ要るのか。** 在席の判定を「入力があったか」だけでやると、動画や講義映像を観ている
時間が丸ごと離席として落ちる。手は動いていないが本人は画面の前にいて、その時間は
確かに消費されている。タイトルに `youtube` が入っていたら動画とみなす、という当て方も
できるが、それは「開いていた」ことしか言えない — タブを開いたまま寝た夜と、
1時間観ていた夜が同じ扱いになる。

そこで**再生されている音そのもの**を見る。オーディオセッションのピークメーターが
0 でなければ、その瞬間に音が出ている。止めれば 0 になるので、放置されたタブは自動的に
落ちる。判定はプロセス名で返す — ブラウザの音声は別プロセス（Chromium なら
Audio Service の子プロセス）から出るので PID では前景と一致しないため。

取得できない環境（音声デバイス無し・COM 初期化失敗）では None を返す。呼び側は
「受動時間は測れなかった」として扱い、収集そのものは止めない。
"""
import ctypes
from ctypes import POINTER, byref, c_float, c_int, c_void_p
from ctypes import wintypes as wt

ole32 = ctypes.WinDLL("ole32")
ole32.CLSIDFromString.argtypes = [wt.LPCWSTR, c_void_p]

CLSCTX_ALL = 0x17
COINIT_APARTMENTTHREADED = 0x2
E_ALREADY_INITIALIZED = -2147417850   # RPC_E_CHANGED_MODE 以外は無視してよい
AUDIO_SESSION_ACTIVE = 1
PEAK_MIN = 0.0005          # これ以下は無音とみなす（完全な 0 は稀なので閾値で見る）

_CLSID_MMDeviceEnumerator = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
_IID_IMMDeviceEnumerator = "{A95664D2-9614-4F35-A746-DE8DB63617E6}"
_IID_IAudioSessionManager2 = "{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}"
_IID_IAudioSessionControl2 = "{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}"
_IID_IAudioMeterInformation = "{C02216F6-8C67-4B5B-9D00-D008E73E0064}"

# vtable の番号。COM のインタフェースは継承分だけ前に詰まる。
_QUERY_INTERFACE, _RELEASE = 0, 2
_GET_DEFAULT_ENDPOINT = 4          # IMMDeviceEnumerator
_ACTIVATE = 3                      # IMMDevice
_GET_SESSION_ENUMERATOR = 5        # IAudioSessionManager2
_GET_COUNT, _GET_SESSION = 3, 4    # IAudioSessionEnumerator
_GET_STATE = 3                     # IAudioSessionControl
_GET_PROCESS_ID = 14               # IAudioSessionControl2
_GET_PEAK_VALUE = 3                # IAudioMeterInformation


class _Guid(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text):
    value = _Guid()
    if ole32.CLSIDFromString(text, byref(value)) != 0:
        raise OSError(f"GUID を解釈できない: {text}")
    return value


def _method(pointer, index, *argtypes):
    """vtable の index 番目を呼べる関数にする。HRESULT は ctypes が失敗時に例外へ変える。"""
    vtable = ctypes.cast(pointer, POINTER(POINTER(c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)
    return prototype(vtable[index])


def _release(pointer):
    if not pointer:
        return
    vtable = ctypes.cast(pointer, POINTER(POINTER(c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)
    prototype(vtable[_RELEASE])(pointer)


def _query(pointer, iid_text):
    """QueryInterface。持っていなければ None（例外にしない）。"""
    iid = _guid(iid_text)
    out = c_void_p()
    try:
        _method(pointer, _QUERY_INTERFACE, c_void_p, c_void_p)(
            pointer, byref(iid), byref(out))
    except OSError:
        return None
    return out


def initialize():
    """呼び出しスレッドで COM を初期化する。既に初期化済みなら何もしない。"""
    result = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    return result >= 0 or result == E_ALREADY_INITIALIZED


def _session_manager():
    enumerator = c_void_p()
    clsid, iid = _guid(_CLSID_MMDeviceEnumerator), _guid(_IID_IMMDeviceEnumerator)
    ole32.CoCreateInstance(byref(clsid), None, CLSCTX_ALL, byref(iid),
                           byref(enumerator))
    if not enumerator:
        return None, None
    device = c_void_p()
    try:
        # eRender(0) / eConsole(0) = 既定の再生デバイス
        _method(enumerator, _GET_DEFAULT_ENDPOINT, c_int, c_int, c_void_p)(
            enumerator, 0, 0, byref(device))
    except OSError:
        _release(enumerator)
        return None, None
    manager = c_void_p()
    manager_iid = _guid(_IID_IAudioSessionManager2)
    try:
        _method(device, _ACTIVATE, c_void_p, wt.DWORD, c_void_p, c_void_p)(
            device, byref(manager_iid), CLSCTX_ALL, None, byref(manager))
    finally:
        _release(device)
        _release(enumerator)
    return manager, manager_iid


def _peaks_by_pid(manager):
    sessions = c_void_p()
    _method(manager, _GET_SESSION_ENUMERATOR, c_void_p)(manager, byref(sessions))
    if not sessions:
        return {}
    peaks = {}
    try:
        count = c_int()
        _method(sessions, _GET_COUNT, c_void_p)(sessions, byref(count))
        for position in range(count.value):
            control = c_void_p()
            _method(sessions, _GET_SESSION, c_int, c_void_p)(
                sessions, position, byref(control))
            if not control:
                continue
            try:
                state = wt.DWORD()
                _method(control, _GET_STATE, c_void_p)(control, byref(state))
                if state.value != AUDIO_SESSION_ACTIVE:
                    continue
                peak = _peak_of(control)
                if peak is None or peak < PEAK_MIN:
                    continue
                pid = _pid_of(control)
                if pid:
                    peaks[pid] = max(peaks.get(pid, 0.0), peak)
            finally:
                _release(control)
    finally:
        _release(sessions)
    return peaks


def _peak_of(control):
    meter = _query(control, _IID_IAudioMeterInformation)
    if not meter:
        return None
    try:
        value = c_float()
        _method(meter, _GET_PEAK_VALUE, c_void_p)(meter, byref(value))
        return value.value
    except OSError:
        return None
    finally:
        _release(meter)


def _pid_of(control):
    control2 = _query(control, _IID_IAudioSessionControl2)
    if not control2:
        return None
    try:
        pid = wt.DWORD()
        _method(control2, _GET_PROCESS_ID, c_void_p)(control2, byref(pid))
        return pid.value
    except OSError:
        return None
    finally:
        _release(control2)


def playing(name_of_pid):
    """いま音を出しているプロセス名の集合。取得できなければ None。

    `name_of_pid` は PID から実行ファイル名を引く関数。ブラウザの音は子プロセスから
    出るので、前景との突き合わせは PID ではなく名前で行う。
    """
    manager = None
    try:
        manager, _ = _session_manager()
        if not manager:
            return None
        names = set()
        for pid in _peaks_by_pid(manager):
            name = name_of_pid(pid)
            if name:
                names.add(name)
        return names
    except OSError:
        return None
    finally:
        _release(manager)
