"""RTSP/1.0 messages as used by Wi-Fi Display: both ends send requests on one TCP connection."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

MAX_HEAD = 16 * 1024
MAX_BODY = 64 * 1024


class RtspProtocolError(ValueError):
    pass


@dataclass
class RtspMessage:
    method: str = ""            # request: OPTIONS, GET_PARAMETER …; "" for a response
    uri: str = ""
    status: int = 0             # response only
    reason: str = ""
    headers: dict[str, str] = field(default_factory=dict)    # keys lower-case
    body: str = ""

    @property
    def is_request(self) -> bool:
        return bool(self.method)

    @property
    def cseq(self) -> int:
        try:
            return int(self.headers.get("cseq", ""))
        except ValueError:
            return -1

    def encode(self) -> bytes:
        first = f"{self.method} {self.uri} RTSP/1.0" if self.is_request else f"RTSP/1.0 {self.status} {self.reason}"
        heads = dict(self.headers)
        body = self.body.encode()
        if body:
            heads["content-length"] = str(len(body))
        lines = [first] + [f"{_canon(k)}: {v}" for k, v in heads.items()]
        return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def _canon(key: str) -> str:
    special = {"cseq": "CSeq", "www-authenticate": "WWW-Authenticate"}
    return special.get(key, "-".join(p.capitalize() for p in key.split("-")))


def request(method: str, uri: str, cseq: int, body: str = "", **headers: str) -> RtspMessage:
    h = {"cseq": str(cseq)}
    h.update({k.replace("_", "-").lower(): v for k, v in headers.items()})
    if body:
        h.setdefault("content-type", "text/parameters")
    return RtspMessage(method=method, uri=uri, headers=h, body=body)


def response(req: RtspMessage, status: int = 200, reason: str = "OK", body: str = "", **headers: str) -> RtspMessage:
    h = {"cseq": req.headers.get("cseq", "0")}
    h.update({k.replace("_", "-").lower(): v for k, v in headers.items()})
    if body:
        h.setdefault("content-type", "text/parameters")
    return RtspMessage(status=status, reason=reason, headers=h, body=body)


def parse_head(head: bytes) -> RtspMessage:
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError as e:
        raise RtspProtocolError("head is not UTF-8") from e
    lines = text.split("\r\n")
    first = lines[0].split(" ", 2)
    msg = RtspMessage()
    if first[0].startswith("RTSP/"):
        if len(first) < 2 or not first[1].isdigit():
            raise RtspProtocolError(f"bad status line {lines[0]!r}")
        msg.status, msg.reason = int(first[1]), first[2] if len(first) > 2 else ""
    else:
        if len(first) != 3 or not first[2].startswith("RTSP/"):
            raise RtspProtocolError(f"bad request line {lines[0]!r}")
        msg.method, msg.uri = first[0], first[1]
    for line in lines[1:]:
        if not line:
            continue
        k, sep, v = line.partition(":")
        if not sep:
            raise RtspProtocolError(f"bad header {line!r}")
        msg.headers[k.strip().lower()] = v.strip()
    return msg


async def read_message(reader: asyncio.StreamReader) -> RtspMessage:
    """Read one message. Raises IncompleteReadError at EOF."""
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError as e:
        raise RtspProtocolError("head too long") from e
    if len(head) > MAX_HEAD:
        raise RtspProtocolError("head too long")
    msg = parse_head(head[:-4].lstrip(b"\r\n"))
    n = msg.headers.get("content-length", "0")
    if not n.isdigit() or int(n) > MAX_BODY:
        raise RtspProtocolError(f"bad Content-Length {n!r}")
    if int(n):
        msg.body = (await reader.readexactly(int(n))).decode("utf-8", errors="replace")
    return msg


def parse_parameters(body: str) -> dict[str, str]:
    """`key: value` lines (text/parameters) → dict. A GET_PARAMETER body has bare keys (value "")."""
    out = {}
    for line in body.replace("\r\n", "\n").split("\n"):
        line = line.strip()
        if line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out
