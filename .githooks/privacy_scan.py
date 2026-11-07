#!/usr/bin/env python3
"""Privacy scanner for the Cast Hub repo.

Enforces "Rule: nothing from this machine goes to GitHub" (docs/SPEC.md).
Stdlib only, so it runs before any project dependencies are installed.

Usage:
  privacy_scan.py --staged            # pre-commit: staged changes
  privacy_scan.py --message FILE      # commit-msg: the commit message
  privacy_scan.py --push LOCAL REMOTE # pre-push: every commit being pushed
  privacy_scan.py --paths P [P ...]   # ad hoc: scan working-tree files

Exit 0 = clean, 1 = findings (commit/push blocked), 2 = usage/internal error.

Matched values from the denylist or machine identifiers are masked in the
output so the report itself can't leak them if it is pasted somewhere.
"""

from __future__ import annotations

import fnmatch
import ipaddress
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ZERO_SHA = "0" * 40

# Paths that must never be tracked, whatever their content.
FORBIDDEN_PATH_GLOBS = [
    "local/*",
    "casthub.toml",
    "*/casthub.toml",
    "*.nmconnection",
    "*.log",
    "*.journal",
    "*.pcap",
    "*.pcapng",
    "*.cap",
    "*.har",
    "*.rom",
    "*.bin",
    "*.fd",
    "*.dmp",
    "*.pem",
    "*.key",
    ".env",
    ".env.*",
    "Screenshot*",
    "*/Screenshot*",
    "screenshot*",
    "*/screenshot*",
    "Screencast*",
    "*/Screencast*",
    "*.heic",
    # Owner decision: the spec PDF is never committed; docs/SPEC.md is tracked instead.
    "SPEC.pdf",
    "*/SPEC.pdf",
    # Owner decision: nothing from sudo / the root helper is ever committed.
    "*root-actions*",
    "*.askpass",
    "*sudo*.log",
    "*/.sudo_as_admin_successful",
    ".sudo_as_admin_successful",
]

# GUIDs and literal secrets are allowed only in made-up test fixtures.
FIXTURE_PREFIXES = ("tests/fixtures/",)

# The scanner and its tests necessarily contain example patterns.
SELF_PATHS = {".githooks/privacy_scan.py", "tests/test_privacy_scan.py"}

MAC_RE = re.compile(r"(?<![0-9A-Fa-f:-])(?:[0-9A-Fa-f]{2}([:-]))(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])")
IPV4_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
IPV6_CANDIDATE_RE = re.compile(r"(?<![0-9A-Fa-f:])[0-9A-Fa-f:]*::?[0-9A-Fa-f:]+(?:%\w+)?")
GUID_RE = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}(?![0-9A-Fa-f])"
)
# A quoted, non-empty, non-placeholder literal assigned to a secret-ish key.
SECRET_RE = re.compile(
    r"""(?ix)\b(pin|pin_hash|psk|password|passwd|passphrase|wpa_passphrase|secret)\b
        \s*[=:]\s*(["'])(?!<)(?!\2)([^"'\n]+)\2"""
)

# A password being fed to sudo, in any form.
SUDO_PW_RE = re.compile(r"\bsudo\b[^\n;&|]*\s-[A-Za-z]*S\b|\bSUDO_ASKPASS\s*=|--stdin\b[^\n]*\bsudo\b")

DOC_V4_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
DOC_V6_NET = ipaddress.ip_network("2001:db8::/32")
# RFC 7042 documentation MAC range 00-00-5E-00-53-xx, plus all-zero / broadcast.
DOC_MAC_PREFIX = "00005e0053"
GENERIC_MACS = {"000000000000", "ffffffffffff"}
GENERIC_HOSTNAMES = {"localhost", "localhost-live", "fedora", "linux"}


@dataclass(frozen=True)
class Finding:
    where: str
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"  {self.where}: [{self.rule}] {self.detail}"


def mask(value: str) -> str:
    value = value.strip()
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


# --------------------------------------------------------------------------
# Denylist: local/denylist.txt plus identifiers read from this machine now.
# --------------------------------------------------------------------------

def _read(path: str) -> str:
    try:
        return Path(path).read_text(errors="ignore").strip()
    except OSError:
        return ""


def machine_identifiers() -> set[str]:
    ids: set[str] = set()
    host = _read("/etc/hostname") or os.uname().nodename
    for h in {host, host.split(".")[0]}:
        if h and len(h) >= 4 and h.lower() not in GENERIC_HOSTNAMES:
            ids.add(h)
    mid = _read("/etc/machine-id")
    if mid:
        ids.add(mid)
    net = Path("/sys/class/net")
    if net.is_dir():
        for iface in net.iterdir():
            mac = _read(str(iface / "address")).lower()
            if mac and mac.replace(":", "") not in GENERIC_MACS:
                ids.add(mac)
    for f in ("product_uuid", "product_serial", "board_serial", "chassis_serial"):
        v = _read(f"/sys/class/dmi/id/{f}")
        if v and v.lower() not in {"none", "default string", "to be filled by o.e.m."} and len(v) >= 4:
            ids.add(v)
    # Saved NetworkManager connection names are usually SSIDs.
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
        for line in out.splitlines():
            name, _, ctype = line.rpartition(":")
            name = name.replace("\\:", ":")
            if "wireless" in ctype and len(name) >= 4:
                ids.add(name)
    except (OSError, subprocess.SubprocessError):
        pass
    return ids


def load_denylist(repo: Path) -> set[str]:
    terms: set[str] = set()
    f = repo / "local" / "denylist.txt"
    if f.is_file():
        for line in f.read_text(errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.add(line)
    return terms


def load_allowlist(repo: Path) -> set[str]:
    """Exact strings reviewed and accepted by the owner (tracked, so visible in diffs)."""
    terms: set[str] = set()
    f = repo / ".githooks" / "allowlist.txt"
    if f.is_file():
        for line in f.read_text(errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                terms.add(line.lower())
    return terms


# --------------------------------------------------------------------------
# Content rules
# --------------------------------------------------------------------------

def _ipv4_ok(s: str) -> bool:
    try:
        ip = ipaddress.IPv4Address(s)
    except ValueError:
        return True  # not an address (e.g. a version string with octet > 255)
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or str(ip).startswith("255."):
        return True
    return any(ip in n for n in DOC_V4_NETS)


def _ipv6_bad(s: str) -> bool:
    s = s.split("%")[0].strip(":") if s.count(":") < 2 else s.split("%")[0]
    if s.count(":") < 2:
        return False
    try:
        ip = ipaddress.IPv6Address(s)
    except ValueError:
        return False
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip in DOC_V6_NET:
        return False
    return True


def scan_text(path: str, lineno_base: int, text: str, deny: set[str], allow: set[str]) -> list[Finding]:
    if path in SELF_PATHS:
        deny_only = True
    else:
        deny_only = False
    in_fixture = path.startswith(FIXTURE_PREFIXES)
    findings: list[Finding] = []
    deny_lower = [(d, d.lower()) for d in deny]

    for i, line in enumerate(text.splitlines(), start=lineno_base):
        where = f"{path}:{i}" if path else f"line {i}"
        low = line.lower()
        for orig, d in deny_lower:
            if d and d in low and d not in allow:
                findings.append(Finding(where, "denylist", f"machine/denylisted value {mask(orig)}"))
        if deny_only:
            continue

        for m in MAC_RE.finditer(line):
            raw = m.group(0).lower()
            flat = re.sub(r"[:-]", "", raw)
            if flat.startswith(DOC_MAC_PREFIX) or flat in GENERIC_MACS or raw in allow:
                continue
            findings.append(Finding(where, "mac", f"MAC address {mask(raw)} (use 00:00:5e:00:53:xx)"))

        for m in IPV4_RE.finditer(line):
            if not _ipv4_ok(m.group(0)) and m.group(0) not in allow:
                findings.append(Finding(where, "ipv4", f"IP address {mask(m.group(0))} (use 192.0.2.x)"))

        for m in IPV6_CANDIDATE_RE.finditer(line):
            if _ipv6_bad(m.group(0)) and m.group(0).lower() not in allow:
                findings.append(Finding(where, "ipv6", f"IPv6 address {mask(m.group(0))} (use 2001:db8::/32)"))

        if not in_fixture:
            for m in GUID_RE.finditer(line):
                g = m.group(0).lower()
                if set(g.replace("-", "")) == {"0"} or g in allow:
                    continue
                findings.append(Finding(where, "guid", f"GUID {mask(g)} outside tests/fixtures/"))

            if SUDO_PW_RE.search(line):
                findings.append(Finding(where, "sudo", "sudo password handling (sudo -S / SUDO_ASKPASS); use tools/rootd.py"))

            for m in SECRET_RE.finditer(line):
                if m.group(3).lower() in allow:
                    continue
                findings.append(Finding(where, "secret", f"literal value for '{m.group(1)}' (leave empty or use <placeholder>)"))
    return findings


def check_path(path: str, deny: set[str], allow: set[str]) -> list[Finding]:
    findings = []
    for g in FORBIDDEN_PATH_GLOBS:
        if fnmatch.fnmatch(path, g) and not path.endswith("casthub.example.toml"):
            findings.append(Finding(path, "path", f"file type/location is never committed ({g})"))
            break
    low = path.lower()
    for d in deny:
        if d.lower() in low and d.lower() not in allow:
            findings.append(Finding(path, "denylist", f"file name contains {mask(d)}"))
    return findings


def blob_to_text(path: str, data: bytes) -> str | None:
    """Return scannable text for a blob, or None if it is opaque binary."""
    if path.lower().endswith(".pdf"):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "f.pdf"
            p.write_bytes(data)
            parts = []
            for cmd in (["pdftotext", "-q", str(p), "-"], ["pdfinfo", str(p)]):
                try:
                    parts.append(subprocess.run(check=False, args=cmd, capture_output=True, text=True, timeout=60).stdout)
                except (OSError, subprocess.SubprocessError):
                    return None
            return "\n".join(parts)
    if b"\0" in data[:8192]:
        # Binary: still search raw bytes for denylist terms (e.g. PNG text chunks, EXIF).
        return data.decode("latin-1")
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Git plumbing
# --------------------------------------------------------------------------

def git(*args: str, binary: bool = False) -> str | bytes:
    out = subprocess.run(["git", *args], capture_output=True, check=True)
    return out.stdout if binary else out.stdout.decode("utf-8", errors="replace")


def scan_blob(path: str, data: bytes, deny: set[str], allow: set[str]) -> list[Finding]:
    findings = check_path(path, deny, allow)
    text = blob_to_text(path, data)
    if text is None:
        findings.append(Finding(path, "unscannable", "could not extract text to scan; review manually"))
        return findings
    is_binary = b"\0" in data[:8192] and not path.lower().endswith(".pdf")
    findings += scan_text(path, 1, text, deny, allow) if not is_binary else [
        f for f in scan_text(path, 1, text, deny, allow) if f.rule == "denylist"
    ]
    return findings


def scan_staged(deny: set[str], allow: set[str]) -> list[Finding]:
    names = git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").split("\0")
    findings: list[Finding] = []
    for path in filter(None, names):
        findings += scan_blob(path, git("show", f":{path}", binary=True), deny, allow)
    return findings


def scan_commit(sha: str, deny: set[str], allow: set[str]) -> list[Finding]:
    findings = [
        Finding(f"commit {sha[:10]} message", f.rule, f.detail)
        for f in scan_text("", 1, git("log", "-1", "--format=%an%n%ae%n%B", sha), deny, allow)
    ]
    names = git("diff-tree", "--root", "--no-commit-id", "-r", "-z", "--name-only", "--diff-filter=ACMR", sha).split("\0")
    for path in filter(None, names):
        for f in scan_blob(path, git("show", f"{sha}:{path}", binary=True), deny, allow):
            findings.append(Finding(f"{sha[:10]} {f.where}", f.rule, f.detail))
    return findings


def main(argv: list[str]) -> int:
    try:
        repo = Path(git("rev-parse", "--show-toplevel").strip())
    except (OSError, subprocess.CalledProcessError):
        repo = Path.cwd()
    deny = load_denylist(repo) | machine_identifiers()
    allow = load_allowlist(repo)

    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    mode, rest = argv[0], argv[1:]
    findings: list[Finding] = []
    if mode == "--staged":
        findings = scan_staged(deny, allow)
    elif mode == "--message" and rest:
        text = "\n".join(ln for ln in Path(rest[0]).read_text().splitlines() if not ln.startswith("#"))
        findings = [Finding("commit message", f.rule, f.detail) for f in scan_text("", 1, text, deny, allow)]
    elif mode == "--push":
        # stdin lines: <local ref> <local sha> <remote ref> <remote sha>
        for line in sys.stdin:
            parts = line.split()
            if len(parts) != 4 or parts[1] == ZERO_SHA:
                continue
            local_sha, remote_sha = parts[1], parts[3]
            rng = [local_sha, "--not", "--remotes"] if remote_sha == ZERO_SHA else [f"{remote_sha}..{local_sha}"]
            for sha in filter(None, git("rev-list", *rng).split()):
                findings += scan_commit(sha, deny, allow)
    elif mode == "--paths":
        for p in rest:
            rel = os.path.relpath(p, repo)
            findings += scan_blob(rel, Path(p).read_bytes(), deny, allow)
    else:
        print(__doc__, file=sys.stderr)
        return 2

    if findings:
        print("privacy-scan: BLOCKED. Machine-specific or sensitive data found:", file=sys.stderr)
        for f in dict.fromkeys(findings):
            print(f, file=sys.stderr)
        print(
            "\nFix: move it to local/, replace with placeholders (192.0.2.10, 00:00:5e:00:53:01, <ssid>),"
            "\nor, for a reviewed false positive, add the exact string to .githooks/allowlist.txt.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
