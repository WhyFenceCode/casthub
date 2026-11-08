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
    via: str                                # mice | p2p
    stop: asyncio.Event = field(default_factory=asyncio.Event)


class Receiver:
    def __init__(self, name: str, output: str, size: tuple[int, int], mice_port: int, rtp_port: int,
                 busy_policy: str, notify: Callable[[dict], Awaitable[None]],
                 pipeline_factory: Callable[..., player.PlayerProcess] = player.PlayerProcess,
                 on_ended: Callable[[Receiver], Awaitable[None]] | None = None,
                 size_now: Callable[[str], Awaitable[tuple[int, int] | None]] | None = None,
                 mirrors: tuple[str, ...] = ()) -> None:
        self.name, self.output, self.size = name, output, size
        self.mirrors = tuple(m for m in mirrors if m != output)
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
    async def run(self, src: Source) -> None:
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
            on_error = lambda e: failed.done() or failed.set_result(e)  # noqa: E731
            pipe = self.pipeline_factory(spec, first_frame.set, on_error)
            try:
                await pipe.start()
            except RuntimeError as e:
                # Seen once with a real PC, not reproducible since: retry, then fall back to software decoding.
                log.warning("%s: %s; retrying with %s", self.name, e, "avdec_h264")
                await asyncio.sleep(0.5)
                pipe = self.pipeline_factory(dataclasses.replace(spec, decoder="avdec_h264"), first_frame.set, on_error)
                await pipe.start()
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
            if self.current is src:
                self.current = None
            if self.on_ended:
                self.name_ready = False         # Loading, while the hub sets up a new name
            await self.set_state("idle")
            if self.on_ended:
                # Not awaited here: a new name can take seconds (Wi-Fi Direct address), and a
                # PC may connect meanwhile.
                self.ended_task = asyncio.create_task(self._ended())
