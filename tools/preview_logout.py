#!/usr/bin/env python3
"""Render the Cast Hub logout screen (shell/.../contents/logout/Logout.qml) off-screen with
the context ksmserver-logout-greeter provides. Output: local/previews/ only.

    python3 tools/preview_logout.py [--type default|logout|reboot|halt] [--scheme light|dark]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTimer, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QPalette  # noqa: E402
from PySide6.QtQml import QQmlPropertyMap  # noqa: E402
from PySide6.QtQuick import QQuickView  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TYPES = {"default": -1, "logout": 0, "reboot": 1, "halt": 2}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", choices=list(TYPES), default="default")
    ap.add_argument("--scheme", choices=["light", "dark"], default="light")
    ap.add_argument("--size", default="1920x1080")
    a = ap.parse_args()
    w, h = map(int, a.size.split("x"))

    stage = ROOT / "local" / "logout-preview"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(ROOT / "shell" / "plasma-shell" / "org.casthub.desktop" / "contents" / "logout", stage)
    shutil.copytree(ROOT / "uikit", stage / "uikit")

    app = QGuiApplication(sys.argv[:1])
    if a.scheme == "dark":   # Kirigami reads the window colour from the palette
        pal = app.palette()
        pal.setColor(QPalette.Window, QColor("#161616"))
        app.setPalette(pal)
    view = QQuickView()
    ctx = view.rootContext()
    st = QQmlPropertyMap(view)
    for k, v in {"ShutdownTypeDefault": -1, "ShutdownTypeNone": 0, "ShutdownTypeReboot": 1, "ShutdownTypeHalt": 2}.items():
        st.insert(k, v)
    spd = QQmlPropertyMap(view)
    spd.insert("SuspendState", True)
    spd.insert("HibernateState", False)
    for k, v in {"sdtype": TYPES[a.type], "ShutdownType": st, "maysd": True, "canLogout": True,
                 "spdMethods": spd, "softwareUpdatePending": False}.items():
        ctx.setContextProperty(k, v)
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(w, h)
    view.setSource(QUrl.fromLocalFile(str(stage / "Logout.qml")))
    for e in view.errors():
        print(e.toString())
    if view.rootObject() is None:
        return 1
    view.show()
    out = ROOT / "local" / "previews" / f"logout-{a.type}-{a.scheme}.png"
    QTimer.singleShot(500, lambda: (view.grabWindow().save(str(out)), print(f"wrote {out.relative_to(ROOT)}"), app.quit()))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
