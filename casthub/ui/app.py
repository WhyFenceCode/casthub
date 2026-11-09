"""Display-mode UI: one full-screen DisplayScreen per output, plus the leave page.

Runs as the `casthub` user inside the Display Sway session (modes/display/sway.conf):

    python3 -m casthub.ui.app [--config /etc/casthub/casthub.toml]

Control socket ($XDG_RUNTIME_DIR/casthub-ui.sock, JSON lines; only this user can reach it):
    {"cmd": "leave"}                                      open the leave page (Ctrl+Alt+Esc; panel: close)
    {"cmd": "state", "output": "eDP-1", "state": "connecting", "device": "<name>", "name": "CAST-XXXXX"}
                name: the receiver name, "" while it is not findable yet (the screen says Loading)
    {"cmd": "busy", "output": "eDP-1", "text": "<message>"}  (empty text hides it)
    {"cmd": "pin", "output": "eDP-1", "pin": "12345670"}     (Wi-Fi Direct pairing; empty hides it)
The receiver hub (M3/M6) drives state and busy; casthub-leave sends "leave".
No PIN here: leaving only returns to the login screen (owner decision, SPEC D7).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQuick import QQuickView

from casthub import config as cfgmod
from casthub.ui.qr import QrProvider

log = logging.getLogger("casthub.ui")
# The bar's battery level (uikit/Battery.qml) reads /sys/class/power_supply with XMLHttpRequest.
os.environ.setdefault("QML_XHR_ALLOW_FILE_READ", "1")
QML = Path(__file__).resolve().parent / "qml" / "DisplayScreen.qml"
HUB_SOCK = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "casthub-hub.sock"     # the hub's control socket
QR_FILE = Path("/run/casthub/hotspot-qr.txt")      # written by the hotspot service (root); holds the join code
LEAVE = "/usr/libexec/casthub/casthub-leave"
PANEL_CLOSE = "/usr/libexec/casthub/casthub-panel-close"
# CASTHUB_PANEL=1: Display mode as a panel inside Workstation or Console (casthub-display).
# There the bar's "Menu" opens a short leave page (Quit panel, Passthrough setup, Resume) and
# Ctrl+Alt+Esc closes the panel at once.
PANEL = os.environ.get("CASTHUB_PANEL") == "1"
ACTIONS = {
    "leave": [LEAVE, "--now"],
    "reboot": ["systemctl", "reboot"],
    "poweroff": ["systemctl", "poweroff"],
}
if PANEL:
    ACTIONS = {"leave": [PANEL_CLOSE]}


ROUTES = ("", "network", "hotspot", "hotspot-alone")


def _wifi_field(payload: str, key: str) -> str:
    """One field of a `WIFI:T:WPA;S:<ssid>;P:<password>;;` join code, unescaped."""
    if not payload.startswith("WIFI:"):
        return ""
    fields, cur, esc = [], "", False
    for c in payload[5:]:
        if esc:
            cur, esc = cur + c, False
        elif c == "\\":
            esc = True
        elif c == ";":
            fields.append(cur)
            cur = ""
        else:
            cur += c
    for f in fields:
        k, sep, v = f.partition(":")
        if sep and k == key:
            return v
    return ""


def hotspot_ssid(payload: str) -> str:
    return _wifi_field(payload, "S")


def hotspot_password(payload: str) -> str:
    return _wifi_field(payload, "P")


class DisplayUI(QObject):
    def __init__(self, app: QGuiApplication, cfg: cfgmod.Config) -> None:
        super().__init__()
        self.app, self.cfg = app, cfg
        self.views: dict[str, QQuickView] = {}
        self.names: dict[str, str] = {}             # output → the hub's receiver name
        self.routes: dict[str, str] = {}            # output → how senders reach the hub
        self.tvs: dict[str, str] = {}               # output → the TV its casts go to (passthrough)
        self.passthrough: dict = {}
        self.leave_when_on = False                  # a TV was picked: back to the Display screen once on
        self.qr = ""
        for s in app.screens():
            self.add_screen(s)
        app.screenAdded.connect(self.add_screen)
        app.screenRemoved.connect(self.remove_screen)
        self.qr_timer = QTimer(self, interval=5000, timeout=self.refresh_qr)
        self.qr_timer.start()
        self.refresh_qr()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self.on_connection)
        sock = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "casthub-ui.sock"
        QLocalServer.removeServer(str(sock))
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(str(sock)):
            log.error("control socket: %s", self.server.errorString())

    # ---- screens --------------------------------------------------------------
    def add_screen(self, screen: QScreen) -> None:
        v = QQuickView()
        v.engine().addImageProvider("qr", QrProvider())
        v.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
        v.setScreen(screen)
        v.setTitle(f"Cast Hub {screen.name()}")
        v.setSource(QUrl.fromLocalFile(str(QML)))
        for e in v.errors():
            log.error("%s", e.toString())
        root = v.rootObject()
        if root is None:
            return
        props = {
            # Only the hub's name for this screen, once it is findable (it changes after every
            # cast); until then "" and the screen says Loading.
            "receiverName": self.names.get(screen.name(), ""),
            "route": self.routes.get(screen.name(), ""),
            "tvName": self.tvs.get(screen.name(), ""),
            "location": self.cfg.location,
            "coordinates": self.cfg.coordinates(),
            "airplay": self.cfg.airplay,
            "theme": self.cfg.theme,
            "qrPayload": self.qr,
            "hotspotName": hotspot_ssid(self.qr),
            "hotspotPassword": hotspot_password(self.qr),
            "panel": PANEL,
        }
        for k, val in props.items():
            root.setProperty(k, val)
        if self.passthrough:
            root.setProperty("passthrough", self.passthrough)
        root.leaveChosen.connect(lambda action, r=root: self.on_action(r, action))
        root.leaveCancelled.connect(lambda r=root: r.setProperty("leaveOpen", False))
        v.showFullScreen()
        self.views[screen.name()] = v
        # Sway puts new windows on the focused output; move each one to its own.
        if os.environ.get("SWAYSOCK"):
            crit = f'[title="^Cast Hub {screen.name()}$"]'
            QTimer.singleShot(400, lambda: subprocess.run(
                ["swaymsg", f"{crit} move container to output {screen.name()}, {crit} fullscreen enable"],
                check=False, capture_output=True))
        log.info("screen %s → %s", screen.name(), props["receiverName"])

    def remove_screen(self, screen: QScreen) -> None:
        v = self.views.pop(screen.name(), None)
        if v:
            v.close()
            v.deleteLater()

    def roots(self):
        return [v.rootObject() for v in self.views.values() if v.rootObject()]

    def refresh_qr(self) -> None:
        # The file exists only while the hotspot is up (casthub-p2p writes it): shown only then (D13).
        try:
            qr = QR_FILE.read_text().strip()
        except OSError:
            qr = ""
        if qr != self.qr:
            self.qr = qr
            for r in self.roots():
                r.setProperty("qrPayload", qr)
                r.setProperty("hotspotName", hotspot_ssid(qr))
                r.setProperty("hotspotPassword", hotspot_password(qr))

    # ---- leave page -------------------------------------------------------------
    def open_leave(self) -> None:
        """Open on the screen that has the pointer/keyboard focus, else the first one."""
        target = self.app.focusWindow() or next(iter(self.views.values()), None)
        for v in self.views.values():
            r = v.rootObject()
            if r:
                r.setProperty("leaveOpen", v is target)
        if target:
            target.requestActivate()

    def on_action(self, root, action: str) -> None:
        if action.startswith("tv_"):                     # passthrough setup (leave page)
            self.passthrough_action(action)
            return
        cmd = ACTIONS.get(action)
        if not cmd:
            return
        log.info("action %s", action)
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if r.returncode != 0:
            log.error("%s failed: %s", action, r.stderr.strip())
            root.setProperty("leaveOpen", False)

    # ---- passthrough setup: the hub's control socket ---------------------------------
    def passthrough_action(self, action: str) -> None:
        name = action.partition(":")[2]
        cmd = {"tv_find": {"cmd": "tv_find"}, "tv_off": {"cmd": "tv_off"}}.get(action) or {"cmd": "tv_on", "name": name}
        if cmd["cmd"] == "tv_find":
            self.set_all("searching", True)
        # A TV picked: back to the Display screen (the ways to connect a phone) once the TV's
        # session is up, not before; a failure stays on the setup page (owner, Nov 9).
        self.leave_when_on = cmd["cmd"] == "tv_on"
        if cmd["cmd"] == "tv_off":
            self.set_all("leaveOpen", False)
        log.info("passthrough: %s", cmd["cmd"])

        def done(reply: dict) -> None:
            if cmd["cmd"] == "tv_find":
                self.set_all("searching", False)
            if not reply.get("ok", False):
                log.warning("passthrough: %s", reply.get("error", "no answer from the hub"))
        self.hub_request(cmd, done)

    def hub_request(self, msg: dict, done) -> None:
        """One request to the hub (casthub-hub.sock); `done(reply)` once, never blocking the UI."""
        sock = QLocalSocket(self)
        state = {"done": False}

        def finish(reply: dict) -> None:
            if not state["done"]:
                state["done"] = True
                done(reply)
                sock.abort()
                sock.deleteLater()

        def ready() -> None:
            if sock.canReadLine():
                try:
                    finish(json.loads(bytes(sock.readLine()).decode(errors="replace")))
                except ValueError:
                    finish({"ok": False, "error": "bad reply"})
        sock.connected.connect(lambda: sock.write(json.dumps(msg).encode() + b"\n"))
        sock.readyRead.connect(ready)
        sock.errorOccurred.connect(lambda _e: finish({"ok": False, "error": sock.errorString()}))
        sock.connectToServer(str(HUB_SOCK))

    def set_all(self, prop: str, value) -> None:
        for r in self.roots():
            r.setProperty(prop, value)

    # ---- control socket ---------------------------------------------------------
    def on_connection(self) -> None:
        while self.server.hasPendingConnections():
            c: QLocalSocket = self.server.nextPendingConnection()
            c.readyRead.connect(lambda c=c: self.on_read(c))
            c.disconnected.connect(c.deleteLater)

    def on_read(self, c: QLocalSocket) -> None:
        while c.canReadLine():
            line = bytes(c.readLine()).decode(errors="replace").strip()
            try:
                self.handle(json.loads(line))
                c.write(b'{"ok": true}\n')
            except (ValueError, KeyError, TypeError) as e:
                c.write(json.dumps({"ok": False, "error": str(e)}).encode() + b"\n")

    def handle(self, msg: dict) -> None:
        cmd = msg["cmd"]
        if cmd == "leave":
            if PANEL:
                subprocess.Popen([PANEL_CLOSE])
            else:
                self.open_leave()
        elif cmd == "passthrough":                      # the hub's passthrough status, every screen
            status = {k: msg.get(k) for k in ("state", "tv", "why", "tvs", "locked", "allowed")}
            status["tvs"] = [str(t) for t in status.get("tvs") or []]
            self.passthrough = status
            self.set_all("passthrough", status)
            if self.leave_when_on and status["state"] in ("on", "failed", "off"):
                self.leave_when_on = False
                if status["state"] == "on":
                    self.set_all("leaveOpen", False)
        elif cmd == "tv":                               # this screen's casts go to that TV ("" = ours)
            v = self.views.get(msg["output"])
            if v is None:
                raise KeyError(f"no screen {msg['output']!r}")
            self.tvs[msg["output"]] = str(msg.get("name", ""))
            v.rootObject().setProperty("tvName", self.tvs[msg["output"]])
        elif cmd in ("state", "busy", "pin"):
            if cmd == "state" and "name" in msg:     # kept even before the screen's view exists
                self.names[msg["output"]] = str(msg["name"])
            v = self.views.get(msg["output"])
            if v is None:
                raise KeyError(f"no screen {msg['output']!r}")
            r = v.rootObject()
            if cmd == "state":
                if msg["state"] not in ("idle", "connecting", "streaming"):
                    raise ValueError(f"bad state {msg['state']!r}")
                r.setProperty("screenState", msg["state"])
                r.setProperty("deviceName", str(msg.get("device", "")))
                if "name" in msg:                    # the hub's name; "" while a new one is being set up
                    r.setProperty("receiverName", str(msg["name"]))
                if "route" in msg:                   # network | hotspot | hotspot-alone; "" = deciding
                    if msg["route"] not in ROUTES:
                        raise ValueError(f"bad route {msg['route']!r}")
                    self.routes[msg["output"]] = msg["route"]
                    r.setProperty("route", msg["route"])
            elif cmd == "pin":
                pin = str(msg.get("pin", ""))
                if pin and not (pin.isdigit() and len(pin) in (4, 8)):
                    raise ValueError("pin must be 4 or 8 digits")
                r.setProperty("pinCode", pin)
            else:
                r.setProperty("busyText", str(msg.get("text", "")))
        else:
            raise ValueError(f"unknown cmd {cmd!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=cfgmod.DEFAULT_PATH)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="casthub-ui: %(message)s")
    try:
        cfg = cfgmod.load(a.config)
    except (cfgmod.ConfigError, PermissionError) as e:
        log.error("config: %s (using defaults)", e)
        cfg = cfgmod.Config()
    app = QGuiApplication(sys.argv[:1])
    app.setApplicationName("casthub-display")
    app.setDesktopFileName("casthub-display")
    ui = DisplayUI(app, cfg)  # noqa: F841 (kept alive for the event loop)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
