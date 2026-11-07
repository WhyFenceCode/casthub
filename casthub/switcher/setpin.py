"""casthub-pin: set the switcher PIN for the LAN page (SPEC D10). Run by the owner in a terminal (as root):

    sudo /usr/libexec/casthub/casthub-pin

Prompts twice (no echo) and writes only `pin_hash` into /etc/casthub/casthub.toml
(root:casthub, 0640), creating the file if needed. The PIN itself is never stored.
"""

from __future__ import annotations

import getpass
import grp
import os
import re
import sys
from pathlib import Path

from casthub.switcher.pin import hash_pin, valid_pin

CONFIG = Path("/etc/casthub/casthub.toml")


def _toml_line(key: str, value: str) -> str:
    return f'{key} = "{value}"'


def set_pin_hash(text: str, pin_hash: str) -> str:
    """Return the TOML text with [switcher] pin_hash set (and any plain `pin` removed)."""
    entry = _toml_line("pin_hash", pin_hash)
    lines = text.splitlines()
    out, in_sw, done, seen_sw = [], False, False, False
    for ln in lines:
        head = re.match(r"\s*\[([^\]]+)\]\s*(#.*)?$", ln)
        if head:
            if in_sw and not done:
                out.append(entry)
                done = True
            in_sw = head.group(1).strip() == "switcher"
            seen_sw = seen_sw or in_sw
        elif in_sw and re.match(r"\s*(pin|pin_hash)\s*=", ln):
            if not done:
                out.append(entry)
                done = True
            continue
        out.append(ln)
    if in_sw and not done:
        out.append(entry)
        done = True
    if not seen_sw:
        out += ["", "[switcher]", entry]
    return "\n".join(out) + "\n"


def main() -> int:
    if os.geteuid() != 0:
        print("run as root: sudo /usr/libexec/casthub/casthub-pin", file=sys.stderr)
        return 1
    pin = getpass.getpass("New switcher PIN (4-8 digits): ")
    if not valid_pin(pin):
        print("PIN must be 4 to 8 digits", file=sys.stderr)
        return 1
    if getpass.getpass("Again: ") != pin:
        print("PINs differ; nothing changed", file=sys.stderr)
        return 1
    text = CONFIG.read_text() if CONFIG.exists() else ""
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(set_pin_hash(text, hash_pin(pin)))
    gid = grp.getgrnam("casthub").gr_gid
    os.chown(tmp, 0, gid)
    os.chmod(tmp, 0o640)
    os.replace(tmp, CONFIG)
    print(f"PIN set (hash only) in {CONFIG}. Used by the LAN page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
