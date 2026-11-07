"""modes/workstation/casthub-appearance: Plasma colours → light/dark file. Made-up homes only."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "modes" / "workstation" / "casthub-appearance"


def run(kdeglobals: str | None) -> str:
    with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as out:
        cfg = Path(home) / ".config"
        cfg.mkdir()
        if kdeglobals is not None:
            (cfg / "kdeglobals").write_text(kdeglobals)
        env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=str(cfg), USER="alice", CASTHUB_APPEARANCE_DIR=out)
        subprocess.run(["bash", str(SCRIPT)], check=True, env=env)
        return (Path(out) / "alice").read_text().strip()


class TestAppearance(unittest.TestCase):
    def test_light(self):
        self.assertEqual(run("[Colors:Window]\nBackgroundNormal=239,240,241\n"), "light")

    def test_dark(self):
        self.assertEqual(run("[Colors:Window]\nBackgroundNormal=32,35,38\n"), "dark")

    def test_other_sections_ignored(self):
        text = "[Colors:View]\nBackgroundNormal=10,10,10\n[Colors:Window]\nBackgroundNormal=250,250,250\n"
        self.assertEqual(run(text), "light")

    def test_missing_file_defaults_to_light(self):
        self.assertEqual(run(None), "light")


if __name__ == "__main__":
    unittest.main()
