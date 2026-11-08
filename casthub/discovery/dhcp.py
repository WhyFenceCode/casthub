"""A minimal DHCP server for the Wi-Fi Direct group, where Cast Hub is the group owner.

One PC joins the group per cast; it asks for an address (DISCOVER → OFFER, REQUEST → ACK) and
then serves its RTSP stream from it. We hand out addresses from a small pool and report each
lease, so casthub-p2p knows where the PC is at once (no lease file to watch).

Why not dnsmasq: casthub-p2p runs with NoNewPrivileges, so starting dnsmasq can't switch to
its SELinux domain; and only this one tiny job is needed. No router or DNS options: the link is
only for the cast. RFC 2131 / RFC 2132.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("casthub.p2p")

MAGIC = b"\x63\x82\x53\x63"
DISCOVER, OFFER, REQUEST, DECLINE, ACK, NAK, RELEASE, INFORM = 1, 2, 3, 4, 5, 6, 7, 8
OPT_MASK, OPT_HOSTNAME, OPT_REQUESTED, OPT_LEASE, OPT_TYPE, OPT_SERVER, OPT_END = 1, 12, 50, 51, 53, 54, 255
LEASE_S = 3600
HEADER = struct.Struct("!BBBBIHH4s4s4s4s16s64s128s")      # op .. file (236 bytes)


@dataclass(frozen=True)
class Request:
    xid: int
    flags: int
    chaddr: bytes            # the client's hardware address (6 bytes)
    ciaddr: str
    kind: int                # DHCP message type
    requested: str = ""      # option 50
    server: str = ""         # option 54
    hostname: str = ""       # option 12

    @property
    def mac(self) -> str:
        return ":".join(f"{b:02x}" for b in self.chaddr)


def parse(data: bytes) -> Request | None:
    """A client message (op 1, Ethernet), or None for anything else."""
    if len(data) < HEADER.size + 4 or data[HEADER.size:HEADER.size + 4] != MAGIC:
        return None
    op, htype, hlen, _hops, xid, _secs, flags, ciaddr, _y, _s, _g, chaddr, _sn, _f = HEADER.unpack_from(data)
    if op != 1 or htype != 1 or hlen != 6:
        return None
    opts: dict[int, bytes] = {}
    i = HEADER.size + 4
    while i < len(data):
        code = data[i]
        if code == OPT_END:
            break
        if code == 0:                 # pad
            i += 1
            continue
        if i + 1 >= len(data):
            break
        n = data[i + 1]
        opts[code] = data[i + 2:i + 2 + n]
        i += 2 + n
    if OPT_TYPE not in opts or len(opts[OPT_TYPE]) != 1:
        return None

    def addr(code: int) -> str:
        v = opts.get(code, b"")
        return socket.inet_ntoa(v) if len(v) == 4 else ""
    return Request(xid, flags, chaddr[:6], socket.inet_ntoa(ciaddr), opts[OPT_TYPE][0],
                   addr(OPT_REQUESTED), addr(OPT_SERVER), opts.get(OPT_HOSTNAME, b"").decode(errors="replace"))


def reply(req: Request, kind: int, yiaddr: str, server: str, mask: str, lease_s: int = LEASE_S) -> bytes:
    """OFFER / ACK (with the address) or NAK (yiaddr 0.0.0.0)."""
    head = HEADER.pack(2, 1, 6, 0, req.xid, 0, req.flags, b"\0" * 4, socket.inet_aton(yiaddr),
                       socket.inet_aton(server), b"\0" * 4, req.chaddr.ljust(16, b"\0"), b"\0" * 64, b"\0" * 128)
    opts = bytes([OPT_TYPE, 1, kind]) + bytes([OPT_SERVER, 4]) + socket.inet_aton(server)
    if kind != NAK:
        opts += bytes([OPT_LEASE, 4]) + struct.pack("!I", lease_s) + bytes([OPT_MASK, 4]) + socket.inet_aton(mask)
    return head + MAGIC + opts + bytes([OPT_END])


class Pool:
    """Addresses for the group's clients; the same client keeps its address."""

    def __init__(self, network: str, server: str, first: int = 10, last: int = 99) -> None:
        self.net = ipaddress.IPv4Network(network)
        self.server = server
        self.free = [str(self.net[i]) for i in range(first, last + 1) if str(self.net[i]) != server]
        self.leases: dict[str, str] = {}       # mac → address

    @property
    def mask(self) -> str:
        return str(self.net.netmask)

    def offer(self, mac: str, requested: str = "") -> str | None:
        if mac in self.leases:
            return self.leases[mac]
        taken = set(self.leases.values())
        if requested in self.free and requested not in taken:
            pick = requested
        else:
            pick = next((a for a in self.free if a not in taken), None)
        if pick:
            self.leases[mac] = pick
        return pick

    def release(self, mac: str) -> None:
        self.leases.pop(mac, None)


def answer(pool: Pool, req: Request) -> tuple[bytes | None, str | None]:
    """The reply to send (or None), and the address just leased (on ACK)."""
    if req.kind == DISCOVER:
        ip = pool.offer(req.mac, req.requested)
        return (reply(req, OFFER, ip, pool.server, pool.mask), None) if ip else (None, None)
    if req.kind == REQUEST:
        if req.server and req.server != pool.server:
            return None, None                          # the client chose another server
        want = req.requested or req.ciaddr
        ip = pool.offer(req.mac, want)
        if ip and (not want or want == "0.0.0.0" or want == ip):
            return reply(req, ACK, ip, pool.server, pool.mask), ip
        return reply(req, NAK, "0.0.0.0", pool.server, pool.mask), None
    if req.kind in (RELEASE, DECLINE):
        pool.release(req.mac)
    return None, None


class Server(asyncio.DatagramProtocol):
    """Serves DHCP on one interface. `on_lease(address, mac, hostname)` after each ACK."""

    def __init__(self, ifname: str, pool: Pool, on_lease: Callable[[str, str, str], None]) -> None:
        self.ifname, self.pool, self.on_lease = ifname, pool, on_lease
        self.transport: asyncio.DatagramTransport | None = None

    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, self.ifname.encode())
        sock.bind(("", 67))
        sock.setblocking(False)
        self.transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: self, sock=sock)
        log.info("DHCP for the Wi-Fi Direct group on %s (%s)", self.ifname, self.pool.net)

    def stop(self) -> None:
        if self.transport:
            self.transport.close()
            self.transport = None

    def datagram_received(self, data: bytes, addr) -> None:
        req = parse(data)
        if req is None:
            return
        out, leased = answer(self.pool, req)
        if out and self.transport:
            # The client has no address yet: broadcast (always allowed, RFC 2131 4.1).
            self.transport.sendto(out, ("255.255.255.255", 68))
        if leased:
            log.info("DHCP: gave the PC %s", leased)
            self.on_lease(leased, req.mac, req.hostname)
