"""brand/splash.py builds a complete Plymouth theme (small size, low fps: fast)."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestSplash(unittest.TestCase):
    def test_build(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "theme"
            cmd = [sys.executable, str(ROOT / "brand" / "splash.py"), "--out", str(out),
                   "--width", "480", "--height", "270", "--fps", "5", "--host", "test-host"]
            subprocess.run(cmd, check=True, capture_output=True)
            script = (out / "casthub.script").read_text()
            frames = sorted(out.glob("sig-*.png"))
            self.assertGreater(len(frames), 10)
            # every frame the script names exists, and only those
            named = [line.split('"')[1] for line in script.splitlines() if line.startswith("files[")]
            self.assertEqual(sorted(named), sorted(f.name for f in frames))
            # the refresh function must update globals, or the animation never advances
            self.assertIn("global.tick = global.tick + 1;", script)
            for f in ("label-host.png", "label-esc.png", "status-boot.png", "status-shutdown.png", "casthub.plymouth"):
                self.assertTrue((out / f).is_file(), f)
            self.assertIn("ModuleName=script", (out / "casthub.plymouth").read_text())


if __name__ == "__main__":
    unittest.main()
