"""One receiver: MS-MICE listener + mDNS record + at most one stream, shown on every screen.

`output` is the main screen (its shape picks the video modes); `mirrors` are the other screens,
which show the same thing: the same idle page and the same video, decoded once.

State: idle → connecting → streaming → idle. A second sender while busy is rejected (the screen
shows a busy note) or replaces the first, per `miracast.busy_policy`.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from casthub.media import pipeline as media
from casthub.media import player
from casthub.mice import protocol as mp
from casthub.mice.listener import MiceConnection, MiceListener
from casthub.rtsp.sink import RtspError, RtspSink, SinkCaps

log = logging.getLogger("casthub.hub")
FIRST_FRAME_IDR_AFTER = 3.0
LINK_LOST_S = 8          # no RTP packets for this long: the link is gone, end the cast


@dataclass
class Source:
    addr: str
    rtsp_port: int
    name: str
    via: str                                # mice | p2p | airplay
    stop: asyncio.Event = field(default_factory=asyncio.Event)


class Receiver:
    def __init__(self, name: str, output: str, size: tuple[int, int], mice_port: int, rtp_port: int,
                 busy_policy: str, notify: Callable[[dict], Awaitable[None]],
                 pipeline_factory: Callable[..., player.PlayerProcess] = player.PlayerProcess,
                 on_ended: Callable[[Receiver], Awaitable[None]] | None = None,
                 size_now: Callable[[str], Awaitable[tuple[int, int] | None]] | None = None,
                 mirrors: tuple[str, ...] = (), airplay_ports: tuple[int, int] = (0, 0)) -> None:
        self.name, self.output, self.size = name, output, size
        self.mirrors = tuple(m for m in mirrors if m != output)
        self.airplay_ports = airplay_ports          # (video, audio) RTP from UxPlay on localhost
        # AirPlay: a player kept listening while idle (set by the hub when UxPlay runs). An
        # iPhone sends its SPS/PPS and its only keyframe as mirroring starts; a player started
        # then misses them, and the depayloader drops everything after (no picture, ever).
        self.airplay_standby = False
        self.standby: player.PlayerProcess | None = None
        self.standby_first: Callable[[], None] | None = None
        self.standby_error: Callable[[str], None] | None = None
        self.standby_framed = False                 # it showed a frame before the cast began
        self.on_state: Callable[[Receiver], Awaitable[None]] | None = None     # the hub: busy or not
        self.size_now = size_now          # the output's size right now (a window can be resized)
        self.on_ended = on_ended          # after each cast: the hub gives the receiver a new name
        # The name is shown only once PCs can find it (mDNS, Wi-Fi Direct): the hub calls
        # show_name(). Until then the screen says Loading.
        self.name_ready = False
        self.device = ""
        self.ended_task: asyncio.Task | None = None
        self.mice_port, self.rtp_port, self.busy_policy = mice_port, rtp_port, busy_policy
        self.notify, self.pipeline_factory = notify, pipeline_factory
        self.state = "idle"
        self.current: Source | None = None
        self.task: asyncio.Task | None = None
        self.listener = MiceListener(mice_port, self.on_mice)

    async def _ended(self) -> None:
        try:
            await self.on_ended(self)
        except Exception:
            log.exception("%s: new name failed", self.name)
            await self.show_name()

    async def start(self) -> None:
        await self.listener.start()
        self.mice_port = self.listener.port
        await self.set_state("idle")

    async def stop(self) -> None:
        await self.listener.stop()
        await self.end_current()
        if self.standby:
            self.standby.stop()
            self.standby = None

    def _airplay_spec(self) -> media.MediaSpec:
        video, audio = self.airplay_ports
        return media.default_spec(video, f"Cast Hub {self.name}", self.output, self.mirrors,
                                  source="airplay", audio_port=audio)

    async def prepare_airplay(self) -> None:
        """Have a player listening for UxPlay's RTP before an iPhone starts mirroring."""
        if not self.airplay_standby or self.standby is not None or self.current is not None:
            return

        def first() -> None:
            if self.standby_first:
                self.standby_first()
            else:
                self.standby_framed = True

        def error(text: str) -> None:
            if self.standby_error:
                self.standby_error(text)
            elif self.standby is pipe:          # died while waiting: start another next time
                log.warning("%s: AirPlay standby player: %s", self.name, text)
                self.standby = None
        pipe = self.pipeline_factory(self._airplay_spec(), first, error)
        try:
            await pipe.start()
        except Exception as e:  # noqa: BLE001 (the cast then starts its own player)
            log.warning("%s: AirPlay standby player did not start: %s", self.name, e)
            return
        if self.current is not None or not self.airplay_standby:
            pipe.stop()                          # a cast began meanwhile; it has its own
            return
        self.standby, self.standby_framed = pipe, False

    @property
    def outputs(self) -> tuple[str, ...]:
        return (self.output, *self.mirrors)

    async def tell(self, msg: dict) -> None:
        """Send a UI message to every screen this receiver shows on."""
        for out in self.outputs:
            await self.notify({**msg, "output": out})

    async def set_state(self, state: str, device: str = "") -> None:
        self.state, self.device = state, device
        log.info("%s: %s %s", self.name, state, device)
        await self.tell({"cmd": "state", "state": "idle" if state == "busy" else state,
                           "device": device, "name": self.name if self.name_ready else ""})
        if self.on_state:
            await self.on_state(self)

    async def show_name(self, name: str | None = None) -> None:
        """PCs can find the receiver under `name` (default: the current one): show it."""
        if name:
            self.name = name
        self.name_ready = True
        await self.set_state(self.state, self.device)

    async def hide_name(self) -> None:
        """A new name is being set up: the screen says Loading."""
        self.name_ready = False
        await self.set_state(self.state, self.device)

    async def show_busy(self, who: str) -> None:
        await self.tell({"cmd": "busy",
                         "text": f"{who or 'Another device'} tried to cast. {self.name} is in use."})
        await asyncio.sleep(6)
        await self.tell({"cmd": "busy", "text": ""})

    async def show_pin(self, pin: str) -> None:
        log.info("%s: %s", self.name, "showing Wi-Fi Direct PIN" if pin else "PIN hidden")
        await self.tell({"cmd": "pin", "pin": pin})

    # ---- entry points -------------------------------------------------------------
    async def on_mice(self, conn: MiceConnection) -> None:
        """The PC keeps this socket open for the whole projection."""
        src: Source | None = None
        async for ev in conn.events():
            if isinstance(ev, mp.SourceReady):
                log.info("%s: SOURCE_READY from %r", self.name, ev.friendly_name)
                src = Source(conn.addr, ev.rtsp_port, ev.friendly_name, "mice")
                if not await self.accept(src):
                    return
            elif isinstance(ev, mp.StopProjection):
                log.info("%s: STOP_PROJECTION", self.name)
                break
            else:
                log.warning("%s: unsupported MS-MICE command 0x%02x (PIN/encryption are out of scope)", self.name, ev.command)
        if src is not None:
            src.stop.set()

    async def on_p2p_source(self, addr: str, rtsp_port: int, name: str) -> None:
        await self.accept(Source(addr, rtsp_port, name, "p2p"))

    async def on_airplay(self, device: str) -> Source | None:
        """UxPlay has an iPhone: the cast's Source (UxPlay sets its stop event when the iPhone
        stops), or None when busy (UxPlay then drops the iPhone)."""
        src = Source("", 0, device or "iPhone", "airplay")
        return src if await self.accept(src) else None

    def on_p2p_gone(self, addr: str) -> None:
        if self.current and self.current.via == "p2p" and self.current.addr == addr:
            self.current.stop.set()

    async def accept(self, src: Source) -> bool:
        if self.current is not None:
            if self.busy_policy != "replace":
                log.info("%s: busy, rejecting %r", self.name, src.name)
                asyncio.ensure_future(self.show_busy(src.name))
                return False
            await self.end_current()
        self.current = src
        self.task = asyncio.create_task(self.run(src))
        return True

    async def end_current(self) -> None:
        if self.current:
            self.current.stop.set()
        if self.task:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task

    # ---- one projection -------------------------------------------------------------
    async def _start_player(self, spec: media.MediaSpec, first_frame: asyncio.Event,
                            on_error: Callable[[str], object]) -> player.PlayerProcess:
        pipe = self.pipeline_factory(spec, first_frame.set, on_error)
        try:
            await pipe.start()
        except RuntimeError as e:
            # Seen once with a real PC, not reproducible since: retry, then fall back to software decoding.
            log.warning("%s: %s; retrying with %s", self.name, e, "avdec_h264")
            await asyncio.sleep(0.5)
            pipe = self.pipeline_factory(dataclasses.replace(spec, decoder="avdec_h264"), first_frame.set, on_error)
            await pipe.start()
        return pipe

    async def _finish(self, src: Source) -> None:
        """After every cast, however it ended: back to idle, then a new name."""
        if self.current is src:
            self.current = None
        if self.on_ended:
            self.name_ready = False         # Loading, while the hub sets up a new name
        await self.set_state("idle")
        asyncio.ensure_future(self.prepare_airplay())
        if self.on_ended:
            # Not awaited here: a new name can take seconds (Wi-Fi Direct address), and a
            # PC may connect meanwhile.
            self.ended_task = asyncio.create_task(self._ended())

    async def run_airplay(self, src: Source) -> None:
        """An AirPlay cast: UxPlay already did the handshake; play its RTP until it says stop.
        No link watch: an iPhone sends no frames while its screen doesn't change, and UxPlay
        notices a lost iPhone itself (-reset)."""
        loop = asyncio.get_running_loop()
        first_frame, failed = asyncio.Event(), loop.create_future()
        pipe = None
        try:
            if self.size_now:
                with contextlib.suppress(Exception):
                    self.size = await self.size_now(self.output) or self.size
            await self.set_state("connecting", src.name)
            on_error = lambda e: failed.done() or failed.set_result(e)  # noqa: E731
            pipe, self.standby = self.standby, None
            if pipe is not None and getattr(getattr(pipe, "proc", None), "returncode", None) is None:
                # Already listening: it has the stream from its first packet.
                self.standby_first, self.standby_error = first_frame.set, on_error
                if self.standby_framed:
                    first_frame.set()
            else:
                log.warning("%s: no AirPlay player was ready; the picture may not appear", self.name)
                pipe = await self._start_player(self._airplay_spec(), first_frame, on_error)
            log.info("%s: AirPlay from %r", self.name, src.name)

            async def frame_watch():
                await first_frame.wait()
                await media.place_on_outputs(self.outputs, getattr(pipe, "pid", None))
                await self.set_state("streaming", src.name)

            watch = asyncio.create_task(frame_watch())
            tasks = {asyncio.create_task(src.stop.wait()), asyncio.ensure_future(failed)}
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            watch.cancel()
            for t in pending:
                t.cancel()
            why = next(iter(done)).result()
            log.info("%s: ended (%s)", self.name, why if isinstance(why, str) else "stopped")
        except Exception:
            log.exception("%s: AirPlay stream failed", self.name)
        finally:
            self.standby_first = self.standby_error = None
            if pipe:
                pipe.stop()
            await self._finish(src)

    async def run(self, src: Source) -> None:
        if src.via == "airplay":
            await self.run_airplay(src)
            return
        loop = asyncio.get_running_loop()
        first_frame, failed = asyncio.Event(), loop.create_future()
        pipe, sink = None, None
        try:
            if self.size_now:             # offer modes in the window's shape at this moment (extend mode)
                with contextlib.suppress(Exception):
                    self.size = await self.size_now(self.output) or self.size
            sink = RtspSink(src.addr, src.rtsp_port, SinkCaps(self.size[0], self.size[1], self.rtp_port, self.name))
            await self.set_state("connecting", src.name)
            spec = media.default_spec(self.rtp_port, f"Cast Hub {self.name}", self.output, self.mirrors)
            pipe = await self._start_player(spec, first_frame,
                                            lambda e: failed.done() or failed.set_result(e))
            neg = await sink.connect()
            log.info("%s: negotiated %s via %s", self.name, neg.video, src.via)

            async def frame_watch():
                try:
                    await asyncio.wait_for(first_frame.wait(), FIRST_FRAME_IDR_AFTER)
                except TimeoutError:
                    await sink.request_idr()
                    await first_frame.wait()
                await media.place_on_outputs(self.outputs, getattr(pipe, "pid", None))
                await self.set_state("streaming", src.name)

            async def link_watch() -> str:
                """The PC's video stopped arriving (e.g. the Wi-Fi Direct link dropped): end the
                cast so the screen goes back to waiting, instead of keeping a dead picture."""
                started = loop.time()
                while True:
                    await asyncio.sleep(1)
                    silent = pipe.silent_for() if hasattr(pipe, "silent_for") else 0.0
                    never = not silent and not getattr(pipe, "last_packet", 1) and loop.time() - started > LINK_LOST_S
                    if silent > LINK_LOST_S or never:
                        log.warning("%s: no video from the PC for %d s; ending the cast", self.name, LINK_LOST_S)
                        return "link lost"

            tasks = {asyncio.create_task(c) for c in (sink.serve(), src.stop.wait(), asyncio.wait_for(failed, None),
                                                      link_watch())}
            watch = asyncio.create_task(frame_watch())
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            watch.cancel()
            for t in pending:
                t.cancel()
            why = next(iter(done)).result()
            log.info("%s: ended (%s)", self.name, why if isinstance(why, str) else "stopped")
        except RtspError as e:
            log.warning("%s: handshake with %r failed: %s", self.name, src.name, e)
        except Exception:
            log.exception("%s: stream failed", self.name)
        finally:
            if pipe:            # first, so the video window is gone at once (Ctrl+Alt+Esc)
                pipe.stop()
            if sink:
                with contextlib.suppress(Exception):
                    await sink.teardown()
            await self._finish(src)
