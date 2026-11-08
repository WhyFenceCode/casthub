"""The hub's own Wi-Fi network (hotspot), for casting where the network keeps devices apart.

Runs inside casthub-p2p (root), which owns the radio. School Wi-Fi typically blocks traffic
between devices: the receiver's name shows up but a cast can't reach it. Phones then join the
hotspot (QR code on the idle screen) and cast over it.

How it shares the one radio (Intel: one AP / P2P-GO / P2P-client at a time, on the Wi-Fi's channel):
- **Route `hotspot`:** a second interface (`casthub-ap`) in AP mode on the channel our Wi-Fi is
  connected on, so the radio never splits its time between two channels. wpa_supplicant runs it
  (no hostapd); `p2p_disabled` keeps it out of Wi-Fi Direct. If the Wi-Fi roams to another
  channel, the hotspot follows (phones rejoin by themselves: same name and password).
- **Route `hotspot-alone`:** the Wi-Fi's channel forbids an access point (radar/DFS channels,
  6 GHz) or the hotspot failed to start there: we leave that network and run the hotspot alone on
  a channel that allows it. Leaving is undone when the hotspot stops.
- **Route `network`:** no hotspot (mode `off`, or mode `auto` saw other devices on the network).
- A Wi-Fi Direct cast needs the radio's one group slot: the hotspot pauses for it and comes back
  after (pause/resume, counted, so a new Wi-Fi Direct address during the cast nests).

Mode `auto` guesses whether the network keeps devices apart: we listen for mDNS from other
devices for a few seconds (after asking once). Any device other than ourselves and the router
means device-to-device traffic gets through. It is a guess: a quiet network looks "blocked" and
then just gets a hotspot it didn't need.

The password is new each time the hotspot starts and lives only in memory and in
/run/casthub/hotspot-qr.txt (tmpfs), which the idle screen turns into the join QR code (SPEC D13).
Internet sharing (`share_internet`) is off by default: the hotspot is only for casting.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
import socket
import struct
import time
from pathlib import Path
from typing import TYPE_CHECKING

from dbus_fast import Variant

from casthub import naming
from casthub.discovery import dhcp, p2p

if TYPE_CHECKING:
    from casthub.discovery.p2pd import P2PDaemon

log = logging.getLogger("casthub.p2p")

AP_IF = "casthub-ap"
QR_FILE = Path("/run/casthub/hotspot-qr.txt")
# wpa_supplicant (SELinux NetworkManager_t) can read NM's run directory (see p2pd.SUPPLICANT_CONF).
AP_CONF = Path("/run/NetworkManager/casthub-ap-supplicant.conf")
ZONE = "casthub-hotspot"            # firewalld zone (system/firewalld/casthub-hotspot.xml)
POLICY = "casthub-share"            # firewalld policy: NAT out, only with share_internet
WPAS = "fi.w1.wpa_supplicant1"
ROOT = "/fi/w1/wpa_supplicant1"
IFACE = f"{WPAS}.Interface"
ALONE_FREQS = (5745, 5180, 2437)    # hotspot alone: the first of these the radio may transmit on
START_TIMEOUT_S = 10                # wait this long for the access point to come up
WATCH_S = 5
LOST_STATION_S = 20                 # Wi-Fi gone this long: let it reconnect, then follow it
REJOIN_S = 30
PEER_LISTEN_S = 6
MDNS = ("224.0.0.251", 5353)
PASS_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no i, l, o, 0, 1: easy to type off a screen

ROUTE_NETWORK, ROUTE_HOTSPOT, ROUTE_ALONE = "network", "hotspot", "hotspot-alone"


# ---- pure helpers (tested) ------------------------------------------------------------
def new_ssid(rand: random.Random | None = None) -> str:
    r = rand or random.SystemRandom()
    return "Cast Hub " + "".join(r.choice(naming.ALPHABET) for _ in range(4))


def new_passphrase(rand: random.Random | None = None, length: int = 12) -> str:
    r = rand or random.SystemRandom()
    return "".join(r.choice(PASS_ALPHABET) for _ in range(length))


def random_mac(rand: random.Random | None = None) -> str:
    """A random locally administered unicast address (the AP interface needs its own)."""
    r = rand or random.SystemRandom()
    b = [r.randrange(256) for _ in range(6)]
    b[0] = (b[0] & 0xFC) | 0x02
    return ":".join(f"{x:02x}" for x in b)


def _qr_escape(v: str) -> str:
    for c in "\\;,:\"":
        v = v.replace(c, "\\" + c)
    return v


def qr_payload(ssid: str, psk: str) -> str:
    """The Wi-Fi join code phones read from a QR code (iOS camera, Android)."""
    return f"WIFI:T:WPA;S:{_qr_escape(ssid)};P:{_qr_escape(psk)};;"


def channel_flags(iw_channels: str) -> dict[int, set[str]]:
    """`iw phy <phy> channels` → {MHz: {"no ir", "radar detection", "disabled", ...}}."""
    out: dict[int, set[str]] = {}
    cur: set[str] | None = None
    for raw in iw_channels.splitlines():
        line = raw.strip()
        if line.startswith("* ") and " MHz" in line:
            try:
                freq = int(float(line[2:].split(" MHz")[0]))
            except ValueError:
                cur = None
                continue
            cur = out.setdefault(freq, set())
            if "(disabled)" in line:
                cur.add("disabled")
        elif cur is not None and line and ":" not in line:
            cur.add(line.lower())                      # "No IR", "Radar detection"
    return out


def ap_allowed(flags: dict[int, set[str]], freq: int, beside_station: bool) -> bool:
    """May we run an access point on `freq`? Radar (DFS) channels never: the driver can't watch for
    radar. 6 GHz never (WPA3 only, and mostly no-IR). "No IR" only next to our own Wi-Fi
    connection on that channel (the driver allows it then on IR-concurrent channels; if it
    refuses, the start fails and we fall back to the hotspot alone)."""
    f = flags.get(freq)
    if f is None or freq >= 5925 or "disabled" in f or "radar detection" in f:
        return False
    return beside_station or "no ir" not in f


def pick_alone(flags: dict[int, set[str]]) -> int:
    return next((f for f in ALONE_FREQS if ap_allowed(flags, f, beside_station=False)), 0)


def mdns_query() -> bytes:
    """One mDNS question: PTR _services._dns-sd._udp.local (every advertiser answers)."""
    name = b"".join(bytes([len(p)]) + p for p in (b"_services", b"_dns-sd", b"_udp", b"local")) + b"\0"
    return struct.pack("!6H", 0, 0, 1, 0, 0, 0) + name + struct.pack("!2H", 12, 1)


async def network_has_peers(ifname: str, ignore: set[str], seconds: float = PEER_LISTEN_S) -> bool | None:
    """True if another device's mDNS reaches us on `ifname` (anything not from `ignore`: our own
    addresses and the router). None if we couldn't listen."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, ifname.encode())
        sock.bind(("", MDNS[1]))
        mreqn = struct.pack("4s4si", socket.inet_aton(MDNS[0]), b"\0" * 4, socket.if_nametoindex(ifname))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreqn)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, mreqn)
        sock.setblocking(False)
    except OSError as e:
        log.warning("hotspot: can't listen for other devices (%s)", e)
        return None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    try:
        with contextlib.suppress(OSError):
            await loop.sock_sendto(sock, mdns_query(), MDNS)
        while (left := deadline - loop.time()) > 0:
            try:
                _, (src, _port) = await asyncio.wait_for(loop.sock_recvfrom(sock, 9000), left)
            except TimeoutError:
                break
            if src not in ignore:
                return True
        return False
    finally:
        sock.close()


# ---- the hotspot ----------------------------------------------------------------------
class Hotspot:
    def __init__(self, d: P2PDaemon) -> None:
        self.d = d
        self.cfg = d.cfg
        self.route = ""               # decided once per hub session: network | hotspot | hotspot-alone
        self.up = False
        self.holds = 0                # pause() calls not yet resumed
        self.ssid = self.psk = ""
        self.freq = 0
        self.path = ""                # wpa_supplicant object of casthub-ap
        self.addr = ""
        self.server: dhcp.Server | None = None
        self.left_wifi = False        # we disconnected the Wi-Fi (route hotspot-alone)
        self.share_zone = ""          # egress zone added to the share policy
        self.saved_forwarding: str | None = None
        self.lock = asyncio.Lock()
        self.watch_task: asyncio.Task | None = None

    @property
    def wifi(self) -> str:
        from casthub.discovery.p2pd import wifi_interface
        return wifi_interface(self.cfg.miracast_interface)

    async def cleanup(self) -> None:
        """At daemon start: remove an access point a previous run left behind."""
        with contextlib.suppress(RuntimeError):
            (path,) = await self.d.call(ROOT, WPAS, "GetInterface", "s", [AP_IF])
            await self.d.call(ROOT, WPAS, "RemoveInterface", "o", [path])
        if (Path("/sys/class/net") / AP_IF).exists():
            await self.d.run("iw", "dev", AP_IF, "del")
        QR_FILE.unlink(missing_ok=True)

    # -- hub session --------------------------------------------------------------------
    async def start(self) -> None:
        """The hub is on screen: decide the route (once) and bring the hotspot up if it needs one."""
        async with self.lock:
            if self.route:                     # decided already: tell this hub too
                return self._decided(self.route, "")
            mode = self.cfg.hotspot_mode
            if mode == "off":
                return self._decided(ROUTE_NETWORK, "hotspot off (casthub.toml)")
            station = await self.d.station_frequency()
            if mode == "auto" and station:
                ignore = set(await self.d.wifi_addresses()) | set(await self._gateway())
                seen = await network_has_peers(self.wifi, ignore)
                if seen:
                    return self._decided(ROUTE_NETWORK, "other devices answer on this network; no hotspot")
                log.info("hotspot: %s", "no other devices heard on this network" if seen is False
                         else "couldn't check the network")
            self.ssid = self.cfg.hotspot_ssid or new_ssid()
            self.psk = new_passphrase()
            await self._bring_up()
            if self.route in (ROUTE_HOTSPOT, ROUTE_ALONE):
                self.watch_task = asyncio.create_task(self._watch())

    async def stop(self) -> None:
        """The hub is gone (or the daemon stops): hotspot down, our Wi-Fi back."""
        if self.watch_task:
            self.watch_task.cancel()
            self.watch_task = None
        async with self.lock:
            await self._down()
            self.route, self.holds = "", 0
            await self._rejoin_wifi()

    async def pause(self, why: str) -> None:
        async with self.lock:
            self.holds += 1
            if self.up:
                log.info("hotspot paused for %s", why)
                await self._down()

    async def resume(self) -> None:
        async with self.lock:
            self.holds = max(0, self.holds - 1)
            if not self.holds and not self.up and self.route in (ROUTE_HOTSPOT, ROUTE_ALONE):
                log.info("hotspot back")
                await self._bring_up()

    def _decided(self, route: str, why: str) -> None:
        if route != self.route:
            log.info("route: %s (%s)", route, why)
        self.route = route
        self.d.broadcast({"event": "route", "route": route, "ssid": self.ssid if route != ROUTE_NETWORK else ""})

    # -- bringing it up -----------------------------------------------------------------
    async def _bring_up(self) -> None:
        if self.holds:
            return                     # resume() brings it up
        flags = await self._channel_flags()
        station = await self.d.station_frequency()
        if station and ap_allowed(flags, station, beside_station=True):
            if await self._up(station):
                return self._decided(ROUTE_HOTSPOT, f"hotspot beside our Wi-Fi on {station} MHz")
            log.warning("hotspot: no access point beside our Wi-Fi on %d MHz; running it alone", station)
        elif station:
            log.info("hotspot: our Wi-Fi's channel (%d MHz) doesn't allow an access point; running it alone",
                     station)
        alone = pick_alone(flags)
        if not alone:
            log.error("hotspot: no channel allows an access point")
            await self._rejoin_wifi()
            return self._decided(ROUTE_NETWORK, "hotspot impossible")
        if station:
            await self._leave_wifi()
        if await self._up(alone):
            return self._decided(ROUTE_ALONE, f"hotspot alone on {alone} MHz")
        await self._rejoin_wifi()
        self._decided(ROUTE_NETWORK, "hotspot failed to start")

    async def _up(self, freq: int) -> bool:
        await self._remove_interface()
        wifi = self.wifi
        rc, out = await self.d.run("iw", "dev", wifi, "interface", "add", AP_IF, "type", "__ap")
        if rc:
            log.error("hotspot: can't add %s: %s", AP_IF, out.strip()[:200])
            return False
        await self.d.run("ip", "link", "set", AP_IF, "address", random_mac())
        fd = os.open(AP_CONF, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("p2p_disabled=1\n")             # Wi-Fi Direct stays on the Wi-Fi interface
        try:
            (self.path,) = await self.d.call(ROOT, WPAS, "CreateInterface", "a{sv}", [{
                "Ifname": Variant("s", AP_IF), "Driver": Variant("s", "nl80211"),
                "ConfigFile": Variant("s", str(AP_CONF))}])
            net = {"ssid": Variant("ay", self.ssid.encode()), "psk": Variant("s", self.psk),
                   "key_mgmt": Variant("s", "WPA-PSK"), "proto": Variant("s", "RSN"),
                   "pairwise": Variant("s", "CCMP"), "group": Variant("s", "CCMP"),
                   "mode": Variant("i", 2), "frequency": Variant("i", freq)}
            (netpath,) = await self.d.call(self.path, IFACE, "AddNetwork", "a{sv}", [net])
            await self.d.call(self.path, IFACE, "SelectNetwork", "o", [netpath])
        except RuntimeError as e:
            log.error("hotspot: wpa_supplicant: %s", e)
            await self._remove_interface()
            return False
        if not await self._wait_running():
            log.error("hotspot: the access point didn't start on %d MHz within %d s", freq, START_TIMEOUT_S)
            await self._remove_interface()
            return False
        in_use = list(self.d.mice_addresses)
        with contextlib.suppress(Exception):
            in_use += await self.d.wifi_addresses()
        net4 = p2p.group_network(in_use)
        self.addr = str(net4[1])
        await self.d.run("ip", "addr", "add", f"{self.addr}/{net4.prefixlen}", "dev", AP_IF)
        await self.d.run("ip", "link", "set", AP_IF, "up")
        rc, out = await self.d.run("firewall-cmd", f"--zone={ZONE}", f"--change-interface={AP_IF}")
        if rc:
            log.warning("hotspot firewall zone: %s", out.strip()[:200])
        router, dns = "", ()
        if self.cfg.hotspot_share and await self._share(True):
            router, dns = self.addr, tuple(await self._dns())
        self.server = dhcp.Server(AP_IF, dhcp.Pool(str(net4), self.addr, first=10, last=250, router=router, dns=dns),
                                  lambda ip, _mac, _host: None, label="the hotspot")
        try:
            await self.server.start()
        except OSError as e:
            log.error("DHCP for the hotspot: %s", e)
        self._write_qr()
        self.freq, self.up = freq, True
        log.info("hotspot up on %d MHz (%s internet)", freq, "shares" if router else "no")
        return True

    async def _wait_running(self) -> bool:
        for _ in range(START_TIMEOUT_S * 4):
            with contextlib.suppress(RuntimeError):
                if str(await self.d.get(self.path, IFACE, "State")) == "completed":
                    return True
            await asyncio.sleep(0.25)
        return False

    async def _down(self) -> None:
        if self.server:
            self.server.stop()
            self.server = None
        QR_FILE.unlink(missing_ok=True)
        if self.share_zone or self.saved_forwarding is not None:
            await self._share(False)
        await self._remove_interface()
        self.up, self.freq = False, 0

    async def _remove_interface(self) -> None:
        if not self.path:
            with contextlib.suppress(RuntimeError):
                (self.path,) = await self.d.call(ROOT, WPAS, "GetInterface", "s", [AP_IF])
        if self.path:
            with contextlib.suppress(RuntimeError):
                await self.d.call(ROOT, WPAS, "RemoveInterface", "o", [self.path])
            self.path = ""
        if (Path("/sys/class/net") / AP_IF).exists():
            await self.d.run("iw", "dev", AP_IF, "del")

    def _write_qr(self) -> None:
        tmp = QR_FILE.with_suffix(".tmp")
        with contextlib.suppress(OSError):
            tmp.write_text(qr_payload(self.ssid, self.psk) + "\n")
            os.chmod(tmp, 0o644)             # the idle screen (casthub user) shows it
            tmp.replace(QR_FILE)

    # -- the Wi-Fi beside it ------------------------------------------------------------
    async def _leave_wifi(self) -> None:
        log.info("hotspot: leaving our Wi-Fi network so the hotspot can run alone")
        rc, _ = await self.d.nmcli("device", "disconnect", self.wifi)
        self.left_wifi = rc == 0

    async def _rejoin_wifi(self) -> None:
        if self.left_wifi:
            self.left_wifi = False
            log.info("hotspot: reconnecting our Wi-Fi")
            await self.d.nmcli("device", "connect", self.wifi, timeout=30)

    async def _watch(self) -> None:
        """Follow our Wi-Fi: another channel (roaming), or gone for a while (let it reconnect)."""
        lost = 0.0
        while True:
            await asyncio.sleep(WATCH_S)
            if self.route != ROUTE_HOTSPOT or not self.up or self.lock.locked():
                lost = 0
                continue
            freq = await self.d.station_frequency()
            if freq == self.freq:
                lost = 0
                continue
            if freq:
                async with self.lock:
                    log.info("hotspot: our Wi-Fi moved to %d MHz; following it", freq)
                    await self._down()
                    await self._bring_up()
                continue
            lost += WATCH_S
            if lost < LOST_STATION_S:
                continue
            async with self.lock:
                log.info("hotspot: our Wi-Fi is gone; pausing the hotspot so it can reconnect")
                await self._down()
                end = time.monotonic() + REJOIN_S
                while time.monotonic() < end and not await self.d.station_frequency():
                    await asyncio.sleep(1)
                if not self.holds:
                    await self._bring_up()
            lost = 0

    async def _channel_flags(self) -> dict[int, set[str]]:
        try:
            phy = (Path("/sys/class/net") / self.wifi / "phy80211" / "name").read_text().strip()
        except OSError:
            return {}
        _, out = await self.d.run("iw", "phy", phy, "channels")
        return channel_flags(out)

    async def _gateway(self) -> list[str]:
        _, out = await self.d.nmcli("-t", "-f", "IP4.GATEWAY", "device", "show", self.wifi)
        return [v for line in out.splitlines() if (v := line.partition(":")[2].strip())]

    async def _dns(self) -> list[str]:
        _, out = await self.d.nmcli("-t", "-f", "IP4.DNS", "device", "show", self.wifi)
        return [v for line in out.splitlines() if (v := line.partition(":")[2].strip())]

    async def _share(self, on: bool) -> bool:
        """share_internet: NAT from the hotspot out through our Wi-Fi's firewall zone."""
        wifi = self.wifi
        if on:
            rc, zone = await self.d.run("firewall-cmd", f"--get-zone-of-interface={wifi}")
            if rc:
                rc, zone = await self.d.run("firewall-cmd", "--get-default-zone")
            zone = zone.strip()
            rc, out = await self.d.run("firewall-cmd", f"--policy={POLICY}", f"--add-egress-zone={zone}")
            if rc:
                log.warning("hotspot: can't share internet: %s", out.strip()[:200])
                return False
            self.share_zone = zone
            fwd = Path(f"/proc/sys/net/ipv4/conf/{wifi}/forwarding")
            with contextlib.suppress(OSError):
                self.saved_forwarding = fwd.read_text().strip()
                fwd.write_text("1")
                Path(f"/proc/sys/net/ipv4/conf/{AP_IF}/forwarding").write_text("1")
            return True
        if self.share_zone:
            await self.d.run("firewall-cmd", f"--policy={POLICY}", f"--remove-egress-zone={self.share_zone}")
            self.share_zone = ""
        if self.saved_forwarding is not None:
            with contextlib.suppress(OSError):
                Path(f"/proc/sys/net/ipv4/conf/{wifi}/forwarding").write_text(self.saved_forwarding)
            self.saved_forwarding = None
        return False
