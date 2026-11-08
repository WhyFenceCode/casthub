"""GStreamer pipeline for one Miracast stream: MPEG-TS over RTP/UDP → H.264 on the GPU + AAC.

    udpsrc ! rtpjitterbuffer ! rtpmp2tdepay ! tsdemux ─┬ h264parse ! vah264dec ! glimagesink
                                                      └ aacparse ! avdec_aac ! pipewiresink

AirPlay (source "airplay"): UxPlay speaks AirPlay and hands us the iPhone's H.264 as RTP and
its decoded audio as L16 RTP on localhost (casthub/airplay/uxplay.py):

    udpsrc ! rtpjitterbuffer ! rtph264depay ! h264parse ! vah264dec ! glimagesink
    udpsrc ! rtpjitterbuffer ! rtpL16depay ! pipewiresink

Mirrored screens (layout mirror_all): the video is decoded once, then a tee feeds one
glimagesink per screen; place_on_outputs() puts one window on each.

No GLib main loop is needed: the bus is polled from asyncio, and the first-frame probe hands
over to the event loop with call_soon_threadsafe.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
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
    mirrors: tuple[str, ...] = ()   # more outputs showing the same video (one more window each)
    audio: bool = True
    decoder: str = "vah264dec"      # software fallback: avdec_h264
    video_sink: str = "glimagesink"    # waylandsink crashed the hub when the output changed size
    audio_sink: str = "pipewiresink"
    latency_ms: int = 120
    capture: str = ""               # also write the PC's MPEG-TS to this file (debugging; keep it in local/)
    source: str = "miracast"        # miracast (MPEG-TS over RTP) | airplay (H.264 + L16 RTP from UxPlay)
    audio_port: int = 0             # airplay: the L16 RTP port (0 = no audio)
    video: bool = True              # airplay: False = audio only (its own player, see receiver.run_airplay)


STATS_S = 5
PLACE_TRIES = 15                # × 0.2 s
# (counter, element, pad): network → jitter buffer → RTP unwrap → demuxed video/audio → parsed → shown
STAGES = [("net", "src", "src"), ("jitter", "jb", "src"), ("rtp-out", "depay", "src"),
          ("video-demuxed", "vq", "sink"), ("audio-demuxed", "aq", "sink"), ("parsed", "vparse", "src"),
          ("shown", "vsink", "sink"), ("audio-out", "asink", "sink")]


def describe(spec: MediaSpec) -> str:
    """gst-launch description of the pipeline (also what the hub logs)."""
    caps = "application/x-rtp,media=video,clock-rate=90000,encoding-name=MP2T"

    def video_sink(output: str, name: str) -> str:
        sink = spec.video_sink
        if sink in ("glimagesink", "waylandsink"):
            # sync=false: show each frame when it is decoded (the PC's timestamps don't match our
            # clock). async=false: see the audio sink below.
            sink += " sync=false async=false"
        if spec.video_sink == "waylandsink":
            # Not the default: GStreamer 1.28's Wayland plugin segfaulted in output_done when the
            # output changed size (resizing the Display panel took the whole hub down), with or
            # without fullscreen-output. glimagesink survives it; Sway fullscreens it, and
            # place_on_outputs() moves it to the receiver's screen.
            sink += " fullscreen=true"
            if output:
                sink += f" fullscreen-output={output}"
        return f"{sink} name={name}"

    if spec.mirrors:
        # One decode, one window per screen. Each branch drops old frames rather than hold
        # the others up (a slow screen must not freeze the rest).
        branch = "queue leaky=downstream max-size-buffers=2 max-size-bytes=0 max-size-time=0"
        video = f" ! tee name=vt vt. ! {branch} ! {video_sink(spec.output, 'vsink')}" + "".join(
            f" vt. ! {branch} ! {video_sink(out, f'vsink{i + 1}')}" for i, out in enumerate(spec.mirrors))
    else:
        video = f" ! {video_sink(spec.output, 'vsink')}"
    if spec.source == "airplay":
        return _describe_airplay(spec, video)
    parts = [
        f'udpsrc name=src port={spec.rtp_port} buffer-size=2097152 caps="{caps}"',
        f"rtpjitterbuffer name=jb latency={spec.latency_ms}",
        "rtpmp2tdepay name=depay" + (" ! tee name=cap ! queue" if spec.capture else ""), "tsdemux name=demux latency=0",
        "demux. ! video/x-h264 ! queue name=vq max-size-time=0 max-size-bytes=0 max-size-buffers=64 ! h264parse name=vparse"
        f" ! {spec.decoder}{video}",
    ]
    desc = " ! ".join(parts[:4]) + " " + parts[4]
    if spec.audio:
        # async=false on both sinks (the real cause of the frozen picture): Windows lists an AAC
        # track but sends nothing on it while the PC is silent. An async audio sink then never
        # prerolls, the pipeline never reaches PLAYING, and the video sink waits after its first
        # frame for good. Reproduced from a capture; with async=false every frame plays.
        # tsdemux feeds audio and video from one thread: if audio backs up, video stops too
        # (the picture froze after 2 frames). So audio never waits on the PC's timestamps, and
        # its queue drops old audio rather than block the demuxer.
        desc += (f" demux. ! audio/mpeg ! queue name=aq leaky=downstream max-size-time=500000000 ! aacparse ! avdec_aac name=adec"
                 f" ! audioconvert ! audioresample ! {AUDIO_OUT} ! {spec.audio_sink} name=asink sync=false async=false")
    if spec.capture:   # the stream exactly as the PC sent it, to replay with filesrc ! tsdemux
        desc += f' cap. ! queue leaky=downstream ! filesink location="{spec.capture}" sync=false async=false'
    return desc


AIRPLAY_VIDEO_CAPS = "application/x-rtp,media=video,clock-rate=90000,encoding-name=H264"
# UxPlay decodes the iPhone's AAC-ELD to 44.1 kHz stereo before rtpL16pay.
AIRPLAY_AUDIO_CAPS = "application/x-rtp,media=audio,clock-rate=44100,encoding-name=L16,encoding-params=2,channels=2"


def _describe_airplay(spec: MediaSpec, video: str) -> str:
    """UxPlay's RTP on localhost. The element names match the Miracast pipeline, so the stage
    counters and first-frame probe work the same. Video and audio play in separate players:
    the video one waits, listening, while the receiver is idle, and an idle audio stream would
    hold PipeWire's speaker output suspended (Windows casts were silent)."""
    desc = ""
    if spec.video:
        desc = (f'udpsrc name=src address=127.0.0.1 port={spec.rtp_port} buffer-size=2097152'
                f' caps="{AIRPLAY_VIDEO_CAPS}" ! rtpjitterbuffer name=jb latency={spec.latency_ms}'
                " ! rtph264depay name=depay ! queue name=vq max-size-time=0 max-size-bytes=0 max-size-buffers=64"
                f" ! h264parse name=vparse ! {spec.decoder}{video}")
    if spec.audio and spec.audio_port:
        desc += (f' udpsrc name={"src" if not spec.video else "asrc"} address=127.0.0.1 port={spec.audio_port}'
                 f' caps="{AIRPLAY_AUDIO_CAPS}"'
                 f" ! rtpjitterbuffer latency={spec.latency_ms} ! rtpL16depay"
                 " ! queue name=aq leaky=downstream max-size-time=500000000 ! audioconvert ! audioresample"
                 f" ! {AUDIO_OUT} ! {spec.audio_sink} name=asink sync=false async=false")
    return desc


# What the speakers get: one fixed format, converted to. The layout matters: avdec_aac (Windows'
# AAC) decodes to non-interleaved (planar) audio, audioconvert passed it through, and
# pipewiresink played it as interleaved, so each channel ran at twice the speed, an octave up.
AUDIO_OUT = "audio/x-raw,format=F32LE,layout=interleaved,rate=48000,channels=2"


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
        self.audio_caps_logged = False
        self.last_packet = 0.0                 # monotonic time of the last RTP packet (0 = none yet)

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
                if stage == "net":
                    self.last_packet = time.monotonic()
                return g.PadProbeReturn.OK
            return probe

        # Every stage counts what passes it, so a frozen picture shows where data stops.
        vsink = self.pipe.get_by_name("vsink")
        if vsink is not None:                      # an audio-only player has none
            vsink.get_static_pad("sink").add_probe(g.PadProbeType.BUFFER, shown)
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

    def silent_for(self) -> float:
        """Seconds since the last RTP packet; 0 before the first one."""
        return time.monotonic() - self.last_packet if self.last_packet else 0.0

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
                if now != last:            # quiet while an AirPlay standby player waits
                    log.info("media in the last %d s: %s | jitterbuffer %s", STATS_S,
                             ", ".join(f"{k} {now[k] - last.get(k, 0)}" for k in now), self.jitter_stats())
                if not self.audio_caps_logged and now["audio-out"]:
                    self.audio_caps_logged = True
                    adec = self.pipe.get_by_name("adec")
                    caps = adec.get_static_pad("src").get_current_caps() if adec else None
                    log.info("decoded audio (before resampling to %s): %s", AUDIO_OUT,
                             caps.to_string() if caps else "?")
                if not self.caps_logged and now["parsed"] and self.pipe.get_by_name("vparse"):
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


def windows_of(tree: dict, pid: int) -> list[int]:
    """Sway con_ids of the windows of process `pid` in a `swaymsg -t get_tree` tree, oldest first."""
    found, todo = [], [tree]
    while todo:
        node = todo.pop()
        if node.get("pid") == pid and node.get("type") in ("con", "floating_con"):
            found.append(node["id"])
        todo += node.get("nodes", []) + node.get("floating_nodes", [])
    return sorted(found)


async def _swaymsg(*args: str) -> tuple[int, bytes]:
    with contextlib.suppress(OSError):
        proc = await asyncio.create_subprocess_exec("swaymsg", *args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.DEVNULL)
        out, _ = await proc.communicate()
        return proc.returncode or 0, out
    return 1, b""


async def place_on_outputs(outputs: tuple[str, ...] | list[str], pid: int | None = None) -> None:
    """Under Sway, put the video windows of process `pid` (default: this one) fullscreen, one
    on each of `outputs` (glimagesink has no output choice of its own). Each cast plays in its
    own process (media/player.py), so the pid picks exactly that cast's windows; a mirrored
    cast has one window per screen."""
    outputs = [o for o in outputs if o]
    if not outputs or not os.environ.get("SWAYSOCK"):
        return
    pid = pid or os.getpid()
    # The first frame can arrive before every window is mapped: retry briefly.
    for _ in range(PLACE_TRIES):
        rc, out = await _swaymsg("-t", "get_tree")
        wins = []
        if rc == 0:
            with contextlib.suppress(ValueError):
                wins = windows_of(json.loads(out), pid)
        if len(wins) >= len(outputs):
            for con, output in zip(wins, outputs, strict=False):
                await _swaymsg(f"[con_id={con}] move container to output {output}, fullscreen enable")
            return
        await asyncio.sleep(0.2)
    log.warning("could not put the video on %s", ", ".join(outputs))


async def place_on_output(output: str, pid: int | None = None) -> None:
    await place_on_outputs([output], pid)


def default_spec(rtp_port: int, title: str, output: str = "", mirrors: tuple[str, ...] = (),
                 source: str = "miracast", audio_port: int = 0) -> MediaSpec:
    """Environment overrides for testing: CASTHUB_DECODER, CASTHUB_VIDEO_SINK, CASTHUB_CAPTURE (a file)."""
    return MediaSpec(rtp_port, title, output, tuple(mirrors), source=source, audio_port=audio_port,
                     decoder=os.environ.get("CASTHUB_DECODER", "vah264dec"),
                     video_sink=os.environ.get("CASTHUB_VIDEO_SINK", "glimagesink"),
                     capture=os.environ.get("CASTHUB_CAPTURE", ""))
