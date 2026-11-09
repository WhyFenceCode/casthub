"""M10: passthrough in the hub (controller, control socket) and in casthub-p2p (TV mode)."""

import asyncio
import unittest
from types import SimpleNamespace

from casthub import config
from casthub.discovery import p2p
from casthub.discovery.p2pd import P2PDaemon
from casthub.hub.__main__ import Hub
from casthub.hub.passthrough import Passthrough
from casthub.rtsp import wfd
from casthub.rtsp.source import Session


class FakeSource:
    instances: list = []

    def __init__(self, host, port):
        self.ended = asyncio.get_running_loop().create_future()
        self.on_idr = None
        self.closed = self.torn_down = False
        FakeSource.instances.append(self)

    async def listen(self):
        return 7236

    async def accept(self):
        return Session(wfd.Mode("CEA", 7, 1920, 1080, 30), "", "constrained-high", wfd.AudioFormat("AAC", 48000, 2),
                       "192.0.2.30", 1028, "1", "<tv>")

    async def serve(self):
        return await self.ended

    async def teardown(self):
        self.torn_down = True
        self.close()

    def close(self):
        self.closed = True
        if not self.ended.done():
            self.ended.set_result("closed")


class FakeProc:
    def __init__(self, target, on_error):
        self.target, self.on_error, self.stopped = target, on_error, False
        self.cards = []

    async def start(self):
        pass

    def keyframe(self):
        pass

    def card(self, **state):
        self.cards.append(state)

    def stop(self):
        self.stopped = True


class FakeReceiver:
    def __init__(self):
        self.tv, self.calls = None, []
        self.name, self.state, self.device = "Brand-1", "idle", ""

    async def set_tv(self, tv, name=""):
        self.tv = tv
        self.calls.append(name if tv else None)


def hub(mode="user", target=""):
    sent, ui = [], []

    async def p2p_send(msg):
        sent.append(msg)
        return True

    async def ui_send(msg):
        ui.append(msg)

    async def end_all():
        pass
    cfg = config.parse({"passthrough": {"mode": mode, "target": target}})
    h = SimpleNamespace(cfg=cfg, receivers=[FakeReceiver()], p2p_send=p2p_send, end_all=end_all,
                        ui=SimpleNamespace(send=ui_send), sent=sent, uimsgs=ui)
    pt = Passthrough(h)
    pt.factory, pt.source_factory = FakeProc, FakeSource
    h.passthrough = pt
    return h, pt


async def until(cond, n=100):
    for _ in range(n):
        if cond():
            return
        await asyncio.sleep(0.01)


class TestController(unittest.TestCase):
    def setUp(self):
        FakeSource.instances = []

    def test_on_then_off(self):
        async def go():
            h, pt = hub()
            await pt.start("<tv>")
            await until(lambda: pt.state == "on")
            rec = h.receivers[0]
            proc = pt.proc
            on = (pt.state, rec.tv is proc, proc.target.width, proc.target.host)
            await pt.stop()
            return h, pt, rec, proc, on
        h, pt, rec, proc, on = asyncio.run(go())
        self.assertEqual(on, ("on", True, 1920, "192.0.2.30"))
        self.assertEqual(proc.target.src_port, 7237)                    # RTP from SETUP's server_port
        self.assertEqual(proc.cards[0]["receiverName"], "Brand-1")       # the TV's own screen
        self.assertEqual(proc.cards[0]["screenState"], "idle")
        self.assertEqual(h.sent, [{"cmd": "tv_on", "name": "<tv>"}, {"cmd": "tv_ready"}, {"cmd": "tv_off"}])
        self.assertEqual(rec.calls, ["<tv>", None])                  # casts back on our screens
        self.assertTrue(proc.stopped)
        self.assertTrue(FakeSource.instances[0].torn_down)
        self.assertEqual(pt.state, "off")

    def test_lost_tv_is_joined_again(self):
        async def go():
            import casthub.hub.passthrough as mod
            saved, mod.RETRY_S = mod.RETRY_S, 0.01
            try:
                h, pt = hub()
                await pt.start("<tv>")
                await until(lambda: pt.state == "on")
                pt.on_p2p_event({"event": "tv_gone"})
                await until(lambda: len(FakeSource.instances) == 2 and pt.state == "on")
                n = len(FakeSource.instances)
                await pt.stop()
                return h, n
            finally:
                mod.RETRY_S = saved
        h, n = asyncio.run(go())
        self.assertEqual(n, 2)
        self.assertEqual([m["cmd"] for m in h.sent], ["tv_on", "tv_ready", "tv_on", "tv_ready", "tv_off"])

    def test_find(self):
        async def go():
            h, pt = hub()
            task = asyncio.ensure_future(pt.find())
            await until(lambda: h.sent)
            pt.on_p2p_event({"event": "tvs", "list": ["<tv>", "<projector>"]})
            return await task
        self.assertEqual(asyncio.run(go()), ["<tv>", "<projector>"])

    def test_control_socket_rules(self):
        async def go():
            out = {}
            for mode in ("off", "fixed", "user"):
                h, pt = hub(mode, "Example TV" if mode == "fixed" else "")
                h.passthrough = pt
                out[mode] = await Hub.control_command(h, {"cmd": "tv_on", "name": "<other>"})
                out[mode + "-status"] = await Hub.control_command(h, {"cmd": "status"})
                await pt.stop()
            return out
        out = asyncio.run(go())
        self.assertFalse(out["off"]["ok"])
        self.assertFalse(out["fixed"]["ok"])                          # the administrator's choice stands
        self.assertTrue(out["fixed-status"]["locked"])
        self.assertTrue(out["user"]["ok"])

    def test_fixed_needs_a_target(self):
        with self.assertRaises(config.ConfigError):
            config.parse({"passthrough": {"mode": "fixed"}})
        with self.assertRaises(config.ConfigError):
            config.parse({"passthrough": {"mode": "sometimes"}})
        self.assertEqual(config.parse({}).passthrough_mode, "user")


class TestWfdRoles(unittest.TestCase):
    def test_dual_role_and_parsing(self):
        sink = p2p.wfd_subelements()
        dual = p2p.wfd_subelements(dual_role=True)
        self.assertEqual(p2p.wfd_device_info(sink), (p2p.WFD_PRIMARY_SINK, 1))
        self.assertEqual(p2p.wfd_device_info(dual), (p2p.WFD_DUAL_ROLE, 1))
        self.assertEqual(p2p.wfd_device_info(p2p.wfd_subelements(available=False)), (p2p.WFD_PRIMARY_SINK, 0))
        self.assertEqual(p2p.parse_wfd_rtsp_port(dual), 7236)
        self.assertIsNone(p2p.wfd_device_info(b""))


class TestP2PTvMode(unittest.TestCase):
    def daemon(self, peers):
        d = P2PDaemon.__new__(P2PDaemon)
        d.advertising, d.busy, d.connecting, d.pair_started, d.pair_restarts = True, False, False, 0.0, 0
        d.peers, d.announced, d.dev, d.clients, d.group_path, d.dhcp = {}, set(), "/dev", set(), "", None
        d.radio, d.calls, d.events = asyncio.Lock(), [], []
        d.pair_watch = None
        ies = {"/peer/tv": p2p.wfd_subelements(), "/peer/pc": b"\x00\x00\x06\x00\x10\x1c\x44\x00\xc8"}

        async def call(path, iface, member, sig="", body=None, dest=""):
            d.calls.append((path, member, body))
            return []

        async def get(path, iface, prop):
            if prop == "Peers":
                return list(peers)
            return {"IEs": ies.get(path, b""), "DeviceName": {"/peer/tv": "<tv>", "/peer/pc": "<pc>"}[path]}[prop]

        async def find_device():
            pass

        async def station_frequency():
            return 0

        holds = []

        class FakeHotspot:
            async def pause(self, why):
                holds.append(why)

            async def resume(self):
                holds.append("resume")
        d.call, d.get, d.find_device, d.station_frequency = call, get, find_device, station_frequency
        d.hotspot, d.holds = FakeHotspot(), holds
        d.broadcast = d.events.append
        return d

    def test_find_lists_only_displays(self):
        d = self.daemon(["/peer/tv", "/peer/pc"])

        async def go():
            import casthub.discovery.p2pd as mod
            saved, mod.TV_FIND_S = mod.TV_FIND_S, 0
            try:
                return await d.find_tvs(0)
            finally:
                mod.TV_FIND_S = saved
        self.assertEqual([t["name"] for t in asyncio.run(go())], ["<tv>"])     # the PC is a source

    def test_tv_on_joins_as_owner_and_pauses_the_hotspot(self):
        d = self.daemon(["/peer/tv"])

        async def go():
            import casthub.discovery.p2pd as mod
            mod.TV_FIND_S, saved = 0, mod.TV_FIND_S
            try:
                await d.tv_on("<tv>")
            finally:
                mod.TV_FIND_S = saved
            await asyncio.sleep(0)                      # let the deadline task start before cancelling it
            d.pair_watch.cancel()
            await d.pair("/peer/pc")                    # a PC can't pair meanwhile
            d.announce("192.0.2.40", "")
            await d.tv_off()
        asyncio.run(go())
        connects = [c for c in d.calls if c[1] == "Connect"]
        self.assertEqual(len(connects), 1)
        self.assertEqual(connects[0][2][0]["peer"].value, "/peer/tv")
        self.assertEqual(connects[0][2][0]["go_intent"].value, 15)
        self.assertEqual(d.events, [{"event": "tv", "addr": "192.0.2.40", "name": "<tv>"}])
        self.assertEqual(d.holds, ["passthrough", "resume"])
        self.assertFalse(d.tv_mode)


if __name__ == "__main__":
    unittest.main()
