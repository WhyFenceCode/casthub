"""M10: Cast Hub as a Miracast source. Format choice, and the handshake against our own sink."""

import asyncio
import unittest

from casthub.rtsp import wfd
from casthub.rtsp.sink import RtspSink, SinkCaps
from casthub.rtsp.source import RtspSource

# A typical TV: CEA 640x480p60, 720p30/60, 1080p30/60 (bits 0, 5, 6, 7, 8), CBP + CHP entries.
TV_FORMATS = ("40 00 01 10 000001e1 00000000 00000000 00 0000 0000 00 none none, "
              "02 10 000001e1 00000000 00000000 00 0000 0000 00 none none")


class TestChoice(unittest.TestCase):
    def test_parse_tv(self):
        modes, profiles, levels = wfd.parse_sink_video_formats(TV_FORMATS)
        self.assertEqual(profiles, 0x03)
        self.assertIn(wfd.Mode("CEA", 8, 1920, 1080, 60), modes)
        self.assertEqual(wfd.choose_mode(modes), wfd.Mode("CEA", 7, 1920, 1080, 30))   # 1080p30 over p60
        self.assertEqual(wfd.choose_mode(modes, max_fps=60), wfd.Mode("CEA", 8, 1920, 1080, 60))
        self.assertEqual(wfd.choose_mode(modes, max_size=(1280, 720)).height, 720)

    def test_m4_is_one_mode_and_round_trips(self):
        modes, profiles, levels = wfd.parse_sink_video_formats(TV_FORMATS)
        value = wfd.source_video_format(wfd.choose_mode(modes), profiles, levels)
        got = wfd.parse_video_format(value)             # what our own sink reads from it
        self.assertEqual((got.width, got.height, got.fps, got.profile), (1920, 1080, 30, "constrained-high"))

    def test_baseline_only_sink(self):
        modes, profiles, levels = wfd.parse_sink_video_formats(
            "00 00 01 01 00000001 00000000 00000000 00 0000 0000 00 none none")
        value = wfd.source_video_format(wfd.choose_mode(modes), profiles, levels)
        self.assertEqual(value.split()[2:4], ["01", "01"])
        self.assertEqual(wfd.parse_video_format(value).width, 640)

    def test_audio(self):
        self.assertEqual(wfd.choose_audio("LPCM 00000003 00, AAC 00000001 00")[1].codec, "AAC")
        self.assertEqual(wfd.choose_audio("LPCM 00000002 00")[0], "LPCM 00000002 00")
        self.assertIsNone(wfd.choose_audio("none"))


class TestLoopback(unittest.TestCase):
    def run_pair(self, end_from: str):
        async def go():
            src = RtspSource("127.0.0.1", 0, our_host="127.0.0.1", max_size=(1280, 720))
            port = await src.listen()
            idr = []
            src.on_idr = lambda: idr.append(1)
            sink = RtspSink("127.0.0.1", port, SinkCaps(1280, 720, 1028, "<sink>"))
            session, negotiated = await asyncio.gather(src.accept(), sink.connect())
            serving = asyncio.ensure_future(sink.serve())
            src_serving = asyncio.ensure_future(src.serve())
            await sink.request_idr()
            if end_from == "source":
                await src.teardown()
            else:
                await sink.teardown()
            why_sink = await asyncio.wait_for(serving, 5)
            why_src = await asyncio.wait_for(src_serving, 5)
            return session, negotiated, idr, why_sink, why_src
        return asyncio.run(go())

    def test_handshake_agrees(self):
        session, negotiated, idr, _, why_src = self.run_pair("sink")
        self.assertEqual((session.video.width, session.video.height), (negotiated.video.width, negotiated.video.height))
        self.assertEqual((session.video.width, session.video.height), (1280, 720))
        self.assertEqual(session.rtp_port, negotiated.rtp_port)
        self.assertEqual(session.audio.codec, "AAC")
        self.assertEqual(session.name, "<sink>")
        self.assertEqual(session.sink_host, "127.0.0.1")
        self.assertEqual(idr, [1])                                  # the sink's key-frame request reached us
        self.assertEqual(why_src, "teardown")

    def test_source_ends_it(self):
        _, _, _, why_sink, why_src = self.run_pair("source")
        self.assertIn(why_sink, ("teardown", "closed"))
        self.assertEqual(why_src, "teardown")


if __name__ == "__main__":
    unittest.main()
