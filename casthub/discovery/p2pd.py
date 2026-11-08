"""casthub-p2p: the root half of Wi-Fi Direct (see discovery/p2p.py for the protocol notes).

    python3 -m casthub.discovery.p2pd [--config /etc/casthub/casthub.toml]

- Advertises the receiver: P2P device name, WFD IEs (primary sink, available) and the MS-MICE
  vendor extension in Probe Responses, then stays in extended listen so Win + K sees us.
- Accepts Wi-Fi Direct casts with push-button pairing. NetworkManager forms the group: it
  removes any group it did not start itself, so we hand it an in-memory wifi-p2p profile for
  the PC and it does the pairing, the addresses and DHCP. (NM 1.58 has no PIN pairing.)
- When the PC's address is known, tells the hub where its RTSP server is.

NetworkManager keeps managing the Wi-Fi interface; we set the advertised IEs in wpa_supplicant.
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
WPS = f"{IFACE}.WPS"
PROPS = "org.freedesktop.DBus.Properties"
RUN = Path("/run/casthub")
SOCK = RUN / "p2p.sock"
NM_LEASES = Path("/var/lib/NetworkManager")    # dnsmasq-<group if>.leases when we are GO
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
        self.connecting = False            # NM is pairing with a PC (one at a time)
        self.saved_methods: str | None = None   # WPS config methods before we advertised
        self.peers: dict[str, int] = {}    # peer path → RTSP port from its WFD IE
        self.announced: set[str] = set()
        self.advertising = False
        self.advertised_dev = ""
        self.probes = 0

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
        # Offer push-button only, so Windows pairs the way NetworkManager can (no PIN).
        if self.saved_methods is None:
            with contextlib.suppress(RuntimeError):
                self.saved_methods = str(await self.get(self.dev, WPS, "ConfigMethods"))
        await self.set(self.dev, WPS, "ConfigMethods", "s", p2p.PUSH_BUTTON_ONLY)
        # Remove the separate IE an earlier version added (harmless if absent).
        old_ie = p2p.wsc_ie(p2p.mice_vendor_extension(host))
        for frame in (p2p.VENDOR_ELEM_PROBE_RESP_P2P, p2p.VENDOR_ELEM_PROBE_RESP_P2P_GO, p2p.VENDOR_ELEM_BEACON_P2P_GO):
            with contextlib.suppress(RuntimeError):
                await self.call(self.dev, IFACE, "VendorElemRem", "iay", [frame, old_ie])
        await self.listen()
        with contextlib.suppress(RuntimeError):      # count discovery scans that reach us (diagnostics)
            await self.call(self.dev, IFACE, "SubscribeProbeReq")
        self.advertising = True
        self.advertised_dev = self.dev
        log.info("advertising %r (MS-MICE host name from this machine)", self.name)
        await self.log_state()

    async def log_state(self) -> None:
        """What wpa_supplicant really advertises now (journal only; no addresses)."""
        try:
            conf = await self.get(self.dev, P2PDEV, "P2PDeviceConfig")
            wfd = await self.get("/fi/w1/wpa_supplicant1", WPAS, "WFDIEs")
            val = {k: v.value for k, v in conf.items()}
            ext = [bytes(v).hex() for v in val.get("VendorExtension", [])]
            log.info("state: name=%r go_intent=%s vendor_ext=%s wfd=%s", val.get("DeviceName"),
                     val.get("GOIntent"), [e[:14] + "…" for e in ext], bytes(wfd).hex())
        except Exception as e:  # diagnostics must never stop advertising

            log.warning("state: %s", e)

    async def watchdog(self) -> None:
        """NetworkManager re-adds the Wi-Fi interface on reconnects (new D-Bus path, our
        settings gone). Re-apply them, and report how many discovery scans reached us."""
        while True:
            await asyncio.sleep(20)
            if self.probes:
                log.info("%d discovery scans from nearby devices in the last 20 s", self.probes)
                self.probes = 0
            if not self.advertising:
                continue
            old = self.advertised_dev
            with contextlib.suppress(RuntimeError):
                await self.find_device()
            if self.dev != old:
                log.info("Wi-Fi interface was re-added; advertising again")
                with contextlib.suppress(RuntimeError):
                    await self.advertise()

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
        await self.nmcli("connection", "delete", p2p.NM_PROFILE)     # ends a Wi-Fi Direct cast, if any
        if self.saved_methods is not None:
            with contextlib.suppress(RuntimeError):
                await self.set(self.dev, WPS, "ConfigMethods", "s", self.saved_methods)
            self.saved_methods = None
        self.advertising = False
        log.info("not advertising (no Display mode hub connected)")

    async def listen(self) -> None:
        # Listen 500 ms every 500 ms: discoverable without blocking the Wi-Fi connection.
        await self.call(self.dev, P2PDEV, "ExtendedListen", "a{sv}",
                        [{"period": Variant("i", 500), "interval": Variant("i", 500)}])

    # ---- groups ---------------------------------------------------------------------
    def on_signal(self, msg: Message) -> bool:
        if msg.message_type == MessageType.SIGNAL and msg.member == "ProbeRequest":
            self.probes += 1
            return False
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
        await self.pair(peer)

    async def on_pd_display_pin(self, peer: str, pin: str, *_):
        log.warning("the PC asked for PIN pairing; Cast Hub only offers push-button (NetworkManager 1.58)")

    async def on_pd_enter_pin(self, peer: str, *_):
        log.warning("the PC shows a PIN; Cast Hub only offers push-button")

    async def on_go_failure(self, *_):
        log.warning("Wi-Fi Direct group negotiation failed")

    async def on_go_request(self, peer: str, *args):
        dev_pw = args[0] if args else p2p.DEV_PW_PUSHBUTTON
        if dev_pw != p2p.DEV_PW_PUSHBUTTON:
            log.warning("the PC wants PIN pairing (password id %s); only push-button works here", dev_pw)
            return
        await self.pair(peer)

    async def nmcli(self, *args: str, timeout: float = 10) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec("nmcli", *args, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            return 124, "timed out"
        return proc.returncode or 0, out.decode(errors="replace")

    async def pair(self, peer: str) -> None:
        """Have NetworkManager connect to this PC (push-button). NM ends up GO or client;
        on_group_started then finds the PC's address."""
        if not self.advertising or self.connecting:
            return
        self.connecting = True
        try:
            with contextlib.suppress(RuntimeError):
                port = p2p.parse_wfd_rtsp_port(bytes(await self.get(peer, PEER, "IEs")))
                if port:
                    self.peers[peer] = port
            addr = p2p.mac(bytes(await self.get(peer, PEER, "DeviceAddress")))
            await self.nmcli("connection", "delete", p2p.NM_PROFILE)
            rc, out = await self.nmcli(*p2p.nm_profile_args(addr, p2p.wfd_subelements(), ZONE))
            if rc:
                log.warning("could not add the Wi-Fi Direct profile: %s", out.strip()[:200])
                return
            log.info("pairing with the PC through NetworkManager (push-button)")
            rc, out = await self.nmcli("--wait", "60", "connection", "up", p2p.NM_PROFILE, timeout=70)
            if rc:
                log.warning("Wi-Fi Direct connection failed: %s", out.strip()[:200])
                await self.end_session()
        finally:
            self.connecting = False

    async def on_group_started(self, props: dict):
        role = props.get("role", Variant("s", "")).value
        ifpath = props["interface_object"].value
        self.group_if = await self.get(ifpath, IFACE, "Ifname")
        log.info("group started, role %s", role)
        asyncio.ensure_future(self._guard(self.find_peer(role)))

    async def nm_p2p_device(self) -> str:
        rc, out = await self.nmcli("-t", "-f", "DEVICE,TYPE", "device")
        for line in out.splitlines():
            dev, _, typ = line.rpartition(":")
            if typ == "wifi-p2p":
                return dev.replace("\\:", ":")
        return ""

    async def find_peer(self, role: str) -> None:
        """Wait for NM to finish addressing, then announce the PC to the hub."""
        port = next(iter(self.peers.values()), 7236)
        nmdev = await self.nm_p2p_device()
        for _ in range(60):
            await asyncio.sleep(0.5)
            found: tuple[str, str] | None = None
            if role == "GO":
                with contextlib.suppress(OSError):
                    found = p2p.peer_from_leases((NM_LEASES / f"dnsmasq-{self.group_if}.leases").read_text())
            elif nmdev:
                _, out = await self.nmcli("-t", "-f", "IP4,DHCP4", "device", "show", nmdev)
                addr = p2p.peer_from_nm(out)
                found = (addr, "") if addr else None
            if found and found[0] not in self.announced:
                self.announced.add(found[0])
                log.info("PC reachable over Wi-Fi Direct; telling the hub")
                self.broadcast({"event": "source", "addr": found[0], "rtsp_port": port, "name": found[1]})
                return
        log.warning("Wi-Fi Direct is up but the PC's address never appeared")

    async def on_group_finished(self, props: dict):
        log.info("group finished")
        await self.end_session()

    async def end_session(self) -> None:
        for addr in self.announced:
            self.broadcast({"event": "gone", "addr": addr})
        self.announced.clear()
        self.peers.clear()
        await self.nmcli("connection", "delete", p2p.NM_PROFILE)
        if self.advertising:          # NM clears the WFD IEs when its P2P connection ends
            with contextlib.suppress(RuntimeError):
                await self.advertise()

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
        self.watch_task = asyncio.create_task(self.watchdog())
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
