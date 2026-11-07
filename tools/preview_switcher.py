#!/usr/bin/env python3
"""Render the Cast Hub Alt+Tab switcher (shell/tabbox/org.casthub.switcher) off-screen with a
stand-in for KWin's TabBoxSwitcher and made-up windows. Output: local/previews/ only.

    python3 tools/preview_switcher.py [--scheme light|dark]
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
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MOCK = """import QtQuick
Item {
    property var model: ListModel {
        ListElement { caption: "<placeholder> : bash — Konsole"; minimized: false; desktopName: "Console" }
        ListElement { caption: "Projects — Dolphin"; minimized: false; desktopName: "Files" }
        ListElement { caption: "notes.md — Kate"; minimized: false; desktopName: "Desktop" }
        ListElement { caption: "Example page — Mozilla Firefox"; minimized: true; desktopName: "Desktop" }
    }
    property rect screenGeometry: Qt.rect(0, 0, 1920, 1080)
    property int currentIndex: 1
}
"""


DIALOG = """import QtQuick
import QtQuick.Window
import org.kde.kirigami as Kirigami
Window {
    id: w
    property Item mainItem
    property int location
    readonly property bool dark: Kirigami.Theme.backgroundColor.hslLightness < 0.5
    width: mainItem ? mainItem.width + 24 : 100
    height: mainItem ? mainItem.height + 24 : 100
    color: dark ? "#161616" : "#e4e4e4"
    Rectangle {
        anchors.fill: parent; anchors.margins: 0
        radius: 10; color: w.dark ? "#1c1c1c" : "#ececec"
        border.width: 1; border.color: w.dark ? "#3a3a3a" : "#c8c8c8"
    }
    onMainItemChanged: if (mainItem) { mainItem.parent = contentItem; mainItem.x = 12; mainItem.y = 12 }
    Component.onCompleted: show()
    Timer {
        interval: 500; running: true
        onTriggered: w.contentItem.grabToImage(function (r) { r.saveToFile(previewOut); console.info("wrote " + previewOut); Qt.quit() })
    }
}
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scheme", choices=["light", "dark"], default="light")
    a = ap.parse_args()
    stage = ROOT / "local" / "switcher-preview"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(ROOT / "shell" / "tabbox" / "org.casthub.switcher" / "contents" / "ui", stage / "ui")
    shutil.copytree(ROOT / "uikit", stage / "ui" / "uikit")
    kwin = stage / "mock" / "org" / "kde" / "kwin"
    kwin.mkdir(parents=True)
    (kwin / "qmldir").write_text("module org.kde.kwin\nTabBoxSwitcher 1.0 TabBoxSwitcher.qml\n")
    (kwin / "TabBoxSwitcher.qml").write_text(MOCK)
    # Plasma's Dialog needs a real compositor; a plain window with the pop-up frame stands in.
    core = stage / "mock" / "org" / "kde" / "plasma" / "core"
    core.mkdir(parents=True)
    (core / "qmldir").write_text("module org.kde.plasma.core\nDialog 1.0 Dialog.qml\nTypes 1.0 Types.qml\n")
    (core / "Types.qml").write_text("import QtQuick\nQtObject { enum Location { Floating } }\n")
    (core / "Dialog.qml").write_text(DIALOG)

    app = QGuiApplication(sys.argv[:1])
    if a.scheme == "dark":
        pal = app.palette()
        pal.setColor(QPalette.Window, QColor("#161616"))
        app.setPalette(pal)
    eng = QQmlApplicationEngine()
    eng.rootContext().setContextProperty("previewOut", str(ROOT / "local" / "previews" / f"switcher-{a.scheme}.png"))
    eng.addImportPath(str(stage / "mock"))
    eng.load(QUrl.fromLocalFile(str(stage / "ui" / "main.qml")))
    if not eng.rootObjects():
        return 1
    QTimer.singleShot(8000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
