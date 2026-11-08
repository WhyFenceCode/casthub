"""M3 receiver: MS-MICE parsing, WFD RTSP handshake against a fake source, IEs, hub flow.

All values are made up: names are placeholders, addresses are loopback.
"""

import asyncio
import struct
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from casthub import config as cfgmod  # noqa: E402
from casthub.discovery import mdns, p2p  # noqa: E402
from casthub.hub.receiver import Receiver  # noqa: E402
from casthub.mice import protocol as mp  # noqa: E402
from casthub.rtsp import message as rm  # noqa: E402
from casthub.rtsp import wfd  # noqa: E402
from casthub.rtsp.sink import RtspError, RtspSink, SinkCaps  # noqa: E402

SOURCE_ID = uuid.UUID(int=1)


class TestMice(unittest.TestCase):
    def test_source_ready_round_trip(self):
        raw = mp.source_ready("<pc-name>", 7236, SOURCE_ID)
        self.assertEqual(struct.unpack(">H", raw[:2])[0], len(raw))
        ev = mp.to_event(mp.parse_message(raw))
        self.assertEqual(ev, mp.SourceReady("<pc-name>", 7236, SOURCE_ID.bytes.hex()))

    def test_stop_projection(self):
        ev = mp.to_event(mp.parse_message(mp.stop_projection("<pc-name>", SOURCE_ID)))
        self.assertIsInstance(ev, mp.StopProjection)

    def test_hand_built_bytes(self):
        # Size 0x0021, version 1, SOURCE_READY; FRIENDLY_NAME "A" (UTF-16LE), RTSP_PORT 7236, SOURCE_ID.
        raw = bytes.fromhex("0021 01 01 00 0002 4100 02 0002 1c44 03 0010") + SOURCE_ID.bytes
        self.assertEqual(mp.to_event(mp.parse_message(raw)).rtsp_port, 7236)

    def test_malformed(self):
        good = mp.source_ready("x", 7236, SOURCE_ID)
        for bad in (good[:3], good[:-1], b"\x00\x04\x02\x01", good[:2] + b"\x01\x09" + good[4:]):
            with self.assertRaises(mp.MiceError):
                mp.to_event(mp.parse_message(bad))
        no_port = mp.build_message(mp.SOURCE_READY, [(mp.SOURCE_ID, SOURCE_ID.bytes)])
        with self.assertRaises(mp.MiceError):
            mp.to_event(mp.parse_message(no_port))

    def test_pin_and_encryption_are_reported_not_fatal(self):
        raw = mp.build_message(mp.SESSION_REQUEST, [(mp.SOURCE_ID, SOURCE_ID.bytes)])
        self.assertEqual(mp.to_event(mp.parse_message(raw)), mp.Unsupported(mp.SESSION_REQUEST))


class TestWfd(unittest.TestCase):
    def test_formats_fit_the_screen(self):
        v = wfd.sink_video_formats(1920, 1080)
        native, _, codec = v.split(" ", 2)
        self.assertEqual(int(native, 16), 8 << 3)          # CEA 1920x1080p60
        cea = int(codec.split()[2], 16)
        self.assertTrue(cea & (1 << 8))                    # 1080p60 offered
        self.assertFalse(cea & (1 << 9))                   # interlaced never
        vesa = int(wfd.sink_video_formats(1366, 768).split()[5], 16)
        self.assertFalse(vesa & (1 << 28))                 # 1920x1200 doesn't fit 1366x768
        self.assertTrue(vesa & (1 << 13))

    def test_parse_choice(self):
        f = wfd.parse_video_format("00 00 02 10 00000100 00000000 00000000 00 0000 0000 00 none none")
        self.assertEqual((f.width, f.height, f.fps, f.profile), (1920, 1080, 60, "constrained-high"))
        with self.assertRaises(wfd.WfdError):
            wfd.parse_video_format("00 00 02 10 00000180 00000000 00000000 00 0000 0000 00 none none")

    def test_audio(self):
        self.assertEqual(wfd.parse_audio_codecs("AAC 00000001 00"), wfd.AudioFormat("AAC", 48000, 2))
        self.assertIsNone(wfd.parse_audio_codecs("none"))

    def test_message_round_trip(self):
        req = rm.request("GET_PARAMETER", "rtsp://localhost/wfd1.0", 3, "wfd_video_formats\r\n")
        parsed = rm.parse_head(req.encode().split(b"\r\n\r\n")[0])
        self.assertEqual((parsed.method, parsed.cseq, parsed.headers["content-length"]), ("GET_PARAMETER", 3, "19"))


class FakeSource:
    """A WFD source on loopback, as Windows behaves after SOURCE_READY."""

    def __init__(self, *, keepalive=False, teardown=False, hold=False):
        self.keepalive, self.teardown, self.hold = keepalive, teardown, hold
        self.m3_reply = ""
        self.setup_transport = ""
        self.done = asyncio.Event()

    async def start(self):
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        cseq = 0

        def send(msg):
            writer.write(msg.encode())

        async def expect_response():
            while True:
                msg = await rm.read_message(reader)
                if not msg.is_request:
                    return msg
                await on_sink_request(msg)

        async def on_sink_request(msg):
            if msg.method == "OPTIONS":                         # M2
                send(rm.response(msg, public="org.wfa.wfd1.0, SETUP, TEARDOWN, PLAY, PAUSE, GET_PARAMETER, SET_PARAMETER"))
            elif msg.method == "SETUP":                         # M6
                self.setup_transport = msg.headers["transport"]
                send(rm.response(msg, session="00000001;timeout=30", transport=msg.headers["transport"] + ";server_port=19000"))
            elif msg.method in ("PLAY", "TEARDOWN", "SET_PARAMETER"):
                send(rm.response(msg))
                if msg.method == "TEARDOWN":
                    self.done.set()

        async def req(method, uri, body=""):
            nonlocal cseq
            cseq += 1
            send(rm.request(method, uri, cseq, body, require="org.wfa.wfd1.0") if method == "OPTIONS"
                 else rm.request(method, uri, cseq, body))
            return await expect_response()

        uri = "rtsp://localhost/wfd1.0"
        self.m1 = await req("OPTIONS", "*")
        # The sink's M2 arrives while we wait for M3's answer; expect_response() serves it.
        r3 = await req("GET_PARAMETER", uri, "wfd_video_formats\r\nwfd_audio_codecs\r\nwfd_client_rtp_ports\r\nwfd_connector_type\r\nmicrosoft_cursor\r\n")
        self.m3_reply = r3.body
        await req("SET_PARAMETER", uri,
                  "wfd_video_formats: 00 00 02 10 00000080 00000000 00000000 00 0000 0000 00 none none\r\n"
                  "wfd_audio_codecs: AAC 00000001 00\r\n"
                  "wfd_presentation_URL: rtsp://127.0.0.1/wfd1.0/streamid=0 none\r\n"
                  "wfd_client_rtp_ports: RTP/AVP/UDP;unicast 1028 0 mode=play\r\n")
        await req("SET_PARAMETER", uri, "wfd_trigger_method: SETUP\r\n")
        # M6 + M7 come from the sink now.
        while not self.setup_transport:
            await on_sink_request(await rm.read_message(reader))
        await on_sink_request(await rm.read_message(reader))   # PLAY
        if self.keepalive:
            r = await req("GET_PARAMETER", uri)                  # M16
            self.keepalive_status = r.status
        if self.teardown:
            await req("SET_PARAMETER", uri, "wfd_trigger_method: TEARDOWN\r\n")
            while not self.done.is_set():
                await on_sink_request(await rm.read_message(reader))
        if self.hold:                                            # stay until the sink tears down
            while not self.done.is_set():
                await on_sink_request(await rm.read_message(reader))
        writer.close()


def run(coro):
    async def main():
        # Own executor: after PySide6 is imported (test_display), asyncio's lazily imported default fails on 3.15.
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(2))
        return await asyncio.wait_for(coro, 10)
    return asyncio.run(main())


class TestRtspHandshake(unittest.TestCase):
    CAPS = SinkCaps(1920, 1080, 1028, "<receiver>")

    def test_m1_to_m7(self):
        async def go():
            src = FakeSource(keepalive=True, teardown=True)
            port = await src.start()
            sink = RtspSink("127.0.0.1", port, self.CAPS)
            neg = await sink.connect()
            why = await sink.serve()
            return src, neg, why

        src, neg, why = run(go())
        self.assertEqual(str(neg.video), "1920x1080p30 H.264 constrained-high")
        self.assertEqual((neg.session, neg.timeout, neg.rtp_port), ("00000001", 30, 1028))
        self.assertIn("client_port=1028", src.setup_transport)
        self.assertIn("org.wfa.wfd1.0", src.m1.headers["public"])
        replies = rm.parse_parameters(src.m3_reply)
        self.assertNotIn("microsoft_cursor", replies)          # unsupported: left out
        self.assertEqual(replies["wfd_connector_type"], "05")
        self.assertEqual(replies["wfd_audio_codecs"], "AAC 00000001 00")
        self.assertEqual(src.keepalive_status, 200)
        self.assertEqual(why, "teardown")

    def test_source_hangs_up(self):
        async def go():
            src = FakeSource()
            port = await src.start()
            sink = RtspSink("127.0.0.1", port, self.CAPS)
            await sink.connect()
            return await sink.serve()

        self.assertEqual(run(go()), "closed")

    def test_nobody_listening(self):
        async def go():
            s = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
            port = s.sockets[0].getsockname()[1]
            s.close()
            await s.wait_closed()
            await RtspSink("127.0.0.1", port, self.CAPS).connect()

        with self.assertRaises(RtspError) as cm:
            run(go())
        self.assertEqual(cm.exception.kind, "connect")


class FakePipeline:
    instances = []

    def __init__(self, spec, on_first_frame, on_error):
        self.spec, self.on_first_frame = spec, on_first_frame
        self.stopped = False
        FakePipeline.instances.append(self)

    def start(self):
        asyncio.get_running_loop().call_later(0.05, self.on_first_frame)

    def stop(self):
        self.stopped = True


class TestReceiverFlow(unittest.TestCase):
    def test_cast_and_busy(self):
        async def go():
            events = []

            async def notify(msg):
                events.append(msg)

            src = FakeSource(keepalive=True, hold=True)
            rtsp_port = await src.start()
            rec = Receiver("<receiver>", "OUT-1", (1920, 1080), 0, 1028, "reject", notify, FakePipeline)
            await rec.start()
            r, w = await asyncio.open_connection("127.0.0.1", rec.mice_port)
            w.write(mp.source_ready("<pc-name>", rtsp_port, SOURCE_ID))
            for _ in range(100):
                if rec.state == "streaming":
                    break
                await asyncio.sleep(0.02)
            streaming = rec.state
            # A second PC while busy is refused.
            r2, w2 = await asyncio.open_connection("127.0.0.1", rec.mice_port)
            w2.write(mp.source_ready("<pc-2>", rtsp_port, SOURCE_ID))
            await asyncio.sleep(0.1)
            w.write(mp.stop_projection("<pc-name>", SOURCE_ID))
            for _ in range(100):
                if rec.state == "idle":
                    break
                await asyncio.sleep(0.02)
            await rec.stop()
            w.close()
            w2.close()
            return streaming, rec.state, events

        streaming, final, events = run(go())
        self.assertEqual(streaming, "streaming")
        self.assertEqual(final, "idle")
        states = [e["state"] for e in events if e["cmd"] == "state"]
        self.assertEqual(states[:3], ["idle", "connecting", "streaming"])
        self.assertTrue(any(e["cmd"] == "busy" and "<pc-2>" in e["text"] for e in events))
        self.assertTrue(FakePipeline.instances[-1].stopped)


class TestDiscovery(unittest.TestCase):
    def test_container_id_is_stable_per_port(self):
        a = mdns.container_id(7250, "0" * 32)
        self.assertEqual(a, mdns.container_id(7250, "0" * 32))
        self.assertNotEqual(a, mdns.container_id(7251, "0" * 32))
        self.assertRegex(a, r"^\{[0-9A-F-]{36}\}$")
        self.assertEqual(mdns.txt_records(7250, "0" * 32), [f"container_id={a}".encode()])

    def test_wfd_device_info(self):
        sub = p2p.wfd_subelements(7236)
        self.assertEqual(sub.hex(), "000006" "0011" "1c44" "00c8")
        self.assertEqual(p2p.parse_wfd_rtsp_port(sub), 7236)
        # Outside Display mode: still a sink, but no session available.
        self.assertEqual(p2p.wfd_subelements(7236, available=False)[3:5].hex(), "0001")

    def test_mice_vendor_extension(self):
        ie = p2p.wsc_ie(p2p.mice_vendor_extension("receiver-host"))
        self.assertEqual(ie[0], 0xDD)
        self.assertEqual(ie[1], len(ie) - 2)
        self.assertEqual(ie[2:6], p2p.WSC_OUI)
        body = ie[6:]
        self.assertEqual(struct.unpack(">HH", body[:4]), (0x1049, len(body) - 4))
        self.assertEqual(body[4:7], bytes.fromhex("000137"))
        self.assertEqual(body[7:12], bytes.fromhex("2001 0001 05"))     # infrastructure supported, version 1
        self.assertEqual(body[12:16], bytes.fromhex("2002 000d"))
        self.assertEqual(body[16:], b"receiver-host")
        with self.assertRaises(ValueError):
            p2p.mice_vendor_extension("host.example")

    def test_vendor_payload_is_the_attribute_value(self):
        # wpa_supplicant adds the 0x1049 header itself; the payload starts with Microsoft's OUI.
        payload = p2p.mice_vendor_payload("receiver-host")
        self.assertEqual(payload[:3], bytes.fromhex("000137"))
        self.assertEqual(p2p.mice_vendor_extension("receiver-host")[4:], payload)

    def test_wps_pin(self):
        self.assertTrue(p2p.wps_pin_valid("12345670"))
        self.assertFalse(p2p.wps_pin_valid("12345678"))
        for _ in range(200):
            self.assertTrue(p2p.wps_pin_valid(p2p.wps_pin()))

    def test_nm_profile(self):
        args = p2p.nm_profile_args("00:00:5e:00:53:01", bytes.fromhex("000006"), "casthub-p2p")
        pairs = iter(args[2:])
        kv = dict(zip(pairs, pairs, strict=True))
        self.assertEqual(kv["wifi-p2p.peer"], "00:00:5e:00:53:01")
        self.assertEqual(kv["wifi-p2p.wfd-ies"], "0x000006")
        self.assertEqual((kv["connection.zone"], kv["save"]), ("casthub-p2p", "no"))
        self.assertEqual(p2p.mac(bytes.fromhex("00005e005301")), "00:00:5e:00:53:01")

    def test_peer_address(self):
        show = ("IP4.ADDRESS[1]:192.0.2.20/24\nIP4.GATEWAY:192.0.2.1\n"
                "DHCP4.OPTION[3]:dhcp_server_identifier = 192.0.2.10\nDHCP4.OPTION[4]:ip_address = 192.0.2.20\n")
        self.assertEqual(p2p.peer_from_nm(show), "192.0.2.10")
        self.assertEqual(p2p.peer_from_nm("IP4.GATEWAY:192.0.2.1\n"), "192.0.2.1")
        self.assertIsNone(p2p.peer_from_nm("IP4.GATEWAY:--\n"))
        self.assertEqual(p2p.peer_from_leases("1700000000 00:00:5e:00:53:01 192.0.2.30 pc *\n"), ("192.0.2.30", "pc"))
        self.assertIsNone(p2p.peer_from_leases(""))
        self.assertEqual(p2p.ipv4(bytes([192, 0, 2, 1])), "192.0.2.1")
        self.assertIsNone(p2p.ipv4(bytes(4)))
        self.assertIsNone(p2p.ipv4(b""))

    def test_receiver_ports_and_p2p(self):
        c = cfgmod.parse({"miracast": {"rtp_base_port": 2000, "p2p_address": "192.0.2.1/24"}})
        self.assertEqual((c.rtp_base_port, c.p2p_address), (2000, "192.0.2.1/24"))
        for bad in ("192.0.2.1", "not-an-ip/24"):
            with self.assertRaises(cfgmod.ConfigError):
                cfgmod.parse({"miracast": {"p2p_address": bad}})


if __name__ == "__main__":
    unittest.main()
