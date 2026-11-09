"""Passthrough (M10): the TV's own screen between casts ("waiting", "connecting" with its dots).

The TV process (media/tvproc.py) starts this renderer with Qt's offscreen platform:

    python3 -I -m casthub.media.tvcard <width> <height> <dir>

It draws Cast Hub's Display screen (ui/qml/DisplayScreen.qml) in its TV mode (tvCard: the clock,
the receiver's name and the status, but not the ways to connect, the QR or the Log out button:
those stay on our own screens) at the TV's size, and writes FRAMES images, one per step of the
loading dots, as card-0.png … card-4.png in <dir>. The TV sender loops them at FPS. Each file is
replaced atomically (write, then rename), so the sender never reads half an image.

State on stdin, JSON lines (all keys optional, kept between lines):
    {"receiverName": "CAST-AB12C", "screenState": "idle|connecting|streaming",
     "deviceName": "<sender>", "theme": "auto|light|dark", "location": "...", "coordinates": "..."}
It draws again on every line and at each new minute (the clock). Prints "ready" after the first set.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

FRAMES = 5                      # the dots' steps: 0-3 one period purple, 4 none (DisplayScreen.qml)
FPS = 5                         # ≈ the 220 ms step on our own screens
SETTLE_MS, STEP_SETTLE_MS = 700, 150     # after a new state, and between the dots' steps
QML = Path(__file__).resolve().parents[1] / "ui" / "qml" / "DisplayScreen.qml"
KEYS = ("receiverName", "screenState", "deviceName", "theme", "location", "coordinates")


def card_state(msg: dict, state: dict) -> dict:
    """Merge one stdin line into the state. A cast that is "streaming" but shows no picture on the
    TV (we only draw between frames) reads as connecting."""
    out = dict(state)
    out.update({k: str(msg[k]) for k in KEYS if k in msg})
    if out.get("screenState") == "streaming":
        out["screenState"] = "connecting"
    return out


def frame_path(directory: Path, i: int) -> Path:
    return directory / f"card-{i}.png"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    width, height, directory = int(args[0]), int(args[1]), Path(args[2])
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickView

    app = QGuiApplication([sys.argv[0]])
    view = QQuickView()
    view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
    view.resize(width, height)
    view.setInitialProperties({"tvCard": True})
    view.setSource(QUrl.fromLocalFile(str(QML)))
    if view.errors():
        print("\n".join(e.toString() for e in view.errors()), file=sys.stderr)
        return 1
    root = view.rootObject()
    view.show()
    state: dict = {"screenState": "idle"}
    told = [False]

    import time as _time

    def settle(ms: int) -> None:
        """Run the scene for a while: colours and the dots change with short animations, and a
        grab in the middle of one mixed two themes."""
        end = _time.monotonic() + ms / 1000
        while _time.monotonic() < end:
            app.processEvents()
            _time.sleep(0.01)

    def draw() -> None:
        for k, v in state.items():
            root.setProperty(k, v)
        settle(SETTLE_MS)
        for i in range(FRAMES):
            root.setProperty("dotStep", i)
            settle(STEP_SETTLE_MS)
            tmp = directory / f".card-{i}.png"
            view.grabWindow().save(str(tmp))
            os.replace(tmp, frame_path(directory, i))
        if not told[0]:
            told[0] = True
            print("ready", flush=True)

    # stdin in a thread, handed over through a queue a timer empties: a QSocketNotifier on the
    # pipe never fired under the offscreen platform.
    import queue
    import threading
    lines: queue.Queue = queue.Queue()

    def read_stdin() -> None:
        for line in sys.stdin.buffer:
            lines.put(line)
        lines.put(None)                         # the TV process is gone
    threading.Thread(target=read_stdin, daemon=True).start()

    def take_lines() -> None:
        nonlocal state
        changed = False
        while not lines.empty():
            line = lines.get_nowait()
            if line is None:
                app.quit()
                return
            try:
                state = card_state(json.loads(line), state)
                changed = True
            except (ValueError, TypeError):
                continue
        if changed:
            draw()
    poll = QTimer()
    poll.timeout.connect(take_lines)
    poll.start(100)

    import time
    shown = [time.localtime().tm_min]

    def tick() -> None:                         # the clock: draw again when the minute changes
        if time.localtime().tm_min != shown[0]:
            shown[0] = time.localtime().tm_min
            draw()
    minute = QTimer()
    minute.timeout.connect(tick)
    minute.start(5_000)
    draw()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
