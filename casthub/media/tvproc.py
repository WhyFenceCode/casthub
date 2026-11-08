"""Passthrough (M10): the TV sender and each cast's input in one child process.

The inter elements that hand a cast's frames to the TV sender only connect pipelines in one
process, so, unlike player.py (one process per cast), one process runs for as long as
passthrough is on:

    python3 -I -c <BOOT> '<TvTarget as JSON>'

Commands on stdin, JSON lines:
    {"cmd": "play", "id": 3, "spec": {...MediaSpec}}    start a cast's input (ends the previous one)
    {"cmd": "end", "id": 3}                             end it, if it is still the current one
    {"cmd": "keyframe"}                                 the TV asked for one
Events on stdout:
    {"event": "started"} or {"event": "failed", "text": ...}   (the sender, first)
    {"event": "input_started" | "input_failed" | "first_frame" | "input_error", "id": 3, ...}
    {"event": "packets", "id": 3, "silent": 1.5, "seen": true}  every REPORT_S for the current input
    {"event": "error", "text": ...}                     the sender failed; the process exits

`TvInput` gives the receiver the same interface as player.PlayerProcess, so a cast plays to the
TV the way it would play on our screens.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import itertools
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Callable

from casthub.media import pipeline as media
from casthub.media import player
from casthub.media.tv import TvSender, TvTarget

log = logging.getLogger("casthub.media")
BOOT = player.BOOT.replace("casthub.media.player", "casthub.media.tvproc")
START_TIMEOUT_S = 10


# ---- hub side ------------------------------------------------------------------------------
class TvProcess:
    def __init__(self, target: TvTarget, on_error: Callable[[str], None]) -> None:
        self.target, self.on_error = target, on_error
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.inputs: dict[int, TvInput] = {}
        self.ids = itertools.count(1)
        self.stopping = False
        self._pending: dict[int, asyncio.Future] = {}

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", BOOT, json.dumps(dataclasses.asdict(self.target)),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        try:
            line = await asyncio.wait_for(self.proc.stdout.readline(), START_TIMEOUT_S)
            ev = json.loads(line) if line else {"event": "failed", "text": "the TV process exited"}
        except (TimeoutError, ValueError):
            ev = {"event": "failed", "text": "the TV process did not start"}
        if ev.get("event") != "started":
            self.stop()
            raise RuntimeError(str(ev.get("text", "TV sender did not start")))
        self.reader = asyncio.create_task(self._read())

    def input(self, spec: media.MediaSpec, on_first_frame: Callable[[], None],
              on_error: Callable[[str], None]) -> TvInput:
        """A pipeline_factory for the receiver."""
        return TvInput(self, next(self.ids), spec, on_first_frame, on_error)

    def keyframe(self) -> None:
        self._send({"cmd": "keyframe"})

    def _send(self, msg: dict) -> None:
        if self.proc and self.proc.stdin and self.proc.returncode is None:
            with contextlib.suppress(ConnectionError, RuntimeError):
                self.proc.stdin.write(json.dumps(msg).encode() + b"\n")

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        while line := await self.proc.stdout.readline():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind, i = ev.get("event"), ev.get("id")
            inp = self.inputs.get(i) if i is not None else None
            if kind in ("input_started", "input_failed"):
                fut = self._pending.pop(i, None)
                if fut and not fut.done():
                    fut.set_result(ev)
            elif inp is None:
                if kind == "error":
                    self.on_error(str(ev.get("text", "TV sender error")))
            elif kind == "first_frame":
                inp.on_first_frame()
            elif kind == "packets":
                inp.silent, inp.seen, inp.reported = float(ev.get("silent", 0)), bool(ev.get("seen")), time.monotonic()
            elif kind == "input_error":
                inp.on_error(str(ev.get("text", "video error")))
        if not self.stopping:
            for inp in list(self.inputs.values()):
                inp.on_error("the TV process exited")
            self.on_error("the TV process exited")

    def stop(self) -> None:
        self.stopping = True
        if self.reader and self.reader is not asyncio.current_task():
            self.reader.cancel()
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()

        def kill() -> None:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        asyncio.get_running_loop().call_later(player.KILL_AFTER_S, kill)


class TvInput:
    """One cast's input in the TV process; the interface of player.PlayerProcess."""

    pid = None                      # no windows to place: TV only

    def __init__(self, tv: TvProcess, ident: int, spec: media.MediaSpec,
                 on_first_frame: Callable[[], None], on_error: Callable[[str], None]) -> None:
        self.tv, self.id, self.spec = tv, ident, spec
        self.on_first_frame, self.on_error = on_first_frame, on_error
        self.silent, self.seen, self.reported = 0.0, False, time.monotonic()
        self.proc = tv.proc         # the receiver checks proc.returncode on an AirPlay standby

    @property
    def last_packet(self) -> float:
        return 1.0 if self.seen else 0.0

    def silent_for(self) -> float:
        return self.silent + (time.monotonic() - self.reported) if self.seen else 0.0

    async def start(self) -> None:
        fut = asyncio.get_running_loop().create_future()
        self.tv._pending[self.id] = fut
        self.tv.inputs[self.id] = self
        self.tv._send({"cmd": "play", "id": self.id, "spec": json.loads(player.encode_spec(self.spec))})
        try:
            ev = await asyncio.wait_for(fut, START_TIMEOUT_S)
        except TimeoutError:
            ev = {"event": "input_failed", "text": "the TV process did not answer"}
        if ev.get("event") != "input_started":
            self.tv.inputs.pop(self.id, None)
            raise RuntimeError(str(ev.get("text", "input did not start")))

    def stop(self) -> None:
        if self.tv.inputs.pop(self.id, None) is not None:
            self.tv._send({"cmd": "end", "id": self.id})


# ---- child side ----------------------------------------------------------------------------
async def run(target: TvTarget) -> int:
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    failed: list[str] = []
    current: dict[str, int] = {"id": 0}

    def sender_error(text: str) -> None:
        failed.append(text)
        done.set()

    sender = TvSender(target, sender_error)
    try:
        sender.start()
    except RuntimeError as e:
        player.emit(event="failed", text=str(e))
        return 1
    player.emit(event="started")
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, done.set)
    stdin = asyncio.StreamReader()
    with contextlib.suppress(OSError, ValueError):
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)

    def handle(msg: dict) -> None:
        cmd = msg.get("cmd")
        if cmd == "keyframe":
            sender.keyframe()
        elif cmd == "end" and msg.get("id") == current["id"]:
            sender.end_input()
            current["id"] = 0
        elif cmd == "play":
            i = int(msg["id"])
            spec = player.decode_spec(json.dumps(msg["spec"]))
            try:
                sender.play(spec, lambda: player.emit(event="first_frame", id=i),
                            lambda text: player.emit(event="input_error", id=i, text=text))
            except RuntimeError as e:
                player.emit(event="input_failed", id=i, text=str(e))
                current["id"] = 0
                return
            current["id"] = i
            player.emit(event="input_started", id=i)

    async def commands() -> None:
        while line := await stdin.readline():
            with contextlib.suppress(ValueError, KeyError, TypeError):
                handle(json.loads(line))
        done.set()                   # the hub is gone
    reader = asyncio.create_task(commands())
    while not done.is_set():
        if current["id"] and sender.input is not None:
            player.emit(event="packets", id=current["id"], silent=round(sender.input.silent_for(), 2),
                        seen=bool(sender.input.last_packet))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(done.wait(), player.REPORT_S)
    reader.cancel()
    if failed:
        player.emit(event="error", text=failed[0])
    sender.stop()
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="casthub-tv: %(name)s: %(message)s", stream=sys.stderr)
    rc = asyncio.run(run(TvTarget(**json.loads(args[0]))))
    sys.stdout.flush()
    os._exit(rc)
