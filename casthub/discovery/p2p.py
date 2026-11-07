"""Wi-Fi Direct: make the receiver discoverable (both transports) and accept Wi-Fi Direct casts.

Windows finds Miracast receivers by Wi-Fi P2P discovery, also for Miracast over Infrastructure:
[MS-MICE] 3 says the source reads a WSC Vendor Extension attribute from our Probe Responses,
checks the "supports infrastructure" bit and resolves the host name in it (Avahi answers that).
Without the bit, or off-network, it falls back to Wi-Fi Direct: it forms a P2P group with us
and we connect to its RTSP port over that link.

This runs as root (`casthub-p2p.service`): wpa_supplicant's D-Bus API is root-only. It talks to
the hub over /run/casthub/p2p.sock (JSON lines, group `casthub`):
    hub → p2p   {"cmd": "advertise", "name": "<receiver name>", "port": 7250}
    p2p → hub   {"event": "source", "addr": "192.0.2.10", "rtsp_port": 7236, "name": "<peer>"}
    p2p → hub   {"event": "gone", "addr": "192.0.2.10"}
    p2p → hub   {"event": "pin", "pin": "12345670"}     show it until {"event": "pin", "pin": ""}
"""

from __future__ import annotations

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


def wfd_subelements(rtsp_port: int = 7236) -> bytes:
    """WFD Device Information subelement, as wpa_supplicant's WFDIEs property expects (id, len, data)."""
    info = struct.pack(">HHH", WFD_PRIMARY_SINK_AVAILABLE, rtsp_port, WFD_MAX_THROUGHPUT_MBPS)
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


def dhcp_range(address: str) -> tuple[str, str, str, str]:
    """`<ip>/<prefix>` of our GO interface → (our ip, first lease, last lease, netmask)."""
    import ipaddress
    iface = ipaddress.ip_interface(address)
    if iface.version != 4 or iface.network.prefixlen > 29:
        raise ValueError(f"p2p_address needs an IPv4 /29 or larger: {address!r}")
    hosts = [h for h in iface.network.hosts() if h != iface.ip]
    return str(iface.ip), str(hosts[0]), str(hosts[-1]), str(iface.network.netmask)
