"""無線 adb の待受を USB 無しで開け直す処理のテスト。宛先・シリアル番号はすべて架空。"""
from chronofit.sources import adb_link, phone

SERIAL = "100.64.0.9:5555"
TLS = "192.168.10.20:41234"
DEVICE = "ABC123"
HOME = ["192.168.10.5", "100.64.0.1"]     # この PC のアドレス
MDNS = (f"List of discovered mdns services\n"
        f"adb-{DEVICE}-xYz\t_adb-tls-connect._tcp.\t{TLS}\n"
        f"adb-OTHER9-qQq\t_adb-tls-connect._tcp\t192.168.10.30:40000\n"
        f"adb-{DEVICE}-far\t_adb-tls-connect._tcp\t10.9.8.7:40001\n")
REFUSED = f"cannot connect to {SERIAL}: 対象のコンピューターによって拒否されたため、接続できませんでした。 (10061)"


class FakePhone:
    """閉じた待受・ワイヤレスデバッグ・Wi-Fi を持つ架空の端末と adb。"""

    def __init__(self, tcp_open=False, paired=True, wifi_debug="1", wlan="192.168.10.20/24",
                 tcpip_code=0, reopens=True):
        self.tcp_open, self.paired, self.wifi_debug, self.wlan = tcp_open, paired, wifi_debug, wlan
        self.tcpip_code, self.reopens = tcpip_code, reopens
        self.connected, self.calls = set(), []

    def __call__(self, *args, adb="adb"):
        self.calls.append(args)
        head = args[0]
        if head == "devices":
            rows = "".join(f"{s}\tdevice\n" for s in sorted(self.connected))
            return 0, "List of devices attached\n" + rows
        if head == "mdns":
            return 0, MDNS if self.wifi_debug == "1" else "List of discovered mdns services\n"
        if head == "disconnect":
            self.connected.discard(args[1])
            return 0, ""
        if head == "connect":
            return self._connect(args[1])
        if head == "-s":
            return self._shell(args[1], args[2:])
        return 1, ""

    def _connect(self, target):
        if target == TLS:
            if not self.paired:
                return 1, f"failed to authenticate to {TLS}"
            self.connected.add(TLS)
            return 0, f"connected to {TLS}"
        if self.tcp_open:
            self.connected.add(target)
            return 0, f"connected to {target}"
        return 1, REFUSED

    def _shell(self, serial, rest):
        if rest == ("tcpip", "5555"):
            if self.tcpip_code:
                return self.tcpip_code, "error: closed"
            self.tcp_open = self.reopens
            self.connected.discard(serial)      # adbd が再起動して TLS が切れる
            return 0, "restarting in TCP mode port: 5555"
        if rest[:2] == ("shell", "dumpsys"):
            return 0, "Last 24 hour events\n"
        if rest[1:] == ("settings", "get", "global", "adb_wifi_enabled"):
            return 0, f"{self.wifi_debug}\n"
        if rest[1:] == ("settings", "put", "global", "adb_wifi_enabled", "1"):
            self.wifi_debug = "1"
            return 0, ""
        if rest[1:4] == ("ip", "-4", "-o"):
            return (0, f"30: wlan0    inet {self.wlan} brd x scope global wlan0\n") if self.wlan else (1, "")
        return 1, ""


def _no_wait(monkeypatch):
    monkeypatch.setattr(adb_link.time, "sleep", lambda _sec: None)
    monkeypatch.setattr(adb_link, "local_addresses", lambda: HOME)


def _tcpip_calls(fake):
    return [call for call in fake.calls if call[-2:] == ("tcpip", "5555")]


def test_接続の失敗を理由に分ける():
    assert adb_link.connect_failure(REFUSED) == adb_link.REFUSED
    assert adb_link.connect_failure("cannot connect to h:5555: (10060)") == "端末に届かない"
    assert adb_link.connect_failure("failed to authenticate to h:5555") == "未認証"
    assert adb_link.connect_failure("already connected to h:5555") is None
    assert adb_link.connect_failure("") == "接続できない"


def test_mDNSからこの端末のワイヤレスデバッグだけを拾う():
    fake = FakePhone()
    # 別の端末と、PC と違うネットワークに出ている広告は拾わない。末尾のドットは許す
    assert adb_link.tls_endpoints(fake, DEVICE, HOME) == [TLS]
    assert adb_link.tls_endpoints(fake, DEVICE, ["172.16.0.2"]) == []
    assert adb_link.tls_endpoints(fake, "-bad", HOME) == []
    assert adb_link.tls_endpoints(fake, None, HOME) == []


def test_mDNSの宛先は数を絞る():
    many = "".join(f"adb-{DEVICE}-{i}\t_adb-tls-connect._tcp\t192.168.10.{i}:4000\n"
                   for i in range(30, 40))
    found = adb_link.tls_endpoints(lambda *_: (0, many), DEVICE, HOME)
    assert len(found) == adb_link.MAX_ENDPOINTS


def test_tcpipが失敗したらTLSの接続を残さない(monkeypatch):
    _no_wait(monkeypatch)
    fake = FakePhone(tcpip_code=1)
    monkeypatch.setattr(phone, "_adb", fake)
    import pytest
    with pytest.raises(RuntimeError, match=adb_link.REFUSED):
        phone.fetch([SERIAL], device=DEVICE)
    assert TLS not in fake.connected


def test_開け直しても見えなければtcpipを打ち直さない(monkeypatch):
    _no_wait(monkeypatch)
    fake = FakePhone(reopens=False)
    monkeypatch.setattr(phone, "_adb", fake)
    notes = []
    import pytest
    with pytest.raises(RuntimeError, match=adb_link.REFUSED):
        phone.fetch([SERIAL], device=DEVICE, notes=notes)
    assert len(_tcpip_calls(fake)) == 1      # adbd を二度落とさない
    assert "reopened" not in notes


def test_失敗の理由は重ねて書かない(monkeypatch):
    fake = FakePhone()
    monkeypatch.setattr(phone, "_adb", fake)
    import pytest
    with pytest.raises(RuntimeError) as error:
        phone.fetch([SERIAL, "100.64.0.9:5556"])
    assert str(error.value).count(adb_link.REFUSED) == 1


def test_閉じた待受をワイヤレスデバッグ経由で開け直す(monkeypatch):
    _no_wait(monkeypatch)
    fake = FakePhone()
    monkeypatch.setattr(phone, "_adb", fake)
    notes = []
    assert phone.fetch([SERIAL], device=DEVICE, notes=notes)[0] == SERIAL
    assert notes == ["reopened"]
    assert ("-s", TLS, "tcpip", "5555") in fake.calls
    assert TLS not in fake.connected         # 死んだ TLS の接続を残さない


def test_端末の指定が無ければ開け直さず_理由を知らせる(monkeypatch):
    fake = FakePhone()
    monkeypatch.setattr(phone, "_adb", fake)
    import pytest
    with pytest.raises(RuntimeError, match=adb_link.REFUSED):
        phone.fetch([SERIAL])
    assert not any(call[0] == "mdns" for call in fake.calls)


def test_届かないだけのときはadbdを再起動しない(monkeypatch):
    fake = FakePhone()

    def unreachable(*args, adb="adb"):
        if args[0] == "connect" and args[1] == SERIAL:
            return 1, f"cannot connect to {SERIAL}: (10060)"
        return fake(*args)

    monkeypatch.setattr(phone, "_adb", unreachable)
    import pytest
    with pytest.raises(RuntimeError, match="端末に届かない"):
        phone.fetch([SERIAL], device=DEVICE)
    assert not any(call[-2:] == ("tcpip", "5555") for call in fake.calls)


def test_ペアしていなければ開け直せない(monkeypatch):
    _no_wait(monkeypatch)
    fake = FakePhone(paired=False)
    monkeypatch.setattr(phone, "_adb", fake)
    notes = []
    import pytest
    with pytest.raises(RuntimeError, match=adb_link.REFUSED):
        phone.fetch([SERIAL], device=DEVICE, notes=notes)
    assert not any(call[-2:] == ("tcpip", "5555") for call in fake.calls)


def test_家のLANにいればワイヤレスデバッグを入れ直す(monkeypatch):
    fake = FakePhone(tcp_open=True, wifi_debug="0")
    monkeypatch.setattr(phone, "_adb", fake)
    monkeypatch.setattr(adb_link, "local_addresses", lambda: HOME)
    assert phone.keep_wireless_debugging(SERIAL) is True
    assert fake.wifi_debug == "1"
    assert phone.keep_wireless_debugging(SERIAL) is False      # 入っていれば触らない


def test_家の外ではワイヤレスデバッグを入れない(monkeypatch):
    monkeypatch.setattr(adb_link, "local_addresses", lambda: ["192.168.10.5"])
    # 広すぎるネットマスク（/16）は、PC を含んでいても家と見なさない
    for fake in (FakePhone(tcp_open=True, wifi_debug="0", wlan="10.1.2.3/24"),
                 FakePhone(tcp_open=True, wifi_debug="0", wlan="192.168.99.9/16"),
                 FakePhone(tcp_open=True, wifi_debug="0", wlan=None)):
        monkeypatch.setattr(phone, "_adb", fake)
        assert phone.keep_wireless_debugging(SERIAL) is False
        assert fake.wifi_debug == "0"


def test_USB接続の端末ではワイヤレスデバッグに触らない(monkeypatch):
    fake = FakePhone(wifi_debug="0")
    monkeypatch.setattr(phone, "_adb", fake)
    assert phone.keep_wireless_debugging("ABC123") is False
    assert fake.calls == []
