"""casthub-hub: screens → receivers (SPEC component contract "hub").

    python3 -m casthub.hub [--config PATH] [--outputs eDP-1[,HDMI-A-1]] [--size 1920x1080]

In the Display session (Sway) it follows Sway's outputs. Elsewhere (e.g. testing from the
Workstation desktop) it uses --outputs/--size or the connected DRM connectors.
Layout `per_screen`: receiver n listens on miracast.base_port + n.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import signal
from pathlib import Path

from casthub import config as cfgmod
from casthub.discovery.mdns import Mdns
from casthub.hub.receiver import Receiver

log = logging.getLogger("casthub.hub")
P2P_SOCK = Path("/run/casthub/p2p.sock")


def drm_outputs() -> list[tuple[str, tuple[int, int]]]:
    """Connected DRM connectors with their preferred mode, e.g. ("eDP-1", (1920, 1080))."""
    out = []
    for c in sorted(Path("/sys/class/drm").glob("card*-*")):
        try:
            if (c / "status").read_text().strip() != "connected":
                continue
            mode = (c / "modes").read_text().split("\n")[0]
        except OSError:
            continue
        w, _, h = mode.partition("x")
        if w.isdigit() and h.rstrip("i").isdigit():
            out.append((c.name.split("-", 1)[1], (int(w), int(h.rstrip("i")))))
    return out


async def sway_outputs() -> list[tuple[str, tuple[int, int]]] | None:
    if not os.environ.get("SWAYSOCK"):
        return None
    from i3ipc.aio import Connection
    conn = await Connection().connect()
    outs = [(o.name, (o.current_mode.width, o.current_mode.height)) for o in await conn.get_outputs() if o.active]
    conn.main_quit()
    return outs


class UiLink:
    """Fire-and-forget JSON lines to the Display UI's control socket; silent when it isn't running."""

    def __init__(self) -> None:
        self.path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "casthub-ui.sock"

    async def send(self, msg: dict) -> None:
        try:
            r, w = await asyncio.wait_for(asyncio.open_unix_connection(str(self.path)), 1)
            w.write(json.dumps(msg).encode() + b"\n")
            await w.drain()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(r.readline(), 1)
            w.close()
        except (OSError, TimeoutError):
            # No Display UI (testing from the desktop): a PIN must still be seen.
            if msg.get("cmd") == "pin" and msg.get("pin"):
                pin = str(msg["pin"])
                with contextlib.suppress(OSError):
                    await asyncio.create_subprocess_exec(
                        "notify-send", "-a", "Cast Hub", "-u", "critical", "Type this PIN on your PC",
                        f"{pin[:4]} {pin[4:]}" if len(pin) == 8 else pin)


class Hub:
    def __init__(self, cfg: cfgmod.Config, outputs: list[tuple[str, tuple[int, int]]]) -> None:
        self.cfg, self.outputs = cfg, outputs
        self.ui = UiLink()
        self.mdns = Mdns()
        self.receivers: list[Receiver] = []
        self.p2p_task: asyncio.Task | None = None

    async def start(self) -> None:
        outs = self.outputs if self.cfg.layout == "per_screen" else self.outputs[:1]
        for i, (output, size) in enumerate(outs):
            r = Receiver(self.cfg.receiver_name(i, output), output, size, self.cfg.miracast_base_port + i,
                         self.cfg.rtp_base_port + 2 * i, self.cfg.busy_policy, self.ui.send)
            await r.start()
            self.receivers.append(r)
            try:
                await self.mdns.publish(r.name, r.mice_port)
            except Exception as e:  # Avahi down: casting by Wi-Fi discovery + host name still works
                log.warning("mdns: %s", e)
        if self.cfg.wifi_direct and self.receivers:
            # Keep a reference: asyncio holds tasks weakly, and an unreferenced one was collected.
            self.p2p_task = asyncio.create_task(self.p2p_link())

    async def p2p_link(self) -> None:
        """Talk to casthub-p2p (root). Wi-Fi Direct casts go to the first receiver."""
        first = self.receivers[0]
        while True:
            try:
                r, w = await asyncio.open_unix_connection(str(P2P_SOCK))
            except OSError:
                await asyncio.sleep(10)
                continue
            w.write(json.dumps({"cmd": "advertise", "name": first.name, "port": first.mice_port}).encode() + b"\n")
            while line := await r.readline():
                with contextlib.suppress(ValueError, KeyError):
                    ev = json.loads(line)
                    if ev["event"] == "source":
                        await first.on_p2p_source(ev["addr"], int(ev["rtsp_port"]), ev.get("name", ""))
                    elif ev["event"] == "gone":
                        first.on_p2p_gone(ev["addr"])
                    elif ev["event"] == "pin":
                        await first.show_pin(str(ev["pin"]))
            await asyncio.sleep(2)

    async def stop(self) -> None:
        for r in self.receivers:
            await r.stop()
        await self.mdns.close()


def parse_size(s: str) -> tuple[int, int]:
    w, _, h = s.lower().partition("x")
    return int(w), int(h)


async def amain(a: argparse.Namespace) -> int:
    try:
        cfg = cfgmod.load(a.config)
    except (cfgmod.ConfigError, PermissionError) as e:
        log.error("config: %s (using defaults)", e)
        cfg = cfgmod.Config()
    outputs = await sway_outputs()
    if outputs is None:
        found = dict(drm_outputs())
        names = a.outputs.split(",") if a.outputs else list(found) or [""]
        outputs = [(n, parse_size(a.size) if a.size else found.get(n, (1920, 1080))) for n in names]
    hub = Hub(cfg, outputs)
    await hub.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, stop.set)
    async def sway_gone():
        sock = os.environ.get("SWAYSOCK")
        while sock and os.path.exists(sock):
            await asyncio.sleep(5)
        if sock:
            log.info("Sway is gone; stopping")
            stop.set()
    watch = asyncio.create_task(sway_gone())  # noqa: F841 (kept for the loop's lifetime)
    await stop.wait()
    await hub.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="casthub-hub")
    ap.add_argument("--config", type=Path, default=cfgmod.DEFAULT_PATH)
    ap.add_argument("--outputs", default="", help="comma-separated output names (outside Sway)")
    ap.add_argument("--size", default="", help="WxH to advertise (outside Sway)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, format="casthub-hub: %(name)s: %(message)s")
    return asyncio.run(amain(a))


if __name__ == "__main__":
    raise SystemExit(main())
