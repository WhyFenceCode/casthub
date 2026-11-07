"""Display-mode UI: one full-screen DisplayScreen per output, plus the PIN page.

Runs as the `casthub` user inside the Display Sway session (modes/display/sway.conf):

    python3 -m casthub.ui.app [--config /etc/casthub/casthub.toml]

Control socket ($XDG_RUNTIME_DIR/casthub-ui.sock, JSON lines; only this user can reach it):
    {"cmd": "pin"}                                        open the PIN page (Ctrl+Alt+Esc)
    {"cmd": "state", "output": "eDP-1", "state": "connecting", "device": "<name>"}
    {"cmd": "busy", "output": "eDP-1", "text": "<message>"}  (empty text hides it)
The receiver hub (M3/M6) drives state and busy; casthub-leave sends "pin".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQuick import QQuickView

from casthub import config as cfgmod
from casthub.switcher.pin import verify_pin
from casthub.ui.qr import QrProvider

log = logging.getLogger("casthub.ui")
QML = Path(__file__).resolve().parent / "qml" / "DisplayScreen.qml"
QR_FILE = Path("/run/casthub/hotspot-qr.txt")      # written by the hotspot service (root); holds the join code
LEAVE = "/usr/libexec/casthub/casthub-leave"
MAX_TRIES, LOCKOUT_S = 5, 60
ACTIONS = {
    "leave": [LEAVE, "--now"],
    "reboot": ["systemctl", "reboot"],
    "poweroff": ["systemctl", "poweroff"],
}


class PinGuard:
    """Counts wrong PINs; after MAX_TRIES the page refuses input for LOCKOUT_S seconds."""

    def __init__(self, pin_hash: str, clock=time.monotonic) -> None:
        self.pin_hash, self.clock = pin_hash, clock
        self.failures, self.locked_until = 0, 0.0

    @property
    def required(self) -> bool:
        return bool(self.pin_hash)

    def check(self, pin: str) -> tuple[bool, str]:
        now = self.clock()
        if now < self.locked_until:
            return False, f"Locked · wait {int(self.locked_until - now) + 1} s"
        if verify_pin(pin, self.pin_hash):
            self.failures = 0
            return True, ""
        self.failures += 1
        if self.failures >= MAX_TRIES:
            self.failures, self.locked_until = 0, now + LOCKOUT_S
            return False, f"Too many tries · wait {LOCKOUT_S} s"
        left = MAX_TRIES - self.failures
        return False, f"Wrong PIN · {left} {'try' if left == 1 else 'tries'} left"


class DisplayUI(QObject):
    def __init__(self, app: QGuiApplication, cfg: cfgmod.Config) -> None:
        super().__init__()
        self.app, self.cfg = app, cfg
        self.guard = PinGuard(cfg.pin_hash)
        self.views: dict[str, QQuickView] = {}
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
        index = len(self.views)
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
            "receiverName": self.cfg.receiver_name(index, screen.name()),
            "location": self.cfg.location,
            "coordinates": self.cfg.coordinates(),
            "airplay": self.cfg.airplay,
            "theme": self.cfg.theme,
            "qrPayload": self.qr,
            "pinRequired": self.guard.required,
        }
        for k, val in props.items():
            root.setProperty(k, val)
        root.pinSubmitted.connect(lambda pin, r=root: self.on_pin(r, pin))
        root.pinChosen.connect(lambda action, r=root: self.on_action(r, action))
        root.pinCancelled.connect(lambda r=root: r.setProperty("pinOpen", False))
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
        qr = ""
        if self.cfg.hotspot:
            try:
                qr = QR_FILE.read_text().strip()
            except OSError:
                qr = ""
        if qr != self.qr:
            self.qr = qr
            for r in self.roots():
                r.setProperty("qrPayload", qr)

    # ---- PIN page ---------------------------------------------------------------
    def open_pin(self) -> None:
        """Open on the screen that has the pointer/keyboard focus, else the first one."""
        target = self.app.focusWindow() or next(iter(self.views.values()), None)
        for v in self.views.values():
            r = v.rootObject()
            if r:
                r.setProperty("pinOpen", v is target)
        if target:
            target.requestActivate()

    def on_pin(self, root, pin: str) -> None:
        # Give QML a frame to show "Checking…" before scrypt runs.
        def check() -> None:
            ok, msg = self.guard.check(pin)
            log.info("pin %s", "accepted" if ok else "refused")
            root.pinResult(ok, msg)
        QTimer.singleShot(30, check)

    def on_action(self, root, action: str) -> None:
        cmd = ACTIONS.get(action)
        if not cmd:
            return
        log.info("action %s", action)
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if r.returncode != 0:
            log.error("%s failed: %s", action, r.stderr.strip())
            root.setProperty("pinOpen", False)

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
        if cmd == "pin":
            self.open_pin()
        elif cmd in ("state", "busy"):
            v = self.views.get(msg["output"])
            if v is None:
                raise KeyError(f"no screen {msg['output']!r}")
            r = v.rootObject()
            if cmd == "state":
                if msg["state"] not in ("idle", "connecting", "streaming"):
                    raise ValueError(f"bad state {msg['state']!r}")
                r.setProperty("screenState", msg["state"])
                r.setProperty("deviceName", str(msg.get("device", "")))
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
