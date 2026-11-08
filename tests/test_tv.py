"""M10 stage 2: the TV sender. Its pipeline, and a live loopback when VA-API is present."""

import asyncio
import os
import unittest

from casthub.media import pipeline as media
from casthub.media.tv import TvSender, TvTarget, describe_sender


class TestDescriptions(unittest.TestCase):
    def test_sender(self):
        d = describe_sender(TvTarget("192.0.2.10", 1028, 1920, 1080, 30))
        self.assertIn("width=1920,height=1080,framerate=30/1", d)
        self.assertIn("profile=high", d)
        self.assertIn("b-frames=0", d)                                   # CHP: no B-frames
        self.assertIn("mux.sink_4113", d)                                # WFD video PID 0x1011
        self.assertIn("mux.sink_4352", d)                                # WFD audio PID 0x1100
        self.assertIn("udpsink name=out host=192.0.2.10 port=1028", d)
        base = describe_sender(TvTarget("192.0.2.10", 1028, 640, 480, 60, profile="constrained-baseline", audio=""))
        self.assertIn("cabac=false", base)
        self.assertNotIn("interaudiosrc", base)

    def test_cast_input_hands_over(self):
        for source in ("miracast", "airplay"):
            d = media.describe(media.MediaSpec(1028, "t", mirrors=("OUT-2",), source=source, audio_port=1034,
                                               tv="1280x720@30"))
            self.assertIn("intervideosink channel=casthub-tv name=vsink", d)
            self.assertIn("interaudiosink channel=casthub-tv", d)
            self.assertIn("add-borders=true", d)
            self.assertNotIn("glimagesink", d)                           # TV only: no windows
            self.assertNotIn("pipewiresink", d)                          # and nothing on our speakers


LIVE = not os.environ.get("CASTHUB_NO_GST")


@unittest.skipUnless(LIVE, "CASTHUB_NO_GST set")
class TestLoopback(unittest.TestCase):
    IN_PORT, TV_PORT = 15028, 15100

    def setUp(self):
        try:
            missing = media.available("vah264enc", "vah264dec", "vapostproc", "intervideosrc", "fdkaacenc",
                                      "mpegtsmux", "tsdemux")
        except (ImportError, ValueError):
            missing = ["gstreamer"]
        if missing:
            self.skipTest(f"missing {missing}")

    def test_cast_then_black(self):
        g = media.gst()

        async def go():
            tv = g.parse_launch(
                f'udpsrc port={self.TV_PORT} caps="application/x-rtp,media=video,clock-rate=90000,encoding-name=MP2T"'
                " ! rtpmp2tdepay ! tsdemux name=d d. ! queue ! h264parse ! fakesink name=v d. ! queue ! fakesink name=a")
            counts = {"v": 0, "a": 0}

            def counter(n):
                def probe(_p, _i):
                    counts[n] += 1
                    return g.PadProbeReturn.OK
                return probe
            for n in counts:
                tv.get_by_name(n).get_static_pad("sink").add_probe(g.PadProbeType.BUFFER, counter(n))
            tv.set_state(g.State.PLAYING)
            errors = []
            sender = TvSender(TvTarget("127.0.0.1", self.TV_PORT, 1280, 720, 30), errors.append)
            sender.start()
            first = asyncio.Event()
            sender.play(media.MediaSpec(self.IN_PORT, "t"), first.set, errors.append)
            pc = g.parse_launch(
                "videotestsrc is-live=true ! video/x-raw,width=960,height=540,framerate=30/1 ! vapostproc ! vah264enc"
                f" ! h264parse ! mpegtsmux name=m alignment=7 ! rtpmp2tpay ! udpsink host=127.0.0.1 port={self.IN_PORT}"
                " audiotestsrc is-live=true ! audio/x-raw,rate=48000,channels=2 ! fdkaacenc ! aacparse ! m.")
            pc.set_state(g.State.PLAYING)
            await asyncio.sleep(2.5)
            cast = dict(counts)
            pc.set_state(g.State.NULL)
            sender.end_input()
            await asyncio.sleep(1.5)
            idle = {k: counts[k] - cast[k] for k in counts}
            caps = tv.get_by_name("v").get_static_pad("sink").get_current_caps()
            sender.stop()
            tv.set_state(g.State.NULL)
            return cast, idle, first.is_set(), errors, caps.get_structure(0)
        cast, idle, first, errors, caps = asyncio.run(go())
        self.assertEqual(errors, [])
        self.assertTrue(first)
        self.assertGreater(cast["v"], 30)                                # ~30 fps reached the "TV"
        self.assertGreater(cast["a"], 10)
        self.assertGreater(idle["v"], 20)                                # black keeps the TV's session fed
        self.assertEqual((caps.get_value("width"), caps.get_value("height")), (1280, 720))


if __name__ == "__main__":
    unittest.main()
