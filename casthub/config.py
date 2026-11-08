"""Load and validate casthub.toml (format: casthub.example.toml).

The real file is /etc/casthub/casthub.toml (root:casthub, 0640) and is never committed.
A missing file gives the defaults, so Display mode still shows its idle screen.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("/etc/casthub/casthub.toml")
LAYOUTS = ("per_screen", "per_radio", "mirror_all")
THEMES = ("auto", "light", "dark")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Screen:
    output: str
    name: str


@dataclass(frozen=True)
class Config:
    name_prefix: str = "Cast Hub"
    layout: str = "per_screen"
    miracast_base_port: int = 7250
    miracast_interface: str = "wlan0"
    wifi_direct: bool = True
    p2p_address: str = ""               # unused since NetworkManager addresses Wi-Fi Direct groups; still validated
    rtp_base_port: int = 1028           # receiver n receives RTP on rtp_base_port + 2n
    busy_policy: str = "reject"
    airplay: bool = True
    hotspot: bool = False
    hotspot_interface: str = ""
    hotspot_ssid: str = ""
    pin_hash: str = ""
    theme: str = "auto"                 # Display screens: auto (by time of day) | light | dark
    location: str = ""                  # shown on the idle screen, e.g. "<city>, <region>"
    latitude: float | None = None
    longitude: float | None = None
    screens: tuple[Screen, ...] = field(default_factory=tuple)

    def receiver_name(self, index: int, output: str = "") -> str:
        """Name of the receiver on a screen: pinned name, else <prefix>-<n> (1-based)."""
        for s in self.screens:
            if s.output == output:
                return s.name
        return f"{self.name_prefix}-{index + 1}"

    def coordinates(self) -> str:
        """`44.23° N · 76.49° W` style, or "" if not set."""
        if self.latitude is None or self.longitude is None:
            return ""
        ns = "N" if self.latitude >= 0 else "S"
        ew = "E" if self.longitude >= 0 else "W"
        return f"{abs(self.latitude):.2f}° {ns} · {abs(self.longitude):.2f}° {ew}"


def _get(d: dict, key: str, typ: type | tuple, default):
    v = d.get(key, default)
    wrong_type = v is not default and not isinstance(v, typ)
    bool_as_number = isinstance(v, bool) and typ in (int, float, (int, float))
    if wrong_type or bool_as_number:
        raise ConfigError(f"{key}: expected {getattr(typ, '__name__', typ)}, got {v!r}")
    return v


def parse(data: dict) -> Config:
    brand, layout = data.get("brand", {}), data.get("layout", {})
    mc, ap, hs = data.get("miracast", {}), data.get("airplay", {}), data.get("hotspot", {})
    sw, disp = data.get("switcher", {}), data.get("display", {})
    d = Config()

    mode = _get(layout, "mode", str, d.layout)
    if mode not in LAYOUTS:
        raise ConfigError(f"layout.mode must be one of {LAYOUTS}, got {mode!r}")
    busy = _get(mc, "busy_policy", str, d.busy_policy)
    if busy not in ("reject", "replace"):
        raise ConfigError(f"miracast.busy_policy must be reject or replace, got {busy!r}")
    theme = _get(disp, "theme", str, d.theme)
    if theme not in THEMES:
        raise ConfigError(f"display.theme must be one of {THEMES}, got {theme!r}")
    port = _get(mc, "base_port", int, d.miracast_base_port)
    if not 1024 <= port <= 65000:
        raise ConfigError(f"miracast.base_port out of range: {port}")
    rtp_port = _get(mc, "rtp_base_port", int, d.rtp_base_port)
    if not 1024 <= rtp_port <= 65000:
        raise ConfigError(f"miracast.rtp_base_port out of range: {rtp_port}")
    p2p_address = _get(mc, "p2p_address", str, d.p2p_address)
    if p2p_address:
        import ipaddress
        try:
            ipaddress.IPv4Interface(p2p_address)
        except ValueError:
            raise ConfigError(f"miracast.p2p_address must be <ipv4>/<prefix>, got {p2p_address!r}") from None
        if "/" not in p2p_address:
            raise ConfigError("miracast.p2p_address needs a prefix length, e.g. /24")
    if _get(sw, "pin", str, ""):
        raise ConfigError("switcher.pin is plain text; set it with `casthub-pin` so only pin_hash is stored")
    lat = _get(disp, "latitude", (int, float), None)
    lon = _get(disp, "longitude", (int, float), None)
    if lat is not None and not -90 <= lat <= 90 or lon is not None and not -180 <= lon <= 180:
        raise ConfigError("display.latitude/longitude out of range")

    screens = []
    for i, s in enumerate(data.get("screen", [])):
        if not isinstance(s, dict) or not isinstance(s.get("output"), str) or not isinstance(s.get("name"), str):
            raise ConfigError(f"screen[{i}] needs string output and name")
        screens.append(Screen(s["output"], s["name"]))

    return Config(
        name_prefix=_get(brand, "name_prefix", str, d.name_prefix),
        layout=mode,
        miracast_base_port=port,
        miracast_interface=_get(mc, "interface", str, d.miracast_interface),
        wifi_direct=_get(mc, "wifi_direct", bool, d.wifi_direct),
        p2p_address=p2p_address,
        rtp_base_port=rtp_port,
        busy_policy=busy,
        airplay=_get(ap, "enabled", bool, d.airplay),
        hotspot=_get(hs, "enabled", bool, d.hotspot),
        hotspot_interface=_get(hs, "interface", str, d.hotspot_interface),
        hotspot_ssid=_get(hs, "ssid", str, d.hotspot_ssid),
        pin_hash=_get(sw, "pin_hash", str, d.pin_hash),
        theme=theme,
        location=_get(disp, "location", str, d.location),
        latitude=None if lat is None else float(lat),
        longitude=None if lon is None else float(lon),
        screens=tuple(screens),
    )


def load(path: Path = DEFAULT_PATH) -> Config:
    try:
        with open(path, "rb") as f:
            return parse(tomllib.load(f))
    except FileNotFoundError:
        return Config()
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
