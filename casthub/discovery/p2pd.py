"""casthub-p2p: the root half of Wi-Fi Direct (see discovery/p2p.py for the protocol notes).

    python3 -m casthub.discovery.p2pd [--config /etc/casthub/casthub.toml]

- Advertises the receiver: P2P device name, WFD IEs (primary sink, available) and the MS-MICE
  vendor extension in Probe Responses, then stays in extended listen so Win + K sees us.
- Accepts Wi-Fi Direct casts with push-button pairing. NetworkManager forms the group: it
  removes any group it did not start itself, so we hand it an in-memory wifi-p2p profile for
  the PC and it does the pairing, the addresses and DHCP. (NM 1.58 has no PIN pairing.)
- When the PC's address is known, tells the hub where its RTSP server is.

- Gives the radio a new random Wi-Fi Direct address when the hub asks (each new receiver name),
  so Windows treats us as a new display. wpa_supplicant only picks that address when it sets up
  the Wi-Fi interface, so new_address() re-adds the interface: Wi-Fi drops for a few seconds.

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
import time
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
PAIR_TIMEOUT_S = 60          # give up on a Wi-Fi Direct pairing after this
PAIR_RETRY_AFTER_S = 3       # a repeated request from the PC this long after ours restarts NM
PAIR_RESTARTS = 4
INVITE_FALLBACK_S = 10        # after refusing an invitation, wait this long for the PC to pair afresh
ZONE = "casthub-p2p"            # firewalld zone (system/firewalld/casthub-p2p.xml): DHCP + RTP
ROOT = "/fi/w1/wpa_supplicant1"
# wpa_supplicant runs as NetworkManager_t (SELinux), which can read NM's own run directory.
SUPPLICANT_CONF = Path("/run/NetworkManager/casthub-p2p-supplicant.conf")
REJOIN_S = 30                   # wait this long for NetworkManager to reconnect Wi-Fi


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
        self.pair_started = 0.0
        self.pair_restarts = 0
        self.pair_watch: asyncio.Task | None = None
        self.saved_methods: str | None = None   # WPS config methods before we advertised
        self.peers: dict[str, int] = {}    # peer path → RTSP port from its WFD IE
        self.announced: set[str] = set()
        self.advertising = False
        self.advertised_dev = ""
        self.probes = 0
        self.radio = asyncio.Lock()        # a new address and advertising never overlap
        self.address_due = False           # the hub asked for a new Wi-Fi Direct address
        self.busy = False                  # the hub is casting: not discoverable (set_busy)
        self.mice_addresses: list[str] = []      # our Wi-Fi IPv4 address(es) in the MS-MICE attributes

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

    async def advertise(self, new_address: bool = False) -> None:
        # A Wi-Fi Direct cast often ends at the hub (RTSP teardown) before its group does:
        # then the new address waits for end_session, once the group is gone.
        self.address_due |= new_address
        async with self.radio:
            if self.address_due and not self.connecting and not self.announced:
                self.address_due = False
                try:
                    await self.new_address()
                except Exception:
                    log.exception("new Wi-Fi Direct address failed")
            await self._advertise()
        self.broadcast({"event": "advertised", "name": self.name})     # the hub shows the name now

    async def _advertise(self) -> None:
        await self.find_device()
        host = socket.gethostname().split(".")[0]
        # Our Wi-Fi address too ([MS-MICE] "IP Address Attribute", as Windows' own sinks do): Windows then skips
        # resolving the host name, which it gives 1.5 s before falling back to Wi-Fi Direct.
        self.mice_addresses = await self.wifi_addresses()
        # The MS-MICE attributes go *inside* wpa_supplicant's own WSC IE (VendorExtension), not
        # in a second IE: Windows did not see them there and fell back to Wi-Fi Direct.
        conf = {"DeviceName": Variant("s", self.name[:32]),
                "PrimaryDeviceType": Variant("ay", p2p.PRIMARY_DEVICE_TYPE),
                "GOIntent": Variant("u", 15),
                "VendorExtension": Variant("aay", [p2p.mice_vendor_payload(host, self.mice_addresses)])}
        await self.set(self.dev, P2PDEV, "P2PDeviceConfig", "a{sv}", conf)
        await self.set("/fi/w1/wpa_supplicant1", WPAS, "WFDIEs", "ay", p2p.wfd_subelements(available=not self.busy))
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
        await self.forget_groups()
        if not self.busy:
            await self.listen()
        with contextlib.suppress(RuntimeError):      # count discovery scans that reach us (diagnostics)
            await self.call(self.dev, IFACE, "SubscribeProbeReq")
        self.advertising = True
        self.advertised_dev = self.dev
        log.info("advertising %r (MS-MICE host name and %d address(es) from this machine)", self.name,
                 len(self.mice_addresses))
        await self.log_state()

    async def wifi_addresses(self) -> list[str]:
        """The Wi-Fi interface's IPv4 addresses (the network PCs cast over), [] if none."""
        wifi = wifi_interface(self.cfg.miracast_interface)
        _, out = await self.nmcli("-t", "-f", "IP4.ADDRESS", "device", "show", wifi)
        return p2p.ipv4_addresses(out)

    async def wifi_state(self, wifi: str) -> str:
        _, out = await self.nmcli("-t", "-f", "DEVICE,STATE", "device")
        for line in out.splitlines():
            dev, _, state = line.rpartition(":")
            if dev == wifi:
                return state
        return ""

    async def iw_p2p_address(self) -> str:
        with contextlib.suppress(OSError):
            proc = await asyncio.create_subprocess_exec("iw", "dev", stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.DEVNULL)
            out, _ = await proc.communicate()
            return p2p.p2p_device_address(out.decode(errors="replace"))
        return ""

    async def new_address(self) -> None:
        """Re-add the Wi-Fi interface to wpa_supplicant with a config file holding a fresh random
        P2P device address (wpa_supplicant 2.11 only sets it when it creates the P2P device,
        and NetworkManager never passes a config file). NM sees the interface come back and
        adopts it (CreateInterface → InterfaceExists → GetInterface), then reconnects Wi-Fi.
        The station link drops meanwhile, so the hub asks only when no screen is casting."""
        wifi = wifi_interface(self.cfg.miracast_interface)
        (path,) = await self.call(ROOT, WPAS, "GetInterface", "s", [wifi])
        driver = str(await self.get(path, IFACE, "Driver"))
        was = await self.wifi_state(wifi)
        address = p2p.random_address()
        fd = os.open(SUPPLICANT_CONF, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(p2p.supplicant_conf(address))
        log.info("new Wi-Fi Direct address: re-adding %s to wpa_supplicant (Wi-Fi drops briefly)", wifi)
        await self.call(ROOT, WPAS, "RemoveInterface", "o", [path])
        args = {"Ifname": Variant("s", wifi), "Driver": Variant("s", driver)}
        try:
            await self.call(ROOT, WPAS, "CreateInterface", "a{sv}",
                            [args | {"ConfigFile": Variant("s", str(SUPPLICANT_CONF))}])
        except RuntimeError as e:
            if "InterfaceExists" in str(e):
                log.warning("NetworkManager re-added Wi-Fi first; the address stays this time")
            else:
                log.warning("wpa_supplicant refused the new address (%s); re-adding Wi-Fi as before", e)
                with contextlib.suppress(RuntimeError):
                    await self.call(ROOT, WPAS, "CreateInterface", "a{sv}", [args])
        now = await self.iw_p2p_address()
        if now == p2p.mac(address):
            log.info("new Wi-Fi Direct address in use")
        else:
            log.warning("the Wi-Fi Direct address did not change")
        # NetworkManager reconnects Wi-Fi by itself; nudge it if it doesn't.
        if was == "connected":
            for _ in range(REJOIN_S * 2):
                await asyncio.sleep(0.5)
                if await self.wifi_state(wifi) == "connected":
                    log.info("Wi-Fi reconnected")
                    break
            else:
                log.warning("Wi-Fi did not reconnect within %d s; asking NetworkManager", REJOIN_S)
                await self.nmcli("device", "connect", wifi, timeout=30)
        await self.find_device()

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
            moved = await self.wifi_addresses() != self.mice_addresses
            if (self.dev != old or moved) and not self.radio.locked():
                log.info("Wi-Fi %s; advertising again", "address changed" if moved else "interface was re-added")
                with contextlib.suppress(RuntimeError):
                    await self.advertise()

    async def set_busy(self, busy: bool) -> None:
        """A cast is on (any kind: Wi-Fi Direct, network, AirPlay): the WFD IE says "session not
        available" and we stop extended listen, so Win + K drops us until the cast ends. A Wi-Fi
        Direct group already formed is not touched."""
        self.busy = busy
        async with self.radio:
            if not self.advertising:
                return
            with contextlib.suppress(RuntimeError):
                await self.find_device()
                await self.set("/fi/w1/wpa_supplicant1", WPAS, "WFDIEs", "ay", p2p.wfd_subelements(available=not busy))
                if busy:
                    await self.call(self.dev, P2PDEV, "ExtendedListen", "a{sv}", [{}])     # not discoverable
                else:
                    await self.listen()
        log.info("%s", "casting: not discoverable" if busy else "cast over: discoverable again")

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
                   "GroupFinished": self.on_group_finished,
                   "InvitationReceived": self.on_invitation}.get(msg.member)
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
        await asyncio.sleep(0.5)     # let wpa_supplicant finish answering the request first
        await self.pair(peer)

    async def on_pd_display_pin(self, peer: str, pin: str, *_):
        log.warning("the PC asked for PIN pairing; Cast Hub only offers push-button (NetworkManager 1.58)")

    async def on_pd_enter_pin(self, peer: str, *_):
        log.warning("the PC shows a PIN; Cast Hub only offers push-button")

    async def on_go_failure(self, *_):
        log.warning("Wi-Fi Direct group negotiation failed")

    async def forget_groups(self) -> None:
        """Drop saved (persistent) groups. Windows marks the group it owns as persistent and
        next time only sends an invitation to it, which NetworkManager can't take: without the
        saved group the PC pairs afresh."""
        with contextlib.suppress(RuntimeError):
            await self.call(self.dev, P2PDEV, "RemoveAllPersistentGroups")

    async def on_invitation(self, props: dict, *_):
        """Windows remembers the last group (it marks the group it owns persistent) and, next
        time, only invites us back to it. Joining it would start a group NetworkManager didn't
        start, which NM removes at once. So we have no saved group: wpa_supplicant answers
        "unknown group", and Windows should fall back to pairing afresh (push-button), which
        reaches pair(). Pairing on top of the invitation made Windows ignore us, so we only
        start it ourselves if the PC hasn't asked within a few seconds."""
        sa = props.get("sa")
        if not sa or not self.advertising:
            return
        await self.forget_groups()
        asked = time.monotonic()
        log.info("the PC invited us to an old group; refused, waiting for it to pair afresh")
        await asyncio.sleep(INVITE_FALLBACK_S)
        if self.connecting or self.announced or self.pair_started > asked or not self.advertising:
            return
        log.info("the PC did not pair afresh within %d s; asking it to", INVITE_FALLBACK_S)
        want = bytes(sa.value).hex()
        with contextlib.suppress(RuntimeError):
            for peer in await self.get(self.dev, P2PDEV, "Peers"):
                if peer.rsplit("/", 1)[-1].lower() == want:
                    await self.pair(peer)
                    return
        log.warning("the inviting PC is not in wpa_supplicant's peer list")

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
        on_group_started then finds the PC's address.

        NM's connect call to wpa_supplicant can fail at once ("unspecified error") when it
        lands right on the PC's request, and NM then just waits 45 s while refusing the PC's
        retries. So we don't wait on NM: if the PC asks again before a group forms, we
        restart NM's attempt, which then answers the PC's new request."""
        if not self.advertising:
            return
        now = time.monotonic()
        if self.connecting:
            if now - self.pair_started > PAIR_RETRY_AFTER_S and self.pair_restarts < PAIR_RESTARTS:
                self.pair_restarts += 1
                self.pair_started = now
                log.info("the PC asked again before Wi-Fi Direct formed; restarting NetworkManager's attempt")
                await self.nmcli("--wait", "0", "connection", "up", p2p.NM_PROFILE)
            return
        self.connecting, self.pair_started, self.pair_restarts = True, now, 0
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
                self.connecting = False
                return
            log.info("pairing with the PC through NetworkManager (push-button)")
            rc, out = await self.nmcli("--wait", "0", "connection", "up", p2p.NM_PROFILE)
            if rc:
                log.warning("NetworkManager would not start Wi-Fi Direct: %s", out.strip()[:200])
        except Exception:
            self.connecting = False
            raise
        self.pair_watch = asyncio.create_task(self._guard(self.pair_deadline(self.pair_started)))

    async def pair_deadline(self, started: float) -> None:
        await asyncio.sleep(PAIR_TIMEOUT_S)
        if self.connecting and self.pair_started >= started:
            log.warning("Wi-Fi Direct did not form within %d s; giving up", PAIR_TIMEOUT_S)
            self.connecting = False
            await self.end_session()

    async def on_group_started(self, props: dict):
        role = props.get("role", Variant("s", "")).value
        ifpath = props["interface_object"].value
        self.group_if = await self.get(ifpath, IFACE, "Ifname")
        self.connecting = False
        log.info("group started, role %s", role)
        # As client, the PC (GO) usually hands out addresses in the 4-way handshake (EAPOL IP
        # allocation): no DHCP, and wpa_supplicant tells us the GO's address right here.
        go_ip = p2p.ipv4(bytes(props["IpAddrGo"].value)) if "IpAddrGo" in props else None
        if role != "GO" and go_ip:
            self.announce(go_ip, "")
            return
        asyncio.ensure_future(self._guard(self.find_peer(role)))

    def announce(self, addr: str, name: str) -> None:
        if addr in self.announced:
            return
        self.announced.add(addr)
        port = next(iter(self.peers.values()), 7236)
        log.info("PC reachable over Wi-Fi Direct (RTSP port %d); telling the hub", port)
        self.broadcast({"event": "source", "addr": addr, "rtsp_port": port, "name": name})

    async def nm_p2p_device(self) -> str:
        rc, out = await self.nmcli("-t", "-f", "DEVICE,TYPE", "device")
        for line in out.splitlines():
            dev, _, typ = line.rpartition(":")
            if typ == "wifi-p2p":
                return dev.replace("\\:", ":")
        return ""

    async def find_peer(self, role: str) -> None:
        """Wait for NM to finish addressing, then announce the PC to the hub."""
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
            if found:
                self.announce(*found)
                return
        log.warning("Wi-Fi Direct is up but the PC's address never appeared")

    async def on_group_finished(self, props: dict):
        log.info("group finished")
        self.connecting = False
        await self.end_session()

    async def end_session(self) -> None:
        for addr in self.announced:
            self.broadcast({"event": "gone", "addr": addr})
        self.announced.clear()
        self.peers.clear()
        await self.nmcli("connection", "delete", p2p.NM_PROFILE)
        await self.forget_groups()
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
                        await self.advertise(new_address=bool(msg.get("new_address")))
                    elif msg.get("cmd") == "busy":
                        await self.set_busy(bool(msg.get("busy")))
                except (ValueError, KeyError, RuntimeError) as e:
                    log.warning("hub message: %s", e)
        finally:
            self.clients.discard(writer)
            writer.close()
            if not self.clients:
                self.busy = False              # the next hub starts discoverable
                if self.advertising:
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
