"""無線 adb の接続を、USB を挿さずに戻す。

PC からスマホへは `adb tcpip <port>` で開いた待受に Tailscale 越しでつなぐ。この待受は
端末の再起動（と adbd の再起動）で閉じ、開け直すには認証済みの adb 接続から
`adb tcpip` を打つしかない。USB を使わない認証済みの経路はワイヤレスデバッグ
（TLS。ペア済みなら鍵だけで入れる）で、同じ LAN にいれば mDNS に `_adb-tls-connect` として出る。

- 待受が閉じていたら: mDNS で端末を探す → TLS でつなぐ → `adb tcpip <port>` → つなぎ直す
- つながっている間: 家の LAN にいるのにワイヤレスデバッグが切れていたら入れ直す。
  端末は Wi-Fi を離れると自分で切るので、戻しておかないと次に待受が閉じたとき詰む
  （切るのは端末に任せる。こちらからは入れるだけ）

mDNS の広告は LAN の誰でも出せる。試す宛先は PC と同じネットワークのものに絞り、数も抑える。
TLS はペア済みの鍵が無いと通らないので、偽の宛先につないでも何も読めない。

adb の呼び出しは `run(*args) -> (returncode, stdout)` として受け取る（テストで差し替える）。
"""
import ipaddress
import re
import socket
import time

TLS_SERVICE = "_adb-tls-connect._tcp"
RESTART_SEC = 4         # `adb tcpip` の後、adbd が立ち直って待受を開くまで
MAX_ENDPOINTS = 2       # 1回に試す mDNS の宛先の数
MIN_PREFIX = 24         # これより広いネットワークは「家の LAN」と見なさない
_ENDPOINT = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}:\d{1,5}")
_DEVICE = re.compile(r"[A-Za-z0-9]+")
_INET = re.compile(r"inet (\d{1,3}(?:\.\d{1,3}){3})/(\d{1,2})")

# `adb connect` の出力 -> ログに残す理由。宛先はログに書かないので、決まった文言だけを返す
REFUSED = "待受が閉じている"
_FAILURES = (
    (REFUSED, ("(10061)", "Connection refused")),
    ("端末に届かない", ("(10060)", "(10065)", "timed out", "No route to host")),
    ("未認証", ("failed to authenticate", "unauthorized")),
)


def connect_failure(output):
    """`adb connect` の出力から失敗の理由を返す。つながっていれば None。"""
    text = output or ""
    if "connected to" in text:          # "connected to" / "already connected to"
        return None
    for reason, marks in _FAILURES:
        if any(mark in text for mark in marks):
            return reason
    return "接続できない"


def _same_lan(address, addresses):
    """`address` が、この PC のどれかのアドレスと同じ /MIN_PREFIX にあるか。"""
    network = ipaddress.ip_network(f"{address}/{MIN_PREFIX}", strict=False)
    return any(ipaddress.ip_address(own) in network for own in addresses)


def tls_endpoints(run, device, addresses):
    """mDNS に出ている、この端末のワイヤレスデバッグの宛先（host:port）。PC と同じ LAN のものだけ。"""
    if not device or not _DEVICE.fullmatch(device):
        return []
    code, out = run("mdns", "services")
    if code != 0:
        return []
    found = []
    for line in out.splitlines():
        parts = line.split()
        if (len(parts) >= 3 and parts[1].rstrip(".") == TLS_SERVICE
                and parts[0].startswith(f"adb-{device}-") and _ENDPOINT.fullmatch(parts[2])
                and _same_lan(parts[2].rsplit(":", 1)[0], addresses)):
            found.append(parts[2])
    return found[:MAX_ENDPOINTS]


def reopen_tcpip(run, serial, device, visible, addresses, wait=time.sleep):
    """閉じた待受 `serial`（host:port）を、ワイヤレスデバッグ経由で開け直す。開けたら True。

    `visible()` はいま状態が device の端末の一覧を返す。`adb tcpip` は1回だけ打つ
    （打てば adbd が再起動する。別の宛先でもう一度打つと、立ち上がりかけた待受をまた落とす）。
    """
    port = serial.rsplit(":", 1)[1]
    for endpoint in tls_endpoints(run, device, addresses):
        _, out = run("connect", endpoint)
        if connect_failure(out) or endpoint not in visible():
            continue
        code, _ = run("-s", endpoint, "tcpip", port)
        # 成否にかかわらず TLS の接続は使い終わり（成功なら adbd の再起動で死んでいる）
        if code != 0:
            run("disconnect", endpoint)
            continue
        wait(RESTART_SEC)
        run("disconnect", endpoint)
        run("disconnect", serial)
        run("connect", serial)
        return serial in visible()
    return False


def local_addresses():
    """この PC の IPv4 アドレス（ループバックを除く）。"""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({info[4][0] for info in infos if not info[4][0].startswith("127.")})


def on_home_lan(run, serial, addresses):
    """端末の Wi-Fi が、この PC と同じ LAN にいるか。"""
    code, out = run("-s", serial, "shell", "ip", "-4", "-o", "addr", "show", "wlan0")
    match = _INET.search(out) if code == 0 else None
    if not match:
        return False
    # ネットマスクは端末の申告。広すぎるもの（よその DHCP が配った /16 など）は信じない
    if int(match[2]) < MIN_PREFIX:
        return False
    network = ipaddress.ip_network(f"{match[1]}/{match[2]}", strict=False)
    return any(ipaddress.ip_address(address) in network for address in addresses)


def enable_wireless_debugging_at_home(run, serial, addresses):
    """ワイヤレスデバッグが切れていて、端末が家の LAN にいれば入れ直す。入れ直したら True。

    家の外では入れない。信頼していないネットワークで入れると、端末に許可を求める画面が出る。
    """
    code, out = run("-s", serial, "shell", "settings", "get", "global", "adb_wifi_enabled")
    if code != 0 or out.strip() == "1":
        return False
    if not on_home_lan(run, serial, addresses):
        return False
    code, _ = run("-s", serial, "shell", "settings", "put", "global", "adb_wifi_enabled", "1")
    return code == 0
