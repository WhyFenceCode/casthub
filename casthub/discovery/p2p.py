"""Wi-Fi Direct: make the receiver discoverable (both transports) and accept Wi-Fi Direct casts.

Windows finds Miracast receivers by Wi-Fi P2P discovery, also for Miracast over Infrastructure:
[MS-MICE] 3 says the source reads a WSC Vendor Extension attribute from our Probe Responses,
checks the "supports infrastructure" bit and resolves the host name in it (Avahi answers that).
Without the bit, or off-network, it falls back to Wi-Fi Direct: it forms a P2P group with us
and we connect to its RTSP port over that link.

This runs as root (`casthub-p2p.service`): wpa_supplicant's D-Bus API is root-only. It talks to
the hub over /run/casthub/p2p.sock (JSON lines, group `casthub`):
    hub → p2p   {"cmd": "advertise", "name": "<receiver name>", "port": 7250, "new_address": true}
                new_address: give the radio a new random Wi-Fi Direct address first (no screen
                is casting); see p2pd.P2PDaemon.new_address
    p2p → hub   {"event": "advertised", "name": "<receiver name>"}   PCs can find it now
    p2p → hub   {"event": "source", "addr": "192.0.2.10", "rtsp_port": 7236, "name": "<peer>"}
    p2p → hub   {"event": "gone", "addr": "192.0.2.10"}
    p2p → hub   {"event": "pin", "pin": "12345670"}     show it until {"event": "pin", "pin": ""}
                (not sent while Wi-Fi Direct pairs by push-button; see p2pd)
"""

from __future__ import annotations

import ipaddress
import os
import random
import struct

# ---- information elements (pure, unit-tested) ----------------------------------------------
WFD_OUI = bytes.fromhex("506f9a0a")         # Wi-Fi Alliance, OUI type 0x0a (WFD)
WSC_OUI = bytes.fromhex("0050f204")         # Microsoft, OUI type 0x04 (WSC)
MS_VENDOR_OUI = bytes.fromhex("000137")     # [MS-MICE] 2.2.8
WSC_VENDOR_EXT = 0x1049
ATTR_CAPABILITY, ATTR_HOSTNAME, ATTR_IP = 0x2001, 0x2002, 0x2005

# WPS Device Password IDs, as the *sender* of a GO Negotiation Request uses them.
DEV_PW_DEFAULT, DEV_PW_USER_SPECIFIED, DEV_PW_PUSHBUTTON, DEV_PW_REGISTRAR_SPECIFIED = 0, 1, 4, 5

# wpa_supplicant VENDOR_ELEM frame IDs (src/common/ieee802_11_defs.h, enum wfd... VENDOR_ELEM_*)
VENDOR_ELEM_PROBE_RESP_P2P = 1
VENDOR_ELEM_BEACON_P2P_GO = 3
VENDOR_ELEM_PROBE_RESP_P2P_GO = 2

# WFD device info bitmap: device type 01 = primary sink, bits 4-5 = 01 session available.
WFD_PRIMARY_SINK_AVAILABLE = 0x0011
WFD_MAX_THROUGHPUT_MBPS = 200
# WSC primary device type: category 7 (displays), OUI 0050F204, sub-category 1 (television).
PRIMARY_DEVICE_TYPE = bytes.fromhex("00070050f2040001")


def wfd_subelements(rtsp_port: int = 7236, available: bool = True) -> bytes:
    """WFD Device Information subelement, as wpa_supplicant's WFDIEs property expects (id, len, data)."""
    flags = WFD_PRIMARY_SINK_AVAILABLE if available else WFD_PRIMARY_SINK_AVAILABLE & ~0x0030
    info = struct.pack(">HHH", flags, rtsp_port, WFD_MAX_THROUGHPUT_MBPS)
    return struct.pack(">BH", 0, len(info)) + info


def mice_capability(stream_encryption: bool = False, pin: bool = False, version: int = 1) -> int:
    """[MS-MICE] section "Capability Attribute", MSB first: X(2) D PinSupported(1) C Version(3) B Encryption(1) A Infrastructure(1)."""
    return (int(pin and stream_encryption) << 5) | (version & 7) << 2 | int(stream_encryption) << 1 | 1


def _attr(attr_id: int, value: bytes) -> bytes:
    return struct.pack(">HH", attr_id, len(value)) + value


def mice_vendor_payload(hostname: str, addresses: list[str] = ()) -> bytes:
    """Value of the WSC Vendor Extension attribute: Microsoft OUI + the MS-MICE attributes.

    This is what wpa_supplicant's P2PDeviceConfig.VendorExtension takes: it writes the 0x1049
    header itself and puts the attribute inside the WSC IE of its P2P Probe Responses.
    """
    if "." in hostname or not hostname.isascii() or not hostname:
        raise ValueError("MS-MICE host name must be a non-empty, unqualified ASCII name")
    body = MS_VENDOR_OUI + _attr(ATTR_CAPABILITY, bytes([mice_capability()])) + _attr(ATTR_HOSTNAME, hostname.encode())
    for a in addresses:
        body += _attr(ATTR_IP, a.encode())
    return body


def mice_vendor_extension(hostname: str, addresses: list[str] = ()) -> bytes:
    """The whole WSC Vendor Extension attribute (0x1049 header + payload)."""
    body = mice_vendor_payload(hostname, addresses)
    return struct.pack(">HH", WSC_VENDOR_EXT, len(body)) + body


def wps_pin() -> str:
    """A random 8-digit WPS PIN with a valid check digit (WSC 2.0 section 7.4.1)."""
    import secrets
    n = secrets.randbelow(10 ** 7)
    acc, d = 0, n
    for w in (3, 1, 3, 1, 3, 1, 3):
        acc += w * (d % 10)
        d //= 10
    return f"{n:07d}{(10 - acc % 10) % 10}"


def wps_pin_valid(pin: str) -> bool:
    if len(pin) != 8 or not pin.isdigit():
        return False
    return sum(int(c) * w for c, w in zip(pin, (3, 1, 3, 1, 3, 1, 3, 1), strict=True)) % 10 == 0


def wsc_ie(attributes: bytes) -> bytes:
    """A vendor-specific IE (0xdd) of type WSC. Receivers concatenate all WSC IEs in a frame."""
    data = WSC_OUI + attributes
    if len(data) > 255:
        raise ValueError("WSC IE too long")
    return bytes([0xDD, len(data)]) + data


def parse_wfd_rtsp_port(ies: bytes) -> int | None:
    """RTSP port from a peer's WFD subelements (Device Information), or None."""
    i = 0
    while i + 3 <= len(ies):
        sid, ln = ies[i], struct.unpack_from(">H", ies, i + 1)[0]
        val = ies[i + 3:i + 3 + ln]
        if sid == 0 and ln >= 4:
            return struct.unpack_from(">H", val, 2)[0]
        i += 3 + ln
    return None


# ---- NetworkManager runs the group (pure, unit-tested) ---------------------------------------
# NetworkManager drops any P2P group it did not start itself (it wraps the new interface,
# ignores it and asks wpa_supplicant to remove it). So the daemon asks NM to connect to the
# peer; NM 1.58 can only do that with push-button pairing.
PUSH_BUTTON_ONLY = "virtual_push_button"      # WPS config methods: Windows then pairs without a PIN


def mac(addr: bytes) -> str:
    return ":".join(f"{b:02x}" for b in addr)




def ipv4(raw: bytes) -> str | None:
    """A 4-byte address from wpa_supplicant (network order); None when missing or 0.0.0.0."""
    return ".".join(map(str, raw)) if len(raw) == 4 and any(raw) else None






# ---- the Wi-Fi Direct group's own network (we are the group owner) ---------------------------
PRIVATE_10 = ipaddress.IPv4Network((10 << 24, 8))        # 10/8 (RFC 1918)


def group_network(in_use: list[str], rand=random.randrange) -> ipaddress.IPv4Network:
    """A random /24 in 10/8 for the group that none of our current addresses fall in (a school
    network may use any private range). Our side is its .1."""
    taken = [ipaddress.IPv4Address(a) for a in in_use if a]
    for _ in range(100):
        net = ipaddress.IPv4Network((int(PRIVATE_10.network_address) | rand(1, 1 << 16) << 8, 24))
        if not any(a in net for a in taken):
            return net
    raise RuntimeError("no free /24 for the Wi-Fi Direct group")


# ---- a new Wi-Fi Direct address per session ------------------------------------------------
def random_address() -> bytes:
    """A random locally administered unicast MAC (first byte: bit 1 set, bit 0 clear)."""
    b = bytearray(os.urandom(6))
    b[0] = (b[0] & 0xFC) | 0x02
    return bytes(b)


def supplicant_conf(address: bytes) -> str:
    """wpa_supplicant config for the Wi-Fi interface: the P2P device is created with `address`
    (mode 2 = this "persistent" address), and each group interface gets a random one too.
    update_config=0: nothing is written back (no saved groups, which Windows' invitations need)."""
    return ("# written by casthub-p2p; a new address after every cast\n"
            "update_config=0\n"
            "p2p_device_random_mac_addr=2\n"
            f"p2p_device_persistent_mac_addr={mac(address)}\n"
            "p2p_interface_random_mac_addr=1\n")


def p2p_device_address(iw_dev: str) -> str:
    """The P2P device's address in `iw dev` output (it has no network interface), or ""."""
    addr = ""
    for line in iw_dev.splitlines():
        line = line.strip()
        if line.startswith(("Interface ", "Unnamed")):
            addr = ""
        elif line.startswith("addr "):
            addr = line[5:].strip().lower()
        elif line == "type P2P-device":
            return addr
    return ""


def ipv4_addresses(show: str) -> list[str]:
    """IPv4 addresses from `nmcli -t -f IP4.ADDRESS device show <dev>` ("IP4.ADDRESS[1]:a.b.c.d/24")."""
    out = []
    for line in show.splitlines():
        key, _, val = line.partition(":")
        if key.startswith("IP4.ADDRESS") and val:
            out.append(val.split("/")[0])
    return out
