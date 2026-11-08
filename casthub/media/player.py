"""One cast's video in its own process, so a stuck video sink can't freeze the hub.

glimagesink once blocked for good when a cast ended (its GL thread waited on the compositor),
inside the hub's event loop: the screen never went back to waiting and the hub stopped
answering. Now the hub runs each stream as

    python3 -I -c <BOOT> '<MediaSpec as JSON>'

and kills it when the cast ends. The video window always goes with the process, and each
cast's windows have their own pid (place_on_outputs picks them by pid; one per mirrored screen).

The child reports on stdout, JSON lines:
    {"event": "started"}  or  {"event": "failed", "text": "..."}     (exactly one, first)
    {"event": "first_frame"}
    {"event": "packets", "silent": 1.5, "seen": true}    every REPORT_S: seconds since the last
                                                          RTP packet, and whether one arrived
    {"event": "error", "text": "..."}                     then it exits
It stops on SIGTERM or when its stdin closes (the hub is gone).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from casthub.media import pipeline as media

log = logging.getLogger("casthub.media")

REPORT_S = 0.5
START_TIMEOUT_S = 10          # the child imports GStreamer and opens the decoder first
KILL_AFTER_S = 1.5            # SIGTERM, then SIGKILL
STOP_WAIT_S = 1.0             # in the child: how long set_state(NULL) may take before we just exit
ROOT = str(Path(__file__).resolve().parents[2])        # the directory holding the casthub package
BOOT = f"import sys; sys.path.insert(0, {ROOT!r}); from casthub.media.player import main; raise SystemExit(main())"


def encode_spec(spec: media.MediaSpec) -> str:
    return json.dumps(dataclasses.asdict(spec))


def decode_spec(text: str) -> media.MediaSpec:
    fields = {f.name for f in dataclasses.fields(media.MediaSpec)}
    data = {k: v for k, v in json.loads(text).items() if k in fields}
    data["mirrors"] = tuple(data.get("mirrors", ()))      # JSON has no tuples
    return media.MediaSpec(**data)


# ---- hub side ------------------------------------------------------------------------------
class PlayerProcess:
    """Same interface the receiver uses on media.Pipeline, with the stream in a child process."""

    def __init__(self, spec: media.MediaSpec, on_first_frame: Callable[[], None],
                 on_error: Callable[[str], None]) -> None:
        self.spec, self.on_first_frame, self.on_error = spec, on_first_frame, on_error
        self.proc: asyncio.subprocess.Process | None = None
        self.reader: asyncio.Task | None = None
        self.stopping = False
        self.silent, self.seen, self.reported = 0.0, False, 0.0

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    @property
    def last_packet(self) -> float:
        """Non-zero once a packet arrived (the receiver's link watch reads only that)."""
        return 1.0 if self.seen else 0.0

    def silent_for(self) -> float:
        """Seconds since the last RTP packet (0 before the first). Counts on if the child stops
        reporting, so a frozen child still ends the cast."""
        if not self.seen:
            return 0.0
        return self.silent + (time.monotonic() - self.reported)

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", BOOT, encode_spec(self.spec),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        try:
            line = await asyncio.wait_for(self.proc.stdout.readline(), START_TIMEOUT_S)
        except TimeoutError:
            line = b'{"event": "failed", "text": "the video process did not start"}'
        try:
            ev = json.loads(line) if line else {"event": "failed", "text": "the video process exited"}
        except ValueError:
            ev = {"event": "failed", "text": line.decode(errors="replace")[:200]}
        if ev.get("event") != "started":
            self.stop()
            raise RuntimeError(str(ev.get("text", "pipeline did not start")))
        self.reported = time.monotonic()
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self.proc and self.proc.stdout
        while line := await self.proc.stdout.readline():
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("event")
            if kind == "first_frame":
                self.on_first_frame()
            elif kind == "packets":
                self.silent, self.seen = float(ev.get("silent", 0)), bool(ev.get("seen"))
                self.reported = time.monotonic()
            elif kind == "error":
                self.on_error(str(ev.get("text", "video error")))
        if not self.stopping:
            self.on_error("the video process exited")

    def stop(self) -> None:
        """Never blocks: SIGTERM now, SIGKILL if it's still there in KILL_AFTER_S."""
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
                log.warning("the video process did not stop; killing it")
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        asyncio.get_running_loop().call_later(KILL_AFTER_S, kill)


# ---- child side ----------------------------------------------------------------------------
def emit(**event) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


async def play(spec: media.MediaSpec) -> int:
    loop = asyncio.get_running_loop()
    done = asyncio.Event()
    failed: list[str] = []

    def on_error(text: str) -> None:
        failed.append(text)
        done.set()

    pipe = media.Pipeline(spec, lambda: emit(event="first_frame"), on_error)
    try:
        pipe.start()
    except RuntimeError as e:
        emit(event="failed", text=str(e))
        return 1
    emit(event="started")
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, done.set)
    stdin = asyncio.StreamReader()
    with contextlib.suppress(OSError, ValueError):
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)

    async def hub_gone() -> None:
        while await stdin.read(4096):
            pass
        done.set()
    watch = asyncio.create_task(hub_gone())
    while not done.is_set():
        emit(event="packets", silent=round(pipe.silent_for(), 2), seen=bool(pipe.last_packet))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(done.wait(), REPORT_S)
    watch.cancel()
    if failed:
        emit(event="error", text=failed[0])
    # A sink can hang in set_state(NULL); give it a moment, then leave anyway.
    if pipe._poll:
        pipe._poll.cancel()
    gst_pipe = pipe.pipe
    if gst_pipe is not None:
        t = threading.Thread(target=lambda: gst_pipe.set_state(media.gst().State.NULL), daemon=True)
        t.start()
        t.join(STOP_WAIT_S)
        if t.is_alive():
            log.warning("the video sink did not stop; exiting anyway")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="casthub-player: %(name)s: %(message)s", stream=sys.stderr)
    rc = asyncio.run(play(decode_spec(args[0])))
    sys.stdout.flush()
    os._exit(rc)            # don't wait for GStreamer threads that may be stuck
