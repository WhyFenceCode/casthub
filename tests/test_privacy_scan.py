"""Tests for .githooks/privacy_scan.py. All values are made up or documentation ranges."""

import importlib.util
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "privacy_scan", Path(__file__).resolve().parents[1] / ".githooks" / "privacy_scan.py"
)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)


def rules(path: str, text: str, deny=frozenset(), allow=frozenset()):
    return [f.rule for f in ps.scan_text(path, 1, text, set(deny), set(allow))]


# Built at runtime so this file itself never contains a literal non-doc value.
REAL_MAC = ":".join(["a4", "c3", "f0", "12", "34", "56"])
REAL_IP = ".".join(["192", "168", "1", "23"])
REAL_V6 = "2600:1f18::" + "1"
REAL_GUID = "-".join(["3f2504e0", "4f89", "11d3", "9a0c", "0305e82c3301"])


class TestRules(unittest.TestCase):
    def test_doc_placeholders_pass(self):
        text = "addr 192.0.2.10 mac 00:00:5e:00:53:01 v6 2001:db8::10 bind 0.0.0.0 lo 127.0.0.1 mdns 224.0.0.251"
        self.assertEqual(rules("docs/x.md", text), [])

    def test_mac_blocked(self):
        self.assertIn("mac", rules("a.py", f"mac={REAL_MAC}"))
        self.assertIn("mac", rules("a.py", REAL_MAC.replace(":", "-")))

    def test_ipv4_blocked(self):
        self.assertIn("ipv4", rules("a.md", f"host {REAL_IP}:7250"))

    def test_version_strings_not_ips(self):
        self.assertEqual(rules("a.md", "gstreamer 1.24.300.5 and 999.1.1.1"), [])

    def test_ipv6_blocked(self):
        self.assertIn("ipv6", rules("a.md", f"peer {REAL_V6}"))

    def test_times_not_ipv6(self):
        self.assertEqual(rules("a.md", "21:04:05 listening"), [])

    def test_guid_only_in_fixtures(self):
        self.assertIn("guid", rules("casthub/mdns.py", REAL_GUID))
        self.assertEqual(rules("tests/fixtures/mice.txt", REAL_GUID), [])

    def test_secret_literal_blocked(self):
        self.assertIn("secret", rules("casthub.example.toml", 'pin = "' + "48" + "21" + '"'))
        self.assertEqual(rules("casthub.example.toml", 'pin = ""'), [])
        self.assertEqual(rules("casthub.example.toml", 'psk = "<psk>"'), [])

    def test_denylist_case_insensitive_and_masked(self):
        findings = ps.scan_text("docs/a.md", 1, "Joined MyHomeNet today", {"myhomenet"}, set())
        self.assertEqual([f.rule for f in findings], ["denylist"])
        self.assertNotIn("myhomenet", str(findings[0]).lower())

    def test_allowlist(self):
        self.assertEqual(rules("a.md", f"x {REAL_IP}", allow={REAL_IP}), [])

    def test_forbidden_paths(self):
        for p in ["local/probe.txt", "casthub.toml", "etc/casthub.toml", "cap.pcapng", "fw.rom",
                  "Screenshot_2026.png", "docs/screenshot-login.png", "x.log", "docs/SPEC.pdf"]:
            self.assertTrue(ps.check_path(p, set(), set()), p)
        for p in ["casthub.example.toml", "brand/wallpaper.png", "design/login.pdf"]:
            self.assertFalse(ps.check_path(p, set(), set()), p)


if __name__ == "__main__":
    unittest.main()
