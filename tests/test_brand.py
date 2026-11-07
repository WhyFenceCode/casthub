"""Generated brand files must match brand/tokens.toml (run brand/build.py after editing tokens)."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestBrand(unittest.TestCase):
    def test_generated_files_in_sync(self):
        r = subprocess.run([sys.executable, "-I", str(ROOT / "brand" / "build.py"), "--check"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_fonts_have_licences(self):
        fonts = ROOT / "brand" / "fonts"
        for family in ("BricolageGrotesque", "InstrumentSans", "DMMono", "MrsSaintDelafield"):
            self.assertTrue((fonts / f"OFL-{family}.txt").is_file(), family)


if __name__ == "__main__":
    unittest.main()
