"""MS-MICE TCP listener: one per receiver (7250, 7251 …).

The PC connects, sends SOURCE_READY, keeps the socket open for the whole projection and sends
STOP_PROJECTION (or just closes) at the end. Each connection is handed to `on_connection` as a
`MiceConnection`, whose `events()` yields SourceReady / StopProjection until the PC hangs up.
"""

from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import AsyncIterator, Awaitable, Callable

from casthub.mice import protocol as p

log = logging.getLogger("casthub.mice")


class MiceConnection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader, self.writer = reader, writer
        peer = writer.get_extra_info("peername") or ("", 0)
        self.addr: str = peer[0]

    async def events(self) -> AsyncIterator[p.Event]:
        """Yield events until the PC closes the socket. Malformed messages end the connection."""
        while True:
            try:
                head = await self.reader.readexactly(2)
            except (asyncio.IncompleteReadError, ConnectionError):
                return
            size = struct.unpack(">H", head)[0]
            if not p.HEADER <= size <= p.MAX_MESSAGE:
                log.warning("mice %s: bad size %d, closing", self.addr, size)
                return
            try:
                rest = await self.reader.readexactly(size - 2)
                yield p.to_event(p.parse_message(head + rest))
            except (asyncio.IncompleteReadError, ConnectionError):
                return
            except p.MiceError as e:
                log.warning("mice %s: %s, closing", self.addr, e)
                return

    def close(self) -> None:
        if not self.writer.is_closing():
            self.writer.close()


class MiceListener:
    def __init__(self, port: int, on_connection: Callable[[MiceConnection], Awaitable[None]], host: str | None = None) -> None:
        self.port, self.host, self.on_connection = port, host, on_connection
        self.server: asyncio.Server | None = None

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, self.host, self.port, reuse_address=True)
        if self.port == 0:      # tests: with host None every address family gets its own port; use IPv4's
            import socket
            self.port = next(s.getsockname()[1] for s in self.server.sockets if s.family == socket.AF_INET)
        log.info("mice listening on %d", self.port)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = MiceConnection(reader, writer)
        try:
            await self.on_connection(conn)
        except Exception:
            log.exception("mice %s: handler failed", conn.addr)
        finally:
            conn.close()

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            self.server = None
