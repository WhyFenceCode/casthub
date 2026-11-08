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
from casthub import naming
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


async def output_size(output: str) -> tuple[int, int] | None:
    """The output's current size under Sway (the Display panel's window can be resized)."""
    with contextlib.suppress(Exception):
        for name, size in await sway_outputs() or []:
            if name == output:
                return size
    return None


class UiLink:
    """JSON lines to the Display UI's control socket; silent when it isn't running.

    The UI may start after the hub, or restart, and would then show its own idea of the name:
    the last state of each screen is kept and sent again until the UI has it (keep_in_sync)."""

    def __init__(self) -> None:
        self.path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "casthub-ui.sock"
        self.states: dict[str, dict] = {}      # output → last "state" message
        self.synced = True
        self.ui_inode = 0                     # the UI socket we last delivered to (0: none)

    def socket_inode(self) -> int:
        try:
            return self.path.stat().st_ino
        except OSError:
            return 0

    async def keep_in_sync(self, every: float = 1.0) -> None:
        while True:
            await asyncio.sleep(every)
            inode = self.socket_inode()
            if inode and self.states and (not self.synced or inode != self.ui_inode):
                results = [await self.deliver(m) for m in list(self.states.values())]
                self.synced = all(results)

    async def deliver(self, msg: dict) -> bool:
        try:
            r, w = await asyncio.wait_for(asyncio.open_unix_connection(str(self.path)), 1)
            w.write(json.dumps(msg).encode() + b"\n")
            await w.drain()
            reply = b""
            with contextlib.suppress(Exception):
                reply = await asyncio.wait_for(r.readline(), 1)
            w.close()
        except (OSError, TimeoutError):
            return False
        self.ui_inode = self.socket_inode()
        try:                                  # e.g. {"ok": false} while the UI has no view for that screen yet
            return bool(json.loads(reply).get("ok", True)) if reply else True
        except ValueError:
            return True

    async def send(self, msg: dict) -> None:
        if msg.get("cmd") == "state" and msg.get("output") is not None:
            self.states[str(msg["output"])] = msg
        if await self.deliver(msg):
            return
        if msg.get("cmd") == "state":
            self.synced = False
        else:
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
        self.p2p_writer: asyncio.StreamWriter | None = None

    async def start(self) -> None:
        outs = self.outputs if self.cfg.layout == "per_screen" else self.outputs[:1]
        for i, (output, size) in enumerate(outs):
            r = Receiver(self.name_for(i, output), output, size, self.cfg.miracast_base_port + i,
                         self.cfg.rtp_base_port + 2 * i, self.cfg.busy_policy, self.ui.send, on_ended=self.rename,
                         size_now=output_size)
            await r.start()
            self.receivers.append(r)
            try:
                await self.mdns.publish(r.name, r.mice_port)
            except Exception as e:  # Avahi down: casting by Wi-Fi discovery + host name still works
                log.warning("mdns: %s", e)
        self.ui_task = asyncio.create_task(self.ui.keep_in_sync())
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
            self.p2p_writer = w
            await self.p2p_advertise(new_address=self.wants_new_address(first))
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

    def name_for(self, index: int, output: str) -> str:
        return self.cfg.pinned_name(output) or naming.session_name(index, naming.session_salt())

    async def rename(self, r: Receiver) -> None:
        """After a cast: a new name (and mDNS ID), so Windows lists a fresh display."""
        i = self.receivers.index(r)
        new = self.name_for(i, r.output)
        if new == r.name:
            return
        log.info("%s is now %s", r.name, new)
        r.name = new
        try:
            await self.mdns.publish(new, r.mice_port)
        except Exception as e:  # noqa: BLE001 (Avahi down: Wi-Fi discovery still works)
            log.warning("mdns: %s", e)
        if i == 0:
            await self.p2p_advertise(new_address=self.wants_new_address(r))

    def wants_new_address(self, first: Receiver) -> bool:
        """A new Wi-Fi Direct address with each new name (D26). It drops Wi-Fi for a few
        seconds, so not while another screen is casting, nor for a pinned name."""
        if self.cfg.pinned_name(first.output):
            return False
        return all(o.state == "idle" for o in self.receivers if o is not first)

    async def p2p_advertise(self, new_address: bool = False) -> None:
        w = self.p2p_writer
        if w is None or w.is_closing() or not self.receivers:
            return
        first = self.receivers[0]
        msg = {"cmd": "advertise", "name": first.name, "port": first.mice_port, "new_address": new_address}
        w.write(json.dumps(msg).encode() + b"\n")
        with contextlib.suppress(ConnectionError):
            await w.drain()

    async def end_all(self) -> None:
        """End every cast (SIGUSR1: casthub-leave, so the leave page isn't under a video)."""
        log.info("ending all casts")
        await asyncio.gather(*(r.end_current() for r in self.receivers), return_exceptions=True)

    async def stop(self) -> None:
        await self.end_all()          # tell each PC the cast is over (RTSP teardown)
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
    loop.add_signal_handler(signal.SIGUSR1, lambda: asyncio.ensure_future(hub.end_all()))
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
