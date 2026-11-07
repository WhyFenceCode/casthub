"""MS-MICE messages (Miracast over Infrastructure, [MS-MICE] section 2.2).

Message: Size (2, big-endian, whole message) | Version (1, 0x01) | Command (1) | TLVs.
TLV:     Type (1) | Length (2, big-endian) | Value.
"""

from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass

VERSION = 1
HEADER = 4

# Commands
SOURCE_READY = 0x01
STOP_PROJECTION = 0x02
SECURITY_HANDSHAKE = 0x03
SESSION_REQUEST = 0x04
PIN_CHALLENGE = 0x05
PIN_RESPONSE = 0x06

# TLV types
FRIENDLY_NAME = 0x00
RTSP_PORT = 0x02
SOURCE_ID = 0x03
SECURITY_TOKEN = 0x04
SECURITY_OPTIONS = 0x05
PIN_CHALLENGE_TLV = 0x06
PIN_RESPONSE_REASON = 0x07

MAX_MESSAGE = 4096          # largest real message is well under 1 KiB (name ≤ 520 bytes)
DEFAULT_RTSP_PORT = 7236


class MiceError(ValueError):
    """A malformed or unsupported MS-MICE message."""


@dataclass(frozen=True)
class Message:
    command: int
    tlvs: tuple[tuple[int, bytes], ...]

    def get(self, typ: int) -> bytes | None:
        for t, v in self.tlvs:
            if t == typ:
                return v
        return None


@dataclass(frozen=True)
class SourceReady:
    friendly_name: str
    rtsp_port: int
    source_id: str          # hex, 32 chars


@dataclass(frozen=True)
class StopProjection:
    friendly_name: str
    source_id: str


@dataclass(frozen=True)
class Unsupported:
    """A valid message we don't act on (PIN, DTLS): v1 is out of scope (SPEC)."""
    command: int


Event = SourceReady | StopProjection | Unsupported


def parse_tlvs(data: bytes) -> tuple[tuple[int, bytes], ...]:
    out, i = [], 0
    while i < len(data):
        if len(data) - i < 3:
            raise MiceError("truncated TLV header")
        typ, length = data[i], struct.unpack_from(">H", data, i + 1)[0]
        i += 3
        if length == 0 or i + length > len(data):
            raise MiceError(f"TLV 0x{typ:02x}: bad length {length}")
        out.append((typ, data[i:i + length]))
        i += length
    return tuple(out)


def parse_message(raw: bytes) -> Message:
    if len(raw) < HEADER:
        raise MiceError("short message")
    size, version, command = struct.unpack_from(">HBB", raw)
    if size != len(raw):
        raise MiceError(f"size field {size} != {len(raw)}")
    if version != VERSION:
        raise MiceError(f"unsupported version {version}")
    return Message(command, parse_tlvs(raw[HEADER:]))


def build_message(command: int, tlvs: list[tuple[int, bytes]]) -> bytes:
    body = b"".join(struct.pack(">BH", t, len(v)) + v for t, v in tlvs)
    return struct.pack(">HBB", HEADER + len(body), VERSION, command) + body


def _name(m: Message) -> str:
    v = m.get(FRIENDLY_NAME)
    # UTF-16 (little-endian on Windows, no BOM); tolerate a BOM.
    return v.decode("utf-16-le" if not v.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-16",
                    errors="replace").rstrip("\x00") if v else ""


def _source_id(m: Message) -> str:
    v = m.get(SOURCE_ID)
    if v is None or len(v) != 16:
        raise MiceError("missing or bad SOURCE_ID")
    return v.hex()


def to_event(m: Message) -> Event:
    if m.command == SOURCE_READY:
        port = m.get(RTSP_PORT)
        if port is None or len(port) != 2:
            raise MiceError("SOURCE_READY without RTSP_PORT")
        return SourceReady(_name(m), struct.unpack(">H", port)[0], _source_id(m))
    if m.command == STOP_PROJECTION:
        return StopProjection(_name(m), _source_id(m))
    if m.command in (SECURITY_HANDSHAKE, SESSION_REQUEST, PIN_CHALLENGE, PIN_RESPONSE):
        return Unsupported(m.command)
    raise MiceError(f"unknown command 0x{m.command:02x}")


def source_ready(name: str, rtsp_port: int, source_id: uuid.UUID) -> bytes:
    """Build a SOURCE_READY (tests and the loopback probe use this)."""
    return build_message(SOURCE_READY, [(FRIENDLY_NAME, name.encode("utf-16-le")),
                                        (RTSP_PORT, struct.pack(">H", rtsp_port)),
                                        (SOURCE_ID, source_id.bytes)])


def stop_projection(name: str, source_id: uuid.UUID) -> bytes:
    return build_message(STOP_PROJECTION, [(FRIENDLY_NAME, name.encode("utf-16-le")), (SOURCE_ID, source_id.bytes)])
