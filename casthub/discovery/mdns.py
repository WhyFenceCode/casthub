"""mDNS records per receiver via Avahi (D-Bus): `<name>._display._tcp.local` on its MS-MICE port.

TXT carries `container_id={GUID}`. The GUID is derived from the machine ID and the port, so it
stays the same across restarts without storing anything, and differs per receiver.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus

log = logging.getLogger("casthub.mdns")
AVAHI = "org.freedesktop.Avahi"
IF_UNSPEC, PROTO_UNSPEC = -1, -1
SERVICE = "_display._tcp"
# Namespace for container IDs (a fixed name, not machine-specific).
_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "display.casthub.invalid")


def container_id(port: int, machine_id: str | None = None, session: str = "") -> str:
    if machine_id is None:
        try:
            machine_id = Path("/etc/machine-id").read_text().strip()
        except OSError:
            machine_id = ""
    # `session` (the receiver's current name) gives a new ID with each new name, so Windows
    # treats the network-path receiver as a new display too.
    key = f"{machine_id}:{port}" + (f":{session}" if session else "")
    return "{" + str(uuid.uuid5(_NS, key)).upper() + "}"


def txt_records(port: int, machine_id: str | None = None, session: str = "") -> list[bytes]:
    return [f"container_id={container_id(port, machine_id, session)}".encode()]


class Mdns:
    """Publishes and withdraws one `_display._tcp` service per receiver."""

    def __init__(self) -> None:
        self.bus: MessageBus | None = None
        self.groups: dict[int, str] = {}     # port → entry group path

    async def _call(self, path: str, iface: str, member: str, signature: str = "", body: list | None = None):
        assert self.bus is not None
        reply = await self.bus.call(Message(destination=AVAHI, path=path, interface=iface, member=member,
                                            signature=signature, body=body or []))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"avahi {member}: {reply.error_name} {reply.body}")
        return reply.body

    async def connect(self) -> None:
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    async def hostname(self) -> str:
        return (await self._call("/", f"{AVAHI}.Server", "GetHostName"))[0]

    async def publish(self, name: str, port: int) -> None:
        if self.bus is None:
            await self.connect()
        await self.withdraw(port)
        (path,) = await self._call("/", f"{AVAHI}.Server", "EntryGroupNew")
        await self._call(path, f"{AVAHI}.EntryGroup", "AddService", "iiussssqaay",
                         [IF_UNSPEC, PROTO_UNSPEC, 0, name, SERVICE, "", "", port, txt_records(port, session=name)])
        await self._call(path, f"{AVAHI}.EntryGroup", "Commit")
        self.groups[port] = path
        log.info("mdns: %s.%s port %d", name, SERVICE, port)

    async def withdraw(self, port: int) -> None:
        path = self.groups.pop(port, None)
        if path and self.bus:
            try:
                await self._call(path, f"{AVAHI}.EntryGroup", "Free")
            except RuntimeError as e:
                log.warning("%s", e)

    async def close(self) -> None:
        for port in list(self.groups):
            await self.withdraw(port)
        if self.bus:
            self.bus.disconnect()
            self.bus = None
