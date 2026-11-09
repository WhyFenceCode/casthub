#!/usr/bin/env python3
"""Render the Display-mode screens off-screen (idle, connecting, streaming+busy, leave page).

    python3 tools/preview_display.py [--size 1080x633] [--scheme light|dark]

Output goes to local/previews/ only. All names and places are made-up placeholders.
The TV is "Example-TV-AKGDJ": the earlier placeholder turned out to match a real TV this
machine has seen, so it was changed to keep this machine's surroundings out of the repo.
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
    "receiverName": os.environ.get("PREVIEW_NAME", "Brand-1"),
    "location": "Springfield, Region",
    "coordinates": "12.34° N · 56.78° W",
    "route": "network",
}
HOTSPOT = {"qrPayload": "WIFI:T:WPA;S:Cast Hub AB12;P:placeholder12;;",
           "hotspotName": "Cast Hub AB12", "hotspotPassword": "placeholder12"}
QML = {
    "screen": ROOT / "casthub" / "ui" / "qml" / "DisplayScreen.qml",
    "leave": ROOT / "casthub" / "switcher" / "qml" / "LeavePage.qml",
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
    ap.add_argument("--only", help="screen name to render (idle, loading, hotspot, alone, connecting, busy, pin, "
                                   "leave, panel-leave, tv, tv-joining, passthrough)")
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
        "loading": ("screen", {"receiverName": "", "route": ""}),
        "hotspot": ("screen", {**HOTSPOT, "route": "hotspot"}),
        "alone": ("screen", {**HOTSPOT, "route": "hotspot-alone"}),
        "connecting": ("screen", {"screenState": "connecting", "deviceName": "Pixel Phone"}),
        "busy": ("screen", {"screenState": "streaming", "deviceName": "Pixel Phone",
                            "busyText": "Laptop tried to connect · Brand-1 is in use"}),
        "pin": ("screen", {"pinCode": "12345670"}),
        "leave": ("leave", {"selected": 0}),
        "panel-leave": ("leave", {"selected": 0, "panel": True}),
        "tv": ("screen", {"screenState": "streaming", "deviceName": "Pixel Phone", "tvName": "Example-TV-AKGDJ"}),
        "tv-joining": ("screen", {"passthrough": {"state": "connecting", "tv": "Example-TV-AKGDJ", "why": "",
                                                  "tvs": [], "locked": False, "allowed": True}}),
        "passthrough": ("leave", {"view": "passthrough", "selected": 0, "passthrough": {
            "state": "on", "tv": "Example-TV-AKGDJ", "why": "", "tvs": ["Example-TV-AKGDJ", "Projector 2"],
            "locked": False, "allowed": True}}),
    }
    for name, (kind, props) in shots.items():
        if a.only and name != a.only:
            continue
        render(app, QML[kind], (w, h), {**base, **props}, out / f"display-{name}-{a.scheme}-{w}x{h}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
