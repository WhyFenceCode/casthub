"""AirPlay through UxPlay: one UxPlay process under the receiver's name (SPEC "Apple: AirPlay").

UxPlay speaks AirPlay (discovery, pairing, the mirror stream) but doesn't show anything:
`-vrtp` / `-artp` hand the iPhone's H.264 and its decoded audio to our player on localhost,
so AirPlay plays like a Miracast cast: hardware decode, every screen (mirror_all), and a video
process the hub can kill. The receiver treats an AirPlay cast as one more source (via "airplay"),
so the busy policy covers both.

We follow UxPlay's log (stdout) for the cast's life:
    connection request from <device> (<model>) with deviceID = <id>   → a cast starts
    raop_rtp_mirror->running is no longer true (Stop Mirroring on the iPhone),
    video_reset: type = RTP_Shutdown, Open connections: 0,
    ***ERROR lost connection with client                              → it's over
("Connection closed on socket" is not used: iOS opens and closes side connections too.)
After each cast the hub renames the receiver; restart() brings UxPlay back under the new name
(and a new device ID, so iPhones list a fresh receiver, as Windows does: D26).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from casthub.discovery import p2p

log = logging.getLogger("casthub.airplay")

RESTART_AFTER_S = 5          # UxPlay exited by itself: try again after this
STOP_WAIT_S = 3              # SIGTERM, then SIGKILL
CONNECT_RE = re.compile(r"connection request from (.*) \(([^()]*)\) with deviceID = (\S+)")
ENDED = ("raop_rtp_mirror->running is no longer true",       # seen: Stop Mirroring on the iPhone
         "video_reset: type = RTP_Shutdown", "Open connections: 0", "lost connection with client")


class Source(Protocol):
    stop: asyncio.Event


@dataclass(frozen=True)
class Event:
    kind: str                # connect | video | ended
    device: str = ""


def parse_line(line: str) -> Event | None:
    """One line of UxPlay's log → what it means for the cast, or None."""
    if m := CONNECT_RE.search(line):
        return Event("connect", m.group(1).strip())
    if "begin video stream" in line:
        return Event("video")
    if any(s in line for s in ENDED):
        return Event("ended")
    return None


def device_id() -> str:
    """A new random AirPlay device ID, as UxPlay's -m wants it: xx:xx:xx:xx:xx:xx."""
    return p2p.random_address().hex(":")


def uxplay_args(name: str, size: tuple[int, int], video_port: int, audio_port: int,
                device_id: str, replace: bool = False, binary: str = "uxplay") -> list[str]:
    w, h = size
    args = [
        binary, "-n", name, "-nh",          # exactly our name, no "@hostname"
        "-m", device_id,                    # a new device per name, so iPhones list a fresh one
        "-p",                               # the usual ports (TCP 7000-7100, UDP 6000-7011)
        "-s", f"{w}x{h}@60", "-fps", "60",
        "-vrtp", f"config-interval=1 ! udpsink host=127.0.0.1 port={video_port}",
        "-artp", f"pt=96 ! udpsink host=127.0.0.1 port={audio_port}",
        "-scrsv", "0",
    ]
    if replace:
        args.append("-nohold")              # busy_policy replace: a new iPhone takes over
    # UxPlay's log goes into a pipe, where C stdio holds it back in 4 KB blocks: the first test
    # cast reached the hub only when UxPlay exited. Line-buffered, each line arrives at once.
    if buf := shutil.which("stdbuf"):
        args = [buf, "-oL", "-eL", *args]
    if pdeath := shutil.which("setpriv"):   # UxPlay goes with the hub, even after SIGKILL
        args = [pdeath, "--pdeathsig", "TERM", *args]
    return args


class Receiver(Protocol):
    name: str
    output: str
    size: tuple[int, int]

    async def on_airplay(self, device: str) -> Source | None: ...


class UxPlay:
    def __init__(self, receiver: Receiver, video_port: int, audio_port: int, replace: bool = False,
                 size_now: Callable[[str], Awaitable[tuple[int, int] | None]] | None = None,
                 binary: str = "uxplay") -> None:
        self.receiver, self.video_port, self.audio_port = receiver, video_port, audio_port
        self.replace, self.size_now, self.binary = replace, size_now, binary
        self.proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task | None = None
        self.current: Source | None = None
        self.stopping = False
        self.restarting = False

    @staticmethod
    def available(binary: str = "uxplay") -> bool:
        return shutil.which(binary) is not None

    async def start(self) -> None:
        self.task = asyncio.create_task(self._supervise())

    async def restart(self) -> None:
        """Start again under the receiver's current name (after a rename), or to drop a client."""
        self.restarting = True
        await self._end_proc()

    async def stop(self) -> None:
        self.stopping = True
        await self._end_proc()
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def _end_proc(self) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), STOP_WAIT_S)
        except TimeoutError:
            log.warning("uxplay did not stop; killing it")
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    async def _supervise(self) -> None:
        while not self.stopping:
            size = self.receiver.size
            if self.size_now:
                with contextlib.suppress(Exception):
                    size = await self.size_now(self.receiver.output) or size
            args = uxplay_args(self.receiver.name, size, self.video_port, self.audio_port,
                               device_id(), self.replace, self.binary)
            log.info("uxplay: AirPlay as %s (%dx%d)", self.receiver.name, *size)
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT)
            except OSError as e:
                log.error("uxplay: %s", e)
                await asyncio.sleep(RESTART_AFTER_S)
                continue
            assert self.proc.stdout
            while line := await self.proc.stdout.readline():
                await self._on_line(line.decode(errors="replace").rstrip())
            rc = await self.proc.wait()
            self._end_cast()
            if self.stopping:
                return
            if self.restarting:
                self.restarting = False
                continue
            log.warning("uxplay exited (%s); starting it again in %d s", rc, RESTART_AFTER_S)
            await asyncio.sleep(RESTART_AFTER_S)

    async def _on_line(self, line: str) -> None:
        log.debug("uxplay: %s", line)
        ev = parse_line(line)
        if ev is None:
            return
        log.info("uxplay: %s", line.strip())
        # "begin video stream" also starts a cast, in case a UxPlay build doesn't log the request.
        if ev.kind in ("connect", "video") and self.current is None:
            self.current = await self.receiver.on_airplay(ev.device)
            if self.current is None:          # busy: drop the iPhone (it says it couldn't connect)
                asyncio.ensure_future(self.restart())
        elif ev.kind == "ended":
            self._end_cast()

    def _end_cast(self) -> None:
        if self.current is not None:
            self.current.stop.set()
            self.current = None
