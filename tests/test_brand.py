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

    def test_fonts_have_licences(self):
        fonts = ROOT / "brand" / "fonts"
        for family in ("BricolageGrotesque", "InstrumentSans", "DMMono", "MrsSaintDelafield"):
            self.assertTrue((fonts / f"OFL-{family}.txt").is_file(), family)


if __name__ == "__main__":
    unittest.main()
