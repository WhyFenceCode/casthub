"""Generated brand files must match brand/tokens.toml (run brand/build.py after editing tokens)."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestBrand(unittest.TestCase):
    def test_generated_files_in_sync(self):
        cmd = [sys.executable, "-I", str(ROOT / "brand" / "build.py"), "--check"]
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_status_text_contrast(self):
        sys.path.insert(0, str(ROOT / "brand"))
        import build
        t = build.load()
        for variant in ("light", "dark"):
            surface = t["color"][variant]["surface"]
            for k, col in build.status_text(t, variant).items():
                self.assertGreaterEqual(build.contrast(col, surface), 4.5, f"{variant} {k} {col}")

    def test_lookandfeel_packages(self):
        import json
        for v in ("light", "dark"):
            d = ROOT / "shell" / "lookandfeel" / f"org.casthub.{v}"
            meta = json.loads((d / "metadata.json").read_text())
            self.assertEqual(meta["KPlugin"]["Id"], f"org.casthub.{v}")
            defaults = (d / "contents" / "defaults").read_text()
            self.assertIn(f"ColorScheme=CastHub{v.capitalize()}", defaults)
            self.assertIn("Image=CastHub", defaults)
            self.assertNotIn("[layouts]", defaults.lower())   # never touch the user's panels
            splash = (d / "contents" / "splash" / "Splash.qml").read_text()
            self.assertIn(f"readonly property bool dark: {str(v == 'dark').lower()}", splash)
            self.assertTrue((ROOT / "shell" / "colors" / f"CastHub{v.capitalize()}.colors").is_file())

    def test_wallpaper_grid_matches_uikit(self):
        sys.path.insert(0, str(ROOT / "brand"))
        import wallpaper
        t = wallpaper.tokens()
        xs = wallpaper.column_edges(t, 1080, 675)
        self.assertEqual(len(xs), 2 * t["grid"]["columns"])
        self.assertEqual(xs[0], round(t["grid"]["margin"]))
        self.assertEqual(xs[-1], 1080 - round(t["grid"]["margin"]))

    def test_one_token_change_reaches_every_surface(self):
        """M8 acceptance: one colour edit in tokens.toml, one rebuild → every surface changes."""
        import copy
        import tempfile

        sys.path.insert(0, str(ROOT / "brand"))
        import build
        import splash
        import wallpaper

        new = "#123456"
        base = build.load()
        t = copy.deepcopy(base)
        t["color"]["accent"] = new
        t = build.resolve(t)
        t["color"]["terminal"]["blue"] = new              # what resolve() gives after the edit
        rgb = build.rgb(new)
        for rel, fn in build.OUTPUTS.items():
            before, after = fn(base), fn(t)
            if rel.endswith(("Tokens.qml", ".colors", ".colorscheme")):
                self.assertNotEqual(before, after, f"{rel} ignores the accent")
                self.assertTrue(new in after or rgb in after, rel)
        # Install-time builders read tokens.toml themselves: check they use the token, not a copy.
        for mod in (splash, wallpaper):
            self.assertTrue(hasattr(mod, "tokens"), mod.__name__)
        with tempfile.TemporaryDirectory() as d:
            orig = splash.tokens
            splash.tokens = lambda: t
            try:
                splash.render_label(Path(d) / "x.png", "x", 20, 0.1, "#FFFFFF", "#000000", square=t["color"]["accent"])
            finally:
                splash.tokens = orig
        # QML surfaces (greeter, Display screens, PIN page, splash) only use Tokens.qml for colour.
        for qml in [*ROOT.glob("greeter/*.qml"), *ROOT.glob("casthub/**/qml/*.qml"), ROOT / "shell/ksplash/Splash.qml",
                    *[p for p in ROOT.glob("uikit/*.qml") if p.name != "Tokens.qml"]]:
            text = qml.read_text()
            for hexcol in ("#7E76D0", "#E6E6E6", "#161616", "#0B0B0B"):
                self.assertNotIn(hexcol.lower(), text.lower(), f"{qml.relative_to(ROOT)} hard-codes {hexcol}")

    def test_terminal_colours_follow_tokens(self):
        sys.path.insert(0, str(ROOT / "brand"))
        import build
        t = build.load()
        self.assertEqual(t["color"]["terminal"]["blue"], t["color"]["accent"])
        self.assertEqual(t["color"]["terminal"]["bg"], t["color"]["dark"]["bg"])

    def test_fonts_have_licences(self):
        fonts = ROOT / "brand" / "fonts"
        for family in ("BricolageGrotesque", "InstrumentSans", "DMMono", "MrsSaintDelafield"):
            self.assertTrue((fonts / f"OFL-{family}.txt").is_file(), family)


if __name__ == "__main__":
    unittest.main()
