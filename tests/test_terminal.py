"""Terminal status chips (system/profile.d/casthub-terminal.sh): ink on pastel, only in Cast Hub Konsole."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "system" / "profile.d" / "casthub-terminal.sh"


def run(cmds: str, casthub: bool = True) -> str:
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "TERM": "xterm-256color"}
    if casthub:
        env["CASTHUB_TERMINAL"] = "1"
    prog = f"PS1='$ '; . {SCRIPT}; {cmds}"
    r = subprocess.run(["bash", "--norc", "--noprofile", "-i", "-c", prog], env=env, capture_output=True, text=True, check=False)
    return r.stdout


class TestTerminal(unittest.TestCase):
    def test_chip_is_ink_on_pastel(self):
        self.assertEqual(run("casthub_chip error failed"), "\x1b[30;41m failed \x1b[0m")
        self.assertEqual(run("casthub_chip warn careful"), "\x1b[30;43m careful \x1b[0m")
        self.assertEqual(run("casthub_chip ok done"), "\x1b[30;42m done \x1b[0m")

    def test_failed_command_puts_chip_in_prompt(self):
        out = run(
            'false; __casthub_prompt_status; printf "%s|" "$PROMPT_START"; '
            'true; __casthub_prompt_status; printf "%s|%s" "$PROMPT_START" "$PS1"'
        )
        self.assertEqual(out, "\\[\\e[30;41m\\] exit 1 \\[\\e[0m\\] ||${PROMPT_START@P}$ ")

    def test_inactive_outside_casthub_konsole(self):
        self.assertEqual(run("type casthub_chip >/dev/null 2>&1 && echo on || echo off", casthub=False).strip(), "off")

    def test_ansi_slots_are_the_status_pastels(self):
        sys.path.insert(0, str(ROOT / "brand"))
        import build
        t = build.load()
        term = t["color"]["terminal"]
        c = t["color"]
        self.assertEqual((term["red"], term["yellow"], term["green"], term["black"]),
                         (c["error"], c["warning"], c["success"], c["on_status"]))


if __name__ == "__main__":
    unittest.main()
