#!/usr/bin/env python3
"""Render the SDDM login theme off-screen with a mocked SDDM API.

Needs PySide6 (kept in the git-ignored venv):
    python3 -m venv local/.venv && local/.venv/bin/pip install PySide6-Essentials
    local/.venv/bin/python tools/preview_greeter.py --mode display --scheme dark

Output goes to local/previews/ only (never tracked). Users and hostnames are
made-up mockup values, not read from this machine.
"""

from __future__ import annotations

import argparse
import configparser
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (  # noqa: E402
    QAbstractListModel, QByteArray, QDateTime, QModelIndex, QObject, Property, Qt, QTimer, QUrl, Signal, Slot,
)
from PySide6.QtGui import QFontDatabase, QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlPropertyMap  # noqa: E402
from PySide6.QtQuick import QQuickView  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class ListModel(QAbstractListModel):
    def __init__(self, roles: list[str], rows: list[dict], last_index: int = 0, last_user: str = ""):
        super().__init__()
        self._roles = {Qt.UserRole + i + 1: r for i, r in enumerate(roles)}
        self._rows = rows
        self._last_index = last_index
        self._last_user = last_user

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self._rows)

    def data(self, index, role):
        name = self._roles.get(role)
        return self._rows[index.row()].get(name) if name else None

    def roleNames(self):  # noqa: N802
        return {k: QByteArray(v.encode()) for k, v in self._roles.items()}

    def _li(self):
        return self._last_index

    def _lu(self):
        return self._last_user

    lastIndex = Property(int, _li, constant=True)
    lastUser = Property(str, _lu, constant=True)


class Sddm(QObject):
    loginFailed = Signal()
    loginSucceeded = Signal()

    def __init__(self, host: str):
        super().__init__()
        self._host = host
        self.attempts: list[tuple[str, str, int]] = []

    def _h(self):
        return self._host

    def _t(self):
        return True

    hostName = Property(str, _h, constant=True)
    canPowerOff = Property(bool, _t, constant=True)
    canReboot = Property(bool, _t, constant=True)

    @Slot(str, str, int)
    def login(self, user, password, session):
        self.attempts.append((user, "***" if password else "", session))
        print(f"login(user={user!r}, password={'set' if password else 'empty'}, session={session})")
        QTimer.singleShot(0, self.loginFailed.emit)

    @Slot()
    def powerOff(self):  # noqa: N802
        print("powerOff()")

    @Slot()
    def reboot(self):
        print("reboot()")


class Keyboard(QObject):
    def _c(self):
        return False

    capsLock = Property(bool, _c, constant=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["workstation", "display", "console"], default="workstation")
    ap.add_argument("--scheme", choices=["light", "dark"], default="light")
    ap.add_argument("--size", default="1080x675")
    ap.add_argument("--admin", action="store_true", help="make the preview user an admin")
    ap.add_argument("--press", default="", help="comma-separated keys to send after load, e.g. Down,Return")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    app = QGuiApplication(sys.argv)
    for f in (ROOT / "brand" / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))

    sessions = [
        {"file": "/usr/share/casthub/wayland-sessions/casthub-workstation.desktop", "name": "Workstation"},
        {"file": "/usr/share/casthub/wayland-sessions/casthub-display.desktop", "name": "Display"},
        {"file": "/usr/share/casthub/wayland-sessions/casthub-console.desktop", "name": "Console"},
    ]
    users = [
        {"name": "torsten", "realName": "Torsten Young"},
        {"name": "guest", "realName": "Guest User"},
        {"name": "casthub", "realName": "Cast Hub display"},
    ]
    last = {"workstation": 0, "display": 1, "console": 2}[a.mode]
    session_model = ListModel(["file", "name"], sessions, last_index=last)
    user_model = ListModel(["name", "realName"], users, last_user="torsten")

    cp = configparser.ConfigParser()
    cp.optionxform = str
    cp.read(ROOT / "greeter" / "theme.conf")
    config = QQmlPropertyMap()
    for k, v in cp["General"].items():
        config.insert(k, v)
    config.insert("ColorScheme", a.scheme)
    config.insert("Location", "Kingston")
    config.insert("AdminUsers", "torsten" if a.admin else "")

    sddm = Sddm("kingston-01")
    view = QQuickView()
    ctx = view.rootContext()
    ctx.setContextProperty("sddm", sddm)
    ctx.setContextProperty("sessionModel", session_model)
    ctx.setContextProperty("userModel", user_model)
    ctx.setContextProperty("config", config)
    keyboard = Keyboard()
    ctx.setContextProperty("keyboard", keyboard)
    ctx.setContextProperty("primaryScreen", True)
    w, h = (int(x) for x in a.size.split("x"))
    view.setResizeMode(QQuickView.SizeRootObjectToView)
    view.resize(w, h)
    view.setSource(QUrl.fromLocalFile(str(ROOT / "greeter" / "Main.qml")))
    if view.status() != QQuickView.Ready:
        for e in view.errors():
            print(e.toString(), file=sys.stderr)
        return 1
    rootobj = view.rootObject()
    rootobj.setProperty("fixedNow", QDateTime.fromString("2026-11-07T01:06:00", Qt.ISODate))
    rootobj.setProperty("selected", {"workstation": 0, "display": 1, "console": 2}[a.mode])
    view.show()

    out = Path(a.out) if a.out else ROOT / "local" / "previews" / f"greeter-{a.mode}-{a.scheme}-{a.size}.png"
    out.parent.mkdir(parents=True, exist_ok=True)

    def press_keys():
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtCore import QEvent
        for k in filter(None, a.press.split(",")):
            key = getattr(Qt, f"Key_{k}")
            for t in (QEvent.KeyPress, QEvent.KeyRelease):
                app.sendEvent(view, QKeyEvent(t, key, Qt.NoModifier))
            print(f"pressed {k}: selected={rootobj.property('selected')}")

    def grab():
        press_keys()
        QTimer.singleShot(400, lambda: (view.grabWindow().save(str(out)), print(out), app.quit()))

    QTimer.singleShot(300, grab)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
