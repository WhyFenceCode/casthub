"""M9 desktop (SPEC D17): packages, generated KWin script, window names, /etc/xdg defaults."""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "shell"
sys.path.insert(0, str(ROOT / "brand"))
sys.path.insert(0, str(ROOT / "tools" / "lib"))

import importlib.util  # noqa: E402

import build  # noqa: E402

_spec = importlib.util.spec_from_file_location("merge_ini", ROOT / "tools" / "lib" / "merge-ini.py")
merge_ini = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(merge_ini)


def ini(path: Path) -> dict:
    return {(tuple(g), k): v for g, k, v in merge_ini.entries(str(path))}


class TestPackages(unittest.TestCase):
    def packages(self):
        yield SHELL / "plasma-shell" / "org.casthub.desktop"
        yield SHELL / "plasma-theme" / "casthub"
        yield SHELL / "decoration" / "org.casthub.decoration"
        yield SHELL / "kwin-script" / "org.casthub.views"
        yield from sorted(p for p in (SHELL / "plasmoids").iterdir() if p.is_dir())

    def test_metadata_ids_match_directories(self):
        for d in self.packages():
            meta = json.loads((d / "metadata.json").read_text())
            self.assertEqual(meta["KPlugin"]["Id"], d.name, d)

    def test_qml_entry_points_exist(self):
        for d in self.packages():
            meta = json.loads((d / "metadata.json").read_text())
            if meta.get("KPackageStructure") in ("Plasma/Applet", "KWin/Decoration"):
                self.assertTrue((d / "contents" / "ui" / "main.qml").is_file(), d)

    def test_shell_and_lnf_share_the_layout(self):
        layout = (SHELL / "layout.js").read_text()
        for f in [SHELL / "plasma-shell" / "org.casthub.desktop" / "contents" / "layout.js",
                  *SHELL.glob("lookandfeel/*/contents/layouts/org.casthub.desktop-layout.js")]:
            self.assertTrue(f.read_text().endswith(layout), f)
        for w in re.findall(r'addWidget\("([^"]+)"\)', layout):
            if w.startswith("org.casthub."):
                self.assertTrue((SHELL / "plasmoids" / w).is_dir(), w)

    def test_desktop_containment(self):
        defaults = (SHELL / "plasma-shell" / "org.casthub.desktop" / "contents" / "defaults").read_text()
        self.assertIn("Containment=org.casthub.desktopview", defaults)
        xml = (SHELL / "plasmoids" / "org.casthub.desktopview" / "contents" / "config" / "main.xml").read_text()
        # Filled in at install time from /etc/casthub/casthub.toml.
        self.assertIn("@LOCATION@", xml)
        self.assertIn("@COORDINATES@", xml)


class TestViewsScript(unittest.TestCase):
    def setUp(self):
        self.t = build.load()
        self.js = (SHELL / "kwin-script" / "org.casthub.views" / "contents" / "code" / "main.js").read_text()

    def const(self, name):
        return float(re.search(rf"const {name} = ([\d.]+);", self.js).group(1))

    def test_no_unfilled_placeholders(self):
        self.assertNotRegex(self.js, r"@[A-Z_]+@")

    def test_geometry_follows_tokens(self):
        # The panel frame (desktop view, Tokens.qml) and the docked window use the same numbers.
        sh = self.t["shell"]
        self.assertEqual(self.const("PANEL_TOP"), sh["panel_top"])
        self.assertEqual(self.const("PANEL_HEADER"), sh["panel_header"])
        self.assertEqual(self.const("PANEL_BOTTOM"), sh["panel_bottom"])
        self.assertEqual(self.const("PANEL_FIRST_COL"), sh["panel_first_col"])
        qml = (ROOT / "uikit" / "Tokens.qml").read_text()
        for key, name in (("shellPanelTop", "PANEL_TOP"), ("shellPanelBottom", "PANEL_BOTTOM"),
                          ("gridMargin", "MARGIN"), ("gridGutter", "GUTTER"), ("gridColumns", "COLUMNS")):
            m = re.search(rf"property \w+ {key}: ([\d.]+)", qml)
            self.assertIsNotNone(m, key)
            self.assertEqual(float(m.group(1)), self.const(name), key)

    def test_three_views_with_shortcuts(self):
        self.assertRegex(self.js, r'name: "Console", app: "org.kde.konsole"')
        self.assertRegex(self.js, r'name: "Files", app: "org.kde.dolphin"')
        self.assertIn('"Meta+" + (i + 1)', self.js)


class TestNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from PySide6.QtQml import QJSEngine
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("PySide6 not installed") from None
        src = (ROOT / "uikit" / "Names.js").read_text().replace(".pragma library", "")
        cls.engine = QJSEngine()
        r = cls.engine.evaluate(src)
        assert not r.isError(), r.toString()

    def call(self, expr):
        r = self.engine.evaluate(expr)
        self.assertFalse(r.isError(), r.toString())
        return r.toString()

    def test_caption_from_decoration_title(self):
        self.assertEqual(self.call('captionFromTitle("Projects — Dolphin")'), "Files · Projects")
        self.assertEqual(self.call('captionFromTitle("<placeholder> — Konsole", " — ")'), "Console — <placeholder>")
        self.assertEqual(self.call('captionFromTitle("Plain")'), "Plain")

    def test_app_labels(self):
        self.assertEqual(self.call('appLabel("org.kde.konsole", "Konsole")'), "Console")
        self.assertEqual(self.call('appLabel("org.example.other", "Other")'), "Other")


class TestSystemDefaults(unittest.TestCase):
    def test_plasmashell_uses_our_shell(self):
        self.assertEqual(ini(ROOT / "system" / "xdg" / "plasmashellrc")[(("Shell",), "ShellPackage")],
                         "org.casthub.desktop")

    def test_kwin_defaults(self):
        k = ini(ROOT / "system" / "xdg" / "kwinrc")
        self.assertEqual(k[(("Plugins",), "org.casthub.viewsEnabled")], "true")
        self.assertEqual(k[(("org.kde.kdecoration2",), "theme")], "org.casthub.decoration")
        # Same decoration as the global themes set.
        for v in ("light", "dark"):
            self.assertIn("theme=org.casthub.decoration",
                          (SHELL / "lookandfeel" / f"org.casthub.{v}" / "contents" / "defaults").read_text())

    def test_merge_ini_reads_nested_groups(self):
        src = ROOT / "tests" / "fixtures" / "nested.ini"
        self.assertEqual(ini(src), {(("A", "B c"), "k"): "v"})


if __name__ == "__main__":
    unittest.main()


class TestLeaveScreens(unittest.TestCase):
    def test_logout_screen_answers_the_greeter(self):
        qml = (SHELL / "plasma-shell" / "org.casthub.desktop" / "contents" / "logout" / "Logout.qml").read_text()
        for sig in ("logoutRequested", "haltRequested", "rebootRequested", "suspendRequested", "cancelRequested",
                    "lockScreenRequested"):
            self.assertIn(f"signal {sig}(", qml)
        self.assertIn("ShutdownType.ShutdownTypeDefault", qml)

    def test_top_bar_log_out_offers_every_choice(self):
        qml = (SHELL / "plasmoids" / "org.casthub.clock" / "contents" / "ui" / "main.qml").read_text()
        self.assertIn('member: "promptAll"', qml)

    def test_lock_screen_uses_signature_background(self):
        k = ini(ROOT / "system" / "xdg" / "kscreenlockerrc")
        self.assertEqual(k[(("Greeter", "Wallpaper", "org.kde.image", "General"), "Image")],
                         "/usr/share/wallpapers/CastHubLock/")

    def test_lock_background_renders(self):
        import tempfile

        import wallpaper
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "lock.png"
            wallpaper.render_lock(out, wallpaper.tokens(), 320, 180)
            self.assertTrue(out.stat().st_size > 0)
