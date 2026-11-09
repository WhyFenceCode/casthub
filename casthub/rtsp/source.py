"""Wi-Fi Display *source* side of the RTSP handshake (M10 passthrough: Cast Hub sends to a TV).

The sink (TV) connects to our RTSP port (7236, announced in our WFD IE) once the Wi-Fi Direct
group is up. Then, mirroring sink.py:

    M1  source → sink  OPTIONS                 M5  source → sink  SET_PARAMETER trigger SETUP
    M2  sink → source  OPTIONS                 M6  sink → source  SETUP   (we answer: Session)
    M3  source → sink  GET_PARAMETER (caps)    M7  sink → source  PLAY    (we start sending)
    M4  source → sink  SET_PARAMETER (choice)  M16 source → sink  GET_PARAMETER (empty, keepalive)

`RtspSource.accept()` waits for one sink and returns the `Session` once it has sent PLAY.
`RtspSource.serve()` keeps the session alive until either side ends it; `on_idr` runs when the
sink asks for a key frame (its decoder joined late or lost packets).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from casthub.rtsp import message as m
from casthub.rtsp import wfd
from casthub.rtsp.sink import RtspError

log = logging.getLogger("casthub.rtsp")
SOURCE_PUBLIC = "org.wfa.wfd1.0, SETUP, TEARDOWN, PLAY, PAUSE, GET_PARAMETER, SET_PARAMETER"
WFD_URI = "rtsp://localhost/wfd1.0"
DEFAULT_PORT = 7236
SESSION_TIMEOUT_S = 30              # in our SETUP answer; the sink expects M16 within it
KEEPALIVE_S = 20
RTCP_LOG_S = 5              # the sink's receiver reports: at most one log line per this


@dataclass(frozen=True)
class ReceiverReport:
    fraction_lost: float     # 0-1, since the sink's previous report
    lost: int                # packets lost in all
    highest_seq: int
    jitter: int              # RTP timestamp units (90 kHz)


def parse_rtcp(data: bytes) -> ReceiverReport | None:
    """The first report block of an RTCP receiver report (RFC 3550 6.4.2), or None."""
    i = 0
    while i + 4 <= len(data):
        if data[i] >> 6 != 2:
            return None
        count, pt = data[i] & 0x1F, data[i + 1]
        length = (int.from_bytes(data[i + 2:i + 4], "big") + 1) * 4
        if pt == 201 and count and i + 8 + 24 <= len(data):        # RR: header, SSRC, then blocks
            b = data[i + 8:i + 32]
            return ReceiverReport(b[4] / 256, int.from_bytes(b[5:8], "big"),
                                  int.from_bytes(b[8:12], "big"), int.from_bytes(b[12:16], "big"))
        i += length
    return None


class _Rtcp(asyncio.DatagramProtocol):
    """The sink's RTCP (and its NAT-opening packets): logs what it says about our stream."""

    def __init__(self) -> None:
        self.last_log = 0.0
        self.reports = 0

    def datagram_received(self, data: bytes, addr) -> None:
        rr = parse_rtcp(data)
        if rr is None:
            return
        self.reports += 1
        now = time.monotonic()
        if now - self.last_log >= RTCP_LOG_S:
            self.last_log = now
            log.info("the TV reports: %.0f%% lost lately, %d lost in all, jitter %.1f ms",
                     rr.fraction_lost * 100, rr.lost, rr.jitter / 90)
M3_PARAMS = ("wfd_video_formats", "wfd_audio_codecs", "wfd_client_rtp_ports", "wfd_content_protection",
             "wfd_display_edid", "wfd_connector_type", "intel_friendly_name")


@dataclass(frozen=True)
class Session:
    video: wfd.Mode
    video_format: str               # what we sent in M4
    profile: str                    # constrained-high | constrained-baseline
    audio: wfd.AudioFormat | None
    sink_host: str
    rtp_port: int                   # the sink's RTP port: we send MPEG-TS over RTP there
    session: str
    name: str                       # the sink's friendly name, if it told us


class RtspSource:
    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT, *, our_host: str = "",
                 max_size: tuple[int, int] = (1920, 1080), max_fps: int = 30,
                 handshake_timeout: float = 20.0) -> None:
        self.host, self.port = host, port
        self.our_host = our_host            # the address the sink reaches us on (presentation URL)
        self.max_size, self.max_fps = max_size, max_fps
        self.handshake_timeout = handshake_timeout
        self.on_idr: Callable[[], None] | None = None
        self.server: asyncio.Server | None = None
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.cseq = 0
        self.pending: dict[int, asyncio.Future[m.RtspMessage]] = {}
        self.session: Session | None = None
        self.last_heard = time.monotonic()
        self._conn: asyncio.Future[None] | None = None
        self._m2: asyncio.Future[None] | None = None
        self._setup: asyncio.Future[str] | None = None        # resolves with the sink's RTP port spec
        self._playing: asyncio.Future[None] | None = None
        self._ended: asyncio.Future[str] | None = None
        self._tasks: set[asyncio.Task] = set()
        self._rtcp: list[asyncio.DatagramTransport] = []
        self._reader_task: asyncio.Task | None = None
        self._chosen: tuple[wfd.Mode, str, str, wfd.AudioFormat | None, str] | None = None
        self._session_id = str(secrets.randbelow(10**9))

    # ---- public -----------------------------------------------------------------
    async def listen(self) -> int:
        """Open the RTSP port; returns it (useful with port 0 in tests)."""
        loop = asyncio.get_running_loop()
        self._conn, self._m2, self._setup = loop.create_future(), loop.create_future(), loop.create_future()
        self._playing, self._ended = loop.create_future(), loop.create_future()
        self.server = await asyncio.start_server(self._on_connect, self.host, self.port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def accept(self) -> Session:
        """Wait for a sink (no time limit: the TV connects when it's ready), then run the handshake."""
        if self.server is None:
            await self.listen()
        assert self._conn is not None
        await self._conn
        try:
            return await asyncio.wait_for(self._handshake(), self.handshake_timeout)
        except TimeoutError:
            self.close()
            raise RtspError("timeout", f"handshake not done after {self.handshake_timeout:.0f} s") from None
        except (RtspError, wfd.WfdError) as e:
            self.close()
            raise e if isinstance(e, RtspError) else RtspError("protocol", str(e)) from None

    async def serve(self) -> str:
        """Keep the session alive until it ends. Returns why: teardown | keepalive | closed | error."""
        assert self._ended is not None
        await self._open_rtcp()
        next_ping = time.monotonic() + KEEPALIVE_S
        while not self._ended.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._ended), 1.0)
            now = time.monotonic()
            if self._ended.done():
                break
            if now >= next_ping:
                next_ping = now + KEEPALIVE_S
                self._spawn(self._keepalive())
            if now - self.last_heard > SESSION_TIMEOUT_S + KEEPALIVE_S:
                self._end("keepalive")
        self.close()
        return self._ended.result()

    async def teardown(self) -> None:
        """Ask the sink to end (M5 trigger TEARDOWN); it answers with its own TEARDOWN."""
        if self.writer and not self.writer.is_closing() and self.session:
            with contextlib.suppress(RtspError, TimeoutError):
                await asyncio.wait_for(self._request("SET_PARAMETER", WFD_URI, "wfd_trigger_method: TEARDOWN\r\n"), 2)
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._ended), 2)
        self._end("teardown")
        self.close()

    async def _open_rtcp(self) -> None:
        """Our RTP "server" ports (RTSP port + 1 and + 2): the sink sends its receiver reports
        there. A TV's went unanswered (firewall) in the first live test."""
        loop = asyncio.get_running_loop()
        for port in (self.port + 1, self.port + 2):
            with contextlib.suppress(OSError):
                t, _ = await loop.create_datagram_endpoint(_Rtcp, local_addr=(self.host, port))
                self._rtcp.append(t)

    def close(self) -> None:
        for t in self._rtcp:
            t.close()
        self._rtcp.clear()
        if self._reader_task and self._reader_task is not asyncio.current_task():
            self._reader_task.cancel()
        for t in list(self._tasks):
            if t is not asyncio.current_task():
                t.cancel()
        if self.writer and not self.writer.is_closing():
            self.writer.close()
        if self.server:
            self.server.close()
        for f in self.pending.values():
            if not f.done():
                f.set_exception(RtspError("closed"))
        self.pending.clear()
        self._end("closed")

    # ---- the handshake -------------------------------------------------------------------
    async def _handshake(self) -> Session:
        assert self._m2 and self._setup and self._playing
        await self._request("OPTIONS", "*", require="org.wfa.wfd1.0")                       # M1
        await self._m2                                                                         # M2
        caps = await self._request("GET_PARAMETER", WFD_URI, "".join(f"{p}\r\n" for p in M3_PARAMS))
        params = m.parse_parameters(caps.body)                                                 # M3
        modes, profiles, levels = wfd.parse_sink_video_formats(params.get("wfd_video_formats", ""))
        mode = wfd.choose_mode(modes, self.max_size, self.max_fps)
        video_format = wfd.source_video_format(mode, profiles, levels)
        audio = wfd.choose_audio(params.get("wfd_audio_codecs", ""))
        ports = params.get("wfd_client_rtp_ports", "RTP/AVP/UDP;unicast 1028 0 mode=play")
        host = self.our_host or (self.writer.get_extra_info("sockname") or ("127.0.0.1",))[0]
        m4 = {"wfd_video_formats": video_format,
              "wfd_presentation_URL": f"rtsp://{host}/wfd1.0/streamid=0 none",
              "wfd_client_rtp_ports": ports}
        if audio:
            m4["wfd_audio_codecs"] = audio[0]
        await self._request("SET_PARAMETER", WFD_URI, "".join(f"{k}: {v}\r\n" for k, v in m4.items()))  # M4
        profile = "constrained-high" if video_format.split()[2] == "02" else "constrained-baseline"
        self._chosen = (mode, video_format, profile, audio[1] if audio else None, params.get("intel_friendly_name", ""))
        await self._request("SET_PARAMETER", WFD_URI, "wfd_trigger_method: SETUP\r\n")       # M5
        await self._setup                                                                      # M6
        await self._playing                                                                    # M7
        assert self.session is not None
        log.info("sending %dx%dp%d H.264 %s to %s, audio %s", mode.width, mode.height, mode.fps, profile,
                 self.session.sink_host, self.session.audio)
        return self.session

    # ---- plumbing -------------------------------------------------------------------------
    async def _on_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.writer is not None:                 # one sink at a time
            writer.close()
            return
        self.reader, self.writer = reader, writer
        self.last_heard = time.monotonic()
        log.info("sink connected from %s", (writer.get_extra_info("peername") or ("?",))[0])
        self._reader_task = asyncio.create_task(self._read_loop())
        if self._conn and not self._conn.done():
            self._conn.set_result(None)

    def _end(self, why: str) -> None:
        if self._ended and not self._ended.done():
            self._ended.set_result(why)
        for f in (self._conn, self._m2, self._setup, self._playing):
            if f and not f.done():
                f.set_exception(RtspError("closed" if why == "closed" else "protocol", why))
                f.exception()

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _send(self, msg: m.RtspMessage) -> None:
        assert self.writer is not None
        log.debug("→ %s", msg.encode().decode(errors="replace").replace("\r\n", " | "))
        self.writer.write(msg.encode())

    async def _request(self, method: str, uri: str, body: str = "", **headers: str) -> m.RtspMessage:
        self.cseq += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.cseq] = fut
        self._send(m.request(method, uri, self.cseq, body, **headers))
        resp = await asyncio.wait_for(fut, 10)
        if resp.status != 200:
            raise RtspError("refused", f"{method} → {resp.status} {resp.reason}")
        return resp

    async def _keepalive(self) -> None:
        with contextlib.suppress(RtspError, TimeoutError):
            await self._request("GET_PARAMETER", WFD_URI, session=self._session_id)          # M16

    async def _read_loop(self) -> None:
        assert self.reader is not None
        try:
            while True:
                msg = await m.read_message(self.reader)
                self.last_heard = time.monotonic()
                if msg.is_request:
                    log.debug("← %s %s %s | %s", msg.method, msg.uri, msg.headers, msg.body.replace("\r\n", " | "))
                    self._on_request(msg)
                else:
                    fut = self.pending.pop(msg.cseq, None)
                    if fut and not fut.done():
                        fut.set_result(msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            self._end("closed")
        except m.RtspProtocolError as e:
            log.warning("sink: %s", e)
            self._end("error")
        except asyncio.CancelledError:
            raise

    # ---- requests from the sink ------------------------------------------------------------
    def _on_request(self, req: m.RtspMessage) -> None:
        if req.method == "OPTIONS":                                                            # M2
            self._send(m.response(req, public=SOURCE_PUBLIC))
            if self._m2 and not self._m2.done():
                self._m2.set_result(None)
        elif req.method == "SETUP":                                                            # M6
            self._on_setup(req)
        elif req.method == "PLAY":                                                             # M7
            self._send(m.response(req, session=self._session_id))
            if self._playing and not self._playing.done() and self.session:
                self._playing.set_result(None)
        elif req.method == "PAUSE":
            self._send(m.response(req, session=self._session_id))
        elif req.method == "TEARDOWN":
            self._send(m.response(req, session=self._session_id))
            self._end("teardown")
        elif req.method == "SET_PARAMETER":
            self._send(m.response(req))
            if "wfd_idr_request" in req.body and self.on_idr:
                self.on_idr()
        elif req.method == "GET_PARAMETER":
            self._send(m.response(req))
        else:
            self._send(m.response(req, 405, "Method Not Allowed", public=SOURCE_PUBLIC))

    def _on_setup(self, req: m.RtspMessage) -> None:
        transport = req.headers.get("transport", "")
        port = 0
        for opt in transport.split(";"):
            k, _, v = opt.strip().partition("=")
            if k == "client_port":
                port = int(v.split("-")[0]) if v.split("-")[0].isdigit() else 0
        if not port or self._chosen is None:
            self._send(m.response(req, 461, "Unsupported Transport"))
            return
        mode, video_format, profile, audio, name = self._chosen
        sink_host = (self.writer.get_extra_info("peername") or ("",))[0] if self.writer else ""
        self.session = Session(mode, video_format, profile, audio, sink_host, port, self._session_id, name)
        self._send(m.response(req, session=f"{self._session_id};timeout={SESSION_TIMEOUT_S}",
                              transport=f"RTP/AVP/UDP;unicast;client_port={port};server_port={self.port + 1}"))
        if self._setup and not self._setup.done():
            self._setup.set_result(transport)
