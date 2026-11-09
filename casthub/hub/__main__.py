"""casthub-hub: screens → receivers (SPEC component contract "hub").

    python3 -m casthub.hub [--config PATH] [--outputs eDP-1[,HDMI-A-1]] [--size 1920x1080]

In the Display session (Sway) it follows Sway's outputs. Elsewhere (e.g. testing from the
Workstation desktop) it uses --outputs/--size or the connected DRM connectors.
Layout `mirror_all`: one receiver on miracast.base_port, shown on every screen (M4: Windows
lists one receiver per Wi-Fi radio, so one receiver per screen can't work).
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
from casthub.airplay.uxplay import UxPlay
from casthub.discovery.mdns import Mdns
from casthub.hub.passthrough import Passthrough
from casthub.hub.receiver import Receiver

log = logging.getLogger("casthub.hub")
P2P_SOCK = Path("/run/casthub/p2p.sock")
ROUTE_WAIT_S = 20            # after the name is confirmed: wait this long for the route (auto mode listens first)
ROUTES = ("network", "hotspot", "hotspot-alone")
P2P_CONFIRM_S = 45          # a new Wi-Fi Direct address re-adds Wi-Fi (up to ~30 s to reconnect)


def CONTROL_SOCK() -> Path:  # noqa: N802 (a path that follows the environment, like UiLink's)
    return Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "casthub-hub.sock"


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


def main_screen_first(o: tuple[str, tuple[int, int]]) -> tuple[bool, str]:
    """Sort key: the built-in panel is the main screen (its shape picks the video modes), then
    by name, so the main screen doesn't depend on the order Sway reports outputs in."""
    return (not o[0].startswith(("eDP", "LVDS", "DSI")), o[0])


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
        # Kept and sent again to a (re)started UI: each screen's state and TV, and passthrough.
        cmd = msg.get("cmd")
        if cmd == "state" and msg.get("output") is not None:
            self.states[str(msg["output"])] = msg
        elif cmd == "tv" and msg.get("output") is not None:
            self.states[f"tv:{msg['output']}"] = msg
        elif cmd == "passthrough":
            self.states["passthrough"] = msg
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
        self.p2p_fallback: asyncio.TimerHandle | None = None
        self.airplay: UxPlay | None = None
        self.p2p_busy = False                # told casthub-p2p we're casting (not discoverable)
        self.passthrough = Passthrough(self)
        self.control: asyncio.Server | None = None

    def wifi_direct_names(self, r: Receiver) -> bool:
        """Does this receiver's name also go out over Wi-Fi Direct (the first one does)?"""
        return self.cfg.wifi_direct and bool(self.receivers) and r is self.receivers[0]

    async def start(self) -> None:
        # mirror_all: one receiver; the first screen is the main one, the rest mirror it.
        mirrors = tuple(o for o, _ in self.outputs[1:])
        # RTP ports: Miracast on rtp_base_port (+1 RTCP), AirPlay video / audio from UxPlay after it.
        airplay_ports = (self.cfg.rtp_base_port + 4, self.cfg.rtp_base_port + 6)
        for i, (output, size) in enumerate(self.outputs[:1]):
            r = Receiver(self.name_for(i, output), output, size, self.cfg.miracast_base_port + i,
                         self.cfg.rtp_base_port + 2 * i, self.cfg.busy_policy, self.ui.send, on_ended=self.rename,
                         size_now=output_size, mirrors=mirrors, airplay_ports=airplay_ports)
            r.on_state = self.on_receiver_state
            await r.start()
            self.receivers.append(r)
            try:
                await self.mdns.publish(r.name, r.mice_port)
            except Exception as e:  # Avahi down: casting by Wi-Fi discovery + host name still works
                log.warning("mdns: %s", e)
            if not self.wifi_direct_names(r):
                r.route = "network"          # the hotspot lives in casthub-p2p, with Wi-Fi Direct
                await r.show_name()          # findable now; the first waits for Wi-Fi Direct
        self.ui_task = asyncio.create_task(self.ui.keep_in_sync())
        if self.cfg.airplay and self.receivers:
            if UxPlay.available():
                self.airplay = UxPlay(self.receivers[0], *airplay_ports,
                                      replace=self.cfg.busy_policy == "replace", size_now=output_size)
                await self.airplay.start()
                self.receivers[0].airplay_standby = True
                await self.receivers[0].prepare_airplay()
            else:
                log.warning("airplay: uxplay is not installed; AirPlay is off")
        if self.cfg.wifi_direct and self.receivers:
            # Keep a reference: asyncio holds tasks weakly, and an unreferenced one was collected.
            self.p2p_task = asyncio.create_task(self.p2p_link())
        await self.serve_control()
        await self.ui.send(self.passthrough.status())
        if self.cfg.passthrough_mode == "fixed" and self.cfg.wifi_direct:
            await self.passthrough.start(self.cfg.passthrough_target)      # an admin's setting

    async def p2p_link(self) -> None:
        """Talk to casthub-p2p (root). Wi-Fi Direct casts go to the first receiver."""
        first = self.receivers[0]
        while True:
            try:
                r, w = await asyncio.open_unix_connection(str(P2P_SOCK))
            except OSError:
                if not first.name_ready:     # no Wi-Fi Direct daemon: mDNS alone finds us
                    log.warning("casthub-p2p not reachable; showing the name without Wi-Fi Direct")
                    first.route = first.route or "network"      # no hotspot either
                    await first.show_name()
                await asyncio.sleep(10)
                continue
            self.p2p_writer = w
            await self.p2p_advertise(new_address=self.wants_new_address(first))
            if self.p2p_busy:
                await self.p2p_send({"cmd": "busy", "busy": True})
            while line := await r.readline():
                with contextlib.suppress(ValueError, KeyError):
                    ev = json.loads(line)
                    if ev["event"] == "advertised":
                        if ev.get("name") == first.name and not first.name_ready:
                            if self.p2p_fallback:
                                self.p2p_fallback.cancel()
                            await first.show_name()
                            self.wait_for_route(first)
                    elif ev["event"] == "route" and ev.get("route") in ROUTES:
                        await first.set_route(ev["route"])
                    elif ev["event"] == "source":
                        await first.on_p2p_source(ev["addr"], int(ev["rtsp_port"]), ev.get("name", ""))
                    elif ev["event"] == "gone":
                        first.on_p2p_gone(ev["addr"])
                    elif ev["event"] == "pin":
                        await first.show_pin(str(ev["pin"]))
                    elif ev["event"] in ("tvs", "tv", "tv_failed", "tv_gone"):
                        self.passthrough.on_p2p_event(ev)
            await asyncio.sleep(2)

    def name_for(self, index: int, output: str) -> str:
        return self.cfg.pinned_name(output) or naming.session_name(index, naming.session_salt())

    async def rename(self, r: Receiver) -> None:
        """After a cast: a new name (and mDNS ID), so Windows lists a fresh display."""
        i = self.receivers.index(r)
        new = self.name_for(i, r.output)
        if new == r.name:                    # pinned in casthub.toml
            await self.restart_airplay(r)
            await r.show_name()
            return
        log.info("%s is now %s", r.name, new)
        r.name = new
        await self.restart_airplay(r)
        try:
            await self.mdns.publish(new, r.mice_port)
        except Exception as e:  # noqa: BLE001 (Avahi down: Wi-Fi discovery still works)
            log.warning("mdns: %s", e)
        if self.wifi_direct_names(r) and await self.p2p_advertise(new_address=self.wants_new_address(r)):
            return                           # shown when casthub-p2p says "advertised"
        await r.show_name()

    async def on_receiver_state(self, r: Receiver) -> None:
        """While a cast is on, Wi-Fi Direct says "not available" and stops answering discovery,
        so Win + K stops listing us (it lists from Wi-Fi Direct; M4)."""
        if self.receivers and r is self.receivers[0]:
            self.passthrough.show_state(r)            # the TV's own screen (passthrough)
        busy = r.state in ("connecting", "streaming")
        if busy == self.p2p_busy or r is not (self.receivers[0] if self.receivers else r):
            return
        self.p2p_busy = busy
        await self.p2p_send({"cmd": "busy", "busy": busy})

    async def p2p_send(self, msg: dict) -> bool:
        w = self.p2p_writer
        if w is None or w.is_closing():
            return False
        w.write(json.dumps(msg).encode() + b"\n")
        try:
            await w.drain()
        except ConnectionError:
            return False
        return True

    async def restart_airplay(self, r: Receiver) -> None:
        """UxPlay again under the receiver's (new) name and a new device ID. Not if an iPhone
        connected in the meantime."""
        if self.airplay and r is self.receivers[0] and not (r.current and r.current.via == "airplay"):
            await self.airplay.restart()

    def wants_new_address(self, first: Receiver) -> bool:
        """A new Wi-Fi Direct address with each new name (D26). It drops Wi-Fi for a few
        seconds, so not while another screen is casting, nor for a pinned name."""
        if self.cfg.pinned_name(first.output):
            return False
        return all(o.state == "idle" for o in self.receivers if o is not first)

    async def p2p_advertise(self, new_address: bool = False) -> bool:
        """Ask casthub-p2p to advertise the first receiver's name; False if it isn't connected.
        It answers {"event": "advertised"} (p2p_link shows the name then); if that never
        comes, the name is shown anyway after P2P_CONFIRM_S."""
        w = self.p2p_writer
        if w is None or w.is_closing() or not self.receivers:
            return False
        first = self.receivers[0]
        msg = {"cmd": "advertise", "name": first.name, "port": first.mice_port, "new_address": new_address}
        w.write(json.dumps(msg).encode() + b"\n")
        try:
            await w.drain()
        except ConnectionError:
            return False
        if self.p2p_fallback:
            self.p2p_fallback.cancel()

        def show_anyway() -> None:
            if not first.name_ready:
                log.warning("casthub-p2p did not confirm %s within %d s; showing it", first.name, P2P_CONFIRM_S)
                first.route = first.route or "network"
                asyncio.ensure_future(first.show_name())
        self.p2p_fallback = asyncio.get_running_loop().call_later(P2P_CONFIRM_S, show_anyway)
        return True

    def wait_for_route(self, first: Receiver) -> None:
        """The name is findable; if casthub-p2p never says how senders reach us (an older one, or
        the hotspot start hangs), show the same-network instructions after ROUTE_WAIT_S."""
        def fallback() -> None:
            if not first.route:
                log.warning("no route from casthub-p2p within %d s; showing same-network instructions", ROUTE_WAIT_S)
                asyncio.ensure_future(first.set_route("network"))
        if not first.route:
            asyncio.get_running_loop().call_later(ROUTE_WAIT_S, fallback)

    async def end_all(self) -> None:
        """End every cast (SIGUSR1: casthub-leave, so the leave page isn't under a video)."""
        log.info("ending all casts")
        await asyncio.gather(*(r.end_current() for r in self.receivers), return_exceptions=True)

    # ---- control socket: the leave page's passthrough setup -----------------------------------
    async def serve_control(self) -> None:
        """$XDG_RUNTIME_DIR/casthub-hub.sock, JSON lines, one reply each (the UI, same user):
        {"cmd": "status"} · {"cmd": "tv_find"} (≈ 10 s) · {"cmd": "tv_on", "name": ...} · {"cmd": "tv_off"}"""
        path = CONTROL_SOCK()
        with contextlib.suppress(OSError):
            path.unlink()
        try:
            self.control = await asyncio.start_unix_server(self.on_control, str(path))
            os.chmod(path, 0o600)
        except OSError as e:
            log.warning("control socket: %s", e)

    async def on_control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    reply = await self.control_command(json.loads(line))
                except (ValueError, KeyError, TypeError) as e:
                    reply = {"ok": False, "error": str(e)}
                writer.write(json.dumps(reply).encode() + b"\n")
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def control_command(self, msg: dict) -> dict:
        pt = self.passthrough
        cmd = msg["cmd"]
        if cmd == "status":
            return {"ok": True, **pt.status()}
        if not pt.allowed:
            return {"ok": False, "error": "passthrough is off in casthub.toml"}
        if pt.locked:
            return {"ok": False, "error": "passthrough is set by the administrator"}
        if cmd == "tv_find":
            return {"ok": True, "tvs": await pt.find()}
        if cmd == "tv_on":
            await pt.start(str(msg["name"]))
            return {"ok": True}
        if cmd == "tv_off":
            await pt.stop()
            return {"ok": True}
        raise ValueError(f"unknown cmd {cmd!r}")

    async def stop(self) -> None:
        if self.control:
            self.control.close()
        await self.passthrough.stop()
        await self.end_all()          # tell each PC the cast is over (RTSP teardown)
        if self.airplay:
            await self.airplay.stop()
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
    outputs.sort(key=main_screen_first)
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
