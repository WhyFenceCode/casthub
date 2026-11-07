"""One receiver = one screen: MS-MICE listener + mDNS record + at most one stream.

State: idle → connecting → streaming → idle. A second sender while busy is rejected (the screen
shows a busy note) or replaces the first, per `miracast.busy_policy`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from casthub.media import pipeline as media
from casthub.mice import protocol as mp
from casthub.mice.listener import MiceConnection, MiceListener
from casthub.rtsp.sink import RtspError, RtspSink, SinkCaps

log = logging.getLogger("casthub.hub")
FIRST_FRAME_IDR_AFTER = 3.0


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
                 pipeline_factory: Callable[..., media.Pipeline] = media.Pipeline) -> None:
        self.name, self.output, self.size = name, output, size
        self.mice_port, self.rtp_port, self.busy_policy = mice_port, rtp_port, busy_policy
        self.notify, self.pipeline_factory = notify, pipeline_factory
        self.state = "idle"
        self.current: Source | None = None
        self.task: asyncio.Task | None = None
        self.listener = MiceListener(mice_port, self.on_mice)

    async def start(self) -> None:
        await self.listener.start()
        self.mice_port = self.listener.port
        await self.set_state("idle")

    async def stop(self) -> None:
        await self.listener.stop()
        await self.end_current()

    async def set_state(self, state: str, device: str = "") -> None:
        self.state = state
        log.info("%s: %s %s", self.name, state, device)
        await self.notify({"cmd": "state", "output": self.output, "state": "idle" if state == "busy" else state,
                           "device": device})

    async def show_busy(self, who: str) -> None:
        await self.notify({"cmd": "busy", "output": self.output,
                           "text": f"{who or 'Another device'} tried to cast. {self.name} is in use."})
        await asyncio.sleep(6)
        await self.notify({"cmd": "busy", "output": self.output, "text": ""})

    async def show_pin(self, pin: str) -> None:
        log.info("%s: %s", self.name, "showing Wi-Fi Direct PIN" if pin else "PIN hidden")
        await self.notify({"cmd": "pin", "output": self.output, "pin": pin})

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
        pipe = None
        sink = RtspSink(src.addr, src.rtsp_port, SinkCaps(self.size[0], self.size[1], self.rtp_port, self.name))
        try:
            await self.set_state("connecting", src.name)
            pipe = self.pipeline_factory(media.default_spec(self.rtp_port, f"Cast Hub {self.name}", self.output),
                                         first_frame.set,
                                         lambda e: failed.done() or failed.set_result(e))
            pipe.start()
            neg = await sink.connect()
            log.info("%s: negotiated %s via %s", self.name, neg.video, src.via)

            async def frame_watch():
                try:
                    await asyncio.wait_for(first_frame.wait(), FIRST_FRAME_IDR_AFTER)
                except TimeoutError:
                    await sink.request_idr()
                    await first_frame.wait()
                await self.set_state("streaming", src.name)

            tasks = {asyncio.create_task(c) for c in (sink.serve(), src.stop.wait(), asyncio.wait_for(failed, None))}
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
            with contextlib.suppress(Exception):
                await sink.teardown()
            if pipe:
                pipe.stop()
            if self.current is src:
                self.current = None
            await self.set_state("idle")
