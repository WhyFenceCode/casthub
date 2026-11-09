"""Wi-Fi Direct as group owner: DHCP for the PC, the group's own subnet, pairing with go_intent 15.

Addresses are documentation ranges (RFC 5737) and made-up MACs (RFC 7042).
"""

import asyncio
import ipaddress
import socket
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dbus_fast import Variant  # noqa: E402

from casthub.discovery import dhcp, p2p  # noqa: E402
from casthub.discovery.p2pd import GO_INTENT, P2PDaemon  # noqa: E402

PC_MAC = bytes.fromhex("00005e005301")
NET, OURS = "192.0.2.0/24", "192.0.2.1"


def client_msg(kind: int, xid: int = 0x1234, requested: str = "", server: str = "", hostname: str = "") -> bytes:
    head = dhcp.HEADER.pack(1, 1, 6, 0, xid, 0, 0x8000, b"\0" * 4, b"\0" * 4, b"\0" * 4, b"\0" * 4,
                            PC_MAC.ljust(16, b"\0"), b"\0" * 64, b"\0" * 128)
    opts = bytes([dhcp.OPT_TYPE, 1, kind])
    if requested:
        opts += bytes([dhcp.OPT_REQUESTED, 4]) + socket.inet_aton(requested)
    if server:
        opts += bytes([dhcp.OPT_SERVER, 4]) + socket.inet_aton(server)
    if hostname:
        opts += bytes([dhcp.OPT_HOSTNAME, len(hostname)]) + hostname.encode()
    return head + dhcp.MAGIC + opts + bytes([dhcp.OPT_END])


def options(data: bytes) -> dict[int, bytes]:
    out, i = {}, dhcp.HEADER.size + 4
    while data[i] != dhcp.OPT_END:
        out[data[i]] = data[i + 2:i + 2 + data[i + 1]]
        i += 2 + data[i + 1]
    return out


class TestDhcp(unittest.TestCase):
    def test_discover_request_ack(self):
        pool = dhcp.Pool(NET, OURS)
        offer, leased = dhcp.answer(pool, dhcp.parse(client_msg(dhcp.DISCOVER, hostname="<pc>")))
        self.assertIsNone(leased)
        yiaddr = socket.inet_ntoa(offer[16:20])
        self.assertIn(ipaddress.IPv4Address(yiaddr), ipaddress.IPv4Network(NET))
        self.assertNotEqual(yiaddr, OURS)
        o = options(offer)
        self.assertEqual(o[dhcp.OPT_TYPE], bytes([dhcp.OFFER]))
        self.assertEqual(socket.inet_ntoa(o[dhcp.OPT_SERVER]), OURS)
        self.assertEqual(socket.inet_ntoa(o[dhcp.OPT_MASK]), "255.255.255.0")
        self.assertEqual(struct.unpack("!I", offer[4:8])[0], 0x1234)            # same transaction
        self.assertEqual(offer[28:34], PC_MAC)

        ack, leased = dhcp.answer(pool, dhcp.parse(client_msg(dhcp.REQUEST, requested=yiaddr, server=OURS)))
        self.assertEqual(options(ack)[dhcp.OPT_TYPE], bytes([dhcp.ACK]))
        self.assertEqual(leased, yiaddr)
        # Asking again (renewal) keeps the address.
        _, again = dhcp.answer(pool, dhcp.parse(client_msg(dhcp.REQUEST, requested=yiaddr)))
        self.assertEqual(again, yiaddr)

    def test_wrong_address_gets_nak_and_other_server_is_ignored(self):
        pool = dhcp.Pool(NET, OURS)
        dhcp.answer(pool, dhcp.parse(client_msg(dhcp.DISCOVER)))
        nak, leased = dhcp.answer(pool, dhcp.parse(client_msg(dhcp.REQUEST, requested="192.0.2.250")))
        self.assertEqual(options(nak)[dhcp.OPT_TYPE], bytes([dhcp.NAK]))
        self.assertIsNone(leased)
        self.assertEqual(dhcp.answer(pool, dhcp.parse(client_msg(dhcp.REQUEST, server="192.0.2.9"))), (None, None))

    def test_garbage_is_ignored(self):
        self.assertIsNone(dhcp.parse(b"\0" * 300))
        self.assertIsNone(dhcp.parse(b"short"))

    def test_hostname(self):
        self.assertEqual(dhcp.parse(client_msg(dhcp.DISCOVER, hostname="<pc>")).hostname, "<pc>")


class TestGroupNetwork(unittest.TestCase):
    def test_private_and_free(self):
        for _ in range(50):
            net = p2p.group_network([])
            self.assertEqual(net.prefixlen, 24)
            self.assertIn(net.network_address, p2p.PRIVATE_10)

    def test_avoids_our_addresses(self):
        picks = iter([5, 5, 6])                       # the first pick overlaps, the next is free
        taken = str(ipaddress.IPv4Network((int(p2p.PRIVATE_10.network_address) | 5 << 8, 24))[20])
        net = p2p.group_network([taken], rand=lambda a, b: next(picks))
        self.assertNotIn(ipaddress.IPv4Address(taken), net)


class TestPairAsGroupOwner(unittest.TestCase):
    def daemon(self):
        d = P2PDaemon.__new__(P2PDaemon)
        d.advertising, d.connecting, d.pair_started, d.pair_restarts = True, False, 0.0, 0
        d.peers, d.announced, d.dev, d.clients = {}, set(), "/dev", set()
        d.dhcp, d.group_path, d.group_if, d.mice_addresses = None, "", "", ["192.0.2.20"]
        d.calls, d.cmds = [], []

        async def call(path, iface, member, sig="", body=None, dest=""):
            d.calls.append((path, member, body))
            return []

        async def get(path, iface, prop):
            return {"IEs": b"", "Ifname": "p2p-test-0"}[prop]

        async def run(*cmd, timeout=10):
            d.cmds.append(cmd)
            return 0, ""

        async def wifi_addresses():
            return ["192.0.2.20"]

        async def station_frequency():
            return d.freq
        d.freq = 5745
        d.call, d.get, d.run, d.wifi_addresses = call, get, run, wifi_addresses
        d.station_frequency = station_frequency

        class FakeHotspot:
            async def pause(self, why):
                d.calls.append(("hotspot", "pause", why))

            async def resume(self):
                d.calls.append(("hotspot", "resume", None))
        d.hotspot, d.hotspot_held = FakeHotspot(), False
        d.pair_peer, d.pair_freq, d.tv_mode = "", 0, False
        d.broadcast = lambda ev: d.calls.append(("hub", ev["event"], ev))
        return d

    def test_connect_asks_to_own_the_group(self):
        d = self.daemon()

        async def go():
            await d.pair("/peer/1")
            await asyncio.sleep(0)            # let the deadline task start before cancelling it
            d.pair_watch.cancel()
        asyncio.run(go())
        path, member, body = next(c for c in d.calls if c[1] == "Connect")
        self.assertEqual(GO_INTENT, 15)
        self.assertEqual(body[0]["go_intent"].value, 15)
        self.assertEqual(body[0]["wps_method"].value, "pbc")
        self.assertEqual(body[0]["peer"].value, "/peer/1")
        self.assertFalse(body[0]["persistent"].value)
        self.assertEqual(body[0]["frequency"].value, 5745)            # our Wi-Fi's channel
        self.assertIn(("hotspot", "pause", "a Wi-Fi Direct cast"), d.calls)   # one radio slot

        async def ended():
            d.advertising = False
            await d.end_session()
        asyncio.run(ended())
        self.assertEqual(d.calls[-1], ("hotspot", "resume", None))           # back after the cast

    def test_connect_without_wifi_lets_the_radio_choose(self):
        d = self.daemon()
        d.freq = 0
        asyncio.run(d.connect("/peer/1"))
        _, _, body = next(c for c in d.calls if c[1] == "Connect")
        self.assertNotIn("frequency", body[0])

    def test_no_common_channel_asks_again_on_any_channel(self):
        # Seen with a TV: our Wi-Fi's channel (5745 MHz) refused with status 7.
        d = self.daemon()
        d.tv_mode, d.connecting = True, True

        async def go():
            await d.connect("/peer/tv")
            await d.on_go_failure({"status": Variant("i", 7)})
            await d.on_go_failure({"status": Variant("i", 7)})       # no channel at all: give up
        asyncio.run(go())
        connects = [c[2][0] for c in d.calls if c[1] == "Connect"]
        self.assertEqual(len(connects), 2)
        self.assertEqual(connects[0]["frequency"].value, 5745)
        self.assertNotIn("frequency", connects[1])
        failed = [c[2] for c in d.calls if c[1] == "tv_failed"]
        self.assertEqual(failed, [{"event": "tv_failed", "why": "no common channel"}])
        self.assertFalse(d.connecting)

    def test_other_failures_do_not_retry(self):
        d = self.daemon()
        d.tv_mode, d.connecting = True, True

        async def go():
            await d.connect("/peer/tv")
            await d.on_go_failure({"status": Variant("i", 11)})
        asyncio.run(go())
        self.assertEqual(sum(c[1] == "Connect" for c in d.calls), 1)
        self.assertEqual([c[2]["why"] for c in d.calls if c[1] == "tv_failed"], ["declined on the other side"])

    def test_group_owner_serves_dhcp_and_announces(self):
        d = self.daemon()
        started = []

        class FakeServer:
            def __init__(self, ifname, pool, on_lease):
                self.ifname, self.pool, self.on_lease = ifname, pool, on_lease

            async def start(self):
                started.append(self)

            def stop(self):
                started.remove(self)

        async def go():
            orig = dhcp.Server
            dhcp.Server = FakeServer
            try:
                await d.on_group_started({"role": Variant("s", "GO"), "interface_object": Variant("o", "/group/0")})
                srv = started[0]
                net = srv.pool.net
                srv.on_lease(str(net[10]), "00:00:5e:00:53:01", "<pc>")
                d.advertising = False
                await d.close_group()
                return srv, net
            finally:
                dhcp.Server = orig
        srv, net = asyncio.run(go())
        self.assertEqual(srv.ifname, "p2p-test-0")
        self.assertNotIn(ipaddress.IPv4Address("192.0.2.20"), net)        # not our Wi-Fi network
        self.assertIn(("ip", "addr", "add", f"{net[1]}/24", "dev", "p2p-test-0"), d.cmds)
        self.assertIn(("firewall-cmd", "--zone=casthub-p2p", "--change-interface=p2p-test-0"), d.cmds)
        hub = [c[2] for c in d.calls if c[0] == "hub"]
        self.assertEqual(hub[0]["event"], "source")
        self.assertEqual(hub[0]["addr"], str(net[10]))
        self.assertIn(("/group/0", "Disconnect", None), d.calls)          # the group ends with the cast
        self.assertEqual(started, [])                                      # DHCP stopped


if __name__ == "__main__":
    unittest.main()
