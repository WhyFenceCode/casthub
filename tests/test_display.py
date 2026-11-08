"""Display mode: config, PIN, the UI app and its control socket. Made-up values only."""

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from casthub import config  # noqa: E402
from casthub.switcher import pin  # noqa: E402
from casthub.switcher.setpin import set_pin_hash  # noqa: E402


class TestConfig(unittest.TestCase):
    def test_example_parses(self):
        cfg = config.load(ROOT / "casthub.example.toml")
        self.assertEqual(cfg.receiver_name(0, "eDP-1"), "Brand-Main")
        self.assertEqual(cfg.receiver_name(1, "HDMI-A-1"), "Brand-2")

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load(Path("/nonexistent/casthub.toml")), config.Config())

    def test_rejects_bad_values(self):
        for bad in ({"layout": {"mode": "x"}}, {"display": {"theme": "pink"}}, {"miracast": {"base_port": 80}},
                    {"switcher": {"pin": "1234"}}, {"display": {"latitude": 91}}, {"hotspot": {"enabled": "yes"}},
                    {"miracast": {"base_port": True}}):
            with self.assertRaises(config.ConfigError, msg=bad):
                config.parse(bad)

    def test_coordinates(self):
        cfg = config.parse({"display": {"latitude": 12.345, "longitude": -56.789}})
        self.assertEqual(cfg.coordinates(), "12.35° N · 56.79° W")


class TestPin(unittest.TestCase):
    def test_hash_and_verify(self):
        h = pin.hash_pin("2468")
        self.assertTrue(h.startswith("scrypt$"))
        self.assertNotIn("2468", h)
        self.assertTrue(pin.verify_pin("2468", h))
        self.assertFalse(pin.verify_pin("2469", h))
        self.assertFalse(pin.verify_pin("2468", "garbage"))
        with self.assertRaises(ValueError):
            pin.hash_pin("12a4")

    def test_set_pin_hash_in_toml(self):
        text = '[brand]\nname_prefix = "Brand"\n\n[switcher]\npin = "<placeholder>"\n\n[display]\ntheme = "auto"\n'
        out = set_pin_hash(text, "scrypt$X")
        self.assertNotIn("<placeholder>", out)
        self.assertEqual(out.count("pin_hash"), 1)
        self.assertIn('[switcher]\npin_hash = "scrypt$X"', out)
        self.assertEqual(config.parse(__import__("tomllib").loads(out)).pin_hash, "scrypt$X")
        self.assertIn('[switcher]\npin_hash = "y"', set_pin_hash("", "y"))
        self.assertEqual(set_pin_hash(out, "z").count("pin_hash"), 1)


class TestApp(unittest.TestCase):
    def test_screens_and_socket(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication

        from casthub.ui import app as uiapp

        tmp = tempfile.mkdtemp()
        os.environ["XDG_RUNTIME_DIR"] = tmp
        os.environ.pop("SWAYSOCK", None)
        qapp = QGuiApplication.instance() or QGuiApplication([])
        cfg = config.parse({"brand": {"name_prefix": "Brand"}, "display": {"location": "Springfield", "theme": "light"}})
        ui = uiapp.DisplayUI(qapp, cfg)
        self.assertEqual(len(ui.views), len(qapp.screens()))
        out_name, view = next(iter(ui.views.items()))
        root = view.rootObject()
        self.assertEqual(root.property("receiverName"), "")          # Loading until the hub names it
        self.assertTrue(root.property("loading"))

        replies = []

        def client():
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(5)
            s.connect(os.path.join(tmp, "casthub-ui.sock"))
            f = s.makefile("rwb")
            for m in ({"cmd": "state", "output": out_name, "state": "connecting", "device": "Phone",
                       "name": "CAST-ABCDE"},
                      {"cmd": "busy", "output": out_name, "text": "in use"}, {"cmd": "nope"}, {"cmd": "leave"},
                      {"cmd": "pin", "output": out_name, "pin": "12345670"}, {"cmd": "pin", "output": out_name, "pin": "x1"}):
                f.write(json.dumps(m).encode() + b"\n")
                f.flush()
                replies.append(json.loads(f.readline()))
            s.close()

        t = threading.Thread(target=client)
        t.start()
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.start(5000)
        while t.is_alive() and deadline.isActive():
            qapp.processEvents()
        t.join(1)
        self.assertEqual([r["ok"] for r in replies], [True, True, False, True, True, False])
        self.assertEqual(root.property("pinCode"), "12345670")
        self.assertEqual(root.property("screenState"), "connecting")
        self.assertEqual(root.property("deviceName"), "Phone")
        self.assertEqual(root.property("receiverName"), "CAST-ABCDE")
        self.assertIn('<font color="#7e76d0">.</font>', root.dotted("Connecting", False).lower())
        self.assertEqual(root.property("busyText"), "in use")
        self.assertTrue(root.property("leaveOpen"))

        # No PIN: the page opens on the choices; arrows move, Esc closes it.
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        page = next(c for c in root.findChildren(type(root)) if c is not root and c.property("selected") is not None)
        view.show()
        view.requestActivate()
        page.forceActiveFocus()
        QTest.keyClick(view, Qt.Key.Key_Down)
        self.assertEqual(page.property("selected"), 1)
        chosen = []
        root.leaveChosen.connect(chosen.append)
        ui.on_action = lambda r, a: chosen.append(("ran", a))   # don't actually reboot the test machine
        # Passthrough setup: its own view, which starts a search for displays; Esc goes back.
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertEqual(page.property("view"), "passthrough")
        self.assertIn(("ran", "tv_find"), chosen)
        ui.handle({"cmd": "passthrough", "state": "off", "tv": "", "why": "", "tvs": ["<tv>"],
                   "locked": False, "allowed": True})
        qapp.processEvents()
        self.assertEqual(page.property("actions").toVariant()[0]["id"], "tv_on:<tv>")
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertIn(("ran", "tv_on:<tv>"), chosen)
        QTest.keyClick(view, Qt.Key.Key_Escape)
        self.assertEqual((page.property("view"), page.property("selected")), ("main", 1))
        QTest.keyClick(view, Qt.Key.Key_Down)
        QTest.keyClick(view, Qt.Key.Key_Return)
        self.assertIn(("ran", "reboot"), chosen)
        QTest.keyClick(view, Qt.Key.Key_Escape)
        self.assertFalse(root.property("leaveOpen"))
        for v in ui.views.values():
            v.close()


if __name__ == "__main__":
    unittest.main()


class TestJoinCode(unittest.TestCase):
    def test_fields_unescaped(self):
        from casthub.discovery import hotspot
        from casthub.ui import app
        code = hotspot.qr_payload('Cast Hub; "AB"', "pa:ss\\word")
        self.assertEqual(app.hotspot_ssid(code), 'Cast Hub; "AB"')
        self.assertEqual(app.hotspot_password(code), "pa:ss\\word")
        self.assertEqual(app.hotspot_ssid(""), "")
        self.assertEqual(app.hotspot_ssid("http://example.invalid"), "")
