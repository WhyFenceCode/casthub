"""M3 receiver: MS-MICE parsing, WFD RTSP handshake against a fake source, IEs, hub flow.

All values are made up: names are placeholders, addresses are loopback.
"""

import asyncio
import json
import struct
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from casthub import config as cfgmod  # noqa: E402
from casthub import naming  # noqa: E402
from casthub.discovery import mdns, p2p  # noqa: E402
from casthub.hub.receiver import Receiver  # noqa: E402
from casthub.media import pipeline as media  # noqa: E402
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

    def test_modes_take_the_window_shape(self):
        def sizes(w, h):
            return {wfd.TABLES[t][i][:2] for t, idx in wfd.offered_modes(w, h).items() for i in idx}
        self.assertEqual(sizes(1024, 768), {(640, 480), (800, 600), (1024, 768)})  # 4:3 window: 4:3 only
        self.assertNotIn((1024, 768), sizes(1920, 1080))
        self.assertIn((1280, 800), sizes(1280, 800))                            # 16:10
        self.assertEqual(sizes(700, 400), {(640, 360)})
        self.assertEqual(sizes(500, 300), {(800, 480)})                         # none fits: the smallest
        native, _, codec = wfd.sink_video_formats(700, 400).split(" ", 2)
        self.assertEqual(int(native, 16), 7 << 3 | 2)                           # HH 640x360p60
        self.assertTrue(int(codec.split()[2], 16) & 1)                          # 640x480p60, mandatory

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
        r3 = await req("GET_PARAMETER", uri, "wfd_video_formats\r\nwfd_audio_codecs\r\nwfd_client_rtp_ports\r\n"
                       "wfd_connector_type\r\nmicrosoft_cursor\r\n")
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

    async def start(self):
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


class SilentPipeline(FakePipeline):
    """Packets arrived once, then stopped (the Wi-Fi Direct link dropped)."""

    last_packet = 1.0

    def silent_for(self):
        return 60.0


class TestLinkLost(unittest.TestCase):
    def test_cast_ends_when_packets_stop(self):
        async def go():
            events = []

            async def notify(msg):
                events.append(msg)

            src = FakeSource(keepalive=True, hold=True)
            rtsp_port = await src.start()
            rec = Receiver("<receiver>", "OUT-1", (1920, 1080), 0, 1028, "reject", notify, SilentPipeline)
            await rec.start()
            _r, w = await asyncio.open_connection("127.0.0.1", rec.mice_port)
            w.write(mp.source_ready("<pc-name>", rtsp_port, SOURCE_ID))
            states = []
            for _ in range(250):          # the link watch checks once a second
                states.append(rec.state)
                if "streaming" in states and rec.state == "idle":
                    break
                await asyncio.sleep(0.02)
            final = rec.state
            w.close()                     # the listener waits for open control connections
            await rec.stop()
            return states, final

        states, final = run(go())
        self.assertIn("streaming", states)
        self.assertEqual(final, "idle")


class TestPipelineDescription(unittest.TestCase):
    def test_sinks_never_hold_up_playback(self):
        """Windows lists an audio track it doesn't fill while the PC is silent: an async sink
        that never prerolls froze the picture after one frame. Both sinks must be async=false
        and must not wait on the PC's timestamps."""
        desc = media.describe(media.default_spec(1028, "t", "OUT-1"))
        for name in ("vsink", "asink"):
            sink = next(part for part in desc.split("!") if f"name={name}" in part)
            self.assertIn("async=false", sink)
            self.assertIn("sync=false", sink)
        self.assertIn("leaky=downstream", desc)        # audio can't back up into the demuxer

    def test_speakers_get_a_fixed_format(self):
        """Windows' audio reached pipewiresink as "F32LE, 1 Hz, mono", which PipeWire never
        linked: both paths resample to a fixed format right before the sink."""
        for spec in (media.default_spec(1028, "t", "OUT-1"),
                     media.default_spec(1032, "t", "OUT-1", source="airplay", audio_port=1034)):
            desc = media.describe(spec)
            self.assertIn(f"audioresample ! {media.AUDIO_OUT} ! pipewiresink", desc)
        # Windows' AAC decodes planar; pipewiresink played planar as interleaved (double speed).
        self.assertIn("layout=interleaved", media.AUDIO_OUT)


class TestMirrorAll(unittest.TestCase):
    """M4: Windows lists one receiver per radio, so every screen mirrors one receiver."""

    def test_one_decode_one_window_per_screen(self):
        desc = media.describe(media.default_spec(1028, "t", "OUT-1", ("OUT-2", "OUT-3")))
        self.assertEqual(desc.count("vah264dec"), 1)
        self.assertEqual(desc.count("glimagesink"), 3)
        self.assertIn("tee name=vt", desc)
        for name in ("vsink", "vsink1", "vsink2"):
            self.assertIn(f"name={name}", desc)
        self.assertNotIn("tee", media.describe(media.default_spec(1028, "t", "OUT-1")))

    def test_windows_by_pid(self):
        tree = {"type": "root", "nodes": [
            {"type": "output", "nodes": [{"type": "workspace", "nodes": [{"type": "con", "id": 9, "pid": 42}]}]},
            {"type": "output", "nodes": [{"type": "workspace", "nodes": [{"type": "con", "id": 4, "pid": 42},
                                                                       {"type": "con", "id": 5, "pid": 7}]}]}]}
        self.assertEqual(media.windows_of(tree, 42), [4, 9])

    def test_ui_messages_go_to_every_screen(self):
        async def go():
            sent = []

            async def notify(msg):
                sent.append(msg)
            rec = Receiver("<receiver>", "OUT-1", (1920, 1080), 0, 1028, "reject", notify, FakePipeline,
                           mirrors=("OUT-1", "OUT-2"))
            await rec.set_state("connecting", "<pc-name>")
            await rec.show_pin("12345670")
            return rec, sent
        rec, sent = asyncio.run(go())
        self.assertEqual(rec.outputs, ("OUT-1", "OUT-2"))      # the main screen isn't listed twice
        self.assertEqual([(m["cmd"], m["output"]) for m in sent],
                         [("state", "OUT-1"), ("state", "OUT-2"), ("pin", "OUT-1"), ("pin", "OUT-2")])

    def test_old_layouts_mean_mirror_all(self):
        self.assertEqual(cfgmod.Config().layout, "mirror_all")
        for old in ("per_screen", "per_radio"):
            self.assertEqual(cfgmod.parse({"layout": {"mode": old}}).layout, "mirror_all")

    def test_built_in_screen_is_the_main_one(self):
        from casthub.hub.__main__ import main_screen_first
        outs = [("HDMI-A-1", (1920, 1080)), ("eDP-1", (1366, 768)), ("DP-1", (1920, 1080))]
        self.assertEqual([o for o, _ in sorted(outs, key=main_screen_first)], ["eDP-1", "DP-1", "HDMI-A-1"])

    def test_spec_survives_the_trip_to_the_player(self):
        from casthub.media import player
        spec = media.default_spec(1028, "t", "OUT-1", ("OUT-2",))
        self.assertEqual(player.decode_spec(player.encode_spec(spec)), spec)


FAKE_UXPLAY = """#!/usr/bin/python3 -I
import sys, time
print("Initialized server socket(s)", flush=True)
time.sleep(0.2)
print("connection request from <iphone> Phone (iPhone99,9) with deviceID = 00:00:5e:00:53:01", flush=True)
time.sleep(0.4)
print("begin video stream wxh = 1920x1080; source 1170x2532", flush=True)
time.sleep(0.4)
print("raop_rtp_mirror->running is no longer true", flush=True)   # Stop Mirroring (live test)
time.sleep(30)
"""


class TestAirPlay(unittest.TestCase):
    def test_log_lines(self):
        from casthub.airplay.uxplay import parse_line
        ev = parse_line("connection request from <owner>'s iPhone (iPhone99,9) with deviceID = 00:00:5e:00:53:01")
        self.assertEqual((ev.kind, ev.device), ("connect", "<owner>'s iPhone"))
        self.assertEqual(parse_line("begin video stream wxh = 1920x1080; source 1170x2532").kind, "video")
        for end in ("raop_rtp_mirror->running is no longer true", "video_reset: type = RTP_Shutdown", "Open connections: 0",
                    "***ERROR lost connection with client (network problem?)"):
            self.assertEqual(parse_line(end).kind, "ended")
        self.assertIsNone(parse_line("Open connections: 1"))
        self.assertIsNone(parse_line("raop_ntp stopping time thread"))

    def test_args(self):
        from casthub.airplay.uxplay import uxplay_args
        a = uxplay_args("CAST-ABCDE", (1366, 768), 1032, 1034, "00:00:5e:00:53:01", replace=True)
        a = a[a.index("uxplay"):]
        self.assertEqual(a[:3], ["uxplay", "-n", "CAST-ABCDE"])
        self.assertIn("-nh", a)
        self.assertEqual(a[a.index("-s") + 1], "1366x768@60")
        self.assertIn("port=1032", a[a.index("-vrtp") + 1])
        self.assertIn("host=127.0.0.1", a[a.index("-artp") + 1])
        self.assertIn("-nohold", a)
        self.assertNotIn("-nohold", uxplay_args("n", (1, 1), 1, 2, "x"))

    def test_log_is_line_buffered(self):
        """The hub must see each UxPlay line as it happens, not when UxPlay exits."""
        import shutil

        from casthub.airplay.uxplay import uxplay_args
        if not shutil.which("stdbuf"):
            self.skipTest("no stdbuf")
        a = uxplay_args("n", (1, 1), 1, 2, "x")
        i = next(i for i, x in enumerate(a) if x.endswith("stdbuf"))
        self.assertEqual(a[i + 1], "-oL")
        self.assertLess(i, a.index("uxplay"))

    def test_device_id_is_text(self):
        """UxPlay refused raw bytes for -m ("non-ascii or non-UTF-8 string") and never started."""
        from casthub.airplay.uxplay import device_id
        ids = {device_id() for _ in range(20)}
        self.assertEqual(len(ids), 20)
        for i in ids:
            self.assertRegex(i, r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")
            self.assertEqual(int(i[:2], 16) & 0x03, 0x02)          # locally administered, unicast

    def test_pipeline(self):
        desc = media.describe(media.default_spec(1032, "t", "OUT-1", ("OUT-2",), source="airplay", audio_port=1034))
        for part in ("rtph264depay", "rtpL16depay", "port=1034", "tee name=vt", "name=vsink1", "address=127.0.0.1"):
            self.assertIn(part, desc)
        self.assertNotIn("tsdemux", desc)
        silent = media.describe(media.default_spec(1032, "t", source="airplay"))
        self.assertNotIn("rtpL16depay", silent)          # no audio port: no audio branch
        import dataclasses
        sound = media.describe(dataclasses.replace(
            media.default_spec(1032, "t", source="airplay", audio_port=1034), video=False))
        self.assertIn("rtpL16depay", sound)
        self.assertNotIn("rtph264depay", sound)
        self.assertNotIn("glimagesink", sound)

    def test_cast_through_uxplay(self):
        import os
        import tempfile

        from casthub.airplay.uxplay import UxPlay

        async def go(tmp):
            fake = Path(tmp) / "uxplay"
            fake.write_text(FAKE_UXPLAY)
            os.chmod(fake, 0o755)
            states = []

            async def notify(msg):
                if msg["cmd"] == "state":
                    states.append((msg["state"], msg["device"]))
            rec = Receiver("CAST-ABCDE", "OUT-1", (1920, 1080), 0, 1028, "reject", notify, FakePipeline,
                           airplay_ports=(1032, 1034))
            ux = UxPlay(rec, 1032, 1034, binary=str(fake))
            await ux.start()
            for _ in range(100):
                if ("idle", "") in states[1:]:
                    break
                await asyncio.sleep(0.05)
            spec = FakePipeline.instances[-1].spec
            await ux.stop()
            return states, spec
        with tempfile.TemporaryDirectory() as tmp:
            states, spec = asyncio.run(go(tmp))
        self.assertIn(("connecting", "<iphone> Phone"), states)
        self.assertIn(("streaming", "<iphone> Phone"), states)
        self.assertEqual(states[-1], ("idle", ""))
        self.assertEqual((spec.source, spec.rtp_port, spec.audio_port, spec.video), ("airplay", 1032, 1034, False))

    def test_standby_player_catches_the_stream_start(self):
        """An iPhone sends SPS/PPS and its only keyframe as mirroring starts: the player must
        already be listening, so the cast uses the standby one and a new one waits after."""
        async def go():
            states = []

            async def notify(msg):
                if msg["cmd"] == "state":
                    states.append(msg["state"])
            FakePipeline.instances.clear()
            rec = Receiver("CAST-ABCDE", "OUT-1", (1920, 1080), 0, 1028, "reject", notify, FakePipeline,
                           airplay_ports=(1032, 1034))
            rec.airplay_standby = True
            await rec.prepare_airplay()
            standby = rec.standby
            await asyncio.sleep(0.1)                    # its first frame arrives before the cast
            src = await rec.on_airplay("<iphone>")
            for _ in range(50):
                if rec.state == "streaming":
                    break
                await asyncio.sleep(0.02)
            streaming = rec.state
            src.stop.set()
            for _ in range(50):
                if rec.standby is not None and rec.standby is not standby:
                    break
                await asyncio.sleep(0.02)
            return standby, streaming, rec, list(FakePipeline.instances), states
        standby, streaming, rec, pipes, states = asyncio.run(go())
        self.assertEqual(streaming, "streaming")
        self.assertIs(pipes[0], standby)               # the cast played on the standby player
        self.assertTrue(standby.stopped)
        self.assertEqual((standby.spec.video, standby.spec.audio_port), (True, 0))    # no idle audio stream
        sound = pipes[1]                               # the sound: its own player, for the cast only
        self.assertEqual((sound.spec.video, sound.spec.audio_port), (False, 1034))
        self.assertTrue(sound.stopped)
        self.assertEqual(len(pipes), 3)                # and a fresh one waits for the next iPhone
        self.assertIs(rec.standby, pipes[2])
        self.assertEqual(pipes[2].spec.audio_port, 0)

    def test_hub_tells_wifi_direct_when_casting(self):
        from types import SimpleNamespace

        from casthub.hub.__main__ import Hub
        sent = []

        async def p2p_send(msg):
            sent.append(msg)
            return True
        r = SimpleNamespace(state="idle")
        hub = SimpleNamespace(receivers=[r], p2p_busy=False, p2p_send=p2p_send,
                              passthrough=SimpleNamespace(show_state=lambda r: None))

        async def go():
            for state in ("connecting", "streaming", "idle", "idle"):
                r.state = state
                await Hub.on_receiver_state(hub, r)
        asyncio.run(go())
        self.assertEqual(sent, [{"cmd": "busy", "busy": True}, {"cmd": "busy", "busy": False}])

    def test_p2pd_busy_stops_discovery(self):
        from casthub.discovery.p2pd import P2PDaemon
        calls = []

        async def call(path, iface, member, sig, args):
            calls.append((member, args))

        async def set_(path, iface, prop, sig, value):
            calls.append((prop, value))

        async def nothing(*a):
            return None

        async def go():
            d = P2PDaemon.__new__(P2PDaemon)
            d.radio, d.advertising, d.busy, d.dev = asyncio.Lock(), True, False, "/dev"
            d.call, d.set, d.find_device = call, set_, nothing
            d.listen = lambda: call("/dev", "", "ExtendedListen", "", ["on"])
            await d.set_busy(True)
            await d.set_busy(False)
            return d
        d = asyncio.run(go())
        self.assertEqual(calls[0], ("WFDIEs", p2p.wfd_subelements(available=False)))
        self.assertEqual(calls[1], ("ExtendedListen", [{}]))
        self.assertEqual(calls[2], ("WFDIEs", p2p.wfd_subelements(available=True)))
        self.assertEqual(calls[3], ("ExtendedListen", ["on"]))
        self.assertFalse(d.busy)

    def test_end_all_ends_airplay_and_renews(self):
        """Ctrl+Alt+Esc (casthub-leave → SIGUSR1 → end_all) during an AirPlay cast: the player
        stops, the receiver gets a new name, and UxPlay restarts under it (new device ID and key),
        which also drops the iPhone."""
        from types import SimpleNamespace

        from casthub.hub.__main__ import Hub

        async def go():
            async def notify(msg):
                pass
            restarted = []
            rec = Receiver("CAST-OLD00", "OUT-1", (1920, 1080), 0, 1028, "reject", notify, FakePipeline,
                           airplay_ports=(1032, 1034))

            async def publish(name, port):
                pass

            async def restart():
                restarted.append(rec.name)
            hub = SimpleNamespace(receivers=[rec], cfg=SimpleNamespace(pinned_name=lambda o: ""),
                                  mdns=SimpleNamespace(publish=publish), airplay=SimpleNamespace(restart=restart),
                                  name_for=lambda i, o: "CAST-NEW00", wifi_direct_names=lambda r: False)
            hub.restart_airplay = lambda r: Hub.restart_airplay(hub, r)
            rec.on_ended = lambda r: Hub.rename(hub, r)
            src = await rec.on_airplay("<iphone>")
            await asyncio.sleep(0.1)
            pipe = FakePipeline.instances[-1]
            await Hub.end_all(SimpleNamespace(receivers=[rec]))
            await rec.ended_task
            return src, pipe, rec, restarted
        src, pipe, rec, restarted = asyncio.run(go())
        self.assertTrue(src.stop.is_set())
        self.assertTrue(pipe.stopped)
        self.assertEqual((rec.state, rec.name, rec.name_ready), ("idle", "CAST-NEW00", True))
        self.assertEqual(restarted, ["CAST-NEW00"])

    def test_busy_drops_the_iphone(self):
        async def go():
            async def notify(msg):
                pass
            rec = Receiver("CAST-ABCDE", "OUT-1", (1920, 1080), 0, 1028, "reject", notify, FakePipeline)
            rec.current = object()                 # a Miracast cast is on
            return await rec.on_airplay("<iphone>")
        self.assertIsNone(asyncio.run(go()))


class TestNames(unittest.TestCase):
    MID = "0" * 32       # made-up machine ID

    def test_session_name_format(self):
        n = naming.session_name(0, "1", self.MID)
        self.assertRegex(n, r"^CAST-[0-9A-HJKMNP-Z]{5}$")
        self.assertEqual(n, naming.session_name(0, "1", self.MID))          # same session: same name

    def test_new_name_per_session_and_screen(self):
        names = {naming.session_name(i, str(salt), self.MID) for i in range(2) for salt in range(200)}
        self.assertEqual(len(names), 400)
        self.assertNotEqual(naming.session_name(0, "1", self.MID), naming.session_name(0, "1", "f" * 32))

    def test_pinned_name_wins(self):
        c = cfgmod.parse({"screen": [{"output": "OUT-1", "name": "<pinned>"}]})
        self.assertEqual(c.pinned_name("OUT-1"), "<pinned>")
        self.assertEqual(c.pinned_name("OUT-2"), "")

    def test_container_id_follows_the_name(self):
        a = mdns.container_id(7250, self.MID, "CAST-AAAAA")
        self.assertNotEqual(a, mdns.container_id(7250, self.MID, "CAST-BBBBB"))
        self.assertEqual(a, mdns.container_id(7250, self.MID, "CAST-AAAAA"))


class TestRenameAfterCast(unittest.TestCase):
    def test_on_ended_runs_and_state_carries_the_name(self):
        async def go():
            events, renamed = [], []

            async def notify(msg):
                events.append(msg)

            async def on_ended(r):
                renamed.append(r.name)
                await asyncio.sleep(0.05)               # setting up the new name takes a moment
                await r.show_name("CAST-NEW00")

            src = FakeSource(keepalive=True, hold=True)
            rtsp_port = await src.start()
            rec = Receiver("CAST-OLD00", "OUT-1", (1920, 1080), 0, 1028, "reject", notify, FakePipeline,
                           on_ended=on_ended)
            await rec.start()
            rec.route = "network"                          # casthub-p2p: how senders reach us
            await rec.show_name()                          # the hub: findable now
            _r, w = await asyncio.open_connection("127.0.0.1", rec.mice_port)
            w.write(mp.source_ready("<pc-name>", rtsp_port, SOURCE_ID))
            for _ in range(100):
                if rec.state == "streaming":
                    break
                await asyncio.sleep(0.02)
            w.write(mp.stop_projection("<pc-name>", SOURCE_ID))
            for _ in range(150):
                if rec.name_ready and rec.name == "CAST-NEW00":
                    break
                await asyncio.sleep(0.02)
            w.close()
            await rec.stop()
            return renamed, events

        renamed, events = run(go())
        self.assertEqual(renamed, ["CAST-OLD00"])
        idle = [e["name"] for e in events if e["cmd"] == "state" and e["state"] == "idle"]
        self.assertEqual(idle[0], "")                              # Loading until the hub says so
        self.assertEqual(idle[1], "CAST-OLD00")
        self.assertEqual(idle[-2:], ["", "CAST-NEW00"])            # Loading after the cast, then the new name


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

    def test_mac(self):
        self.assertEqual(p2p.mac(bytes.fromhex("00005e005301")), "00:00:5e:00:53:01")

    def test_mice_ip_address_attribute(self):
        show = "IP4.ADDRESS[1]:192.0.2.20/24\nIP4.ADDRESS[2]:192.0.2.21/24\n"
        self.assertEqual(p2p.ipv4_addresses(show), ["192.0.2.20", "192.0.2.21"])
        self.assertEqual(p2p.ipv4_addresses("IP4.GATEWAY:192.0.2.1\n"), [])
        payload = p2p.mice_vendor_payload("receiver-host", ["192.0.2.20"])
        # [MS-MICE] "IP Address Attribute": 0x2005, length, the address as ASCII text
        self.assertTrue(payload.endswith(bytes.fromhex("2005000a") + b"192.0.2.20"))

    def test_new_p2p_address(self):
        for _ in range(200):
            addr = p2p.random_address()
            self.assertEqual(len(addr), 6)
            self.assertEqual(addr[0] & 0x03, 0x02)          # locally administered, unicast
        conf = p2p.supplicant_conf(bytes.fromhex("00005e005301"))
        self.assertIn("p2p_device_random_mac_addr=2\n", conf)
        self.assertIn("p2p_device_persistent_mac_addr=00:00:5e:00:53:01\n", conf)
        self.assertIn("update_config=0\n", conf)

    def test_p2p_device_address_from_iw(self):
        iw = ("phy#0\n\tUnnamed/non-netdev interface\n\t\twdev 0xb\n\t\taddr 00:00:5E:00:53:02\n"
              "\t\ttype P2P-device\n\tInterface wlan0\n\t\taddr 00:00:5e:00:53:01\n\t\ttype managed\n")
        self.assertEqual(p2p.p2p_device_address(iw), "00:00:5e:00:53:02")
        self.assertEqual(p2p.p2p_device_address("phy#0\n\tInterface wlan0\n\t\ttype managed\n"), "")

    def test_hub_asks_for_a_new_address_only_when_idle(self):
        from types import SimpleNamespace

        from casthub.hub.__main__ import Hub
        first, other = SimpleNamespace(output="OUT-1", state="idle"), SimpleNamespace(output="OUT-2", state="idle")
        cfg = SimpleNamespace(pinned_name=lambda out: "")
        hub = SimpleNamespace(cfg=cfg, receivers=[first, other])
        self.assertTrue(Hub.wants_new_address(hub, first))
        other.state = "streaming"
        self.assertFalse(Hub.wants_new_address(hub, first))
        other.state, first.state = "idle", "ending"             # the one that just ended may not be idle yet
        self.assertTrue(Hub.wants_new_address(hub, first))
        cfg.pinned_name = lambda out: "Pinned"
        self.assertFalse(Hub.wants_new_address(hub, first))

    def test_peer_address(self):
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


class FakeChild:
    """Swap the video process for a small script (no GStreamer)."""

    def __init__(self, script: str):
        self.script, self.old = script, None

    def __enter__(self):
        from casthub.media import player
        self.old = player.BOOT
        player.BOOT = self.script
        return player

    def __exit__(self, *exc):
        from casthub.media import player
        player.BOOT = self.old


class TestPlayerProcess(unittest.TestCase):
    def test_reports_and_stops(self):
        script = ("import json,sys,time\n"
                  "p=lambda **e: (print(json.dumps(e)), sys.stdout.flush())\n"
                  "p(event='started'); p(event='first_frame'); p(event='packets', silent=0.2, seen=True)\n"
                  "time.sleep(30)\n")

        async def go():
            first = asyncio.Event()
            with FakeChild(script) as player:
                pp = player.PlayerProcess(media.MediaSpec(5000, "t"), first.set, lambda e: None)
                await pp.start()
                await asyncio.wait_for(first.wait(), 5)
                await asyncio.sleep(0.2)
                seen, silent = pp.last_packet, pp.silent_for()
                pp.stop()
                rc = await asyncio.wait_for(pp.proc.wait(), 5)
            return seen, silent, rc

        seen, silent, rc = run(go())
        self.assertEqual(seen, 1.0)
        self.assertGreater(silent, 0.2)            # keeps counting while the child is quiet
        self.assertNotEqual(rc, 0)                 # terminated

    def test_a_child_ignoring_sigterm_is_killed(self):
        script = ("import json,signal,sys,time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                  "print(json.dumps({'event': 'started'})); sys.stdout.flush(); time.sleep(30)\n")

        async def go():
            with FakeChild(script) as player:
                pp = player.PlayerProcess(media.MediaSpec(5000, "t"), lambda: None, lambda e: None)
                await pp.start()
                pp.stop()
                return await asyncio.wait_for(pp.proc.wait(), player.KILL_AFTER_S + 3)

        self.assertEqual(run(go()), -9)

    def test_start_failure_raises(self):
        script = "import json\nprint(json.dumps({'event': 'failed', 'text': 'no decoder'}))\n"

        async def go():
            with FakeChild(script) as player:
                pp = player.PlayerProcess(media.MediaSpec(5000, "t"), lambda: None, lambda e: None)
                with self.assertRaises(RuntimeError) as cm:
                    await pp.start()
                return str(cm.exception)

        self.assertEqual(run(go()), "no decoder")

    def test_spec_round_trip(self):
        from casthub.media import player
        spec = media.MediaSpec(5000, "Cast Hub CAST-ABCDE", "OUT-1", capture="local/x.ts")
        self.assertEqual(player.decode_spec(player.encode_spec(spec)), spec)


class TestUiSync(unittest.TestCase):
    def test_state_reaches_a_ui_that_starts_late(self):
        import tempfile

        from casthub.hub.__main__ import UiLink

        async def go(tmp):
            link = UiLink()
            link.path = Path(tmp) / "ui.sock"
            await link.send({"cmd": "state", "output": "OUT-1", "state": "idle", "name": "CAST-ABCDE"})
            got = []

            async def ui(r, w):
                got.append(json.loads(await r.readline()))
                w.write(b'{"ok": true}\n')
                await w.drain()
                w.close()
            server = await asyncio.start_unix_server(ui, str(link.path))
            sync = asyncio.create_task(link.keep_in_sync(every=0.05))
            for _ in range(100):
                if got:
                    break
                await asyncio.sleep(0.02)
            sync.cancel()
            server.close()
            return got

        with tempfile.TemporaryDirectory() as tmp:
            got = run(go(tmp))
        self.assertEqual(got[0]["name"], "CAST-ABCDE")


class TestRoute(unittest.TestCase):
    def test_name_waits_for_the_route(self):
        async def go():
            events = []

            async def notify(msg):
                events.append(msg)
            rec = Receiver("CAST-ABCDE", "OUT-1", (1920, 1080), 0, 1028, "reject", notify, FakePipeline)
            await rec.show_name()                         # findable, but the hotspot isn't decided yet
            await rec.set_route("hotspot")
            await rec.set_route("hotspot")                # no change: no new message
            return [(e["name"], e["route"]) for e in events if e["cmd"] == "state"]
        self.assertEqual(asyncio.run(go()), [("", ""), ("CAST-ABCDE", "hotspot")])
