"""Passthrough (M10, SPEC D29): what goes to the TV, as a Miracast source.

One sender pipeline runs for as long as passthrough is on:

    intervideosrc ─ VA-API H.264 ─┐
                                   ├─ mpegtsmux ─ rtpmp2tpay ─ udpsink → TV
    interaudiosrc ─ AAC ──────────┘

It runs at the TV's frame rate whatever comes in, so the TV keeps its Miracast session. Between
casts the input is the TV's own screen (media/tvcard.py: waiting, connecting with its dots),
looped from images; a cast replaces it from its first frame until it ends. Each cast's input (our Miracast or AirPlay receive path,
media/pipeline.py with `tv` set) decodes, scales to the TV's mode with black borders and hands
frames and audio over through the inter elements: so it must run in this same process. TV only:
nothing plays on our screens or speakers meanwhile.

WFD wants fixed PIDs: video 0x1011, audio 0x1100 (mpegtsmux pads named sink_<pid>), the program
map on 0x0100. A real TV took our first stream (PMT on mpegtsmux's default PID) and showed nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from casthub.media import pipeline as media

log = logging.getLogger("casthub.media")
VIDEO_PID, AUDIO_PID = 0x1011, 0x1100
PMT_PID = 0x0100                  # WFD's program map PID (program 1), as Android's source sends it
KEYFRAME_EVERY_S = 1              # a TV that joins late or loses packets recovers within a second


@dataclass(frozen=True)
class TvTarget:
    host: str
    port: int                     # the TV's RTP port (wfd_client_rtp_ports)
    width: int
    height: int
    fps: int
    profile: str = "constrained-high"      # or constrained-baseline (from the handshake)
    audio: str = "AAC"                     # AAC | LPCM | "" (none)
    bitrate_kbps: int = 5000              # the TV link is 2.4 GHz, sharing the radio with our Wi-Fi
    src_port: int = 0             # our RTP port as SETUP told the TV (server_port); 0 = any

    @property
    def tv(self) -> str:
        """For MediaSpec.tv: what the cast's input scales to."""
        return f"{self.width}x{self.height}@{self.fps}"


def describe_card(t: TvTarget, directory: str) -> str:
    """The TV's own screen as an input: tvcard.py's images, looped at its frame rate."""
    from casthub.media import tvcard
    last = tvcard.FRAMES - 1
    return (f"multifilesrc location={directory}/card-%d.png index=0 start-index=0 stop-index={last} loop=true"
            f" caps=image/png,framerate={tvcard.FPS}/1 ! pngdec ! videoconvert ! videoscale"
            f" ! video/x-raw,format=NV12,width={t.width},height={t.height},pixel-aspect-ratio=1/1"
            f" ! intervideosink channel={media.TV_CHANNEL} name=card")


def describe_sender(t: TvTarget) -> str:
    """gst-launch description of the sender pipeline."""
    baseline = t.profile == "constrained-baseline"
    enc = (f"vah264enc name=enc rate-control=cbr bitrate={t.bitrate_kbps} key-int-max={t.fps * KEYFRAME_EVERY_S}"
           f" b-frames=0 aud=true{' cabac=false' if baseline else ''}")     # AUDs: TVs split frames by them
    video = (f"intervideosrc channel={media.TV_CHANNEL} name=vsrc"
             f" ! video/x-raw,format=NV12,width={t.width},height={t.height},framerate={t.fps}/1,pixel-aspect-ratio=1/1"
             f" ! queue max-size-buffers=4 leaky=downstream ! vapostproc ! {enc}"
             f" ! video/x-h264,profile={'constrained-baseline' if baseline else 'high'},stream-format=byte-stream"
             f" ! h264parse config-interval=-1 ! queue ! mux.sink_{VIDEO_PID}")
    progs = f"program_map,sink_{VIDEO_PID}=(int)1,sink_{AUDIO_PID}=(int)1,PMT_1=(uint){PMT_PID}"
    out = (f" mpegtsmux name=mux alignment=7 prog-map=\"{progs}\" ! rtpmp2tpay name=pay pt=33"
           f" ! udpsink name=out host={t.host} port={t.port}"
           # From the server_port we gave in SETUP: a TV took a stream from another port, sent
           # receiver reports that counted none of it, and stayed black (live test 8).
           f"{f' bind-port={t.src_port}' if t.src_port else ''} sync=false async=false")
    audio = ""
    if t.audio == "AAC":
        audio = (f" interaudiosrc channel={media.TV_CHANNEL} name=asrc ! {media.TV_AUDIO} ! audioconvert"
                 f" ! fdkaacenc bitrate=128000 ! aacparse ! audio/mpeg,mpegversion=4,stream-format=adts"
                 f" ! queue ! mux.sink_{AUDIO_PID}")
    # LPCM-only TVs: no audio for now (mpegtsmux's LPCM isn't the WFD flavour); logged by the caller.
    return video + audio + out


class TvSender:
    """The sender pipeline plus the current cast's input pipeline, both in this process."""

    def __init__(self, target: TvTarget, on_error: Callable[[str], None]) -> None:
        self.target, self.on_error = target, on_error
        self.pipe = None
        self.input: media.Pipeline | None = None
        self.card = None                    # the TV's own screen between casts (a pipeline)
        self.card_dir = ""                  # tvcard.py's images; "" = black between casts
        self._poll: asyncio.Task | None = None
        self.sent = 0                       # RTP packets out (diagnostics, tests)

    def start(self) -> None:
        g = media.gst()
        desc = describe_sender(self.target)
        log.info("tv sender: %s", desc)
        if self.target.audio not in ("AAC", ""):
            log.warning("tv: the TV only takes %s audio; sending video only", self.target.audio)
        self.pipe = g.parse_launch(desc)

        def count(_pad, _info):
            self.sent += 1
            return g.PadProbeReturn.OK
        self.pipe.get_by_name("pay").get_static_pad("src").add_probe(g.PadProbeType.BUFFER, count)
        if self.pipe.set_state(g.State.PLAYING) == g.StateChangeReturn.FAILURE:
            self.stop()
            raise RuntimeError("tv sender did not start")
        self._poll = asyncio.create_task(self._watch())

    def keyframe(self) -> None:
        """The TV asked for one (wfd_idr_request)."""
        if self.pipe is None:
            return
        g = media.gst()
        import gi
        gi.require_version("GstVideo", "1.0")
        from gi.repository import GstVideo
        enc = self.pipe.get_by_name("enc")
        enc.get_static_pad("src").send_event(GstVideo.video_event_new_upstream_force_key_unit(g.CLOCK_TIME_NONE, True, 0))

    def show_card(self) -> None:
        """The TV's own screen (between casts, and while a cast connects)."""
        if self.card is not None or not self.card_dir:
            return
        g = media.gst()
        try:
            self.card = g.parse_launch(describe_card(self.target, self.card_dir))
        except Exception as e:          # noqa: BLE001 (GLib errors): black instead of the card
            log.warning("tv card: %s", e)
            return
        self.card.set_state(g.State.PLAYING)

    def hide_card(self) -> None:
        if self.card is not None:
            self.card.set_state(media.gst().State.NULL)
            self.card = None

    def play(self, spec: media.MediaSpec, on_first_frame: Callable[[], None], on_error: Callable[[str], None]) -> None:
        """Start a cast's input (ends the previous one). spec.tv is set to the TV's mode here.
        The card ("connecting") stays until the cast's first frame."""
        self.end_input()
        import dataclasses
        spec = dataclasses.replace(spec, tv=self.target.tv, mirrors=())

        def first_frame() -> None:
            self.hide_card()
            self.keyframe()                 # the new picture starts clean on the TV
            on_first_frame()
        self.input = media.Pipeline(spec, first_frame, on_error)
        self.input.start()
        self.show_card()

    def end_input(self) -> None:
        if self.input:
            self.input.stop()
            self.input = None
        self.show_card()

    async def _watch(self) -> None:
        g = media.gst()
        bus = self.pipe.get_bus()
        while self.pipe is not None:
            msg = bus.pop_filtered(g.MessageType.ERROR | g.MessageType.EOS)
            if msg is None:
                await asyncio.sleep(0.1)
                continue
            if msg.type == g.MessageType.ERROR:
                e, dbg = msg.parse_error()
                log.error("tv sender: %s (%s)", e.message, dbg)
                self.on_error(e.message)
            else:
                self.on_error("end of stream")
            return

    def stop(self) -> None:
        self.card_dir = ""
        self.end_input()
        self.hide_card()
        if self._poll and self._poll is not asyncio.current_task():
            self._poll.cancel()
        if self.pipe is not None:
            with contextlib.suppress(Exception):
                self.pipe.set_state(media.gst().State.NULL)
            self.pipe = None
