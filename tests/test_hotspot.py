"""The hub's hotspot: credentials and QR, channel rules, route choice, pausing for Wi-Fi Direct."""

import asyncio
import random
import socket
import tempfile
import unittest
from pathlib import Path

from casthub import config
from casthub.discovery import dhcp, hotspot
from casthub.discovery.hotspot import ROUTE_ALONE, ROUTE_HOTSPOT, ROUTE_NETWORK, Hotspot

IW_CHANNELS = """Band 1:
    * 2437 MHz [6]
      Maximum TX power: 22.0 dBm
      Channel widths: 20MHz HT40- HT40+
    * 2484 MHz [14] (disabled)
Band 2:
    * 5180 MHz [36]
      Maximum TX power: 22.0 dBm
      No IR
      Channel widths: 20MHz HT40+ VHT80
    * 5260 MHz [52]
      Maximum TX power: 22.0 dBm
      No IR
      Radar detection
      Channel widths: 20MHz HT40+ VHT80 VHT160
      DFS state: usable (for 100 sec)
    * 5745 MHz [149]
      Maximum TX power: 22.0 dBm
      Channel widths: 20MHz HT40+ VHT80
"""


class TestHelpers(unittest.TestCase):
    def test_credentials(self):
        r = random.Random(1)
        ssid, psk = hotspot.new_ssid(r), hotspot.new_passphrase(r)
        self.assertRegex(ssid, r"^Cast Hub [0-9A-Z]{4}$")
        self.assertEqual(len(psk), 12)
        self.assertTrue(set(psk) <= set(hotspot.PASS_ALPHABET))

    def test_mac_is_local_unicast(self):
        for seed in range(20):
            first = int(hotspot.random_mac(random.Random(seed)).split(":")[0], 16)
            self.assertEqual(first & 0x03, 0x02)

    def test_qr_payload_escapes(self):
        self.assertEqual(hotspot.qr_payload("Cast Hub AB12", "abc"), "WIFI:T:WPA;S:Cast Hub AB12;P:abc;;")
        self.assertEqual(hotspot.qr_payload('a;b,c:d"e\\', "p"), 'WIFI:T:WPA;S:a\\;b\\,c\\:d\\"e\\\\;P:p;;')

    def test_channel_rules(self):
        flags = hotspot.channel_flags(IW_CHANNELS)
        self.assertEqual(flags[2437], set())
        self.assertIn("disabled", flags[2484])
        self.assertIn("radar detection", flags[5260])
        self.assertTrue(hotspot.ap_allowed(flags, 5745, beside_station=True))
        self.assertFalse(hotspot.ap_allowed(flags, 5260, beside_station=True))      # DFS: never
        self.assertTrue(hotspot.ap_allowed(flags, 5180, beside_station=True))       # no-IR: only beside Wi-Fi
        self.assertFalse(hotspot.ap_allowed(flags, 5180, beside_station=False))
        self.assertFalse(hotspot.ap_allowed(flags, 6115, beside_station=True))      # 6 GHz / unknown
        self.assertEqual(hotspot.pick_alone(flags), 5745)
        self.assertEqual(hotspot.pick_alone({2437: set()}), 2437)
        self.assertEqual(hotspot.pick_alone({}), 0)

    def test_mdns_query(self):
        q = hotspot.mdns_query()
        self.assertEqual(q[4:6], b"\x00\x01")                 # one question
        self.assertIn(b"\x09_services\x07_dns-sd\x04_udp\x05local\x00", q)


class FakeServer:
    started: list = []

    def __init__(self, ifname, pool, on_lease, label=""):
        self.ifname, self.pool = ifname, pool

    async def start(self):
        FakeServer.started.append(self)

    def stop(self):
        FakeServer.started.remove(self)


class FakeDaemon:
    def __init__(self, cfg, station=5745):
        self.cfg, self.station = cfg, station
        self.mice_addresses = ["192.0.2.20"]
        self.calls, self.cmds, self.nm, self.events = [], [], [], []
        self.ap_state = "completed"

    async def call(self, path, iface, member, sig="", body=None, dest=""):
        self.calls.append((path, member, body))
        if member == "GetInterface":
            raise RuntimeError("GetInterface: fi.w1.wpa_supplicant1.InterfaceUnknown")
        return {"CreateInterface": ["/ap/0"], "AddNetwork": ["/ap/0/net/0"]}.get(member, [])

    async def get(self, path, iface, prop):
        return {"State": self.ap_state}[prop]

    async def run(self, *cmd, timeout=10):
        self.cmds.append(cmd)
        if cmd[:3] == ("iw", "phy", "phy0"):
            return 0, IW_CHANNELS
        return 0, "public\n" if "--get-zone-of-interface=wlan0" in cmd else ""

    async def nmcli(self, *args, timeout=10):
        self.nm.append(args)
        if args[:2] == ("device", "disconnect"):
            self.station = 0
        if "IP4.DNS" in args:
            return 0, "IP4.DNS[1]:192.0.2.53\n"
        return 0, ""

    async def station_frequency(self):
        return self.station

    async def wifi_addresses(self):
        return ["192.0.2.20"] if self.station else []

    def broadcast(self, ev):
        self.events.append(ev)


class Wlan0Hotspot(Hotspot):
    wifi = "wlan0"


class TestHotspot(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.patches = {"QR_FILE": tmp / "qr.txt", "AP_CONF": tmp / "ap.conf"}
        self.saved = {k: getattr(hotspot, k) for k in self.patches}
        for k, v in self.patches.items():
            setattr(hotspot, k, v)
        self.saved_server, dhcp.Server = dhcp.Server, FakeServer
        FakeServer.started = []

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(hotspot, k, v)
        dhcp.Server = self.saved_server

    def hotspot(self, d):
        h = Wlan0Hotspot(d)

        async def flags():
            return hotspot.channel_flags(IW_CHANNELS)
        h._channel_flags = flags
        return h

    def run_start(self, h):
        async def go():
            await h.start()
            if h.watch_task:
                h.watch_task.cancel()
        asyncio.run(go())

    def test_always_beside_our_wifi(self):
        d = FakeDaemon(config.parse({}))
        h = self.hotspot(d)
        self.run_start(h)
        self.assertEqual(h.route, ROUTE_HOTSPOT)
        self.assertTrue(h.up)
        net = next(c[2][0] for c in d.calls if c[1] == "AddNetwork")
        self.assertEqual(net["frequency"].value, 5745)                    # our Wi-Fi's channel
        self.assertEqual(net["mode"].value, 2)
        self.assertEqual(net["ssid"].value, h.ssid.encode())
        self.assertEqual(hotspot.QR_FILE.read_text().strip(), hotspot.qr_payload(h.ssid, h.psk))
        self.assertIn(("firewall-cmd", "--zone=casthub-hotspot", "--change-interface=casthub-ap"), d.cmds)
        pool = FakeServer.started[0].pool
        self.assertEqual((pool.router, pool.dns), ("", ()))              # local only by default
        self.assertEqual(d.events[-1]["route"], ROUTE_HOTSPOT)
        self.assertNotIn("psk", d.events[-1])
        self.assertEqual(d.nm, [])                                        # Wi-Fi left alone

    def test_radar_channel_runs_alone_and_gives_wifi_back(self):
        d = FakeDaemon(config.parse({}), station=5260)
        h = self.hotspot(d)
        self.run_start(h)
        self.assertEqual(h.route, ROUTE_ALONE)
        self.assertIn(("device", "disconnect", "wlan0"), d.nm)
        net = next(c[2][0] for c in d.calls if c[1] == "AddNetwork")
        self.assertEqual(net["frequency"].value, 5745)
        asyncio.run(h.stop())
        self.assertFalse(h.up)
        self.assertFalse(hotspot.QR_FILE.exists())
        self.assertEqual(d.nm[-1][:3], ("device", "connect", "wlan0"))

    def test_pause_nests_and_resumes(self):
        d = FakeDaemon(config.parse({}))
        h = self.hotspot(d)
        self.run_start(h)
        ssid, psk = h.ssid, h.psk

        async def go():
            await h.pause("a Wi-Fi Direct cast")
            await h.pause("a new Wi-Fi Direct address")
            self.assertFalse(h.up)
            self.assertFalse(hotspot.QR_FILE.exists())                    # no QR while paused
            await h.resume()
            self.assertFalse(h.up)                                        # still held by the cast
            await h.resume()
        asyncio.run(go())
        self.assertTrue(h.up)
        self.assertEqual((h.ssid, h.psk), (ssid, psk))                    # phones rejoin by themselves

    def test_off_and_auto(self):
        d = FakeDaemon(config.parse({"hotspot": {"mode": "off"}}))
        h = self.hotspot(d)
        self.run_start(h)
        self.assertEqual((h.route, h.up), (ROUTE_NETWORK, False))

        saved = hotspot.network_has_peers
        try:
            for heard, route in ((True, ROUTE_NETWORK), (False, ROUTE_HOTSPOT), (None, ROUTE_HOTSPOT)):
                async def peers(ifname, ignore, seconds=0, heard=heard):
                    self.assertIn("192.0.2.20", ignore)                   # never counts ourselves
                    return heard
                hotspot.network_has_peers = peers
                d = FakeDaemon(config.parse({"hotspot": {"mode": "auto"}}))
                h = self.hotspot(d)
                self.run_start(h)
                self.assertEqual(h.route, route, heard)
        finally:
            hotspot.network_has_peers = saved

    def test_share_internet(self):
        d = FakeDaemon(config.parse({"hotspot": {"share_internet": True}}))
        h = self.hotspot(d)

        async def share(on):
            d.cmds.append(("share", on))
            if on:
                h.share_zone = "public"
            else:
                h.share_zone = ""
            return on
        h._share = share
        self.run_start(h)
        pool = FakeServer.started[0].pool
        self.assertEqual(pool.router, h.addr)
        self.assertEqual(pool.dns, ("192.0.2.53",))
        asyncio.run(h.stop())
        self.assertIn(("share", False), d.cmds)

    def test_failed_start_falls_back_to_network(self):
        d = FakeDaemon(config.parse({}))
        d.ap_state = "disconnected"
        h = self.hotspot(d)
        saved = hotspot.START_TIMEOUT_S
        hotspot.START_TIMEOUT_S = 0
        try:
            self.run_start(h)
        finally:
            hotspot.START_TIMEOUT_S = saved
        self.assertEqual(h.route, ROUTE_NETWORK)
        self.assertFalse(h.up)
        self.assertEqual(d.nm[-1][:3], ("device", "connect", "wlan0"))   # Wi-Fi given back


class TestConfig(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(config.parse({}).hotspot_mode, "always")
        self.assertFalse(config.parse({}).hotspot_share)
        self.assertEqual(config.parse({"hotspot": {"enabled": False}}).hotspot_mode, "off")
        self.assertEqual(config.parse({"hotspot": {"mode": "auto", "enabled": False}}).hotspot_mode, "auto")
        self.assertEqual(config.parse({"hotspot": {"ssid": "<ssid>"}}).hotspot_ssid, "")
        for bad in ({"mode": "sometimes"}, {"share_internet": "yes"}, {"ssid": "x" * 33}):
            with self.assertRaises(config.ConfigError):
                config.parse({"hotspot": bad})


class TestDhcpOptions(unittest.TestCase):
    def test_router_and_dns_only_when_given(self):
        req = dhcp.Request(1, 0, b"\x00\x00\x5e\x00\x53\x01", "0.0.0.0", dhcp.DISCOVER)
        plain = dhcp.reply(req, dhcp.OFFER, "192.0.2.10", "192.0.2.1", "255.255.255.0")
        self.assertNotIn(bytes([dhcp.OPT_ROUTER, 4]), plain[240:])
        shared = dhcp.reply(req, dhcp.OFFER, "192.0.2.10", "192.0.2.1", "255.255.255.0",
                            router="192.0.2.1", dns=("192.0.2.53",))
        self.assertIn(bytes([dhcp.OPT_ROUTER, 4]) + socket.inet_aton("192.0.2.1"), shared)
        self.assertIn(bytes([dhcp.OPT_DNS, 4]) + socket.inet_aton("192.0.2.53"), shared)


if __name__ == "__main__":
    unittest.main()
