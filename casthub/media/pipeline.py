"""GStreamer pipeline for one Miracast stream: MPEG-TS over RTP/UDP → H.264 on the GPU + AAC.

    udpsrc ! rtpjitterbuffer ! rtpmp2tdepay ! tsdemux ─┬ h264parse ! vah264dec ! waylandsink
                                                      └ aacparse ! avdec_aac ! pipewiresink

No GLib main loop is needed: the bus is polled from asyncio, and the first-frame probe hands
over to the event loop with call_soon_threadsafe.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("casthub.media")

_gst = None


def gst():
    """Import and init GStreamer on first use (tests that don't stream never load it)."""
    global _gst
    if _gst is None:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        _gst = Gst
    return _gst


@dataclass(frozen=True)
class MediaSpec:
    rtp_port: int
    title: str                      # PipeWire client name
    output: str = ""                # Wayland output to go fullscreen on ("" = compositor's choice)
    audio: bool = True
    decoder: str = "vah264dec"      # software fallback: avdec_h264
    video_sink: str = "waylandsink"
    audio_sink: str = "pipewiresink"
    latency_ms: int = 120


STATS_S = 5
# (counter, element, pad): network → jitter buffer → RTP unwrap → demuxed video/audio → parsed → shown
STAGES = [("net", "src", "src"), ("jitter", "jb", "src"), ("rtp-out", "depay", "src"),
          ("video-demuxed", "vq", "sink"), ("audio-demuxed", "aq", "sink"), ("parsed", "vparse", "src"),
          ("shown", "vsink", "sink")]


def describe(spec: MediaSpec) -> str:
    """gst-launch description of the pipeline (also what the hub logs)."""
    caps = "application/x-rtp,media=video,clock-rate=90000,encoding-name=MP2T"
    sink = spec.video_sink
    if sink == "waylandsink":
        # sync=false: show each frame when it is decoded. With sync=true the sink waited on the
        # PC's timestamps, which don't match our clock; after the first frame every frame was
        # "late" and dropped, so the picture froze.
        sink += " fullscreen=true sync=false"
        if spec.output:
            sink += f" fullscreen-output={spec.output}"
    parts = [
        f'udpsrc name=src port={spec.rtp_port} buffer-size=2097152 caps="{caps}"',
        f"rtpjitterbuffer name=jb latency={spec.latency_ms}",
        "rtpmp2tdepay name=depay", "tsdemux name=demux latency=0",
        "demux. ! video/x-h264 ! queue name=vq max-size-time=0 max-size-bytes=0 max-size-buffers=64 ! h264parse name=vparse"
        f" ! {spec.decoder} ! {sink} name=vsink",
    ]
    desc = " ! ".join(parts[:4]) + " " + parts[4]
    if spec.audio:
        # tsdemux feeds audio and video from one thread: if audio backs up, video stops too
        # (the picture froze after 2 frames). So audio never waits on the PC's timestamps, and
        # its queue drops old audio rather than block the demuxer.
        desc += (f" demux. ! audio/mpeg ! queue name=aq leaky=downstream max-size-time=500000000 ! aacparse ! avdec_aac"
                 f" ! audioconvert ! audioresample ! {spec.audio_sink} name=asink sync=false")
    return desc


def available(*names: str) -> list[str]:
    """The element factories from `names` that are missing."""
    g = gst()
    return [n for n in names if g.ElementFactory.find(n) is None]


class Pipeline:
    """One running stream. `on_first_frame()` fires once a decoded frame reaches the sink; `on_error(text)` on failure."""

    def __init__(self, spec: MediaSpec, on_first_frame: Callable[[], None], on_error: Callable[[str], None]) -> None:
        self.spec, self.on_first_frame, self.on_error = spec, on_first_frame, on_error
        self.pipe = None
        self._poll: asyncio.Task | None = None
        self._seen_frame = False
        self.counts = dict.fromkeys([name for name, _, _ in STAGES], 0)   # bumped on streaming threads
        self.caps_logged = False

    def start(self) -> None:
        g = gst()
        loop = asyncio.get_running_loop()
        desc = describe(self.spec)
        log.info("pipeline: %s", desc)
        self.pipe = g.parse_launch(desc)
        asink = self.pipe.get_by_name("asink")
        if asink is not None and asink.find_property("client-name") is not None:
            asink.set_property("client-name", self.spec.title)

        def shown(_pad, _info):
            self.counts["shown"] += 1
            if not self._seen_frame:
                self._seen_frame = True
                loop.call_soon_threadsafe(self.on_first_frame)
            return g.PadProbeReturn.OK

        def counter(stage):
            def probe(_pad, _info):
                self.counts[stage] += 1
                return g.PadProbeReturn.OK
            return probe

        # Every stage counts what passes it, so a frozen picture shows where data stops.
        self.pipe.get_by_name("vsink").get_static_pad("sink").add_probe(g.PadProbeType.BUFFER, shown)
        for stage, element, pad in STAGES:
            el = self.pipe.get_by_name(element)
            if el is not None and stage != "shown":
                el.get_static_pad(pad).add_probe(g.PadProbeType.BUFFER, counter(stage))
        if self.pipe.set_state(g.State.PLAYING) == g.StateChangeReturn.FAILURE:
            why = "pipeline did not start"
            bus = self.pipe.get_bus()
            while (msg := bus.pop()) is not None:
                if msg.type in (g.MessageType.ERROR, g.MessageType.WARNING):
                    e, dbg = msg.parse_error() if msg.type == g.MessageType.ERROR else msg.parse_warning()
                    why += f"; {msg.src.get_name()}: {e.message} ({dbg})"
            for el in self.pipe.iterate_elements():
                _, cur, pend = el.get_state(0)
                if cur != g.State.PLAYING:
                    why += f"; {el.get_name()} stuck in {cur.value_nick}"
            self.stop()
            raise RuntimeError(why)
        self._poll = asyncio.create_task(self._watch())

    def jitter_stats(self) -> str:
        jb = self.pipe.get_by_name("jb") if self.pipe else None
        if jb is None:
            return "-"
        st = jb.get_property("stats")
        return " ".join(f"{k.removeprefix('num-')}={st.get_value(k)}"
                        for k in ("num-pushed", "num-lost", "num-late", "num-duplicates") if st.has_field(k))

    async def _watch(self) -> None:
        g = gst()
        bus = self.pipe.get_bus()
        mask = g.MessageType.ERROR | g.MessageType.EOS | g.MessageType.WARNING
        loop = asyncio.get_running_loop()
        next_stats, last = loop.time() + STATS_S, {}
        while self.pipe is not None:
            if loop.time() >= next_stats:          # where data stops, if the picture freezes
                now = dict(self.counts)
                log.info("media in the last %d s: %s | jitterbuffer %s", STATS_S,
                         ", ".join(f"{k} {now[k] - last.get(k, 0)}" for k in now), self.jitter_stats())
                if not self.caps_logged and now["parsed"]:
                    self.caps_logged = True
                    caps = self.pipe.get_by_name("vparse").get_static_pad("src").get_current_caps()
                    log.info("video caps: %s", caps.to_string() if caps else "?")
                next_stats, last = loop.time() + STATS_S, now
            msg = bus.pop_filtered(mask)
            if msg is None:
                await asyncio.sleep(0.05)
                continue
            if msg.type == g.MessageType.WARNING:
                w, dbg = msg.parse_warning()
                log.warning("gst warning: %s (%s)", w.message, dbg)
            elif msg.type == g.MessageType.ERROR:
                e, dbg = msg.parse_error()
                log.error("gst error: %s (%s)", e.message, dbg)
                self.on_error(e.message)
                return
            else:
                self.on_error("end of stream")
                return

    def stop(self) -> None:
        if self._poll and self._poll is not asyncio.current_task():
            self._poll.cancel()
        if self.pipe is not None:
            with contextlib.suppress(Exception):
                self.pipe.set_state(gst().State.NULL)
            self.pipe = None


def default_spec(rtp_port: int, title: str, output: str = "") -> MediaSpec:
    """Environment overrides for testing on other machines: CASTHUB_DECODER, CASTHUB_VIDEO_SINK."""
    return MediaSpec(rtp_port, title, output,
                     decoder=os.environ.get("CASTHUB_DECODER", "vah264dec"),
                     video_sink=os.environ.get("CASTHUB_VIDEO_SINK", "waylandsink"))
