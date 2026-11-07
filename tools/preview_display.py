#!/usr/bin/env python3
"""Render the Display-mode screens off-screen (idle, connecting, streaming+busy, PIN page).

    python3 tools/preview_display.py [--size 1080x633] [--scheme light|dark]

Output goes to local/previews/ only. All names and places are made-up placeholders.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QDateTime, QUrl  # noqa: E402
from PySide6.QtGui import QFontDatabase, QGuiApplication  # noqa: E402
from PySide6.QtQuick import QQuickView  # noqa: E402

from casthub.ui.qr import QrProvider  # noqa: E402

FAKE = {
    "receiverName": "Brand-1",
    "location": "Springfield, Region",
    "coordinates": "12.34° N · 56.78° W",
    "qrPayload": "WIFI:T:WPA;S:<ssid>;P:<placeholder>;;",
}
QML = {
    "screen": ROOT / "casthub" / "ui" / "qml" / "DisplayScreen.qml",
    "pin": ROOT / "casthub" / "switcher" / "qml" / "PinPage.qml",
}


def render(app: QGuiApplication, qml: Path, size: tuple[int, int], props: dict, out: Path, settle_ms: int = 400) -> None:
    view = QQuickView()
    view.engine().addImageProvider("qr", QrProvider())
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(*size)
    view.setSource(QUrl.fromLocalFile(str(qml)))
    errs = [e.toString() for e in view.errors()]
    if errs:
        raise SystemExit("\n".join(errs))
    root = view.rootObject()
    for k, v in props.items():
        root.setProperty(k, v)
    view.show()
    end = QDateTime.currentMSecsSinceEpoch() + settle_ms
    while QDateTime.currentMSecsSinceEpoch() < end:
        app.processEvents()
    view.grabWindow().save(str(out))
    print("wrote", out.relative_to(ROOT))
    view.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="1080x633")
    ap.add_argument("--scheme", default="light", choices=["light", "dark"])
    ap.add_argument("--only", help="screen name to render (idle, connecting, busy, pin, pin-error, pin-menu)")
    a = ap.parse_args()
    w, h = (int(v) for v in a.size.split("x"))
    app = QGuiApplication(sys.argv)
    for f in (ROOT / "brand" / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))
    out = ROOT / "local" / "previews"
    out.mkdir(parents=True, exist_ok=True)
    fixed = QDateTime.fromString("2026-11-07T01:06:00", "yyyy-MM-ddTHH:mm:ss")
    base = {**FAKE, "theme": a.scheme, "fixedNow": fixed}
    shots = {
        "idle": ("screen", {}),
        "connecting": ("screen", {"screenState": "connecting", "deviceName": "Pixel Phone"}),
        "busy": ("screen", {"screenState": "streaming", "deviceName": "Pixel Phone",
                            "busyText": "Laptop tried to connect · Brand-1 is in use"}),
        "pin": ("pin", {"entered": 2}),
        "pin-error": ("pin", {"entered": 0, "error": "Wrong PIN · 2 tries left"}),
        "pin-menu": ("pin", {"unlocked": True, "selected": 0}),
    }
    for name, (kind, props) in shots.items():
        if a.only and name != a.only:
            continue
        if kind == "pin" and not QML["pin"].exists():
            continue
        render(app, QML[kind], (w, h), {**base, **props}, out / f"display-{name}-{a.scheme}-{w}x{h}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
