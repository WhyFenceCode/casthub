"""Wi-Fi Display sink side of the RTSP handshake (M1–M7) and the session after it (M16 keepalive).

The sink connects to the source's RTSP port (MS-MICE: the port in SOURCE_READY). Both ends then
send requests on the same connection:

    M1  source → sink  OPTIONS                 M5  source → sink  SET_PARAMETER trigger SETUP
    M2  sink → source  OPTIONS                 M6  sink → source  SETUP
    M3  source → sink  GET_PARAMETER (caps)    M7  sink → source  PLAY
    M4  source → sink  SET_PARAMETER (choice)  M16 source → sink  GET_PARAMETER (empty, keepalive)

`RtspSink.connect()` returns the negotiated format once PLAY succeeds, or raises RtspError.
`RtspSink.serve()` then answers the source until it tears down, goes quiet or hangs up.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass

from casthub.rtsp import message as m
from casthub.rtsp import wfd

log = logging.getLogger("casthub.rtsp")
SINK_PUBLIC = "org.wfa.wfd1.0, GET_PARAMETER, SET_PARAMETER"
WFD_URI = "rtsp://localhost/wfd1.0"


class RtspError(Exception):
    """kind: connect | timeout | protocol | refused | closed."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind, self.detail = kind, detail


@dataclass(frozen=True)
class SinkCaps:
    width: int
    height: int
    rtp_port: int
    name: str


@dataclass(frozen=True)
class Negotiated:
    video: wfd.VideoFormat
    audio: wfd.AudioFormat | None
    rtp_port: int
    url: str
    session: str
    timeout: int        # seconds; the source sends M16 a little more often than this


class RtspSink:
    def __init__(self, host: str, port: int, caps: SinkCaps, *, handshake_timeout: float = 20.0) -> None:
        self.host, self.port, self.caps = host, port, caps
        self.handshake_timeout = handshake_timeout
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self.cseq = 0
        self.pending: dict[int, asyncio.Future[m.RtspMessage]] = {}
        self.params: dict[str, str] = {}
        self.negotiated: Negotiated | None = None
        self.last_heard = time.monotonic()
        self._playing: asyncio.Future[Negotiated] | None = None
        self._ended: asyncio.Future[str] | None = None
        self._tasks: set[asyncio.Task] = set()
        self._reader_task: asyncio.Task | None = None

    # ---- public -----------------------------------------------------------------
    async def connect(self) -> Negotiated:
        loop = asyncio.get_running_loop()
        self._playing, self._ended = loop.create_future(), loop.create_future()
        try:
            self.reader, self.writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), 5)
        except (OSError, TimeoutError) as e:
            raise RtspError("connect", f"{self.host}:{self.port}: {e}") from None
        self._reader_task = asyncio.create_task(self._read_loop())
        try:
            return await asyncio.wait_for(asyncio.shield(self._playing), self.handshake_timeout)
        except TimeoutError:
            self.close()
            raise RtspError("timeout", f"handshake not done after {self.handshake_timeout:.0f} s") from None
        except RtspError:
            self.close()
            raise

    async def serve(self) -> str:
        """Run until the session ends. Returns why: teardown | keepalive | closed | error."""
        assert self._ended is not None and self.negotiated is not None
        limit = self.negotiated.timeout + 10
        while not self._ended.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._ended), 1.0)
            if not self._ended.done() and time.monotonic() - self.last_heard > limit:
                self._end("keepalive")
        self.close()
        return self._ended.result()

    async def request_idr(self) -> None:
        """Ask the source for a key frame (decoder joined late or lost packets)."""
        with contextlib.suppress(RtspError):
            await self._request("SET_PARAMETER", WFD_URI, "wfd_idr_request\r\n")

    async def teardown(self) -> None:
        if self.negotiated and self.writer and not self.writer.is_closing():
            with contextlib.suppress(RtspError, TimeoutError):
                await asyncio.wait_for(self._request("TEARDOWN", self.negotiated.url, session=self.negotiated.session), 2)
        self._end("teardown")
        self.close()

    def close(self) -> None:
        if self._reader_task and self._reader_task is not asyncio.current_task():
            self._reader_task.cancel()
        for t in list(self._tasks):
            if t is not asyncio.current_task():
                t.cancel()
        if self.writer and not self.writer.is_closing():
            self.writer.close()
        for f in self.pending.values():
            if not f.done():
                f.set_exception(RtspError("closed"))
        self.pending.clear()
        self._end("closed")

    # ---- plumbing ---------------------------------------------------------------
    def _end(self, why: str) -> None:
        if self._ended and not self._ended.done():
            self._ended.set_result(why)
        if self._playing and not self._playing.done():
            self._playing.set_exception(RtspError("closed" if why == "closed" else "protocol", why))
            self._playing.exception()   # mark retrieved; connect() re-raises it

    def _fail(self, err: RtspError) -> None:
        if self._playing and not self._playing.done():
            self._playing.set_exception(err)
        self._end("error")

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
                    log.debug("← %d %s %s | %s", msg.status, msg.reason, msg.headers, msg.body.replace("\r\n", " | "))
                    fut = self.pending.pop(msg.cseq, None)
                    if fut and not fut.done():
                        fut.set_result(msg)
        except (asyncio.IncompleteReadError, ConnectionError):
            self._end("closed")
        except m.RtspProtocolError as e:
            self._fail(RtspError("protocol", str(e)))
        except asyncio.CancelledError:
            raise

    # ---- requests from the source ---------------------------------------------------
    def _on_request(self, req: m.RtspMessage) -> None:
        if req.method == "OPTIONS":                                  # M1
            self._send(m.response(req, public=SINK_PUBLIC))
            self._spawn(self._m2())
        elif req.method == "GET_PARAMETER":
            if req.body.strip():                                     # M3
                self._send(m.response(req, body=self._m3_reply(req.body)))
            else:                                                    # M16 keepalive
                self._send(m.response(req))
        elif req.method == "SET_PARAMETER":
            self._on_set_parameter(req)
        else:
            self._send(m.response(req, 405, "Method Not Allowed", public=SINK_PUBLIC))

    def _m3_reply(self, body: str) -> str:
        """Answer only what we support, in the order asked. Windows stalled after M3 when we
        answered its wfd2_* / microsoft_* parameters with `none`; sinks that work with it
        (e.g. lazycast) leave unsupported ones out."""
        ours = wfd.sink_parameters(self.caps.width, self.caps.height, self.caps.rtp_port, self.caps.name)
        asked = m.parse_parameters(body)
        return "".join(f"{k}: {ours[k]}\r\n" for k in asked if k in ours)

    def _on_set_parameter(self, req: m.RtspMessage) -> None:
        params = m.parse_parameters(req.body)
        trigger = params.get("wfd_trigger_method", "").upper()
        if not trigger:                                              # M4 (or a later format change)
            self.params.update(params)
            self._send(m.response(req))
            return
        self._send(m.response(req))
        if trigger == "SETUP":                                       # M5
            self._spawn(self._setup_and_play())
        elif trigger == "TEARDOWN":
            self._spawn(self.teardown())
        elif trigger == "PLAY" and self.negotiated:
            self._spawn(self._request("PLAY", self.negotiated.url, session=self.negotiated.session))
        elif trigger == "PAUSE" and self.negotiated:
            self._spawn(self._request("PAUSE", self.negotiated.url, session=self.negotiated.session))

    # ---- our requests -----------------------------------------------------------------
    async def _m2(self) -> None:
        try:
            await self._request("OPTIONS", "*", require="org.wfa.wfd1.0")
        except (RtspError, TimeoutError) as e:
            self._fail(e if isinstance(e, RtspError) else RtspError("timeout", "M2"))

    async def _setup_and_play(self) -> None:
        try:
            video = wfd.parse_video_format(self.params.get("wfd_video_formats", ""))
            audio = wfd.parse_audio_codecs(self.params.get("wfd_audio_codecs", ""))
            url = wfd.presentation_url(self.params.get("wfd_presentation_URL", ""))
            rtp_port = self.caps.rtp_port
            ports = self.params.get("wfd_client_rtp_ports", "").split()
            if len(ports) > 1 and ports[1].isdigit():
                rtp_port = int(ports[1])
            setup = await self._request("SETUP", url, transport=f"RTP/AVP/UDP;unicast;client_port={rtp_port}")
            sess, _, opts = setup.headers.get("session", "").partition(";")
            timeout = 60
            for opt in opts.split(";"):
                k, _, v = opt.strip().partition("=")
                if k == "timeout" and v.isdigit():
                    timeout = int(v)
            if not sess:
                raise RtspError("protocol", "SETUP response has no Session")
            await self._request("PLAY", url, session=sess)
        except wfd.WfdError as e:
            self._fail(RtspError("protocol", str(e)))
            return
        except RtspError as e:
            self._fail(e)
            return
        except TimeoutError:
            self._fail(RtspError("timeout", "SETUP/PLAY"))
            return
        self.negotiated = Negotiated(video, audio, rtp_port, url, sess.strip(), timeout)
        log.info("playing %s, audio %s, rtp %d", video, audio, rtp_port)
        if self._playing and not self._playing.done():
            self._playing.set_result(self.negotiated)
