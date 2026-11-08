"""Wi-Fi Display parameters: the sink's capabilities (M3 reply) and the source's choice (M4)."""

from __future__ import annotations

from dataclasses import dataclass

# Resolution tables, bit index → (width, height, fps, interlaced). WFD Technical Spec 1.x, tables 34–36.
CEA = [(640, 480, 60, False), (720, 480, 60, False), (720, 480, 60, True), (720, 576, 50, False),
       (720, 576, 50, True), (1280, 720, 30, False), (1280, 720, 60, False), (1920, 1080, 30, False),
       (1920, 1080, 60, False), (1920, 1080, 60, True), (1280, 720, 25, False), (1280, 720, 50, False),
       (1920, 1080, 25, False), (1920, 1080, 50, False), (1920, 1080, 50, True), (1280, 720, 24, False),
       (1920, 1080, 24, False)]
VESA = [(800, 600, 30, False), (800, 600, 60, False), (1024, 768, 30, False), (1024, 768, 60, False),
        (1152, 864, 30, False), (1152, 864, 60, False), (1280, 768, 30, False), (1280, 768, 60, False),
        (1280, 800, 30, False), (1280, 800, 60, False), (1360, 768, 30, False), (1360, 768, 60, False),
        (1366, 768, 30, False), (1366, 768, 60, False), (1280, 1024, 30, False), (1280, 1024, 60, False),
        (1400, 1050, 30, False), (1400, 1050, 60, False), (1440, 900, 30, False), (1440, 900, 60, False),
        (1600, 900, 30, False), (1600, 900, 60, False), (1600, 1200, 30, False), (1600, 1200, 60, False),
        (1680, 1024, 30, False), (1680, 1024, 60, False), (1680, 1050, 30, False), (1680, 1050, 60, False),
        (1920, 1200, 30, False), (1920, 1200, 60, False)]
HH = [(800, 480, 30, False), (800, 480, 60, False), (854, 480, 30, False), (854, 480, 60, False),
      (864, 480, 30, False), (864, 480, 60, False), (640, 360, 30, False), (640, 360, 60, False),
      (960, 540, 30, False), (960, 540, 60, False), (848, 480, 30, False), (848, 480, 60, False)]
TABLES = {"CEA": CEA, "VESA": VESA, "HH": HH}
PROFILES = {0x01: "constrained-baseline", 0x02: "constrained-high"}
LEVEL_42 = 0x10


class WfdError(ValueError):
    pass


@dataclass(frozen=True)
class VideoFormat:
    width: int
    height: int
    fps: int
    profile: str
    table: str

    def __str__(self) -> str:
        return f"{self.width}x{self.height}p{self.fps} H.264 {self.profile}"


@dataclass(frozen=True)
class AudioFormat:
    codec: str          # AAC, LPCM, AC3
    rate: int
    channels: int


def _mask(table: list, width: int, height: int, max_fps: int = 60) -> int:
    m = 0
    for i, (w, h, fps, interlaced) in enumerate(table):
        if not interlaced and w <= width and h <= height and fps <= max_fps:
            m |= 1 << i
    return m


def _native(width: int, height: int) -> int:
    """Native resolution field: index << 3 | table (0 CEA, 1 VESA, 2 HH). Exact match at 60 fps, else 640x480."""
    for t, table in enumerate((CEA, VESA, HH)):
        for i, (w, h, fps, interlaced) in enumerate(table):
            if (w, h, fps, interlaced) == (width, height, 60, False):
                return i << 3 | t
    return 0


def sink_video_formats(width: int, height: int) -> str:
    """Our `wfd_video_formats` for a screen of width × height: every progressive mode that fits, CBP and CHP."""
    cea, vesa, hh = _mask(CEA, width, height), _mask(VESA, width, height), _mask(HH, width, height)
    # One H.264 entry, constrained high (a CHP decoder also plays CBP), as known-good
    # Windows sinks send it; a second codec entry is unusual.
    codec = f"02 {LEVEL_42:02x} {cea:08x} {vesa:08x} {hh:08x} 00 0000 0000 00 none none"
    return f"{_native(width, height):02x} 00 {codec}"


def parse_video_format(value: str) -> VideoFormat:
    """The source's choice in M4: exactly one bit set in one of the three masks."""
    parts = value.replace(",", " ").split()
    if len(parts) < 7:
        raise WfdError(f"short wfd_video_formats {value!r}")
    try:
        profile, cea, vesa, hh = int(parts[2], 16), int(parts[4], 16), int(parts[5], 16), int(parts[6], 16)
    except ValueError as e:
        raise WfdError(f"bad wfd_video_formats {value!r}") from e
    chosen = [(name, m) for name, m in (("CEA", cea), ("VESA", vesa), ("HH", hh)) if m]
    if len(chosen) != 1 or chosen[0][1] & (chosen[0][1] - 1):
        raise WfdError(f"wfd_video_formats does not pick one mode: {value!r}")
    name, m = chosen[0]
    idx = m.bit_length() - 1
    table = TABLES[name]
    if idx >= len(table):
        raise WfdError(f"unknown {name} mode bit {idx}")
    w, h, fps, _ = table[idx]
    return VideoFormat(w, h, fps, PROFILES.get(profile, f"profile-0x{profile:02x}"), name)


def sink_audio_codecs() -> str:
    return "AAC 00000001 00"        # AAC 48 kHz stereo (SPEC: H.264 + AAC)


def parse_audio_codecs(value: str) -> AudioFormat | None:
    if not value or value.strip() == "none":
        return None
    codec, modes = value.split()[:2]
    m = int(modes, 16)
    if codec == "LPCM":
        return AudioFormat("LPCM", 44100 if m & 1 else 48000, 2)
    if codec == "AAC":
        ch = {1: 2, 2: 4, 4: 6, 8: 8}.get(m, 2)
        return AudioFormat("AAC", 48000, ch)
    if codec == "AC3":
        return AudioFormat("AC3", 48000, 2)
    raise WfdError(f"unknown audio codec {codec!r}")


def client_rtp_ports(rtp_port: int) -> str:
    return f"RTP/AVP/UDP;unicast {rtp_port} 0 mode=play"


def presentation_url(value: str) -> str:
    """`wfd_presentation_URL: rtsp://<ip>/wfd1.0/streamid=0 none` → the first URL."""
    url = value.split()[0] if value else ""
    if not url.startswith("rtsp://"):
        raise WfdError(f"bad wfd_presentation_URL {value!r}")
    return url


def sink_parameters(width: int, height: int, rtp_port: int, name: str) -> dict[str, str]:
    """Answers for M3. Anything the source asks for that isn't here is left out of the reply."""
    return {
        "wfd_video_formats": sink_video_formats(width, height),
        "wfd_audio_codecs": sink_audio_codecs(),
        "wfd_client_rtp_ports": client_rtp_ports(rtp_port),
        "wfd_content_protection": "none",
        "wfd_coupled_sink": "none",
        "wfd_uibc_capability": "none",
        "wfd_standby_resume_capability": "none",
        "wfd_connector_type": "05",                 # HDMI, as known-good sinks report
        "wfd_3d_video_formats": "none",
        "wfd_idr_request_capability": "1",          # we send wfd_idr_request when the decoder needs a key frame
        "intel_friendly_name": name,
        "intel_sink_manufacturer_name": "Cast Hub",
        "intel_sink_model_name": "Cast Hub",
        "intel_sink_version": "1.0.0",
        "intel_sink_device_URL": "none",
    }
