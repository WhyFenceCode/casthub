"""modes/casthub-session decides who may start which mode. Users here are made up."""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestSessionGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        src = (ROOT / "modes" / "casthub-session").read_text()
        # Replace the real `exec` with an echo so nothing is launched.
        (d / "gate.sh").write_text(src.replace("    exec ", "    echo EXEC "))
        (d / "bin").mkdir()
        shims = {
            "logger": '#!/bin/sh\nexit 0\n',
            "id": '#!/bin/sh\ncase "$1" in -un) echo "$FAKE_USER";; -nG) echo "$FAKE_GROUPS";; esac\n',
        }
        for name, body in shims.items():
            p = d / "bin" / name
            p.write_text(body)
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        cls.dir = d

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_gate(self, user, groups, mode):
        env = dict(os.environ, FAKE_USER=user, FAKE_GROUPS=groups, PATH=f"{self.dir / 'bin'}:{os.environ['PATH']}")
        r = subprocess.run(["bash", str(self.dir / "gate.sh"), mode], capture_output=True, text=True, env=env)
        return r.returncode, r.stdout

    def test_allowed(self):
        cases = [
            ("alice", "alice", "workstation", "startplasma-wayland"),
            ("alice", "alice wheel", "console", "console/sway.conf"),
            ("casthub", "casthub nopasswdlogin", "display", "display/sway.conf"),
        ]
        for user, groups, mode, expect in cases:
            code, out = self.run_gate(user, groups, mode)
            self.assertEqual(code, 0, (user, mode))
            self.assertIn(expect, out)

    def test_refused(self):
        cases = [
            ("alice", "alice", "console"),             # not an admin
            ("alice", "alice wheel", "display"),       # display is casthub-only
            ("casthub", "casthub nopasswdlogin", "workstation"),
            ("casthub", "casthub wheel", "console"),   # even if misconfigured into wheel
            ("alice", "alice", "bogus"),
        ]
        for user, groups, mode in cases:
            code, out = self.run_gate(user, groups, mode)
            self.assertNotEqual(code, 0, (user, mode))
            self.assertNotIn("EXEC", out)


if __name__ == "__main__":
    unittest.main()
