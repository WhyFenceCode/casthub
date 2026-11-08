"""casthub-p2p: the root half of Wi-Fi Direct (see discovery/p2p.py for the protocol notes).

    python3 -m casthub.discovery.p2pd [--config /etc/casthub/casthub.toml]

- Advertises the receiver: P2P device name, WFD IEs (primary sink, available) and the MS-MICE
  vendor extension in Probe Responses, then stays in extended listen so Win + K sees us.
- Accepts Wi-Fi Direct groups with push-button (no PIN, SPEC v1 scope). We ask to be group
  owner; on our GO interface we set `miracast.p2p_address` and serve DHCP with dnsmasq.
- When the PC has a lease, tells the hub where its RTSP server is.

NetworkManager keeps managing the Wi-Fi interface; we only use its P2P device in wpa_supplicant.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import grp
import json
import logging
import os
import socket
import subprocess
from pathlib import Path

from dbus_fast import BusType, Message, MessageType, Variant
from dbus_fast.aio import MessageBus

from casthub import config as cfgmod
from casthub.discovery import p2p

log = logging.getLogger("casthub.p2p")
WPAS = "fi.w1.wpa_supplicant1"
IFACE = f"{WPAS}.Interface"
P2PDEV = f"{IFACE}.P2PDevice"
PEER = f"{WPAS}.Peer"
PROPS = "org.freedesktop.DBus.Properties"
RUN = Path("/run/casthub")
SOCK = RUN / "p2p.sock"
LEASES = RUN / "p2p-leases"
ZONE = "casthub-p2p"            # firewalld zone (system/firewalld/casthub-p2p.xml): DHCP + RTP


def wifi_interface(preferred: str) -> str:
    if (Path("/sys/class/net") / preferred / "wireless").exists():
        return preferred
    for d in sorted(Path("/sys/class/net").iterdir()):
        if (d / "wireless").exists() and not d.name.startswith("p2p-"):
            return d.name
    raise RuntimeError("no Wi-Fi interface")


class P2PDaemon:
    def __init__(self, cfg: cfgmod.Config) -> None:
        self.cfg = cfg
        self.bus: MessageBus | None = None
        self.dev = ""                      # object path of the P2P device interface
        self.name = cfg.receiver_name(0)
        self.port = cfg.miracast_base_port
        self.clients: set[asyncio.StreamWriter] = set()
        self.group_if = ""
        self.dnsmasq: subprocess.Popen | None = None
        self.peers: dict[str, int] = {}    # peer path → RTSP port from its WFD IE
        self.announced: set[str] = set()
        self.pin = ""
        self.advertising = False

    # ---- D-Bus helpers -------------------------------------------------------------
    async def call(self, path: str, iface: str, member: str, sig: str = "", body: list | None = None, dest: str = WPAS):
        reply = await self.bus.call(Message(destination=dest, path=path, interface=iface, member=member,
                                            signature=sig, body=body or []))
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{member}: {reply.error_name} {reply.body}")
        return reply.body

    async def get(self, path: str, iface: str, prop: str):
        return (await self.call(path, PROPS, "Get", "ss", [iface, prop]))[0].value

    async def set(self, path: str, iface: str, prop: str, sig: str, value) -> None:
        await self.call(path, PROPS, "Set", "ssv", [iface, prop, Variant(sig, value)])

    # ---- advertising ---------------------------------------------------------------
    async def find_device(self) -> None:
        """Look up the P2P device's object path. It changes whenever NetworkManager re-adds the
        Wi-Fi interface (e.g. after a reconnect), so this runs before every advertise."""
        wifi = wifi_interface(self.cfg.miracast_interface)
        # With NetworkManager, P2P runs on wpa_supplicant's hidden p2p-dev device, but D-Bus
        # exposes its P2PDevice API on the Wi-Fi interface's own object.
        for name in (f"p2p-dev-{wifi}", wifi):
            try:
                (self.dev,) = await self.call("/fi/w1/wpa_supplicant1", WPAS, "GetInterface", "s", [name])
                return
            except RuntimeError:
                continue
        raise RuntimeError(f"wpa_supplicant does not manage {wifi}")

    async def start(self) -> None:
        self.bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        await self.find_device()
        self.bus.add_message_handler(self.on_signal)
        await self.call("/org/freedesktop/DBus", "org.freedesktop.DBus", "AddMatch", "s",
                        [f"type='signal',sender='{WPAS}'"], dest="org.freedesktop.DBus")
        # Silent until a hub (Display mode) connects and asks: Cast Hub is only a cast
        # target while Display mode is on screen.
        await self.unadvertise()

    async def advertise(self) -> None:
        await self.find_device()
        host = socket.gethostname().split(".")[0]
        # The MS-MICE attributes go *inside* wpa_supplicant's own WSC IE (VendorExtension), not
        # in a second IE: Windows did not see them there and fell back to Wi-Fi Direct.
        conf = {"DeviceName": Variant("s", self.name[:32]),
                "PrimaryDeviceType": Variant("ay", p2p.PRIMARY_DEVICE_TYPE),
                "GOIntent": Variant("u", 15),
                "VendorExtension": Variant("aay", [p2p.mice_vendor_payload(host)])}
        await self.set(self.dev, P2PDEV, "P2PDeviceConfig", "a{sv}", conf)
        await self.set("/fi/w1/wpa_supplicant1", WPAS, "WFDIEs", "ay", p2p.wfd_subelements())
        # Remove the separate IE an earlier version added (harmless if absent).
        old_ie = p2p.wsc_ie(p2p.mice_vendor_extension(host))
        for frame in (p2p.VENDOR_ELEM_PROBE_RESP_P2P, p2p.VENDOR_ELEM_PROBE_RESP_P2P_GO, p2p.VENDOR_ELEM_BEACON_P2P_GO):
            with contextlib.suppress(RuntimeError):
                await self.call(self.dev, IFACE, "VendorElemRem", "iay", [frame, old_ie])
        await self.listen()
        self.advertising = True
        log.info("advertising %r (MS-MICE host name from this machine)", self.name)

    async def unadvertise(self) -> None:
        """Stop answering P2P discovery and drop the Miracast IEs."""
        with contextlib.suppress(RuntimeError):
            await self.find_device()
        with contextlib.suppress(RuntimeError):
            await self.call(self.dev, P2PDEV, "ExtendedListen", "a{sv}", [{}])     # no period = off
        with contextlib.suppress(RuntimeError):
            await self.call(self.dev, P2PDEV, "StopFind")
        conf = {"DeviceName": Variant("s", socket.gethostname().split(".")[0][:32]),
                "VendorExtension": Variant("aay", [])}
        with contextlib.suppress(RuntimeError):
            await self.set(self.dev, P2PDEV, "P2PDeviceConfig", "a{sv}", conf)
        with contextlib.suppress(RuntimeError):     # sink, session *not* available
            await self.set("/fi/w1/wpa_supplicant1", WPAS, "WFDIEs", "ay", p2p.wfd_subelements(available=False))
        self.advertising = False
        log.info("not advertising (no Display mode hub connected)")

    async def listen(self) -> None:
        # Listen 500 ms every 500 ms: discoverable without blocking the Wi-Fi connection.
        await self.call(self.dev, P2PDEV, "ExtendedListen", "a{sv}",
                        [{"period": Variant("i", 500), "interval": Variant("i", 500)}])

    # ---- groups ---------------------------------------------------------------------
    def on_signal(self, msg: Message) -> bool:
        if msg.message_type != MessageType.SIGNAL or msg.interface != P2PDEV:
            return False
        handler = {"GONegotiationRequest": self.on_go_request,
                   "ProvisionDiscoveryPBCRequest": self.on_pbc_request,
                   "ProvisionDiscoveryRequestDisplayPin": self.on_pd_display_pin,
                   "ProvisionDiscoveryRequestEnterPin": self.on_pd_enter_pin,
                   "GONegotiationFailure": self.on_go_failure,
                   "GroupStarted": self.on_group_started,
                   "GroupFinished": self.on_group_finished}.get(msg.member)
        if msg.member not in ("DeviceFound", "DeviceFoundProperties", "DeviceLost", "FindStopped"):
            log.info("p2p signal %s", msg.member)
        if handler:
            asyncio.ensure_future(self._guard(handler(*msg.body)))
        return False

    async def _guard(self, coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("p2p handler failed")

    async def on_pbc_request(self, peer: str, *_):
        log.info("push-button request")

    async def on_pd_display_pin(self, peer: str, pin: str, *_):
        """The PC will ask its user for a PIN: show this one on the screen."""
        self.show_pin(pin)

    async def on_pd_enter_pin(self, peer: str, *_):
        log.warning("the PC shows a PIN and wants it typed here; Cast Hub has no keypad (use its PIN prompt instead)")

    async def on_go_failure(self, *_):
        log.warning("Wi-Fi Direct group negotiation failed")
        self.show_pin("")

    def show_pin(self, pin: str) -> None:
        self.pin = pin
        self.broadcast({"event": "pin", "pin": pin})

    async def on_go_request(self, peer: str, *args):
        if not self.cfg.p2p_address:
            log.warning("Wi-Fi Direct cast refused: set miracast.p2p_address in casthub.toml")
            return
        with contextlib.suppress(RuntimeError):
            ies = await self.get(peer, PEER, "IEs")
            port = p2p.parse_wfd_rtsp_port(bytes(ies))
            if port:
                self.peers[peer] = port
        dev_pw = args[0] if args else p2p.DEV_PW_PUSHBUTTON
        opts = {"peer": Variant("o", peer), "go_intent": Variant("i", 15), "persistent": Variant("b", False)}
        if dev_pw == p2p.DEV_PW_PUSHBUTTON:
            opts["wps_method"] = Variant("s", "pbc")
        elif dev_pw == p2p.DEV_PW_REGISTRAR_SPECIFIED:
            log.warning("the PC displays a PIN; Cast Hub can only show one")
            return
        else:                       # the PC's user types a PIN: we display it
            if not self.pin:
                self.show_pin(p2p.wps_pin())
            opts["wps_method"] = Variant("s", "display")
            opts["pin"] = Variant("s", self.pin)
        await self.call(self.dev, P2PDEV, "Connect", "a{sv}", [opts])

    async def on_group_started(self, props: dict):
        role = props.get("role", Variant("s", "")).value
        ifpath = props["interface_object"].value
        self.group_if = await self.get(ifpath, IFACE, "Ifname")
        log.info("group started, role %s", role)
        self.show_pin("")
        if role != "GO":
            log.warning("we are not group owner; Wi-Fi Direct needs us as GO (no DHCP client here)")
            return
        ours, first, last, mask = p2p.dhcp_range(self.cfg.p2p_address)
        prefix = self.cfg.p2p_address.split("/")[1]
        subprocess.run(["ip", "addr", "replace", f"{ours}/{prefix}", "dev", self.group_if], check=True)
        subprocess.run(["ip", "link", "set", self.group_if, "up"], check=True)
        subprocess.run(["firewall-cmd", f"--zone={ZONE}", f"--change-interface={self.group_if}"],
                       check=False, capture_output=True)
        LEASES.unlink(missing_ok=True)
        self.dnsmasq = subprocess.Popen(
            ["dnsmasq", "--keep-in-foreground", "--conf-file=/dev/null", "--port=0", "--bind-interfaces",
             f"--interface={self.group_if}", "--except-interface=lo",
             f"--dhcp-range={first},{last},{mask},1h", f"--dhcp-leasefile={LEASES}",
             "--dhcp-option=3", "--dhcp-option=6", f"--pid-file={RUN / 'p2p-dnsmasq.pid'}"])
        asyncio.ensure_future(self._guard(self.wait_for_lease()))

    async def wait_for_lease(self) -> None:
        port = next(iter(self.peers.values()), 7236)
        for _ in range(60):
            await asyncio.sleep(0.5)
            try:
                lines = LEASES.read_text().split("\n")
            except OSError:
                continue
            for line in lines:
                f = line.split()
                if len(f) >= 3 and f[2] not in self.announced:
                    self.announced.add(f[2])
                    self.broadcast({"event": "source", "addr": f[2], "rtsp_port": port, "name": f[3] if len(f) > 3 else ""})
                    return
        log.warning("Wi-Fi Direct peer never took a DHCP lease")

    async def on_group_finished(self, props: dict):
        log.info("group finished")
        for addr in self.announced:
            self.broadcast({"event": "gone", "addr": addr})
        self.announced.clear()
        self.peers.clear()
        if self.dnsmasq:
            self.dnsmasq.terminate()
            self.dnsmasq = None
        if self.advertising:
            await self.listen()

    # ---- hub socket ----------------------------------------------------------------------
    def broadcast(self, event: dict) -> None:
        line = json.dumps(event).encode() + b"\n"
        for w in list(self.clients):
            w.write(line)

    @staticmethod
    def allowed(writer: asyncio.StreamWriter) -> bool:
        """root, the casthub user, and admins (wheel: testing from the desktop)."""
        import pwd
        import struct as st
        sock = writer.get_extra_info("socket")
        try:
            _pid, uid, gid = st.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, st.calcsize("3i")))
            name = pwd.getpwuid(uid).pw_name
            groups = {g.gr_name for g in grp.getgrall() if name in g.gr_mem} | {grp.getgrgid(gid).gr_name}
        except (OSError, KeyError):
            return False
        return uid == 0 or name == "casthub" or bool(groups & {"casthub", "wheel"})

    async def on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if not self.allowed(writer):
            writer.close()
            return
        self.clients.add(writer)
        if self.pin:
            self.broadcast({"event": "pin", "pin": self.pin})
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                    if msg.get("cmd") == "advertise":
                        self.name, self.port = str(msg["name"]), int(msg.get("port", self.port))
                        await self.advertise()
                except (ValueError, KeyError, RuntimeError) as e:
                    log.warning("hub message: %s", e)
        finally:
            self.clients.discard(writer)
            writer.close()
            if not self.clients and self.advertising:
                await self.unadvertise()

    async def serve(self) -> None:
        RUN.mkdir(exist_ok=True)
        SOCK.unlink(missing_ok=True)
        server = await asyncio.start_unix_server(self.on_client, str(SOCK))
        os.chmod(SOCK, 0o666)        # who may talk is checked per connection (allowed())
        await self.start()
        async with server:
            await server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=cfgmod.DEFAULT_PATH)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="casthub-p2p: %(message)s")
    asyncio.run(P2PDaemon(cfgmod.load(a.config)).serve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
